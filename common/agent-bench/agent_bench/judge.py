"""The judge (v4b, Nov 10 2026). Per task (job x input), over every provider's run in results/raw/:

1. Extract: each provider's output -> the units that count for the job (Claude, no tools).
2. Merge: all providers' units -> one canonical list under shuffled anonymous labels. Units are merged only when they
   state the same fact with the same values, dates, attribution and qualifiers; each label's own wording is kept in
   "variants". Company-list jobs are canonicalised by domain.
3. Evidence: every URL any provider cited is fetched once (Nimble Extract, Exa contents, Firecrawl scrape, Nimble
   rendered, in that order until one returns text) and cached in evidence_cache/.
4. Verify: Sonnet first decides each unit from the matching passages of those saved pages (no tools, 15 units a call).
   Units it cannot decide, and every unit of a company-list job, go to a verifier agent that can Read/Grep the saved
   pages, search the web and fetch pages (5 units a call). When variants differ in substance, each label's own wording
   gets its own verdict (label_verdicts).
5. Retry: every unit still unverifiable gets a second, thorough attempt. Units left unverifiable count half wrong.
6. Audit: a seeded ~10% sample is re-checked independently by Opus; used only to report agreement.
7. Score: scoring.score (deterministic).

Then run agent_bench.corrections (duplicate merge + ownership rule) on the judged tasks.

    python -m agent_bench.judge --suite <folder> [job ...]   ->  results/verified/<job>/<i>.json (existing files are skipped)
"""
import hashlib
import json
import os
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor

from .claude_cli import AUDIT_MODEL, FETCH, LOG, evidence_call, extract_call, plain_call, web_call
from .config import EVID, PROJECT, PROVIDERS, RAW, VERIFIED, load_tasks, today
from .scoring import score

TASKS = {u["id"]: u for u in load_tasks()}
OUT = VERIFIED
LISTS = ("gtm_account_discovery", "fin_deal_sourcing_mapping")

# --- prompts (part of the method; kept exactly as run) ---

EXTRACT = """Today is {{today}}. Turn ONE research output into the units that count for this task.
UNIT DEFINITION: {unit_rule}
- Each unit is self-contained (names the entity).
- Skip process notes, null/empty fields, and statements about what was NOT found.
- Keep the cited URL for the unit if given.
Reply with JSON only: [{{{{"claim": "...", "url": "..."}}}}]. Include every unit the output contains (up to 80)."""

MERGE = """Merge unit lists from several anonymous systems (labels) for the SAME task into one canonical list.
UNIT DEFINITION: {unit_rule}
- Merge units from different labels ONLY when they state the same fact with the same values, dates, attribution and
  qualifiers (same company, same person-in-role, same event, same field value).
- If one label's version adds something the others don't say (a characterization, a quote, an attribution, an extra
  figure) or words it so that it could be true for one and false for another, keep it as a SEPARATE unit.
- Different values for the same field or role (two CFOs, two dates for one event) -> separate units, cross-referenced
  in conflicts_with.
- For every canonical unit keep each label's own wording in "variants".
Reply with JSON only: [{{"cid": "c1", "claim": "...", "asserted_by": ["A", "B"], "variants": {{"A": "...", "B": "..."}},
"urls": ["..."], "conflicts_with": []}}]"""

