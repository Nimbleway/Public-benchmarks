"""Head-to-head: 6 use cases x 3 inputs x 4 providers at price-matched tiers.

    python -m usecase_bench.run                      # all arms
    python -m usecase_bench.run nimble exa           # some arms
Results: results/raw/<use_case>/<input#>_<arm>.json (skips ones already completed). Hard stop on spend per arm.
"""
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from . import RAW, load_tasks
from .arms import ARMS

OUT = RAW
CAP_USD = {"nimble": 20, "exa": 20, "parallel": 20, "firecrawl": 60}
LOG = lambda *a: print(time.strftime("%H:%M:%S"), *a, flush=True)  # noqa: E731

spent = {a: 0.0 for a in ARMS}
lock = threading.Lock()


def spec(u):
    return {"instructions": u["instructions"].replace("{today}", date.today().isoformat()),
            "output_schema": u["output_schema"], "use_case": u["use_case"], "exa_data_sources": u.get("exa_data_sources")}


def job(arm, u, i, task):
    path = os.path.join(OUT, u["id"], f"{i}_{arm}.json")
    if os.path.exists(path) and json.load(open(path)).get("status") == "completed":
        return
    with lock:
        if spent[arm] >= CAP_USD[arm]:
            LOG(arm, "cap reached, skip", u["id"], i)
            return
    try:
        rec = ARMS[arm](spec(u), task, u["tier"])
    except Exception as e:
        rec = {"arm": arm, "status": "error", "error": str(e)[:800]}
    rec.update(sub_use_case=u["id"], input=task, input_n=i)
    with lock:
        spent[arm] += rec.get("cost_usd") or 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(rec, open(path, "w"), indent=1, default=str)
    LOG(arm, u["id"], i, rec["status"], rec.get("seconds"), "s $", rec.get("cost_usd"), (rec.get("error") or "")[:120])


def main(arms):
    jobs = [(a, u, i, t) for u in load_tasks() for i, t in enumerate(u["inputs"]) for a in arms]
    per_arm = {"nimble": 6, "exa": 4, "parallel": 6, "firecrawl": 5}
    pools = {a: ThreadPoolExecutor(per_arm[a]) for a in arms}
    futs = [pools[a].submit(job, a, u, i, t) for a, u, i, t in jobs]
    for f in futs:
        f.result()
    LOG("spent", spent)


if __name__ == "__main__":
    main(sys.argv[1:] or list(ARMS))
