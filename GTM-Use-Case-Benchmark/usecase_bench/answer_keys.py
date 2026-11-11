"""Fixed answer keys for the decision jobs (tasks.json "answer_key": true), built independently of the provider outputs.

Step 1 (key): a separate Claude run that never sees any provider output researches the input and writes the 5-8
questions any good answer must cover, with the answer, importance (critical|useful) and a source.
Step 2 (grade): one call per task grades every run (anonymous labels) on every key item: correct | wrong | missing.
An existing key is kept; a task is re-graded when a provider was added or the key was changed by key_audit (graded_arms = []).

    python -m usecase_bench.answer_keys [job ...]   ->  results/answer_keys/<job>/<i>.json
"""
import json
import os
import random
import sys
from concurrent.futures import ThreadPoolExecutor

from . import KEYS, PROVIDERS, load_tasks, today
from .claude_cli import FETCH, LOG, plain_call, web_call
from .judge import rec

OUT = KEYS

KEY = """Today is {today}. You are writing the ANSWER KEY for a research task, before seeing anyone's answer.
TASK TYPE: {name}
INSTRUCTIONS THE RESEARCHERS GOT:
{instructions}

Write the 5-8 questions that any good answer to this task MUST cover for the person who has to act on it today
(an investment committee, a lender, a compliance officer, a seller before the meeting, a portfolio manager).
Pick the decision-relevant questions, not trivia: e.g. current rating and outlook, upcoming debt maturities, recent
default or restructuring, going-concern warnings, the correct legal entity and its registry number, sanctions / PEP /
enforcement results, the events after the since-date that change the position, current leadership and strategy.
Research each answer yourself: search, and read pages with Bash: {fetch} "<url>" when a page is blocked or a PDF.
Mark "importance": "critical" when getting it wrong or missing it would change the decision, else "useful".
If the true answer is "none found" (e.g. no sanctions hit), say so and give the check you ran.
Reply with JSON only: [{{"id": "k1", "question": "...", "answer": "...", "importance": "critical|useful", "source": "url"}}]"""

GRADE = """Today is {today}. Grade several anonymous answers (labels) against a fixed answer key for the same task.
TASK: {task}
For every key item and every label, decide:
- "correct": the answer states the key's answer (same substance; wording, rounding and minor date differences are fine)
- "wrong": the answer states something that contradicts the key on that item (wrong entity, wrong value, wrong status)
- "missing": the answer does not address the item
Reply with JSON only: [{{"item": "k1", "label": "A", "grade": "correct|wrong|missing"}}, ...] covering every item x label."""


def one(u, i):
    sub = u["id"]
    TODAY = today()
    path = os.path.join(OUT, sub, f"{i}.json")
    task = u["inputs"][i]
    arms = [a for a in PROVIDERS if (r := rec(sub, i, a)) and r.get("status") in ("completed", "failed")]
    if os.path.exists(path):
        old = json.load(open(path))
        if set(arms) <= set(old.get("graded_arms", old["labels"])):
            return
        key, c1 = old["key"], 0.0  # keep the fixed key; re-grade every run side by side with the new ones
    else:
        instr = u["instructions"].replace("{today}", TODAY).split("General rules:")[0][:3500]
        key, c1 = web_call(KEY.format(today=TODAY, name=u["name"], instructions=f"{instr}\n\nINPUT: {task}", fetch=FETCH), f"INPUT: {task}")
        key = [k for k in key or [] if isinstance(k, dict) and k.get("id") and k.get("question")]
        if not key:
            return LOG("key failed", sub, i)
    labels = arms[:]
    random.Random(f"key{sub}{i}").shuffle(labels)
    lab = dict(zip(labels, "ABCDEFGHIJ", strict=False))
    body = json.dumps(key, ensure_ascii=False) + "\n\n" + "\n\n".join(
        f"=== {lab[a]} ===\n{json.dumps(rec(sub, i, a).get('output'), ensure_ascii=False)[:25000]}" for a in arms)
    grades, c2 = plain_call(GRADE.format(today=TODAY, task=task), "ANSWER KEY:\n" + body, "sonnet")
    if not grades:
        return LOG("grade failed", sub, i)
    inv = {L: a for a, L in lab.items()}
    g = {}
    for x in grades:
        if isinstance(x, dict) and x.get("label") in inv:
            g.setdefault(inv[x["label"]], {})[x.get("item")] = x.get("grade")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump({"sub": sub, "i": i, "task": task, "key": key, "labels": lab, "graded_arms": arms, "grades": g,
               "cost_usd": round(c1 + c2, 3)},
              open(path, "w"), indent=1, ensure_ascii=False)
    LOG("key", sub, i, len(key), "items $", round(c1 + c2, 2))


if __name__ == "__main__":
    if sys.argv[1:2] in (["-h"], ["--help"]):
        print(__doc__)
        sys.exit(0)
    only = sys.argv[1:]
    jobs = [(u, i) for u in load_tasks() if u.get("answer_key") and (not only or u["id"] in only) for i in range(len(u["inputs"]))]
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda j: one(*j), jobs))
