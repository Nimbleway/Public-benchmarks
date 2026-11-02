"""Step C + D of judging: verify every merged unit on the web with use-case-specific rules, then score.

Reads the extracted and merged units from results/claims/ (judge_extract.py). Per task:
  - verify in batches of 5; the verifier (Claude via the Claude Code CLI, model sonnet, tools WebSearch, WebFetch and
    one Bash command) opens the cited URLs, then searches. Only when WebFetch fails or the page is a PDF / long filing
    does it read the page through fetch.py (Nimble Extract).
  - each unit gets verdict true / false / unverifiable, in_scope, and a use-case tag (materiality, signal type,
    KYC group, or material flag) that sets its weight.
  - 10% of units are re-checked independently by a second model (opus) to measure judge agreement ("audit").
Scoring (per provider, against the pooled truth = units verified true and in scope):
  coverage = weighted share of the pooled truth the provider asserted
  accuracy = weighted true / (true + false or off-scope) among the units it asserted (unverifiable excluded)
  combined = F2 of (coverage, accuracy) = 5*acc*cov / (4*acc + cov)
  - KYC: registration 40% (7 fields) / people 20% (capped at 5) / screening findings 40%; a false screening hit weighs 3x
  - account brief, due diligence, credit risk: materiality weights critical 3 / useful 2 / minor 1; DD red-flag recall
  - account signals: buying signal 2, minor release 1; portfolio monitoring: non-material events are off scope

    python -m usecase_bench.judge_verify [use_case ...]         ->  results/verified/<use_case>/<i>.json
    python -m usecase_bench.judge_verify --fix [use_case ...]   re-verify units a failed batch left unchecked, re-score
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

from . import CLAIMS, PKG, VERIFIED, load_tasks

SRC, OUT = CLAIMS, VERIFIED
TODAY = date.today().isoformat()
FETCH = f"{sys.executable} {os.path.join(PKG, 'fetch.py')}"
LOG = lambda *a: print(time.strftime("%H:%M:%S"), *a, flush=True)  # noqa: E731
TASKS = {u["id"]: u for u in load_tasks()}

VERIFY = """Today is {today}. You verify units produced for this task, one by one, with the same standard for each.
TASK: {task}
INSTRUCTIONS EVERY SYSTEM GOT:
{instructions}
A unit is a {unit_name}. Verdict rule: {verify_rule}

How to check: open the unit's URLs first. If WebFetch fails, is blocked (403), or the page is a PDF or a long filing,
read it with Bash: {fetch} "<url>". Then search for confirmation. Make a real attempt for EVERY unit before you answer
"unverifiable" - "did not check" is not allowed.

