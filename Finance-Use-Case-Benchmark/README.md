# usecase-bench: finance (v2)

This benchmark measures how well web research agents do real finance research
jobs: write a due diligence memo, assess credit risk, run a KYC screen, source
deals, research a company for a hedge fund, answer a current question with
sources, investigate adverse media and test an investment thesis. Every
provider gets the same instructions and the same output schema. Every fact a
provider returns is checked against the pages the providers cited and the live
web, and each provider is scored on how many of the verified facts it found
(coverage) and how many of its own facts were right (accuracy). Seven of the
eight jobs are also graded against fixed answer keys written before any
answer was seen.

It is the finance half of a GTM and finance suite; the GTM half is in
[`GTM-Use-Case-Benchmark/`](../GTM-Use-Case-Benchmark). Both were run together
with the same harness, judge and method.

Runs: November 3 to 10, 2026. Judged: November 10, 2026, every run of a task
side by side. Corrections: November 11, 2026 (see
[Corrections log](#corrections-log)).

**v2 replaces v1 (October 2026).** v1 had 12 finance tasks, four providers
and an older judge that verified units with its own web browsing only. v2 has
40 finance tasks (8 jobs x 5 inputs), six providers including two frontier
models with their own web tools (GPT-6.1 Sol and Claude Opus 5.5), a judge
that fetches every cited page and decides from the saved sources, answer keys
written before seeing any answer, a stale-key audit, and a scoring-correction
pass after an external review. The v1 results are in the git history.

## Results

Higher is better for combined, accuracy, coverage and critical key items.
Wins = tasks where the provider had the highest combined score; an exact tie
splits the win.

### Finance (40 tasks)

| Provider | Combined | Accuracy | Coverage | Critical key items correct | Wins | Avg cost / run | Median seconds |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Nimble | 0.594 | 0.920 | 0.485 | 0.948 | 14/40 | $0.45 | 269 |
| Claude Opus 5.5 | 0.583 | 0.932 | 0.464 | 0.970 | 19/40 | $0.79 | 194 |
| GPT-6.1 Sol | 0.513 | 0.975 | 0.408 | 0.848 | 5/40 | $0.64 | 323 |
| Exa | 0.455 | 0.977 | 0.349 | 0.858 | 1/40 | $0.48 | 177 |
| Parallel | 0.418 | 0.982 | 0.323 | 0.820 | 1/40 | $0.54 | 522 |
| Firecrawl | 0.388 | 0.945 | 0.300 | 0.774 | 0/40 | $0.36 | 222 |

Critical key items correct is the share of critical answer-key questions the
provider got right, averaged over the 35 keyed tasks (all jobs except deal
sourcing).

Units per provider, as judged. True = true and in scope; wrong = contradicted
by a source; off scope = true but outside the request or failing a list
criterion; unverifiable = still unsettled after two attempts. The four add up
to the provider's units.

| Provider | Units | True | Wrong | Off scope | Unverifiable |
| :--- | :---: | :---: | :---: | :---: | :---: |
| Nimble | 1307 | 1205 | 56 | 11 | 35 |
| Claude Opus 5.5 | 1058 | 1018 | 23 | 8 | 9 |
| GPT-6.1 Sol | 873 | 845 | 12 | 6 | 10 |
| Exa | 739 | 723 | 9 | 2 | 5 |
| Parallel | 680 | 665 | 7 | 2 | 6 |
| Firecrawl | 696 | 664 | 21 | 1 | 10 |

### Combined score by job (mean of 5 tasks)

| Job | Tier | Metric | Nimble | Exa | Parallel | Firecrawl | GPT-6.1 Sol | Claude Opus 5.5 |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| Investment due diligence | high | F1 | **0.522** | 0.382 | 0.334 | 0.329 | 0.468 | 0.486 |
| Credit and counterparty risk assessment | high | F1 | **0.579** | 0.440 | 0.426 | 0.360 | 0.511 | 0.567 |
| KYC, AML and compliance screening | high | F1 | 0.815 | 0.621 | 0.725 | 0.620 | 0.755 | **0.837** |
| Deal sourcing and market mapping | high | F2 | **0.536** | 0.249 | 0.297 | 0.209 | 0.370 | 0.220 |
| Hedge-fund company research | high | F1 | 0.573 | 0.397 | 0.373 | 0.403 | 0.487 | **0.608** |
| Answer a current question with sources | medium | F1 | **0.745** | 0.520 | 0.412 | 0.538 | 0.601 | 0.730 |
| Adverse media investigation | high | F1 | 0.502 | **0.565** | 0.424 | 0.286 | 0.493 | 0.514 |
| Investment thesis testing | high | F1 | 0.479 | 0.466 | 0.356 | 0.358 | 0.423 | **0.706** |

### Across both suites (50 tasks)

The finance tasks plus the 10 GTM tasks:

| Provider | Combined | Accuracy | Coverage | Critical key items correct | Wins | Avg cost / run | Median seconds |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Nimble | 0.574 | 0.928 | 0.474 | 0.923 | 17/50 | $0.38 | 220 |
| Claude Opus 5.5 | 0.571 | 0.944 | 0.455 | 0.967 | 23/50 | $0.69 | 174 |
| GPT-6.1 Sol | 0.500 | 0.980 | 0.402 | 0.829 | 8/50 | $0.56 | 297 |
| Exa | 0.445 | 0.977 | 0.348 | 0.827 | 1/50 | $0.40 | 177 |
| Parallel | 0.406 | 0.981 | 0.322 | 0.771 | 1/50 | $0.45 | 460 |
| Firecrawl | 0.374 | 0.942 | 0.295 | 0.736 | 0/50 | $0.31 | 217 |

Critical key items are averaged over the 40 keyed tasks across both suites.

Judge agreement: on the random audit sample, a second model (Claude Opus)
agreed with the verdict on 175 of 200 units (88%) in this suite, and on 221
of 258 units (86%) across both suites.

No run errored or timed out; every provider completed all 40 tasks.

## Scope

This suite and the GTM suite together cover 10 of the 14 GTM and finance jobs
in our use-case catalog. The other four (account discovery, company and
contact enrichment, finding people, portfolio monitoring) were run but are
not published in v2.

## Use cases and tasks

Eight jobs, five inputs each. The full instructions, output schema, unit
definition and judge rules for each job are in
[`usecase_bench/tasks.json`](usecase_bench/tasks.json).

| Job | Tier | Inputs |
| :--- | :--- | :--- |
| Investment due diligence | high | Due diligence memo on Rippling · Canva · Deel · Databricks · Revolut |
| Credit and counterparty risk assessment | high | Credit and counterparty risk of Spirit Airlines · Carvana · Beyond Meat · Wolfspeed · AMC Entertainment |
| KYC, AML and compliance screening | high | KYC screen on Revolut (UK) · Wirecard (Germany) · Greensill Capital (UK) · Binance (France) · Adyen (Netherlands) |
| Deal sourcing and market mapping | high | Founder-owned Texas commercial HVAC service companies · founder- or family-owned US veterinary clinic groups · bootstrapped European logistics vertical SaaS · founder-owned US specialty chemical distributors · family-owned German machine-tool makers |
| Hedge-fund company research | high | NVIDIA · Tesla · Boeing · Peloton · Carvana |
| Answer a current question with sources | medium | Snowflake consumption trends on the last call · Nike guidance and stock reaction · TSMC Arizona fab timeline · latest FOMC statement changes · Salesforce Agentforce pricing changes in 2026 |
| Adverse media investigation | high | Embraer · Glencore · Adani Green Energy · Trafigura · Ericsson |
| Investment thesis testing | high | Vistra and data-center power · Micron HBM through 2027 · GLP-1 and Mondelez · Chipotle same-store sales · Rivian R2 gross profit |

## Providers and tiers

Each job runs at one tier. The four research APIs are matched on list price
per run; the two frontier models run at the same reasoning effort as the tier.

| Tier | Nimble Web Search Agent | Exa Agent | Parallel Task | Firecrawl Agent (spark-2) | GPT-6.1 Sol | Claude Opus 5.5 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| medium | medium, $0.10 | medium, $0.10 | pro, $0.10 | effort medium, maxCredits 2500 | reasoning effort medium | effort medium |
| high | high, $0.50 | high, $0.50 | ultra2x, $0.60 | effort high, maxCredits 2500 | reasoning effort high | effort high |

There is no x-high tier: due diligence runs at high.

- **GPT-6.1 Sol** (`gpt-6.1-sol`) runs through the OpenAI Responses API with
  the built-in `web_search` tool (search, open page, find in page), in
  background mode; the model loops its own tool calls inside one response.
  The task instructions are the `instructions`, followed by the output schema.
- **Claude Opus 5.5** (`claude-opus-5-5`) runs through the Claude Code CLI in
  headless mode (`claude -p`) with only the WebSearch and WebFetch tools
  enabled, the task instructions plus the output schema as the system prompt,
  and `--effort` set to the tier.

Add-ons a normal self-serve user would turn on were enabled: Exa runs with the
Exa Connect data partners listed per job in `tasks.json`
(`exa_data_sources`), and Firecrawl runs with its provider exchange enabled.

Cost basis per provider:

| Provider | Cost per run |
| :--- | :--- |
| Nimble | list price for the tier |
| Exa | Exa's billed cost for the run (`costDollars.total`), list price if none reported |
| Parallel | list price for the processor |
| Firecrawl | (run credits + paid provider credits) x $0.99 per 1,000 credits (Standard plan). Runs that used the free daily allowance report no credits; they are priced at the median billed credits for the same effort (294 high, 77 medium) over all billed Firecrawl runs of the full experiment |
| GPT-6.1 Sol | tokens x list price ($2 input, $0.10 cached input, $10 output per 1M tokens) + $10 per 1,000 web search calls. The token prices are from third-party price trackers and were not yet confirmed on openai.com |
| Claude Opus 5.5 | the API-equivalent total Claude Code reports for the run (`total_cost_usd`), including $10 per 1,000 web searches |

Seconds are wall-clock from submit to result, including queue time.

## Method

1. **Run.** Every provider gets the same instructions and the same output
   schema for a job, plus the task text. Parallel's schema is made strict
   (all properties required, no extra keys) because its API requires it; the
   fields are the same. One run per provider per task.
2. **Extract.** The judge (Claude Sonnet through the Claude Code CLI, no
   tools) turns each provider's output into units: atomic, checkable facts of
   the kind the job asks for (one credit fact, one adverse event, one KYC
   field, one candidate company). Process notes, empty fields and statements
   about what was not found are skipped.
3. **Merge.** Units from all providers for one task are merged into one
   canonical list under shuffled anonymous labels. The same company, person,
   event or value from several providers becomes one unit only when they
   state the same fact with the same values, dates and attribution. A
   provider that adds a characterization, quote or figure the others don't
   make gets its own unit, or its own verdict on its own wording. Conflicting
   values stay separate. Each label's own wording is kept (`variants`).
   Deal-sourcing candidates are canonicalised by company domain.
4. **Evidence.** Every URL any provider cited for the task is fetched once,
   trying four web data layers in turn (Nimble Extract, Exa contents,
   Firecrawl scrape, Nimble Extract with JS rendering), and saved. 2,573 of
   2,601 cited pages in this suite were fetched.
5. **Verify.** Claude Sonnet first decides each unit from the passages of the
   saved pages that best match it (no tools, 15 units per call). Units it
   cannot decide, and every deal-sourcing unit, go to a verifier agent that
   can read and search the saved pages, search the web and fetch pages (5
   units per call). Each unit gets true / false / unverifiable, whether it is
   in scope, a confidence, and tags that set its weight (materiality and
   freshness, KYC group, related entity, false hit, red flag, list
   criteria). When providers word a unit differently in substance, each
   provider's wording gets its own verdict (`label_verdicts`). The judge is
   told never to mark a unit false for duplicating another.
6. **Retry.** Every unit still unverifiable gets a second, thorough attempt
   (every cited URL read through the fetcher, then other sources). Units left
   unverifiable count as unsupported claims (half wrong).
7. **Corrections.** After an external review, two deterministic-input
   corrections run on the judged tasks:
   - *Duplicate merge.* The merge step sometimes kept the same fact as
     several units (one event described three ways). Claude Sonnet groups
     units that state the same fact (same entity, same event or figure, same
     date); each group becomes one unit asserted by the union of providers
     (`merged_from`). Each provider keeps the verdict of its own wording
     (`label_verdicts`), so a wrong date on a real event still counts as
     wrong. Units in a conflict pair are never merged. Not applied to deal
     sourcing (already one unit per company).
   - *Ownership rule (deal sourcing).* Founder-owned, family-owned and
     owner-operated companies all satisfy a founder- or family-owned
     criterion when there are no institutional investors (PE, VC, a
     corporate parent or a public listing). A criterion fails only on
     evidence of such investors and is unknown when there is no ownership
     evidence. Claude Sonnet re-decided only the ownership criteria under
     this rule, from the recorded evidence notes (`result_original` keeps the
     first result).
8. **Score.** The truth set for a task is the pool of units verified true and
   in scope, from any provider.
   - **Coverage** = weighted share of the truth set the provider returned.
     Materiality is judged for the person deciding today: critical 6, useful
     2, minor 0.5, x1.5 when fresh (last 90 days or the current state). No
     cap.
   - **Accuracy** = weighted true / (true + wrong + off scope + 0.5 x
     unverifiable) among the provider's own units.
   - **Combined**: decision jobs, where an error is costly (due diligence,
     credit risk, KYC, hedge-fund research, grounded answers, adverse media,
     thesis testing), use F1 = `2 * acc * cov / (acc + cov)`. Deal sourcing,
     where reviewing an extra candidate is cheap, uses F2 =
     `5 * acc * cov / (4 * acc + cov)`, coverage weighted twice.
9. **Answer keys.** For the seven decision jobs (35 tasks), a separate Claude
   run that never saw any provider output researched the input and wrote the
   5 to 8 questions any good answer must cover, with the answer, a source and
   whether it is critical (252 key items in this suite). Every run is graded
   on each question (correct, wrong or missing) in one call per task, with
   providers under anonymous labels. The task score is
   `combined x (0.5 + 0.5 x share of critical key items graded correct)`,
   so missing or getting wrong a decisive fact limits the score however many
   other facts are right.
10. **Stale-key audit.** Keys are written before the answers and some go
    stale. Every key item at least one provider was graded wrong on was
    re-checked from primary sources by Claude Sonnet 5.5 (effort high, web
    search and page fetch), a model that is not a contestant, with the
    conflicting answers shown under anonymous labels and no provider names.
    It decides key_wrong (the key answer is replaced with a corrected answer
    from pages it opened), both_ok (the key is widened to accept either) or
    key_right / unclear (no change). In this suite 14 items were audited: 5
    corrected, 5 widened, 4 kept. Every provider on a changed task was
    re-graded. The original answer is kept under `key_original`.
11. **Audit.** A seeded random ~10% of units is re-checked independently by
    Claude Opus with web search and page fetch. Its verdicts are stored as
    `audit` and only used to report agreement; they do not change scores.

A run that failed or returned nothing scores 0 on its task (none did in v2).
The win on a task goes to the highest combined score; an exact tie splits it.

Scoring unit per job:

| Job | Scoring unit |
| :--- | :--- |
| Investment due diligence | Unit = material finding, weighted for an investment committee deciding today: critical 6, useful 2, minor 0.5; x1.5 when fresh. Red-flag recall reported separately. F1. |
| Credit and counterparty risk assessment | Unit = credit fact, weighted for a lender deciding today: critical 6 (current defaults, ratings, liquidity, leverage, active proceedings), useful 2, minor 0.5; x1.5 when fresh. F1. |
| KYC, AML and compliance screening | Unit = KYC fact, by group: registration 40% (7 fields), officers and owners 20% (capped at 5), screening findings 40% (critical 6, useful 2, minor 0.5). A false hit (wrong entity, or an event that did not happen) weighs 3x in accuracy; a wrong date or figure on a real finding counts 1x. A fact about another group entity counts as a half error; a screening finding about an affiliate counts at half weight. F1. |
| Deal sourcing and market mapping | Unit = company. Each thesis criterion is checked pass / fail / unknown. Qualifies if no criterion fails and the sector criterion passes; an unconfirmed company counts half wrong in accuracy, and a company that passes everything except an unproven ownership criterion counts half in coverage and accuracy. F2. |
| Hedge-fund company research | Unit = research finding (catalyst, result vs guidance, event-driven item, credit or alternative signal, insider move, risk), weighted for a fund analyst deciding today: critical 6, useful 2, minor 0.5; x1.5 when fresh. F1. |
| Answer a current question with sources | Unit = fact the answer states or rests on, weighted by whether it is needed to answer correctly: critical 6, useful 2, minor 0.5; x1.5 when fresh. F1. |
| Adverse media investigation | Unit = adverse event (one allegation, charge, settlement or conviction in one jurisdiction) or excluded namesake; a wrong legal status or a namesake event is false. Weighted critical 6, useful 2, minor 0.5; x1.5 when fresh. F1. |
| Investment thesis testing | Unit = fact of evidence for or against the thesis, weighted by how much it decides whether the thesis holds: critical 6, useful 2, minor 0.5; x1.5 when fresh. F1. |

Coverage is relative to what the providers found together, so it measures who
missed what the others proved exists. It is not an exhaustive answer set.

## Judge note

The judge is Claude (Sonnet for extraction, merge, verification, grading and
corrections; Opus for the audit sample), and Claude wrote the answer keys,
while Claude Opus 5.5 is one of the contestants. The judge may prefer answers
written the way Claude writes; we have not measured this. Mitigations:
providers are shown under shuffled anonymous labels, verdicts are decided
from the saved source pages first, and the stale-key audit hides provider
names.

Nimble Extract is one of the four page fetchers the judge uses (with Exa
contents and Firecrawl scrape), and the verifier can also read a page through
`usecase_bench/fetch.py` (Nimble Extract with rendering). The fetchers are
used the same way for every provider's citations and never see which
provider cited a page.

## Limitations

- 5 inputs per job and one run per provider per task. Results vary from run
  to run; small differences between providers are within that noise.
- The jobs, inputs and instructions were written by Nimble from its use-case
  catalog.
- The Nimble arm is a rerun made after Nimble's November 2026
  extract-template release (November 9 to 10); the other providers ran once
  (November 3 to 10).
- Coverage is pooled from what the six providers found, not from an
  exhaustive answer set.
- Verdicts, answer keys and audits are model judgments. They have not been
  validated by finance practitioners.
- Instructions contain the run date and several tasks ask about recent
  events, so a later run is a different test.

## Corrections and disputes

If you think a verdict, an answer-key item or a score is wrong, open a GitHub
issue with the job id, the input number, the unit id (`cid` in
`results/verified/`) or key item id (`k1`...), and the source that shows it.
Corrections are logged below and rerun through the same code for every
provider.

## Corrections log

- **v2 corrections (Nov 11 2026, after external review):** stale answer-key
  items re-checked by a non-contestant model (in this suite 5 corrected and 5
  widened; all providers re-graded); 1,205 duplicate units merged across the
  50 tasks of both suites (4,672 → 3,467 in the 45 tasks the pass applies
  to; in this suite 3,930 → 2,984 over 35 tasks); 14 deal-sourcing ownership
  verdicts re-decided under one rule; method text aligned with the scoring
  code; per-provider unit tally added.
- **v2 (Nov 2026):** replaces v1. New judge with page fetching and a retry
  pass, answer keys and stale-key audit, two frontier-model baselines, 40
  finance tasks.

## Results files

```
results/
  raw/<job>/<i>_<provider>.json        provider output, status, seconds, cost, cost basis, run_id, usage
  verified/<job>/<i>.json              merged units, verdict per unit, audit sample, corrections_v2, per-provider scores
  answer_keys/<job>/<i>.json           key questions and answers, key_original/audit where changed, grades per provider
  key_audit/<job>/<i>.json             stale-key audit verdict per disputed key item
  summary.json                         the tables above, per-task scores and runs, judge agreement
```

Provider file names: `nimble`, `exa`, `parallel`, `firecrawl`, `gpt`, `opus`.
In `verified/` and `answer_keys/`, `labels` maps each provider to the
anonymous letter the judge saw; `asserted_by`, `variants` and
`label_verdicts` use those letters. `raw/` keeps each provider's own
citations as returned (Parallel's `basis` includes its short cited excerpts).
`verified/` does not include the per-provider extraction lists (`claims`),
the fetched page text or evidence passages, and verdict notes are cut to 300
characters.

## Licensing

Code: Apache-2.0 (see [LICENSE](LICENSE)). Our annotations (units, verdicts,
answer keys, audits and scores in `results/`): CC BY 4.0. Source URLs are
listed, but no third-party page content is redistributed; the judge's page
cache (`evidence_cache/`) is not shipped. Provider outputs in `results/raw/`
are as the providers returned them. Provider adapters call third-party APIs
governed by those providers' own terms.

## Reproduce

Requires Python 3.11+ and the [Claude Code CLI](https://docs.claude.com/en/docs/claude-code)
(`claude`, logged in) for the Claude Opus 5.5 arm and the judge.

```bash
cd Finance-Use-Case-Benchmark
uv sync            # or: pip install -e .
cp .env.example .env   # add your keys
```

Keys: `NIMBLE_API_KEY`, `EXA_API_KEY`, `PARALLEL_API_KEY`,
`FIRECRAWL_API_KEY`, `OPENAI_API_KEY`. The judge's page fetcher uses the
Nimble, Exa and Firecrawl keys.

Recompute the tables from the shipped results (offline, no keys needed):

```bash
python -m usecase_bench.summary      # writes results/summary.json, prints the tables
```

To rerun, move the shipped `results/` aside first; every step skips files that
already exist.

```bash
# 1. run providers (all, or a subset): results/raw/
python -m usecase_bench.run
python -m usecase_bench.run nimble opus

# 2. judge: extract, merge, fetch evidence, verify, retry, audit, score: results/verified/
python -m usecase_bench.judge

# 3. corrections: duplicate merge + deal-sourcing ownership rule (in place, once per task)
python -m usecase_bench.corrections

# 4. answer keys and grading: results/answer_keys/
python -m usecase_bench.answer_keys

# 5. stale-key audit, apply, re-grade
python -m usecase_bench.key_audit
python -m usecase_bench.key_audit apply
python -m usecase_bench.answer_keys

# 6. aggregate
python -m usecase_bench.summary
```

Each step takes optional job ids (for example `fin_kyc_screening`) to run a
subset. `BENCH_TODAY=YYYY-MM-DD` sets the date put into the instructions and
judge prompts. `run.py` stops submitting to a provider once its spend passes
a cap (`CAP_<PROVIDER>` in USD).

Approximate cost for all 40 tasks:

| Item | Cost |
| :--- | :--- |
| Nimble | $18.00 (list) |
| Exa | $19.05 (billed) |
| Parallel | $21.50 (list) |
| Firecrawl | $14.45 (credits at the Standard plan rate) |
| GPT-6.1 Sol | $25.77 (tokens and search calls at list price) |
| Claude Opus 5.5 | $31.76 (Claude Code API-equivalent) |
| Judge (extract, merge, verify, retry, audit) | about $112 |
| Answer keys, grading, key audit, corrections | under $20 (key-writing cost was not fully recorded) |

Results vary from run to run. The web changes, providers update their agents,
and several tasks depend on what has happened by the run date.

## License

Apache-2.0 for the code; CC BY 4.0 for our annotations (see Licensing).
