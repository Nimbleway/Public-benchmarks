"""Aggregate results/ into results/summary.json and print the README tables. Offline: no network, no model calls.

    python -m usecase_bench.summary

Per task:
  - every provider is re-scored from the stored verdicts (scoring.score);
  - if the job has an answer key, combined = judge score x (0.5 + 0.5 x share of critical key items graded correct);
  - a run that errored or timed out returned nothing and scores 0 on that task;
  - the winner is the provider with the highest combined; a tie splits the win.
Averages are means over tasks; median seconds is the median wall-clock time per run.
Cost: list price per run for Nimble and Parallel; Exa's billed cost; Firecrawl = (run credits + paid provider credits)
x the Standard plan rate; GPT-6.1 Sol = tokens x list price + $10 per 1,000 web search calls; Claude Opus 5.5 = the
API-equivalent total Claude Code reports.

If the sibling suite (GTM-Use-Case-Benchmark next to Finance-Use-Case-Benchmark, or the reverse) is present, the
"across both suites" view is computed too, from both folders' results.
"""
import json
import os
import statistics as st

from . import NAMES, PKG, PROVIDERS, RESULTS, ROOT, TASKS, load_tasks
from .scoring import ANSWER_JOBS, score, tally

FC_CREDIT_USD = 99 / 100_000
# Firecrawl free daily runs report no credits; they are priced at the median billed credits for the same effort over
# all billed Firecrawl runs of the full experiment (14 jobs x 5 inputs; this release publishes 10 of the jobs, so the
# medians are fixed here rather than recomputed from the shipped runs).
FC_FREE_RUN_CREDITS = {"spark-2/high": 294, "spark-2/medium": 77}
SUITES = ["Finance-Use-Case-Benchmark", "GTM-Use-Case-Benchmark"]


def words(r):
    """Length of a question-set answer: words in its answer, key facts and caveats (None when it has no answer)."""
    o = (r or {}).get("output")
    if not isinstance(o, dict) or "answer" not in o:
        return None
    parts = [o.get("answer") or ""] + [(f or {}).get("fact") or "" for f in o.get("key_facts") or []] + \
        [c or "" for c in o.get("caveats") or []]
    return sum(len(x.split()) for x in parts)


def load_json(p):
    return json.load(open(p)) if os.path.exists(p) else None


def cost(r):
    if not r or r.get("status") not in ("completed", "failed"):
        return None, None
    if r["arm"] == "firecrawl":
        prov = (r.get("exchange") or {}).get("creditsUsed") or 0
        if r.get("credits"):
            return round((r["credits"] + prov) * FC_CREDIT_USD, 4), f"{r['credits']} run credits + {prov} provider credits"
        med = FC_FREE_RUN_CREDITS.get(r.get("tier"))
        return (round((med + prov) * FC_CREDIT_USD, 4), f"free run, priced at median {med} credits + {prov} provider credits") if med else (None, "free run")
    return r.get("cost_usd"), r.get("cost_basis")


def suite_tasks(root):
    """Per-task rows for one suite folder."""
    res = os.path.join(root, "results") if root != ROOT else RESULTS
    rows = []
    for u in load_tasks(root):
        for i, inp in enumerate(u["inputs"]):
            j = load_json(os.path.join(res, "verified", u["id"], f"{i}.json"))
            if not j:
                continue
            scores = score(u["id"], i, j["merged"], j["verified"], j["labels"])
            key = load_json(os.path.join(res, "answer_keys", u["id"], f"{i}.json"))
            arms = {}
            for a in PROVIDERS:
                r = load_json(os.path.join(res, "raw", u["id"], f"{i}_{a}.json"))
                c, basis = cost(r)
                s = scores.get(a)
                if not s and r and r.get("status") not in ("completed", "failed"):
                    # A run that errored or timed out returned nothing: it scores zero on this task.
                    truth = max((x["truth"] for x in scores.values()), default=0)
                    s = {"units": 0, "unverifiable": 0, "true": 0, "wrong": 0, "truth": truth, "coverage": 0.0,
                         "accuracy": None, "combined": 0.0}
                if s and key:
                    items = key["key"]
                    g = key["grades"].get(a, {})
                    crit = [k["id"] for k in items if k.get("importance") == "critical"] or [k["id"] for k in items]
                    s = {**s, "judge_score": s["combined"],
                         "key_recall": round(sum(g.get(k["id"]) == "correct" for k in items) / len(items), 3),
                         "key_critical": round(sum(g.get(k) == "correct" for k in crit) / len(crit), 3),
                         "key_wrong": sum(g.get(k["id"]) == "wrong" for k in items), "key_items": len(items)}
                    # missing or wrong on the questions that decide the job limits the score
                    s["combined"] = round(s["combined"] * (0.5 + 0.5 * s["key_critical"]), 3)
                if s and a in j["labels"]:
                    s = {**s, "tally": tally(j["merged"], j["verified"], j["labels"][a])}
                if s:
                    s = {**s, "cost_usd": c, "efficiency": round(s["combined"] / c, 2) if c else None}
                arms[a] = {"status": (r or {}).get("status", "not run"), "tier": (r or {}).get("tier"),
                           "seconds": (r or {}).get("seconds"), "cost_usd": c, "cost_basis": basis, "score": s}
                if u["id"] in ANSWER_JOBS and words(r) is not None:
                    arms[a]["words"] = words(r)
            sc = {a: arms[a]["score"] for a in PROVIDERS if arms[a]["score"]}
            top = max(x["combined"] for x in sc.values())
            winners = [a for a in PROVIDERS if a in sc and abs(sc[a]["combined"] - top) < 1e-9]
            audit = [((j["verified"].get(c["cid"]) or {}).get("verdict", "not checked"), j["audit"][c["cid"]].get("verdict"))
                     for c in j["merged"] if c["cid"] in (j.get("audit") or {})]
            rows.append({"job": u["id"], "name": u["name"], "vertical": u["vertical"], "tier": u["tier"], "i": i, "input": inp,
                         "metric": u.get("metric"), "keyed": bool(key), "units": len(j["merged"]), "winners": winners,
                         "audit": audit, "arms": arms})
    return rows