For each unit return: "verdict": "true|false|unverifiable", "in_scope": true|false, {tag_spec},
"source": the URL that decided it, "note": one short sentence.
Reply with JSON only: [{{"cid": "c1", "verdict": "...", "in_scope": true, ..., "source": "...", "note": "..."}}]"""

AUDIT = """Today is {today}. Independently check whether each unit is true, using web search and page fetches
({fetch} "<url>" for blocked pages or PDFs). TASK: {task}. Verdict rule: {verify_rule}
Reply with JSON only: [{{"cid": "c1", "verdict": "true|false|unverifiable", "note": "short"}}]"""


def claude(system, user, model="sonnet"):
    args = ["claude", "-p", "--strict-mcp-config", "--disable-slash-commands", "--setting-sources", "project",
            "--model", model, "--system-prompt", system, "--output-format", "json",
            "--tools", "WebSearch,WebFetch,Bash", "--allowedTools", "WebSearch", "WebFetch", f"Bash({FETCH}:*)"]
    cost = 0.0
    for _ in range(2):
        p = subprocess.run(args, input=user, capture_output=True, text=True, timeout=3000)
        try:
            env = json.loads(p.stdout)
            cost += env.get("total_cost_usd") or 0
            m = re.search(r"\[.*\]", env.get("result") or "", re.S)
            return json.loads(m.group(0)), cost
        except Exception as e:
            LOG("parse fail", str(e)[:80], p.stderr[:120])
    return [], cost


def f2(cov, acc):
    return 5 * acc * cov / (4 * acc + cov) if (acc + cov) else 0.0


def score(sub, i, merged, ver, labels):
    """Per-provider coverage / accuracy / combined under the use case's rule."""
    out = {}
    V = lambda c: ver.get(c["cid"], {})  # noqa: E731
    for a, L in labels.items():
        mine = [c for c in merged if L in c.get("asserted_by", [])]
        unv = sum(1 for c in mine if V(c).get("verdict") == "unverifiable")
        s = {"units": len(mine), "unverifiable": unv}

        if sub == "fin_kyc_screening":
            groups = {"registration": (0.40, 7), "people": (0.20, 5), "screening": (0.40, None)}
            truth_by = {g: [c for c in merged if V(c).get("verdict") == "true" and V(c).get("in_scope", True) and V(c).get("group") == g] for g in groups}
            cov, wsum, true_n, wrong_w, true_w = 0.0, 0.0, 0, 0.0, 0.0
            for g, (w, cap) in groups.items():
                denom = cap if cap else len(truth_by[g])
                if not denom:
                    continue  # no screening findings exist for this entity: weight drops out
                got = sum(1 for c in mine if c in truth_by[g])
                cov += w * min(got, denom) / denom
                wsum += w
                true_n += got
            cov = cov / wsum if wsum else 0.0
            for c in mine:
                v = V(c)
                if v.get("verdict") == "true" and v.get("in_scope", True):
                    true_w += 1
                elif v.get("verdict") == "false" or (v.get("verdict") == "true" and not v.get("in_scope", True)):
                    wrong_w += 3 if v.get("group") == "screening" else 1
            acc = true_w / (true_w + wrong_w) if (true_w + wrong_w) else None
            s.update(true=true_n, wrong=wrong_w, truth=sum(len(t) for t in truth_by.values()), coverage=cov,
                     accuracy=acc, combined=f2(cov, acc or 0))

        else:  # weighted findings / events
            key = TASKS[sub]["judge_tag"]["key"]

            def w(c):
                v = V(c)
                if key == "materiality":
                    return {"critical": 3, "useful": 2, "minor": 1}.get(v.get("materiality"), 1)
                if key == "signal":
                    return 2 if v.get("signal") == "buying" else 1
                return 1
            good = lambda c: V(c).get("verdict") == "true" and V(c).get("in_scope", True) and (key != "material" or V(c).get("material", True))  # noqa: E731
            bad = lambda c: V(c).get("verdict") == "false" or (V(c).get("verdict") == "true" and not good(c))  # noqa: E731
            truth = [c for c in merged if good(c)]
            tw = sum(w(c) for c in truth)
            got = sum(w(c) for c in mine if good(c))
            badw = sum(w(c) for c in mine if bad(c))
            cov = got / tw if tw else 0.0
            acc = got / (got + badw) if (got + badw) else None
            s.update(true=sum(1 for c in mine if good(c)), wrong=sum(1 for c in mine if bad(c)), truth=len(truth),
                     coverage=cov, accuracy=acc, combined=f2(cov, acc or 0))
            if sub == "fin_due_diligence":
                flags = [c for c in truth if V(c).get("red_flag")]
                s["red_flag_recall"] = (sum(1 for c in mine if c in flags) / len(flags)) if flags else None

        for k in ("coverage", "accuracy", "combined"):
            s[k] = round(s[k], 3) if s[k] is not None else None
        out[a] = s
    return out


