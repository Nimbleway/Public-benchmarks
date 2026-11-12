"""Scoring-correction pass (v2 corrections, Nov 11 2026, after external review). Runs after the judge, on
results/verified/, in place. A task that already has "corrections_v2" is skipped.

1. Duplicate units: the merge step sometimes kept the same fact or event as several units. Duplicates inflate the truth
   set and split credit. Sonnet groups units that state the same fact (same entity, same event or figure, same date);
   each group becomes one unit asserted by the union of providers ("merged_from"). Each provider keeps the verdict of ITS
   OWN variant (label_verdicts), so a provider with a wrong date on a real event is still scored wrong. Units in a
   conflict pair are never merged. Skipped for deal sourcing (companies are already canonicalised by domain).
2. Ownership criterion in deal sourcing: one rule for every task: founder-owned, family-owned and owner-operated all
   satisfy a founder/family-owned criterion when there are no institutional investors (PE, VC, corporate parent, public
   listing); fail only on evidence of such investors; unknown when there is no ownership evidence. Sonnet re-decides
   only the ownership criteria under that rule, from the evidence notes already recorded ("result_original" keeps the
   judge's first result).

    python -m usecase_bench.corrections [job ...]
"""
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

from . import VERIFIED, load_tasks
from .claude_cli import LOG, plain_call
from .scoring import score

OWN = re.compile(r"own|family|founder|bootstrap|investor|private equity|\bpe\b|venture|backed", re.I)

DEDUP = """You are cleaning a list of factual units extracted from several research answers to the same task: "{task}".
Find units that state the SAME fact: the same entity and the same event, figure or attribute (same date or period where one is
given), only worded differently or with more or less detail. Do NOT group units that differ in a figure, date or entity
(those are different claims, one may be wrong), units about different events on the same day, or a general statement with a
specific one. Reply with JSON only: [["c5","c6","c7"], ...] listing only groups of 2+ unit ids; [] if none."""

OWNERSHIP = """Re-decide ownership criteria for candidate companies in a deal-sourcing task: "{task}".
Apply ONE rule to every unit: founder-owned, family-owned (including later generations) and owner-operated companies all
SATISFY a founder-owned or family-owned criterion, as long as there is no institutional ownership. Result "fail" ONLY when
the evidence shows private equity, venture capital, another institutional investor, a corporate parent or a public listing.
Result "unknown" when the evidence says nothing about ownership. Use only the evidence notes given.
Reply with JSON only: [{{"cid": "c1", "idx": 0, "result": "pass|fail|unknown", "why": "..."}}] for every criterion listed."""


def verdict_for(ver, cid, L):
    v = ver.get(cid, {})
    return (v.get("label_verdicts") or {}).get(L) or v.get("verdict")


def dedup(j):
    units = [{"cid": c["cid"], "claim": c["claim"], "verdict": j["verified"].get(c["cid"], {}).get("verdict")} for c in j["merged"]]
    if len(units) < 2:
        return j, [], 0.0
    groups, cost = plain_call(DEDUP.format(task=j["task"]), json.dumps(units, ensure_ascii=False))
    by = {c["cid"]: c for c in j["merged"]}
    conflict = {(c["cid"], x) for c in j["merged"] for x in (c.get("conflicts_with") or [])}
    done, log = set(), []
    for g in groups or []:
        g = [x for x in g if isinstance(x, str) and x in by and x not in done]
        if len(g) < 2 or any((a, b) in conflict or (b, a) in conflict for a in g for b in g):
            continue
        # representative: a member verified true with the most asserters, else the most-asserted member
        rank = lambda x: (j["verified"].get(x, {}).get("verdict") == "true", len(by[x].get("asserted_by", [])))  # noqa: E731,B023
        rep = max(g, key=rank)
        lv = {}
        for x in g:
            for L in by[x].get("asserted_by", []):
                v = verdict_for(j["verified"], x, L)
                # a provider asserting two variants keeps its better one
                if L not in lv or (v == "true" and lv[L] != "true"):
                    lv[L] = v
        r = by[rep]
        r["asserted_by"] = sorted(lv)
        r["urls"] = sorted({u for x in g for u in (by[x].get("urls") or [])})
        r["merged_from"] = g
        j["verified"].setdefault(rep, {})["label_verdicts"] = lv
        for x in g:
            done.add(x)
            if x != rep:
                j["verified"].pop(x, None)
        log.append({"kept": rep, "merged": [x for x in g if x != rep]})
    drop = {x for e in log for x in e["merged"]}
    j["merged"] = [c for c in j["merged"] if c["cid"] not in drop]
    return j, log, cost


def ownership(j):
    items = []
    for cid, v in j["verified"].items():
        for k, x in enumerate(v.get("criteria") or []):
            if OWN.search(x.get("criterion") or ""):
                items.append({"cid": cid, "idx": k, "criterion": x.get("criterion"), "result": x.get("result"),
                              "evidence": (x.get("evidence") or v.get("note") or "")[:600]})
    if not items:
        return j, [], 0.0
    out, cost = plain_call(OWNERSHIP.format(task=j["task"]), json.dumps(items, ensure_ascii=False))
    log = []
    for o in out or []:
        if not isinstance(o, dict) or o.get("result") not in ("pass", "fail", "unknown"):
            continue
        crit = (j["verified"].get(o.get("cid"), {}).get("criteria") or [])
        k = o.get("idx")
        if isinstance(k, int) and 0 <= k < len(crit) and crit[k].get("result") != o["result"]:
            log.append({"cid": o["cid"], "criterion": crit[k].get("criterion"), "from": crit[k].get("result"),
                        "to": o["result"], "why": o.get("why")})
            crit[k]["result_original"] = crit[k].get("result")
            crit[k]["result"] = o["result"]
    return j, log, cost


def one(sub, i):
    path = os.path.join(VERIFIED, sub, f"{i}.json")
    if not os.path.exists(path):
        return None
    j = json.load(open(path))
    if "corrections_v2" in j:
        return None
    before = len(j["merged"])
    if sub == "fin_deal_sourcing_mapping":
        j, log, cost = ownership(j)
        entry = {"ownership_changes": log, "cost": round(cost, 3)}
    else:
        j, log, cost = dedup(j)
        entry = {"units_before": before, "units_after": len(j["merged"]), "dedup_groups": log, "cost": round(cost, 3)}
    j["corrections_v2"] = entry
    j["scores"] = score(sub, i, j["merged"], j["verified"], j["labels"])
    json.dump(j, open(path, "w"), indent=1, ensure_ascii=False)
    LOG("fix", sub, i, {k: (len(v) if isinstance(v, list) else v) for k, v in entry.items()})
    return entry


if __name__ == "__main__":
    if sys.argv[1:2] in (["-h"], ["--help"]):
        print(__doc__)
        sys.exit(0)
    only = sys.argv[1:]
    jobs = [(u["id"], i) for u in load_tasks() for i in range(len(u["inputs"])) if not only or u["id"] in only]
    with ThreadPoolExecutor(8) as ex:
        log = [e for e in ex.map(lambda a: one(*a), jobs) if e]
    LOG("corrections done, $", round(sum(e["cost"] for e in log), 2))
