"""Run every job x input x provider at the job's tier.

    python -m agent_bench.run --suite <folder>              # all providers
    python -m agent_bench.run --suite <folder> nimble exa   # some providers
    ONLY=fin_credit_risk:0,fin_kyc_screening python -m agent_bench.run --suite Finance-Use-Case-Benchmark opus

Results: results/raw/<job>/<input#>_<provider>.json. A file whose run completed is skipped.
Each provider stops submitting once its spend passes a cap: CAP_<PROVIDER> in USD (e.g. CAP_OPUS=100).
"""
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .arms import ARMS
from .config import PROVIDERS, RAW, load_tasks, today

OUT = RAW
DEFAULT_CAP = {"nimble": 40, "exa": 20, "parallel": 20, "firecrawl": 60, "gpt": 40, "opus": 100}
CAP_USD = {a: float(os.environ.get(f"CAP_{a.upper()}", c)) for a, c in DEFAULT_CAP.items()}
LOG = lambda *a: print(time.strftime("%H:%M:%S"), *a, flush=True)  # noqa: E731

spent = {a: 0.0 for a in ARMS}
lock = threading.Lock()


def spec(u):
    return {"instructions": u["instructions"].replace("{today}", today()),
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
    rec.update(sub_use_case=u["id"], input=task, input_n=i, completed_on=datetime.now(timezone.utc).date().isoformat())
    with lock:
        spent[arm] += rec.get("cost_usd") or 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(rec, open(path, "w"), indent=1, default=str)
    LOG(arm, u["id"], i, rec["status"], rec.get("seconds"), "s $", rec.get("cost_usd"), (rec.get("error") or "")[:120])


def main(arms):
    bad = [a for a in arms if a not in ARMS]
    if bad:
        sys.exit(f"unknown provider(s) {bad}; choose from {PROVIDERS}")
    only = [x.split(":") for x in os.environ.get("ONLY", "").split(",") if x]  # e.g. ONLY=fin_due_diligence:0,fin_kyc_screening
    keep = lambda u, i: not only or any(o[0] == u["id"] and (len(o) == 1 or int(o[1]) == i) for o in only)  # noqa: E731
    jobs = [(a, u, i, t) for u in load_tasks() for i, t in enumerate(u["inputs"]) for a in arms if keep(u, i)]
    per_arm = {"nimble": 6, "exa": 4, "parallel": 6, "firecrawl": 5, "gpt": 6, "opus": 4}
    pools = {a: ThreadPoolExecutor(per_arm[a]) for a in arms}
    futs = [pools[a].submit(job, a, u, i, t) for a, u, i, t in jobs]
    for f in futs:
        f.result()
    LOG("spent", spent)


if __name__ == "__main__":
    if sys.argv[1:2] in (["-h"], ["--help"]):
        print(__doc__)
        sys.exit(0)
    main(sys.argv[1:] or PROVIDERS)