VERIFY_V3 = """Today is {today}. You verify units produced for this task, one by one, with the same standard for each.
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

MATERIALITY_RULE = """
MATERIALITY (when asked): judge it as of {today} for the person who has to decide today (a seller before the meeting,
an investment committee, a lender, a compliance officer, a portfolio manager). critical = would change that decision
now (current status, current ratings, liquidity, defaults, recent events that change the picture, active legal or
regulatory action); useful = relevant context; minor = stale history or background that would barely change the
decision. Old events count as critical only if they still drive the decision today.
FRESH (when asked): true if the unit is dated within 90 days before {today} or describes the current state."""

DECIDE = """
EVIDENCE: every page any system cited for this task has been fetched through four web data layers and saved under
evidence_cache/ (manifest below: url -> file). Each unit lists "evidence": the saved files of the pages its own systems
cite for it, plus any excerpt a system quoted, and "asserted_by_n", how many independent systems state it.
For each unit, FIRST Grep/Read those saved files (and Grep the whole evidence_cache/ for its key figures, names and
dates), then use your own search. Decide at the highest confidence you can from (1) the saved source pages, (2) your
own search and page reads, (3) agreement between independent systems that cite different sources. A unit supported by
a saved source page is true even if the live page is blocked; one contradicted by a source is false.
Give "confidence": "high|medium".
"unverifiable" is allowed only when no passage addresses the unit AND your search finds nothing; such a unit is
treated as an unsupported claim."""

VERIFY = VERIFY_V3.replace(
    'For each unit return:',
    'When a unit has "variants" that differ in substance, also return "label_verdicts": {{"A": "true|false|unverifiable", ...}} '
    'judging each label\'s own wording.\n{materiality_rule}\n' + DECIDE.replace('{', '{{').replace('}', '}}') + '\n\nFor each unit return:')

INLINE = """
EVIDENCE: every page any system cited for this task was fetched and saved. Each unit below comes with "passages": the
parts of those saved pages that best match the unit's names, figures and dates (url + text), any excerpt a system
quoted, and "asserted_by_n", how many independent systems state it. You have no tools: decide from the passages, from
agreement between independent systems that cite different sources, and from what you reliably know as of your training.
A unit supported by a passage is true; one contradicted by a passage is false. Give "confidence": "high|medium".
"unverifiable" only when no passage addresses the unit and you cannot otherwise tell; it counts as an unsupported claim."""

VERIFY_INLINE = VERIFY_V3.split("How to check:")[0] + (
    'When a unit has "variants" that differ in substance, also return "label_verdicts": {{"A": "true|false|unverifiable", ...}} '
    "judging each label's own wording.\n{materiality_rule}\n" + INLINE.replace("{", "{{").replace("}", "}}") +
    "\n\nFor each unit return:" + VERIFY_V3.split("For each unit return:")[1])

RETRY = """Today is {today}. These units were marked unverifiable on a first pass. Make a second, thorough attempt.
TASK: {task}
A unit is a {unit_name}. Verdict rule: {verify_rule}
For each unit: read EVERY given URL with Bash: {fetch} "<url>" (renders JS, reads PDFs, gets past most blocks), then
search for other sources (news coverage, the issuer's own site, registries, filings, archive.org copies).
Answer "unverifiable" only if nothing confirms or refutes it after that.
Return the same fields as before: "verdict", "in_scope", {tag_spec}, "source", "note"{label_note}.
Reply with JSON only: [{{"cid": "c1", "verdict": "...", ...}}]"""

NO_DUP = """
DUPLICATES: never mark a unit false because it repeats another unit or another system's answer. Judge every unit on
its own facts; duplicates are handled by the scorer."""
LIST_EVIDENCE = """
EVIDENCE FOR LIST COMPANIES: a criterion passes only when a saved page, a page you opened or a search result you saw
states it. A system's own claim is not evidence. Not finding investor news does not prove independence: mark an
ownership criterion "unknown" unless a source states the ownership (family-owned, founder-owned, a named owner). If
you could not open any source for a company, every criterion you could not see stated is "unknown". Say in the note
which source settled each criterion."""
KYC_ENTITY = """
TARGET ENTITY: the legal entity the input names; if the input names a brand or group, its main operating company
(name it in the note of the first registration unit, and use the same target for every label). Registration and
officer units about any other group entity: judge whether the fact is true and set "related_entity": true (the
scorer counts these as a partial error: the wrong entity was resolved). Screening findings about a group affiliate
(a fine on a subsidiary, a parent's sanctions exposure): judge them, set "related_entity": true; they count at half
weight. Apply the same rule to every label."""

# --- helpers ---

URL_RE = re.compile(r"https?://[^\s\"'<>\\)\]]+")
STOP = set("The A An In On Of And For To With By From At As Its Is Was Were Has Have Inc Ltd LLC Corp Company".split())


def rec(sub, i, arm):
    p = os.path.join(RAW, sub, f"{i}_{arm}.json")
    return json.load(open(p)) if os.path.exists(p) else None


def text(r):
    if not r or r.get("status") != "completed" or r.get("output") in (None, "", {}, []):
        return None
    return r["output"] if isinstance(r["output"], str) else json.dumps(r["output"], ensure_ascii=False)


def canon_companies(merged):
    """Company tasks: one unit per company domain, whoever listed it, so a second provider finding the same company
    is credited instead of called a duplicate."""
    out, by = [], {}
    for c in merged:
        m = re.search(r"\b((?:[a-z0-9-]+\.)+[a-z]{2,})\b", (c.get("claim") or "").lower().split(" fits")[0].split(" matches")[0])
        d = m.group(1).removeprefix("www.") if m else None
        if d and d in by:
            k = by[d]
            k["asserted_by"] = list(dict.fromkeys((k.get("asserted_by") or []) + (c.get("asserted_by") or [])))
            k["variants"] = {**(c.get("variants") or {}), **(k.get("variants") or {})}
            k["urls"] = list(dict.fromkeys((k.get("urls") or []) + (c.get("urls") or [])))
            continue
        c = {**c}
        if d:
            by[d] = c
        out.append(c)
    return out


def _fetch_chain(url):
    """Try the data layers in turn: Nimble Extract, Exa contents, Firecrawl scrape, Nimble rendered."""
    import httpx
    from nimble_python import Nimble
    nimble = lambda render, timeout: Nimble(timeout=timeout, max_retries=0).extract.run(  # noqa: E731
        url=url, formats=["markdown"], **({"render": True} if render else {})).model_dump().get("data", {}).get("markdown")
    firecrawl = lambda: httpx.post("https://api.firecrawl.dev/v2/scrape", timeout=90,  # noqa: E731
                                   headers={"Authorization": f"Bearer {os.environ.get('FIRECRAWL_API_KEY', '')}"},
                                   json={"url": url, "formats": ["markdown"], "parsers": ["pdf"]}).json().get("data", {}).get("markdown")
    exa = lambda: (httpx.post("https://api.exa.ai/contents", timeout=60, headers={"x-api-key": os.environ.get("EXA_API_KEY", "")},  # noqa: E731
                              json={"urls": [url], "text": True, "livecrawl": "fallback"}).json().get("results") or [{}])[0].get("text")
    tries = [(lambda: nimble(False, 60)), exa, firecrawl, (lambda: nimble(True, 120))]
    names = ["nimble", "exa", "firecrawl", "nimble_render"]
    for n, t in enumerate(tries):
        try:
            txt = t() or ""
            if len(txt) > 200:
                return txt[:300_000], names[n]
        except Exception:  # noqa: BLE001
            continue
    return "", None


def evidence_file(url):
    return os.path.join(EVID, hashlib.sha1(url.encode()).hexdigest() + ".md")


def _fetch(url):
    """A cited page as text, fetched once through the data-layer chain and cached on disk ('' when every layer fails)."""
    path = evidence_file(url)
    if os.path.exists(path):
        return open(path).read()
    txt, via = _fetch_chain(url)
    if not txt:
        LOG("evidence fetch failed on every layer", url[:100])
    os.makedirs(EVID, exist_ok=True)
    open(path, "w").write(f"<!-- {url} via {via} -->\n{txt}" if txt else "")
    return open(path).read()


def evidence_pack(recs):
    """Every URL any provider cited, with its fetched text; Parallel's cited excerpts per URL; each provider's raw text."""
    urls, excerpts, texts = [], {}, {}
    for a, r in recs.items():
        for b in (r or {}).get("basis") or []:
            for c in b.get("citations") or []:
                if c.get("url"):
                    excerpts.setdefault(c["url"], []).extend(c.get("excerpts") or [])
        texts[a] = json.dumps((r or {}).get("output"), ensure_ascii=False) + json.dumps((r or {}).get("basis") or [], ensure_ascii=False)
        for u in URL_RE.findall(texts[a]):
            u = u.rstrip(".,;")
            if u not in urls:
                urls.append(u)
    with ThreadPoolExecutor(16) as ex:
        pages = dict(zip(urls, ex.map(_fetch, urls), strict=True))
    return pages, excerpts, texts


def _tokens(claim, generic):
    """Distinctive tokens of a claim with weights: numbers and dates 3, other names 1; the task's own words dropped."""
    out = {}
    for t in re.findall(r"\$?\d[\d.,]*[%MBK]?|[A-Z][\w&.-]{2,}", claim or ""):
        t = t.strip(".,")
        if t in STOP or t.lower() in generic or len(t) < 3:
            continue
        out[t] = 3 if t[0].isdigit() or t[0] == "$" else 1
    return out


def _best_window(txt, toks, win):
    if not txt:
        return 0, ""
    step = win // 2
    best, bp = -1, 0
    for p in range(0, max(1, len(txt) - win + 1), step):
        seg = txt[p:p + win]
        sc = sum(w for t, w in toks.items() if t in seg)
        if sc > best:
            best, bp = sc, p
    return best, txt[bp:bp + win]


def unit_evidence(c, pages, excerpts, texts, labels, generic):
    """Where to look for this unit: the saved files of the pages its asserting providers cite next to it in their own
    output (or that the merge kept), and any excerpt a provider quoted from them."""
    variants = c.get("variants") or {}
    toks = _tokens(" ".join([c.get("claim") or ""] + list(variants.values())), generic)
    near = list(dict.fromkeys(c.get("urls") or []))
    for L in c.get("asserted_by") or []:
        _, seg = _best_window(texts.get(labels.get(L), ""), toks, 1500) if toks else (0, "")
        near += [u.rstrip(".,;") for u in URL_RE.findall(seg)]
    out = []
    for u in dict.fromkeys(near):
        has = bool(pages.get(u))
        if has or excerpts.get(u):
            out.append({"url": u, "file": os.path.relpath(evidence_file(u), PROJECT) if has else None,
                        "provider_excerpts": excerpts.get(u, [])[:2]})
    return out[:5]


def windows(txt, toks, win=900, k=2):
    if not txt or not toks:
        return []
    step = win // 2
    scored = []
    for p in range(0, max(1, len(txt) - win + 1), step):
        seg = txt[p:p + win]
        sc = sum(w for t, w in toks.items() if t in seg)
        if sc > 0:
            scored.append((sc, p))
    scored.sort(reverse=True)
    out, used = [], []
    for sc, p in scored:
        if any(abs(p - q) < win for q in used):
            continue
        used.append(p)
        out.append((sc, txt[p:p + win]))
        if len(out) == k:
            break
    return out


def passages(c, ev, pages, generic):
    toks = _tokens(" ".join([c.get("claim") or ""] + list((c.get("variants") or {}).values())), generic)
    own = [e["url"] for e in ev]
    got = []
    for u in own:
        for sc, seg in windows(pages.get(u), toks):
            got.append((sc + 2, u, seg))  # pages the unit's own systems cite get a small boost
    for u, t in pages.items():  # stand-in for "grep the whole cache"
        if u in own:
            continue
        for sc, seg in windows(t, toks, k=1):
            got.append((sc, u, seg))
    got.sort(key=lambda x: -x[0])
    return [{"url": u, "text": seg} for _, u, seg in got[:6]]


# --- one task ---

def judge(u, i):
    sub = u["id"]
    path = os.path.join(OUT, sub, f"{i}.json")
    if os.path.exists(path):
        return
    TODAY = today()
    unit_name, unit_rule, verify_rule = u["unit"]["name"], u["unit"]["definition"], u["unit"]["verify_rule"]
    arms = [a for a in PROVIDERS if (r := rec(sub, i, a)) and r.get("status") in ("completed", "failed")]
    if not arms:
        return LOG("skip, no runs", sub, i)
    task = u["inputs"][i]
    labels = arms[:]
    random.Random(f"v4{sub}{i}").shuffle(labels)
    lab = dict(zip(labels, "ABCDEFGHIJ", strict=False))
    cost = 0.0

    claims = {}
    for a in arms:
        t = text(rec(sub, i, a))
        if not t:
            claims[a] = []
            continue
        c, k = extract_call(EXTRACT.format(unit_rule=unit_rule).format(today=TODAY), f"TASK: {task}\n\nOUTPUT:\n{t[:30000]}")
        cost += k
        claims[a] = c or []

    if not any(claims.values()) and any(text(rec(sub, i, a)) for a in arms):
        raise RuntimeError(f"claim extraction returned nothing for {sub} {i}")  # e.g. a rate-limit burst; never save it
    merged, k = extract_call(MERGE.format(unit_rule=unit_rule), f"TASK: {task}\n\n" + "\n\n".join(
        f"=== {lab[a]} ===\n{json.dumps(claims[a], ensure_ascii=False)}" for a in arms))
    cost += k
    merged = [c for c in (merged or []) if isinstance(c, dict) and c.get("cid")]
    if not merged and any(claims.values()):
        raise RuntimeError(f"merge returned nothing for {sub} {i}")  # never save a task whose merge failed
    if sub in LISTS:
        merged = canon_companies(merged)
    extra = NO_DUP + (LIST_EVIDENCE if sub in LISTS else "") + (KYC_ENTITY if sub == "fin_kyc_screening" else "")

    tag_spec = u["judge_tag"]["spec"]
    instr = u["instructions"].split("General rules:")[0][:3500]
    sysmsg = VERIFY.format(today=TODAY, task=task, instructions=instr, unit_name=unit_name, verify_rule=verify_rule,
                           fetch=FETCH, tag_spec=tag_spec, materiality_rule=MATERIALITY_RULE.format(today=TODAY)) + extra
    pages, excerpts, texts = evidence_pack({a: rec(sub, i, a) for a in arms})
    generic = {w.lower() for w in re.findall(r"[A-Za-z][\w&.-]{2,}", task)}
    by_label = {L: a for a, L in lab.items()}
    ev = {c["cid"]: unit_evidence(c, pages, excerpts, texts, by_label, generic) for c in merged}
    manifest = "\n\nSAVED PAGES (url -> file):\n" + "\n".join(
        f"{x} -> {os.path.relpath(evidence_file(x), PROJECT)}" for x, t in pages.items() if t)
    payload = lambda b: json.dumps([{**{x: c.get(x) for x in ("cid", "claim", "variants", "urls")},  # noqa: E731
                                     "asserted_by_n": len(c.get("asserted_by") or []), "evidence": ev[c["cid"]]} for c in b], ensure_ascii=False)
    ver = {}
    # Sonnet first decides from the matching passages put in the prompt (no tools, 15 units a call); units it can't
    # decide go to the page-reading agent. Company-list tasks need search (private ownership, criteria not on cited
    # pages), so they go straight to the agent.
    fast = []
    if sub not in LISTS:
        isys = VERIFY_INLINE.format(today=TODAY, task=task, instructions=instr, unit_name=unit_name, verify_rule=verify_rule,
                                    tag_spec=tag_spec, materiality_rule=MATERIALITY_RULE.format(today=TODAY)) + extra
        ipay = lambda b: json.dumps([{**{x: c.get(x) for x in ("cid", "claim", "variants")},  # noqa: E731
                                      "asserted_by_n": len(c.get("asserted_by") or []),
                                      "provider_excerpts": [x for e in ev[c["cid"]] for x in e["provider_excerpts"]][:2],
                                      "passages": passages(c, ev[c["cid"]], pages, generic)} for c in b], ensure_ascii=False)
        with ThreadPoolExecutor(8) as ex:
            for res, k in ex.map(lambda b: plain_call(isys, ipay(b)), [merged[j:j + 15] for j in range(0, len(merged), 15)]):
                cost += k
                for v in res or []:
                    if isinstance(v, dict) and v.get("cid") and v.get("verdict") in ("true", "false"):
                        ver[v["cid"]] = {**v, "via": "passages"}
        fast = list(ver)
    todo = [c for c in merged if c["cid"] not in ver]
    with ThreadPoolExecutor(8) as ex:
        for res, k in ex.map(lambda b: evidence_call(sysmsg + manifest, payload(b)), [todo[j:j + 5] for j in range(0, len(todo), 5)]):
            cost += k
            for v in res or []:
                if isinstance(v, dict) and v.get("cid"):
                    ver[v["cid"]] = {**v, "via": "agent"}

    # a second attempt on every unverifiable unit
    unv = [c for c in merged if ver.get(c["cid"], {}).get("verdict") in ("unverifiable", None)]
    retried = 0
    if unv:
        rmsg = RETRY.format(today=TODAY, task=task, unit_name=unit_name, verify_rule=verify_rule, fetch=FETCH,
                            tag_spec=tag_spec, label_note=', and "label_verdicts" when variants differ in substance') + MATERIALITY_RULE.format(today=TODAY) + extra
        with ThreadPoolExecutor(8) as ex:
            for res, k in ex.map(lambda b: evidence_call(rmsg + manifest, payload(b)), [unv[j:j + 5] for j in range(0, len(unv), 5)]):
                cost += k
                for v in res or []:
                    if isinstance(v, dict) and v.get("cid") and v.get("verdict") in ("true", "false"):
                        ver[v["cid"]] = {**v, "retried": True}
                        retried += 1

    rnd = random.Random(f"audit4{sub}{i}")
    sample = [c for c in merged if rnd.random() < 0.10]
    audit = {}
    if sample:
        res, k = web_call(AUDIT.format(today=TODAY, task=task, verify_rule=verify_rule, fetch=FETCH), payload(sample), model=AUDIT_MODEL)
        cost += k
        audit = {v["cid"]: v for v in res or [] if isinstance(v, dict) and v.get("cid")}

    res = {"sub_use_case": sub, "input_n": i, "task": task, "labels": lab, "unit": unit_name,
           "evidence_pages": len(pages), "evidence_fetched": sum(1 for t in pages.values() if t),
           "merged": merged, "claims": claims, "verified": ver, "audit": audit, "retried_to_verdict": retried,
           "unverifiable_first_pass": len(unv), "decided_from_passages": len(fast),
           "scores": score(sub, i, merged, ver, lab), "judge": "v4b", "judge_cost_usd": round(cost, 3)}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(res, open(path, "w"), indent=1, ensure_ascii=False)
    LOG("judged", sub, i, "units", len(merged), "retry", f"{retried}/{len(unv)}", "$", round(cost, 2),
        {a: s["combined"] for a, s in res["scores"].items()})


def main(only):
    jobs = [(u, i) for u in TASKS.values() for i in range(len(u["inputs"])) if not only or u["id"] in only]
    with ThreadPoolExecutor(int(os.environ.get("JUDGE_PAR", "10"))) as ex:
        def safe(j):
            try:
                judge(*j)
            except Exception as e:  # noqa: BLE001
                LOG("judge failed", j[0]["id"], j[1], str(e)[:200])
        list(ex.map(safe, jobs))


if __name__ == "__main__":
    if sys.argv[1:2] in (["-h"], ["--help"]):
        print(__doc__)
        sys.exit(0)
    main(sys.argv[1:])
