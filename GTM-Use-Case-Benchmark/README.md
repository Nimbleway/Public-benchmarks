# usecase-bench: GTM (v2)

This benchmark measures how well web research agents do real GTM research
jobs: brief a seller before a meeting, and track account signals. Every
provider gets the same instructions and the same output schema. Every fact a
provider returns is checked against the pages the providers cited and the live
web, and each provider is scored on how many of the verified facts it found
(coverage) and how many of its own facts were right (accuracy). Meeting prep
is also graded against fixed answer keys written before any answer was seen.

It is the GTM half of a GTM and finance suite; the finance half is in
[`Finance-Use-Case-Benchmark/`](../Finance-Use-Case-Benchmark). Both were run
together with the same harness, judge and method.

Runs: November 3 to 10, 2026. Judged: November 10, 2026, every run of a task
side by side. Corrections: November 11, 2026 (see
[Corrections log](#corrections-log)).

**v2 replaces v1 (October 2026).** v1 had 6 GTM tasks, four providers and an
older judge that verified units with its own web browsing only. v2 has 10 GTM
tasks (2 jobs x 5 inputs), six providers including two frontier models with
their own web tools (GPT-6.1 Sol and Claude Opus 5.5), a judge that fetches
every cited page and decides from the saved sources, answer keys written
before seeing any answer, a stale-key audit, and a scoring-correction pass
after an external review. The v1 results are in the git history.

## Results

Higher is better for combined, accuracy, coverage and critical key items.
Wins = tasks where the provider had the highest combined score; an exact tie
splits the win.

### GTM (10 tasks)

| Provider | Combined | Accuracy | Coverage | Critical key items correct | Wins | Avg cost / run | Median seconds |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Claude Opus 5.5 | 0.520 | 0.992 | 0.419 | 0.950 | 4/10 | $0.28 | 80 |
| Nimble | 0.497 | 0.960 | 0.427 | 0.750 | 3/10 | $0.10 | 134 |
| GPT-6.1 Sol | 0.448 | 0.999 | 0.375 | 0.700 | 3/10 | $0.21 | 60 |
| Exa | 0.405 | 0.979 | 0.345 | 0.610 | 0/10 | $0.10 | 99 |
| Parallel | 0.357 | 0.981 | 0.314 | 0.430 | 0/10 | $0.10 | 230 |
| Firecrawl | 0.316 | 0.932 | 0.279 | 0.470 | 0/10 | $0.10 | 112 |

Critical key items correct is the share of critical answer-key questions the
provider got right, averaged over the 5 keyed tasks (meeting prep; signal
tracking has no answer key).

Units per provider, as judged. True = true and in scope; wrong = contradicted
by a source; off scope = true but outside the request; unverifiable = still
unsettled after two attempts. The four add up to the provider's units.

| Provider | Units | True | Wrong | Off scope | Unverifiable |
| :--- | :---: | :---: | :---: | :---: | :---: |
| Claude Opus 5.5 | 150 | 148 | 1 | 1 | 0 |
| Nimble | 162 | 157 | 5 | 0 | 0 |
| GPT-6.1 Sol | 145 | 144 | 1 | 0 | 0 |
| Exa | 130 | 129 | 1 | 0 | 0 |
| Parallel | 124 | 121 | 3 | 0 | 0 |
| Firecrawl | 133 | 127 | 5 | 1 | 0 |

### Combined score by job (mean of 5 tasks)

| Job | Tier | Metric | Nimble | Exa | Parallel | Firecrawl | GPT-6.1 Sol | Claude Opus 5.5 |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| Meeting prep and account research | medium | F1 | 0.430 | 0.346 | 0.226 | 0.294 | 0.385 | **0.496** |
| Track signals that create sales opportunities | medium | F2 | **0.564** | 0.465 | 0.488 | 0.337 | 0.511 | 0.543 |

### Across both suites (50 tasks)

The GTM tasks plus the 40 finance tasks:

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
agreed with the verdict on 46 of 58 units (79%) in this suite, and on 221 of
258 units (86%) across both suites.

No run errored or timed out; every provider completed all 10 tasks.

## Scope

This suite and the finance suite together cover 10 of the 14 GTM and finance
jobs in our use-case catalog. The other four (account discovery, company and
contact enrichment, finding people, portfolio monitoring) were run but are
not published in v2.

## Use cases and tasks

Two jobs, five inputs each. The full instructions, output schema, unit
definition and judge rules for each job are in
[`usecase_bench/tasks.json`](usecase_bench/tasks.json).

| Job | Tier | Inputs |
| :--- | :--- | :--- |
| Meeting prep and account research | medium | Prepare me for a meeting with Attio · Datadog · Gong · Vercel · Monday.com |
| Track signals that create sales opportunities | medium | Check Mercury · Clay · Rippling · Anthropic · Klarna for changes since 2026-09-21 |

## Providers and tiers

Each job runs at one tier. The four research APIs are matched on list price
per run; the two frontier models run at the same reasoning effort as the tier.

| Tier | Nimble Web Search Agent | Exa Agent | Parallel Task | Firecrawl Agent (spark-2) | GPT-6.1 Sol | Claude Opus 5.5 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| medium | medium, $0.10 | medium, $0.10 | pro, $0.10 | effort medium, maxCredits 2500 | reasoning effort medium | effort medium |
| high | high, $0.50 | high, $0.50 | ultra2x, $0.60 | effort high, maxCredits 2500 | reasoning effort high | effort high |

Both GTM jobs run at medium.

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
   the kind the job asks for (one finding a seller would use, one dated
   event). Process notes, empty fields and statements about what was not
   found are skipped.
3. **Merge.** Units from all providers for one task are merged into one
   canonical list under shuffled anonymous labels. The same company, person,
   event or value from several providers becomes one unit only when they
   state the same fact with the same values, dates and attribution. A
   provider that adds a characterization, quote or figure the others don't
   make gets its own unit, or its own verdict on its own wording. Conflicting
   values stay separate. Each label's own wording is kept (`variants`).
4. **Evidence.** Every URL any provider cited for the task is fetched once,
   trying four web data layers in turn (Nimble Extract, Exa contents,
   Firecrawl scrape, Nimble Extract with JS rendering), and saved. 443 of 447
   cited pages in this suite were fetched.
5. **Verify.** Claude Sonnet first decides each unit from the passages of the
   saved pages that best match it (no tools, 15 units per call). Units it
   cannot decide go to a verifier agent that can read and search the saved
   pages, search the web and fetch pages (5 units per call). Each unit gets
   true / false / unverifiable, whether it is in scope, a confidence, and a
   tag that sets its weight (materiality and freshness for meeting prep,
   buying or minor signal for signal tracking). When providers word a unit
   differently in substance, each provider's wording gets its own verdict
   (`label_verdicts`). The judge is told never to mark a unit false for
   duplicating another.
6. **Retry.** Every unit still unverifiable gets a second, thorough attempt
   (every cited URL read through the fetcher, then other sources). Units left
   unverifiable count as unsupported claims (half wrong).
7. **Duplicate merge (correction).** After an external review: the merge step
   sometimes kept the same fact as several units (for example one Mercury
   product launch described three ways). Claude Sonnet groups units that
   state the same fact (same entity, same event or figure, same date); each
   group becomes one unit asserted by the union of providers
   (`merged_from`). Each provider keeps the verdict of its own wording
   (`label_verdicts`), so a wrong date on a real event still counts as
   wrong. Units in a conflict pair are never merged. (The finance suite also
   applies one ownership rule to deal sourcing; it does not affect GTM.)
8. **Score.** The truth set for a task is the pool of units verified true and
   in scope, from any provider.
   - **Coverage** = weighted share of the truth set the provider returned. No
     cap.
   - **Accuracy** = weighted true / (true + wrong + off scope + 0.5 x
     unverifiable) among the provider's own units.
   - **Combined**: meeting prep is a decision job, where an error is costly,
     and uses F1 = `2 * acc * cov / (acc + cov)`. Signal tracking, where
     reviewing an extra event is cheap, uses F2 =
     `5 * acc * cov / (4 * acc + cov)`, coverage weighted twice.
9. **Answer keys.** For meeting prep (5 tasks), a separate Claude run that
   never saw any provider output researched the input and wrote the 5 to 8
   questions any good answer must cover, with the answer, a source and
   whether it is critical (35 key items). Every run is graded on each
   question (correct, wrong or missing) in one call per task, with providers
   under anonymous labels. The task score is
   `combined x (0.5 + 0.5 x share of critical key items graded correct)`.
10. **Stale-key audit.** Every key item at least one provider was graded wrong
    on is re-checked from primary sources by Claude Sonnet 5.5 (effort high),
    a model that is not a contestant, with provider names hidden. No GTM key
    item was graded wrong for any provider, so no GTM key changed (14 finance
    items were audited; see the finance README).
11. **Audit.** A seeded random ~10% of units is re-checked independently by
    Claude Opus with web search and page fetch. Its verdicts are stored as
    `audit` and only used to report agreement; they do not change scores.

A run that failed or returned nothing scores 0 on its task (none did in v2).
The win on a task goes to the highest combined score; an exact tie splits it.

Scoring unit per job:

| Job | Scoring unit |
| :--- | :--- |
| Meeting prep and account research | Unit = distinct finding, weighted by how much it matters to the seller today: critical 6, useful 2, minor 0.5; x1.5 when fresh (last 90 days or current state). F1. |
| Track signals that create sales opportunities | Unit = event after the since-date. Buying signals (funding, leadership, expansion, hiring push, major launch, M&A, pricing) weigh 3; minor releases weigh 0.5. F2. |

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
  validated by sales practitioners.
- Instructions contain the run date and signal tracking asks about events
  after a fixed date, so a later run is a different test.

## Corrections and disputes

If you think a verdict, an answer-key item or a score is wrong, open a GitHub
issue with the job id, the input number, the unit id (`cid` in
`results/verified/`) or key item id (`k1`...), and the source that shows it.
Corrections are logged below and rerun through the same code for every
provider.

## Corrections log

- **v2 corrections (Nov 11 2026, after external review):** stale answer-key
  items re-checked by a non-contestant model (no GTM key changed; all
  providers re-graded where a key changed); 1,205 duplicate units merged
  across the 50 tasks of both suites (4,672 → 3,467 in the 45 tasks the pass
  applies to; in this suite 742 → 483 over 10 tasks); 14 deal-sourcing
  ownership verdicts re-decided under one rule (finance suite); method text
  aligned with the scoring code; per-provider unit tally added.
- **v2 (Nov 2026):** replaces v1. New judge with page fetching and a retry
  pass, answer keys and stale-key audit, two frontier-model baselines, 10 GTM
  tasks.

## Results files

```
results/
  raw/<job>/<i>_<provider>.json        provider output, status, seconds, cost, cost basis, run_id, usage
  verified/<job>/<i>.json              merged units, verdict per unit, audit sample, corrections_v2, per-provider scores
  answer_keys/<job>/<i>.json           key questions and answers, key_original/audit where changed, grades per provider
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
cd GTM-Use-Case-Benchmark
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

# 3. corrections: duplicate merge (in place, once per task)
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

Each step takes optional job ids (for example `gtm_signal_tracking`) to run a
subset. `BENCH_TODAY=YYYY-MM-DD` sets the date put into the instructions and
judge prompts. `run.py` stops submitting to a provider once its spend passes
a cap (`CAP_<PROVIDER>` in USD).

Approximate cost for all 10 tasks:

| Item | Cost |
| :--- | :--- |
| Nimble | $1.00 (list) |
| Exa | $1.00 (billed) |
| Parallel | $1.00 (list) |
| Firecrawl | $0.96 (credits at the Standard plan rate) |
| GPT-6.1 Sol | $2.08 (tokens and search calls at list price) |
| Claude Opus 5.5 | $2.78 (Claude Code API-equivalent) |
| Judge (extract, merge, verify, retry, audit) | about $17 |
| Answer keys, grading, corrections | under $5 |

Results vary from run to run. The web changes, providers update their agents,
and signal tracking depends on what has happened by the run date.

## License

Apache-2.0 for the code; CC BY 4.0 for our annotations (see Licensing).
