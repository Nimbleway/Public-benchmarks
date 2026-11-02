"""Step A + B of judging: turn each provider's output into checkable units, then merge them across providers.

Per task (use case x input):
  A. extract - each provider's output -> atomic, checkable units relevant to the task (no web)
  B. merge   - all providers' units -> one canonical list; who asserted what; conflicting values kept apart
Providers are shown to the judge under shuffled anonymous labels (A-D), seeded per task.
Verification and scoring happen in judge_verify.py.

The judge is Claude, called through the Claude Code CLI (`claude -p`, model sonnet). No tools are enabled here.

    python -m usecase_bench.judge_extract [use_case ...]   ->  results/claims/<use_case>/<i>.json
Env: ARMS (default nimble,exa,parallel,firecrawl) = arms to include if present; REQUIRED (default
nimble,exa,firecrawl) = arms that must have finished before a task is judged.
"""
import json
import os
import random
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from . import CLAIMS, RAW, load_tasks

CMP, OUT = RAW, CLAIMS
ARMS = os.environ.get("ARMS", "nimble,exa,parallel,firecrawl").split(",")
REQUIRED = os.environ.get("REQUIRED", "nimble,exa,firecrawl").split(",")
TODAY = date.today().isoformat()
LOG = lambda *a: print(time.strftime("%H:%M:%S"), *a, flush=True)  # noqa: E731


def claude(system, user, tools=False, retries=2):
    args = ["claude", "-p", "--strict-mcp-config", "--disable-slash-commands", "--setting-sources", "project",
            "--model", "sonnet", "--system-prompt", system, "--output-format", "json"]
    args += ["--tools", "WebSearch,WebFetch", "--allowedTools", "WebSearch,WebFetch"] if tools else ["--tools", ""]
    cost = 0.0
    for _ in range(retries):
        p = subprocess.run(args, input=user, capture_output=True, text=True, timeout=2400)
        try:
            env = json.loads(p.stdout)
            cost += env.get("total_cost_usd") or 0
            m = re.search(r"[\[{].*[\]}]", env.get("result") or "", re.S)
            return json.loads(m.group(0)), cost
        except Exception as e:
            LOG("claude parse fail", str(e)[:80], p.stderr[:150])
    return None, cost


EXTRACT = """Today is {{today}}. Turn ONE research output into the units that count for this task.
UNIT DEFINITION: {unit_rule}
- Each unit is self-contained (names the entity).
- Skip process notes, null/empty fields, and statements about what was NOT found.
- Keep the cited URL for the unit if given.
Reply with JSON only: [{{{{"claim": "...", "url": "..."}}}}]. Include every unit the output contains (up to 80)."""

MERGE = """Merge unit lists from several anonymous systems (labels) for the SAME task into one canonical list.
UNIT DEFINITION: {unit_rule}
- The same unit stated differently (same company, same person-in-role, same event, same field value) -> ONE canonical
  unit, listing every label that has it.
- Different values for the same field or role (two different CFOs, two dates for one event) -> separate units,
  cross-referenced in conflicts_with.
Reply with JSON only: [{{"cid": "c1", "claim": "...", "asserted_by": ["A"], "urls": ["..."], "conflicts_with": []}}]"""


def load(sub, i):
    out = {}
    for a in ARMS:
        p = os.path.join(CMP, sub, f"{i}_{a}.json")
        out[a] = json.load(open(p)) if os.path.exists(p) else None
    return out


def text(r):
    if not r or r.get("status") != "completed" or r.get("output") in (None, "", {}, []):
        return None
    return r["output"] if isinstance(r["output"], str) else json.dumps(r["output"], ensure_ascii=False)


def judge(u, i):
    path = os.path.join(OUT, u["id"], f"{i}.json")
    if os.path.exists(path):
        return json.load(open(path))
    recs = load(u["id"], i)
    # an arm that never ran (no credit) is left out; one that ran and failed is scored as empty
    arms = [a for a in ARMS if recs[a] and recs[a].get("status") in ("completed", "failed")]
    if any(a not in arms for a in REQUIRED):
        LOG("skip, required arms not done", u["id"], i)
        return None
    recs = {a: recs[a] for a in arms}
    task = next(iter(recs.values()))["input"]
    labels = arms[:]
    random.Random(f"{u['id']}{i}").shuffle(labels)
    lab = dict(zip(labels, "ABCD"))
    cost = 0.0

    claims = {}
    for a in arms:
        t = text(recs[a])
        if not t:
            claims[a] = []
            continue
        c, k = claude(EXTRACT.format(unit_rule=u["unit"]["definition"]).format(today=TODAY), f"TASK: {task}\n\nOUTPUT:\n{t[:30000]}")
        cost += k
        claims[a] = c or []

    merged, k = claude(MERGE.format(unit_rule=u["unit"]["definition"]), f"TASK: {task}\n\n" + "\n\n".join(
        f"=== {lab[a]} ===\n{json.dumps(claims[a], ensure_ascii=False)}" for a in arms))
    cost += k
    merged = merged or []

    res = {"sub_use_case": u["id"], "input_n": i, "task": task, "labels": lab, "unit": u["unit"]["name"],
           "arms_judged": arms, "claims": claims, "merged": merged, "judge_cost_usd": round(cost, 3)}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(res, open(path, "w"), indent=1, ensure_ascii=False)
    LOG("extracted", u["id"], i, "units", len(merged), "$", round(cost, 2))
    return res


if __name__ == "__main__":
    only = sys.argv[1:]
    jobs = [(u, i) for u in load_tasks() for i in range(len(u["inputs"])) if not only or u["id"] in only]
    with ThreadPoolExecutor(3) as ex:
        list(ex.map(lambda j: judge(*j), jobs))
