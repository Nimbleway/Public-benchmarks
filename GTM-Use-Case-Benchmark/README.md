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
together with the same harness, judge and method
([agent-bench](../common/agent-bench)).

Runs: November 3 to 10, 2026 (per provider, from each result's `completed_on`:
Nimble Nov 9, Exa Nov 3–9, Parallel Nov 4–9, Firecrawl Nov 3–9, GPT-6.1 Sol Nov 10, Claude Opus 5.5 Nov 10). Judged: November 10, 2026, every run of a task
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
[`tasks.json`](tasks.json).

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

## How we measured

This suite runs on [agent-bench](../common/agent-bench), the runner and judge
shared with the finance, due diligence and lead enrichment benchmarks. The full method (run, extract,
merge, evidence, verify, retry, corrections, scoring, answer keys, stale-key
audit and the audit sample) is in
[METHOD.md](../common/agent-bench/METHOD.md). What is specific to this suite:

- **Evidence.** 443 of 447 cited pages in this suite were fetched.
- **Corrections.** The duplicate merge applies (for example one Mercury
  product launch had been kept as three units); the ownership rule is for
  finance deal sourcing only.
- **Combined.** Meeting prep, a decision job, uses F1; signal tracking, where
  reviewing an extra event is cheap, uses F2.
- **Answer keys.** Meeting prep (5 tasks) is also graded against answer keys:
  35 key items.
- **Stale-key audit.** No GTM key item was graded wrong for any provider, so
  no GTM key changed (14 finance items were audited; see the finance README).
- **Failed runs.** None in v2.

Scoring unit per job:

| Job | Scoring unit |
| :--- | :--- |
| Meeting prep and account research | Unit = distinct finding, weighted by how much it matters to the seller today: critical 6, useful 2, minor 0.5; x1.5 when fresh (last 90 days or current state). F1. |
| Track signals that create sales opportunities | Unit = event after the since-date. Buying signals (funding, leadership, expansion, hiring push, major launch, M&A, pricing) weigh 3; minor releases weigh 0.5. F2. |

Coverage is relative to what the providers found together, so it measures who
missed what the others proved exists. It is not an exhaustive answer set.

## Judge note

The judge is Claude (`claude-sonnet-5-5` for extraction, merge, verification,
grading and corrections; `claude-opus-5-5` for the audit sample; the code pins
these IDs, overridable with `JUDGE_MODEL` and `AUDIT_MODEL`; the stale-key
audit uses `claude-sonnet-5-5`, overridable with `KEY_AUDIT_MODEL`), and Claude wrote the answer keys,
while Claude Opus 5.5 is one of the contestants. The judge may prefer answers
written the way Claude writes; we have not measured this. Mitigations:
providers are shown under shuffled anonymous labels, verdicts are decided
from the saved source pages first, and the stale-key audit hides provider
names.

Nimble Extract is one of the four page fetchers the judge uses (with Exa
contents and Firecrawl scrape), and the verifier can also read a page through
[`fetch.py`](../common/agent-bench/agent_bench/fetch.py) (Nimble Extract with rendering). The fetchers are
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
- Evidence passages and fetched page text are not shipped (copyright). Each
  verdict ships with its source URL and a note cut to 300 characters, so a
  verdict can be checked by opening the URL, not against the exact passage
  the judge read.
- Every unit in this suite has a verdict. (In the finance suite 21 units
  were never given one; see the finance README.)
- Instructions contain the run date and signal tracking asks about events
  after a fixed date, so a later run is a different test.

## Corrections and disputes

If you think a verdict, an answer-key item or a score is wrong, open a GitHub
issue with the job id, the input number, the unit id (`cid` in
`results/verified/`) or key item id (`k1`...), and the source that shows it.
Corrections are logged below and rerun through the same code for every
provider.

## Corrections log

- **Known issue (v2):** 21 finance units have no judge verdict and are
  excluded from scoring for every provider (none in this suite).
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

Each `raw/` record has `completed_on`: the date the result file was last
written (UTC); a few records were rewritten the same week when cost fields
were added.

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
cache is not shipped. Provider outputs in `results/raw/`
are as the providers returned them. Provider adapters call third-party APIs
governed by those providers' own terms.

## Reproduce

Setup, keys and every command are in
[agent-bench's README](../common/agent-bench/README.md#commands). Recompute
this suite's tables from the shipped results (offline, no keys needed), from
`common/agent-bench`:

```bash
uv run python -m agent_bench.summary --suite GTM-Use-Case-Benchmark --suite Finance-Use-Case-Benchmark
```

`uv run pytest` there checks that every benchmark's shipped
`results/summary.json` recomputes unchanged. To rerun this suite, run the
steps in that README with `--suite GTM-Use-Case-Benchmark`. Rerunning will not reproduce the
exact numbers ([why](../common/agent-bench/METHOD.md#what-reproduces-exactly)).

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