def summarize(rows, arms=PROVIDERS):
    """Averages over tasks where every provider in `arms` was scored; wins counted among `arms` only."""
    rows = [t for t in rows if all(t["arms"][a]["score"] for a in arms)]
    out = {}
    for a in arms:
        sc = [t["arms"][a]["score"] for t in rows]
        if not sc:
            continue
        accs = [s["accuracy"] for s in sc if s["accuracy"] is not None]
        unv = sum(s["unverifiable"] for s in sc) / max(1, sum(s["units"] for s in sc))
        comb, c = st.mean(s["combined"] for s in sc), st.mean(s["cost_usd"] or 0 for s in sc)

        def share(t, a=a):  # a tie splits the win
            top = max(t["arms"][x]["score"]["combined"] for x in arms)
            w = [x for x in arms if abs(t["arms"][x]["score"]["combined"] - top) < 1e-9]
            return 1 / len(w) if a in w else 0
        keyed = [s for s in sc if s.get("key_critical") is not None]
        out[a] = {"n": len(sc), "combined": round(comb, 3), "coverage": round(st.mean(s["coverage"] for s in sc), 3),
                  "accuracy": round(st.mean(accs), 3) if accs else None, "empty_runs": len(sc) - len(accs),
                  "wins": round(sum(share(t) for t in rows), 1), "avg_cost_usd": round(c, 3),
                  "efficiency": round(comb / c, 2) if c else None, "unverifiable_rate": round(unv, 3),
                  "key_critical": round(st.mean(s["key_critical"] for s in keyed), 3) if keyed else None, "key_n": len(keyed),
                  "median_seconds": st.median(float(t["arms"][a]["seconds"] or 0) for t in rows),
                  "tally": {k: sum((s.get("tally") or {}).get(k, 0) for s in sc) for k in ("true", "wrong", "off_scope", "unverifiable")}}
        if all("thoroughness" in s for s in sc):  # question sets: thoroughness and answer length
            out[a].update(thoroughness=round(st.mean(s["thoroughness"] for s in sc), 3),
                          median_words=st.median(t["arms"][a].get("words") or 0 for t in rows))
    # length check: how often the longest answer is also a winner (a tie for longest counts if any of them won)
    sized = [t for t in rows if all("words" in t["arms"][a] for a in arms)]
    longest = [{a for a in arms if t["arms"][a]["words"] == max(t["arms"][x]["words"] for x in arms)} for t in sized]
    won = [{a for a in arms if abs(t["arms"][a]["score"]["combined"] - max(t["arms"][x]["score"]["combined"] for x in arms)) < 1e-9}
           for t in sized]
    view = {"n_tasks": len(rows), "arms": out}
    if sized:
        view["longest_won"] = [sum(bool(lo & w) for lo, w in zip(longest, won, strict=True)), len(sized)]
    return view


def by_job(rows):
    out = {}
    for t in rows:
        out.setdefault(t["job"], {"name": t["name"], "tier": t["tier"], "metric": t["metric"], "rows": []})["rows"].append(t)
    return {k: {"name": v["name"], "tier": v["tier"], "metric": v["metric"], "n_tasks": len(v["rows"]),
                "combined": {a: round(st.mean(t["arms"][a]["score"]["combined"] for t in v["rows"]), 3) for a in PROVIDERS}}
            for k, v in out.items()}


def audit_rate(rows):
    agree = [x for t in rows for x in t["audit"]]
    n = sum(1 for a, b in agree if a == b)
    return {"units": len(agree), "agree": n, "rate": round(n / len(agree), 3) if agree else None}


