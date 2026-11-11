"""Neutral stale-key audit.

Answer keys are written before any provider answer is seen, and some go stale. A stale key marks a provider "wrong"
for giving the current, correct answer. This step re-checks every key item that at least one run was graded "wrong" on:

- An adjudicator (Claude Sonnet 5.5, effort high; not a benchmark contestant) gets the key question, the key answer and
  its source, and the conflicting answers labelled anonymously (no provider names).
- It researches the question itself with web search and page fetch and decides, from primary sources:
  key_wrong (key is stale or incorrect) / both_ok (both are defensible, e.g. different as-of dates) /
  key_right (the run was wrong) / unclear.
- key_wrong: the key answer is replaced with the adjudicator's corrected answer, written from the pages it opened.
  both_ok: the key answer is widened to accept either. key_right / unclear: no change.
- Changed tasks get graded_arms = [] so answer_keys re-grades EVERY provider on that task against the fixed key.
  The original key item is kept under key_original; the verdict under audit.

    python -m usecase_bench.key_audit [job ...]   # audit: writes results/key_audit/<job>/<i>.json, does not touch keys
    python -m usecase_bench.key_audit apply       # apply verdicts to results/answer_keys/, then re-run answer_keys
"""
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from . import KEY_AUDIT, KEYS, PROVIDERS, ROOT, today
from .claude_cli import BASE, LOG
from .judge import rec

OUT = KEY_AUDIT

SYS = """Today is {today}. You are checking an ANSWER KEY item used to grade research answers. The key was written
earlier and may be stale. Below are the key question, the key answer and its source, and one or more ANSWERS that a
grader marked as contradicting the key. Research the question yourself with web search and page fetch, preferring
primary sources (company filings and releases, regulators, courts, official statistics) and the most recent ones.
Decide:
- "key_wrong": the key answer is out of date or incorrect on the disputed point, per a source you opened.
- "both_ok": both are defensible (different as-of dates, rounding, definitions, or both supported by sources).
- "key_right": the key is correct and the answers are wrong.
- "unclear": you cannot settle it from sources you opened.
For key_wrong, write "corrected_answer": the full key answer with only the disputed point fixed, keeping everything
else; state only what you read on a page you opened, and give that page in "source". Do not copy details from the
answers below that you did not confirm yourself. For both_ok, write "corrected_answer" as the key answer plus a short
note on what else is acceptable.
Reply with JSON only: {{"verdict": "...", "disputed_point": "...", "why": "...", "corrected_answer": "... or null", "source": "url or null"}}"""


def ask(system, user):
    args = BASE + ["--model", "claude-sonnet-5-5", "--effort", "high", "--system-prompt", system, "--output-format", "json",
                   "--tools", "WebSearch,WebFetch", "--allowedTools", "WebSearch", "WebFetch"]
    for _ in range(2):
        p = subprocess.run(args, input=user, capture_output=True, text=True, timeout=1800, cwd=ROOT)
        try:
            env = json.loads(p.stdout)
            m = re.search(r"\{.*\}", env.get("result") or "", re.S)
            return json.loads(m.group(0)), env.get("total_cost_usd") or 0
        except Exception as e:  # noqa: BLE001
            LOG("audit parse fail", str(e)[:80])
    return None, 0


def audit(kp):
    k = json.load(open(kp))
    sub, i = k["sub"], k["i"]
    op = os.path.join(OUT, sub, f"{i}.json")
    done = json.load(open(op)) if os.path.exists(op) else {}
    for it in k["key"]:
        if it["id"] in done:
            continue
        wrong = [a for a in PROVIDERS if k["grades"].get(a, {}).get(it["id"]) == "wrong"]
        if not wrong:
            continue
        answers = "\n\n".join(f"=== ANSWER {chr(65 + n)} ===\n" +
                              json.dumps((rec(sub, i, a) or {}).get("output"), ensure_ascii=False)[:15000]
                              for n, a in enumerate(wrong))
        user = (f"TASK: {k['task']}\n\nKEY QUESTION: {it['question']}\nKEY ANSWER: {it['answer']}\nKEY SOURCE: {it.get('source')}\n\n"
                f"ANSWERS MARKED AS CONTRADICTING THE KEY (find the part about this question):\n{answers}")
        v, cost = ask(SYS.format(today=today()), user)
        if not v:
            continue
        done[it["id"]] = {**v, "wrong_arms": wrong, "cost": round(cost, 3)}
        os.makedirs(os.path.dirname(op), exist_ok=True)
        json.dump(done, open(op, "w"), indent=1, ensure_ascii=False)
        LOG("key audit", sub, i, it["id"], v.get("verdict"), "wrong:", wrong, "$", round(cost, 2))


def apply():
    for sub in os.listdir(OUT):
        for f in os.listdir(os.path.join(OUT, sub)):
            kp, v = os.path.join(KEYS, sub, f), json.load(open(os.path.join(OUT, sub, f)))
            k = json.load(open(kp))
            changed = False
            for it in k["key"]:
                a = v.get(it["id"])
                if not a or a.get("verdict") not in ("key_wrong", "both_ok") or not a.get("corrected_answer") or it.get("key_original"):
                    continue
                it["key_original"] = {"answer": it["answer"], "source": it.get("source")}
                it["answer"], it["source"] = a["corrected_answer"], a.get("source") or it.get("source")
                it["audit"] = {"verdict": a["verdict"], "why": a.get("why"), "disputed_point": a.get("disputed_point")}
                changed = True
            if changed:
                k["graded_arms"] = []  # re-grade every provider against the fixed key
                json.dump(k, open(kp, "w"), indent=1, ensure_ascii=False)
                LOG("key fixed", sub, f)


if __name__ == "__main__":
    if sys.argv[1:2] in (["-h"], ["--help"]):
        print(__doc__)
        sys.exit(0)
    if sys.argv[1:] == ["apply"]:
        apply()
    else:
        only = sys.argv[1:]
        files = [os.path.join(KEYS, s, f) for s in sorted(os.listdir(KEYS)) if not only or s in only
                 for f in sorted(os.listdir(os.path.join(KEYS, s)))]
        with ThreadPoolExecutor(6) as ex:
            list(ex.map(audit, files))
