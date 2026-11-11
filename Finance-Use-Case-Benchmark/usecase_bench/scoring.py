"""Deterministic scoring from stored verdicts. No network, no model calls.

score(): per-provider coverage / accuracy / combined for one task, under the job's rule.
tally(): how each of a provider's own units was judged (true / wrong / off_scope / unverifiable; sums to its units).

Truth set = the pooled units verified true and in scope, from any provider. Coverage = weighted share of the truth set a
provider asserted; accuracy = weighted true / (true + false or off-scope + half of unverifiable) among its own units.
Combined = F1 of (coverage, accuracy) for decision jobs (accuracy matters as much as coverage), F2 (coverage weighted
2x) for list and event jobs. Each provider is judged on its own wording of a unit when wordings differ (label_verdicts).
For ANSWER_JOBS the coverage truth set is only the units that answer the question; "thoroughness" is F1 over all of it.
"""
import re

W = {"critical": 6, "useful": 2, "minor": 0.5}
FRESH = 1.5
OWNERSHIP = re.compile(r"own|investor|backed|founder|family|bootstrap|private equity|\bpe\b|venture", re.I)
DECISION = {"fin_due_diligence", "fin_credit_risk", "fin_kyc_screening", "gtm_meeting_prep_research",
            "fin_hf_company_research", "fin_grounded_answer", "fin_adverse_media", "fin_thesis_test",
            "dd_question", "le_question"}
# judge tag that sets a unit's weight, per job (the full tag spec the judge is asked for is in tasks.json)
TAG_KEY = {"gtm_meeting_prep_research": "materiality", "gtm_signal_tracking": "signal", "fin_due_diligence": "materiality",
           "fin_credit_risk": "materiality", "fin_kyc_screening": "group", "fin_deal_sourcing_mapping": "criteria",
           "fin_hf_company_research": "materiality", "fin_grounded_answer": "materiality",
           "fin_adverse_media": "materiality", "fin_thesis_test": "materiality",
           "dd_question": "materiality", "le_question": "materiality"}
# question-set jobs (question_tasks.py): coverage counts only the true facts tagged as answering the question, so extra
# true context cannot outscore a short correct answer; coverage of every true fact is kept as "thoroughness"
ANSWER_JOBS = {"dd_question", "le_question"}


def f1(cov, acc):
    return 2 * acc * cov / (acc + cov) if (acc + cov) else 0.0


def f2(cov, acc):
    return 5 * acc * cov / (4 * acc + cov) if (acc + cov) else 0.0


