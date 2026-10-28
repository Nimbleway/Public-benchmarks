"""Aggregate results/verified (scores) and results/raw (runs) into results/summary.json and print markdown tables.

    python -m usecase_bench.score

Averages are over tasks where every compared provider was scored. Wins: per task, the provider with the highest
(combined, coverage). Cost: list price per run for Nimble, Exa and Parallel; Exa's billed cost when it reports one;
Firecrawl = (run credits + paid provider credits) x the Standard plan credit rate.
"""
import json
import os
import statistics as st
from collections import defaultdict

from . import RAW, RESULTS, VERIFIED, load_tasks
from .judge_verify import score as rescore

ARMS = ["nimble", "exa", "parallel", "firecrawl"]
THREE = ["nimble", "exa", "firecrawl"]
FC_CREDIT_USD = 99 / 100_000
TASKS = load_tasks()


def rec(sub, i, a):
    p = os.path.join(RAW, sub, f"{i}_{a}.json")
    return json.load(open(p)) if os.path.exists(p) else None


# Firecrawl free daily runs report 0 credits; price them at the median billed credits for the same effort
billed = defaultdict(list)
for u in TASKS:
    for i in range(len(u["inputs"])):
        r = rec(u["id"], i, "firecrawl")
        if r and r.get("credits"):
            billed[r.get("tier")].append(r["credits"])


def cost(r):
    if not r or r.get("status") not in ("completed", "failed"):
        return None
    if r["arm"] == "firecrawl":
        prov = (r.get("exchange") or {}).get("creditsUsed") or 0
        if r.get("credits"):
            return round((r["credits"] + prov) * FC_CREDIT_USD, 4)
        med = st.median(billed[r.get("tier")]) if billed.get(r.get("tier")) else None
        return round((med + prov) * FC_CREDIT_USD, 4) if med else None
    return r.get("cost_usd")


rows, mismatches, agree = [], 0, []
for u in TASKS:
    for i, inp in enumerate(u["inputs"]):
        p = os.path.join(VERIFIED, u["id"], f"{i}.json")
        if not os.path.exists(p):
            continue
        j = json.load(open(p))
        if rescore(u["id"], i, j["merged"], j["verified"], j["labels"]) != j["scores"]:
            mismatches += 1
        for cid, au in (j.get("audit") or {}).items():
            agree.append((j["verified"].get(cid, {}).get("verdict"), au.get("verdict")))
        arms = {}
        for a in ARMS:
            s = j["scores"].get(a)
            r = rec(u["id"], i, a)
            if s:
                arms[a] = {**s, "cost": cost(r), "seconds": (r or {}).get("seconds")}
        rows.append({"id": u["id"], "name": u["name"], "vertical": u["vertical"], "tier": u["tier"], "i": i,
                     "input": inp, "arms": arms, "judge_cost_usd": j.get("judge_cost_usd")})


def summarize(rs, arms):
    rs = [t for t in rs if all(a in t["arms"] for a in arms)]
    out = {}
    for a in arms:
        sc = [t["arms"][a] for t in rs]
        if not sc:
            continue
        accs = [s["accuracy"] for s in sc if s["accuracy"] is not None]
        wins = sum(1 for t in rs if max(arms, key=lambda x: (t["arms"][x]["combined"], t["arms"][x]["coverage"])) == a)
        out[a] = {"n": len(sc), "combined": round(st.mean(s["combined"] for s in sc), 3),
                  "accuracy": round(st.mean(accs), 3) if accs else None,
                  "coverage": round(st.mean(s["coverage"] for s in sc), 3), "wins": wins,
                  "avg_cost_usd": round(st.mean(s["cost"] or 0 for s in sc), 3),
                  "median_seconds": st.median(s["seconds"] or 0 for s in sc),
                  "unverifiable_rate": round(sum(s["unverifiable"] for s in sc) / max(1, sum(s["units"] for s in sc)), 3)}
    return {"n_tasks": len(rs), "tasks": [f"{t['id']}#{t['i']}" for t in rs], "arms": out}


views = {
    "finance_3way": summarize([t for t in rows if t["vertical"] == "finance"], THREE),
    "gtm_3way": summarize([t for t in rows if t["vertical"] == "gtm"], THREE),
    "all_3way": summarize(rows, THREE),
    "parallel_4way": summarize(rows, ARMS),
}
by_use_case = {}
for u in TASKS:
    rs = [t for t in rows if t["id"] == u["id"]]
    by_use_case[u["id"]] = {"name": u["name"], "vertical": u["vertical"], "tier": u["tier"],
                            "combined": {a: round(st.mean(t["arms"][a]["combined"] for t in rs), 3)
                                         for a in THREE if rs and all(a in t["arms"] for t in rs)}}
audit = {"units": len(agree), "agree": sum(1 for a, b in agree if a == b),
         "rate": round(sum(1 for a, b in agree if a == b) / len(agree), 3) if agree else None}
summary = {"views": views, "by_use_case": by_use_case, "per_task": rows, "judge_audit": audit,
           "judge_cost_usd": round(sum(t["judge_cost_usd"] or 0 for t in rows), 2), "rescore_mismatches": mismatches}
json.dump(summary, open(os.path.join(RESULTS, "summary.json"), "w"), indent=1, ensure_ascii=False)


def table(view, title):
    v = views[view]
    print(f"\n### {title} ({v['n_tasks']} tasks)\n")
    print("| Provider | Combined | Accuracy | Coverage | Wins | Avg cost / run | Median seconds |")
    print("| :--- | :---: | :---: | :---: | :---: | :---: | :---: |")
    for a, s in sorted(v["arms"].items(), key=lambda kv: -kv[1]["combined"]):
        print(f"| {a} | {s['combined']:.3f} | {s['accuracy']:.3f} | {s['coverage']:.3f} | {s['wins']}/{v['n_tasks']} "
              f"| ${s['avg_cost_usd']:.2f} | {s['median_seconds']:.0f} |")


if __name__ == "__main__":
    table("all_3way", "All use cases")
    table("finance_3way", "Finance")
    table("gtm_3way", "GTM")
    table("parallel_4way", "Partial: tasks where all four providers were scored")
    print("\n### Combined score by use case\n")
    print("| Use case | Vertical | Tier | Nimble | Exa | Firecrawl |")
    print("| :--- | :--- | :--- | :---: | :---: | :---: |")
    for b in by_use_case.values():
        c = b["combined"]
        print(f"| {b['name']} | {b['vertical']} | {b['tier']} | {c.get('nimble', 0):.3f} | {c.get('exa', 0):.3f} | {c.get('firecrawl', 0):.3f} |")
    print(f"\nJudge audit agreement (second model on ~10% of units): {audit['agree']}/{audit['units']} = {audit['rate']}")
    print(f"Judge cost (verify step): ${summary['judge_cost_usd']}   rescore mismatches: {mismatches}")