def judge(u, i):
    path = os.path.join(OUT, u["id"], f"{i}.json")
    if os.path.exists(path):
        return
    src_path = os.path.join(SRC, u["id"], f"{i}.json")
    if not os.path.exists(src_path):
        LOG("skip, no extracted units", u["id"], i)
        return
    src = json.load(open(src_path))
    merged, labels, task = src["merged"], src["labels"], src["task"]
    sysmsg = VERIFY.format(today=TODAY, task=task, instructions=u["instructions"].split("General rules:")[0][:3500],
                           unit_name=u["unit"]["name"], verify_rule=u["unit"]["verify_rule"], fetch=FETCH,
                           tag_spec=u["judge_tag"]["spec"])
    batches = [merged[j:j + 5] for j in range(0, len(merged), 5)]
    ver, cost = {}, 0.0
    with ThreadPoolExecutor(4) as ex:
        for res, k in ex.map(lambda b: claude(sysmsg, json.dumps([{x: c.get(x) for x in ("cid", "claim", "urls")} for c in b], ensure_ascii=False)), batches):
            cost += k
            for v in res:
                ver[v["cid"]] = v

    rnd = random.Random(f"audit{u['id']}{i}")
    sample = [c for c in merged if rnd.random() < 0.10]
    audit = {}
    if sample:
        res, k = claude(AUDIT.format(today=TODAY, task=task, verify_rule=u["unit"]["verify_rule"], fetch=FETCH),
                        json.dumps([{x: c.get(x) for x in ("cid", "claim", "urls")} for c in sample], ensure_ascii=False), model="opus")
        cost += k
        audit = {v["cid"]: v for v in res}

    res = {"sub_use_case": u["id"], "input_n": i, "task": task, "labels": labels, "unit": src.get("unit"),
           "merged": merged, "claims": src["claims"], "verified": ver, "audit": audit,
           "scores": score(u["id"], i, merged, ver, labels), "judge_cost_usd": round(cost, 3)}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(res, open(path, "w"), indent=1, ensure_ascii=False)
    LOG("judged", u["id"], i, "$", round(cost, 2), {a: (s["coverage"], s["accuracy"], s["combined"]) for a, s in res["scores"].items()})


def fix(u, i):
    """Re-verify units that a failed verify batch left unchecked, then re-score the task."""
    path = os.path.join(OUT, u["id"], f"{i}.json")
    if not os.path.exists(path):
        return
    j = json.load(open(path))
    missing = [c for c in j["merged"] if c["cid"] not in j["verified"]]
    if not missing:
        return
    sysmsg = VERIFY.format(today=TODAY, task=j["task"], instructions=u["instructions"].split("General rules:")[0][:3500],
                           unit_name=u["unit"]["name"], verify_rule=u["unit"]["verify_rule"], fetch=FETCH,
                           tag_spec=u["judge_tag"]["spec"])
    batches = [missing[k:k + 5] for k in range(0, len(missing), 5)]
    with ThreadPoolExecutor(4) as ex:
        for res, cost in ex.map(lambda b: claude(sysmsg, json.dumps([{x: c.get(x) for x in ("cid", "claim", "urls")} for c in b], ensure_ascii=False)), batches):
            j["judge_cost_usd"] = round(j["judge_cost_usd"] + cost, 3)
            for v in res:
                j["verified"][v["cid"]] = v
    j["scores"] = score(u["id"], i, j["merged"], j["verified"], j["labels"])
    json.dump(j, open(path, "w"), indent=1, ensure_ascii=False)
    LOG("fixed", u["id"], i, "re-verified", len(missing), "still missing", sum(1 for c in j["merged"] if c["cid"] not in j["verified"]))


if __name__ == "__main__":
    args = sys.argv[1:]
    run = fix if "--fix" in args else judge
    only = [a for a in args if a != "--fix"]
    jobs = [(u, i) for u in load_tasks() for i in range(len(u["inputs"])) if not only or u["id"] in only]
    with ThreadPoolExecutor(3) as ex:
        list(ex.map(lambda j: run(*j), jobs))