def score(sub, i, merged, ver, labels):
    """Per-provider coverage / accuracy / combined under the v4 rules."""
    out = {}

    def V(c, L=None):
        v = ver.get(c["cid"], {})
        lv = (v.get("label_verdicts") or {}).get(L) if L else None
        return {**v, "verdict": lv} if lv else v

    for a, L in labels.items():
        mine = [c for c in merged if L in c.get("asserted_by", [])]
        unv = sum(1 for c in mine if V(c, L).get("verdict") == "unverifiable")
        s = {"units": len(mine), "unverifiable": unv}

        if sub in ("gtm_account_discovery", "fin_deal_sourcing_mapping"):
            def status(c, L=None):
                v = V(c, L)
                crit = v.get("criteria") or []
                res = [x.get("result") for x in crit]
                if v.get("verdict") == "false" or "fail" in res or v.get("core_pass") is False:
                    return "wrong"
                if v.get("verdict") == "true" and v.get("core_pass") and "unknown" not in res:
                    return "ok"
                unknown = [x.get("criterion") or "" for x in crit if x.get("result") == "unknown"]
                if sub == "fin_deal_sourcing_mapping" and v.get("core_pass") and unknown and all(OWNERSHIP.search(u) for u in unknown):
                    return "partial"
                return "unconfirmed" if v.get("core_pass") or v.get("verdict") != "false" else "wrong"
            val = {"ok": 1.0, "partial": 0.5}
            truth_w = sum(val.get(status(c), 0) for c in merged)
            st = [status(c, L) for c in mine]
            got = sum(val.get(x, 0) for x in st)
            wrong = st.count("wrong")
            unc = st.count("unconfirmed")
            cov = got / truth_w if truth_w else 0.0
            denom = got + wrong + 0.5 * unc + 0.5 * st.count("partial")
            acc = got / denom if denom else None
            s.update(true=st.count("ok"), partial=st.count("partial"), wrong=wrong, unconfirmed=unc, truth=truth_w,
                     coverage=cov, accuracy=acc, combined=f2(cov, acc or 0))

        elif sub == "fin_kyc_screening":
            groups = {"registration": (0.40, 7), "people": (0.20, 5), "screening": (0.40, None)}
            rel = lambda v: bool(v.get("related_entity"))  # noqa: E731
            # a related-entity registration / officer fact is not part of the target's truth set
            good = lambda v: v.get("verdict") == "true" and v.get("in_scope", True) and not (rel(v) and v.get("group") != "screening")  # noqa: E731
            wt = lambda v: W.get(v.get("materiality"), 2) * (0.5 if rel(v) else 1) if v.get("group") == "screening" else 1  # noqa: E731
            truth_by = {g: [c for c in merged if good(V(c)) and V(c).get("group") == g] for g in groups}
            cov, wsum, true_n, wrong_w, true_w = 0.0, 0.0, 0, 0.0, 0.0
            for g, (w, cap) in groups.items():
                if not truth_by[g]:
                    continue
                mine_true = [c for c in mine if good(V(c, L)) and V(c).get("group") == g and c in truth_by[g]]
                if cap:
                    part = min(len(mine_true), cap) / cap
                else:  # screening: weighted by materiality, no cap
                    part = sum(wt(V(c)) for c in mine_true) / sum(wt(V(c)) for c in truth_by[g])
                cov += w * part
                wsum += w
                true_n += len(mine_true)
            cov = cov / wsum if wsum else 0.0
            for c in mine:
                v = V(c, L)
                if good(v):
                    true_w += 1
                elif rel(V(c)) and v.get("verdict") == "true":
                    wrong_w += 0.5  # true, but about another group entity: the wrong entity was resolved
                elif v.get("verdict") == "false" or (v.get("verdict") == "true" and not v.get("in_scope", True)):
                    wrong_w += 3 if (V(c).get("group") == "screening" and V(c).get("false_hit")) else 1
                elif v.get("verdict") == "unverifiable":
                    wrong_w += 0.5  # unsupported claim
            acc = true_w / (true_w + wrong_w) if (true_w + wrong_w) else None
            s.update(true=true_n, wrong=wrong_w, truth=sum(len(t) for t in truth_by.values()), coverage=cov,
                     accuracy=acc, combined=f2(cov, acc or 0))

        else:  # weighted findings / events
            key = TAG_KEY[sub]

            def w(c):
                v = V(c)
                if key == "signal":
                    return 3 if v.get("signal") == "buying" else 0.5
                return W.get(v.get("materiality"), 2) * (FRESH if v.get("fresh") else 1)
            good = lambda v: v.get("verdict") == "true" and v.get("in_scope", True) and (key != "material" or v.get("material", True))  # noqa: E731
            bad = lambda v: v.get("verdict") == "false" or (v.get("verdict") == "true" and not good(v))  # noqa: E731
            truth = [c for c in merged if good(V(c))]
            tw = sum(w(c) for c in truth)
            got = sum(w(c) for c in mine if good(V(c, L)) and c in truth)
            badw = sum(w(c) for c in mine if bad(V(c, L))) + sum(0.5 * w(c) for c in mine if V(c, L).get("verdict") == "unverifiable")
            cov = got / tw if tw else 0.0
            acc = got / (got + badw) if (got + badw) else None
            s.update(true=sum(1 for c in mine if good(V(c, L))), wrong=sum(1 for c in mine if bad(V(c, L))), truth=len(truth),
                     truth_weight=round(tw, 2), coverage=cov, accuracy=acc, combined=f2(cov, acc or 0))
            if sub in ANSWER_JOBS:
                ans = [c for c in truth if V(c).get("answers") not in (False, "false")]
                aw = sum(w(c) for c in ans)
                s.update(thorough_coverage=cov, answer_truth=len(ans),
                         coverage=sum(w(c) for c in mine if c in ans and good(V(c, L))) / aw if aw else 0.0)
            if sub == "fin_due_diligence":
                flags = [c for c in truth if V(c).get("red_flag")]
                s["red_flag_recall"] = (sum(1 for c in mine if c in flags) / len(flags)) if flags else None

        if sub in DECISION:  # accuracy matters as much as coverage for these jobs
            s["combined"] = f1(s["coverage"], s["accuracy"] or 0)
        if "thorough_coverage" in s:
            s["thoroughness"] = f1(s["thorough_coverage"], s["accuracy"] or 0)
        s["metric"] = "F1" if sub in DECISION else "F2"
        for k in ("coverage", "accuracy", "combined", "thorough_coverage", "thoroughness"):
            if k in s:
                s[k] = round(s[k], 3) if s[k] is not None else None
        out[a] = s
    return out


def tally(merged, ver, label):
    """Plain tally that sums to the provider's units: how each of its units was judged."""
    tl = {"true": 0, "wrong": 0, "off_scope": 0, "unverifiable": 0}
    for cu in merged:
        if label not in cu.get("asserted_by", []):
            continue
        vv = ver.get(cu["cid"], {})
        vd = (vv.get("label_verdicts") or {}).get(label) or vv.get("verdict")
        off = vv.get("in_scope") in (False, "False") or vv.get("material") is False or \
            any(x.get("result") == "fail" for x in vv.get("criteria") or []) or vv.get("core_pass") is False
        tl["true" if vd == "true" and not off else "off_scope" if vd == "true" else "wrong" if vd == "false" else "unverifiable"] += 1
    return tl