def table(view, title):
    arms = sorted(view["arms"], key=lambda a: -view["arms"][a]["combined"])
    n = view["n_tasks"]
    qs = "thoroughness" in view["arms"][arms[0]]  # question sets: answer score, thoroughness, length
    lines = [f"### {title} ({n} tasks)", "",
             "| Provider | Combined | Accuracy | Coverage | " + ("Thoroughness | Median words | " if qs else "Critical key items correct | ")
             + "Wins | Avg cost / run | Median seconds |",
             "| :--- | :---: | :---: | :---: | :---: | " + (":---: | " if qs else "") + ":---: | :---: | :---: |"]
    for a in arms:
        x = view["arms"][a]
        kc = f"{x['thoroughness']:.3f} | {x['median_words']:g}" if qs else f"{x['key_critical']:.3f}" if x["key_critical"] is not None else "-"
        wins = f"{x['wins']:g}"
        lines.append(f"| {NAMES[a]} | {x['combined']:.3f} | {x['accuracy']:.3f} | {x['coverage']:.3f} | {kc} | {wins}/{n} | "
                     f"${x['avg_cost_usd']:.2f} | {x['median_seconds']:.0f} |")
    if view.get("longest_won"):
        lw, m = view["longest_won"]
        lines += ["", f"The longest answer was among the winners on {lw} of {m} questions."]
    lines += ["", "Units per provider, as judged (true / wrong / off scope / unverifiable):", "",
              "| Provider | Units | True | Wrong | Off scope | Unverifiable |", "| :--- | :---: | :---: | :---: | :---: | :---: |"]
    for a in arms:
        tl = view["arms"][a]["tally"]
        lines.append(f"| {NAMES[a]} | {sum(tl.values())} | {tl['true']} | {tl['wrong']} | {tl['off_scope']} | {tl['unverifiable']} |")
    return "\n".join(lines)


def job_table(jobs):
    lines = ["| Job | Tier | Metric | " + " | ".join(NAMES[a] for a in PROVIDERS) + " |",
             "| :--- | :--- | :--- | " + " | ".join(":---:" for _ in PROVIDERS) + " |"]
    for v in jobs.values():
        best = max(v["combined"].values())
        cells = [f"**{v['combined'][a]:.3f}**" if v["combined"][a] == best else f"{v['combined'][a]:.3f}" for a in PROVIDERS]
        lines.append(f"| {v['name']} | {v['tier']} | {v['metric']} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main():
    rows = suite_tasks(ROOT)
    here = os.path.basename(ROOT)
    other = os.path.join(PKG, "tasks.json") != TASKS  # another suite's tasks (BENCH_TASKS): no sibling view
    title = rows[0]["name"] if other and rows else "Finance" if rows and rows[0]["vertical"] == "finance" else "GTM"
    views = {"suite": summarize(rows)}
    jobs = by_job(rows)
    out = {"suite": title, "views": views, "by_job": jobs, "audit_agreement": {"suite": audit_rate(rows)}}
    sibling = [os.path.join(os.path.dirname(ROOT), s) for s in SUITES if s != here]
    sibling = [s for s in sibling if not other and os.path.exists(os.path.join(s, "usecase_bench", "tasks.json"))]
    both = rows + [t for s in sibling for t in suite_tasks(s)]
    if sibling:
        views["both"] = summarize(both)
        out["by_vertical"] = {v: summarize([t for t in both if t["vertical"] == v]) for v in ("finance", "gtm")}
        out["audit_agreement"]["both"] = audit_rate(both)
    out["zero_scored_runs"] = [{"job": t["job"], "i": t["i"], "provider": a, "status": t["arms"][a]["status"]}
                               for t in rows for a in PROVIDERS if t["arms"][a]["status"] not in ("completed", "failed")]
    out["tasks"] = [{k: t[k] for k in ("job", "i", "input", "units", "winners")} |
                    {"scores": {a: t["arms"][a]["score"] for a in PROVIDERS},
                     "runs": {a: {x: t["arms"][a][x] for x in ("status", "tier", "seconds", "cost_usd", "cost_basis")} for a in PROVIDERS}}
                    for t in rows]
    json.dump(out, open(os.path.join(RESULTS, "summary.json"), "w"), indent=1, ensure_ascii=False)
    print(table(views["suite"], title))
    print(f"\n### Combined score by job (mean of {len(rows) // max(1, len(jobs))} tasks)\n")
    print(job_table(jobs))
    if sibling:
        print()
        print(table(views["both"], "Across both suites"))
    a = out["audit_agreement"]
    print(f"\nJudge audit agreement: {a['suite']['agree']}/{a['suite']['units']} in this suite"
          + (f", {a['both']['agree']}/{a['both']['units']} across both suites" if "both" in a else ""))
    print("Zero-scored runs:", out["zero_scored_runs"] or "none")


if __name__ == "__main__":
    main()
