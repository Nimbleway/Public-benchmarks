# usecase-bench: GTM and finance

This benchmark measures how well web research agents do real GTM and finance
research jobs: write a due diligence memo, assess credit risk, run a KYC screen,
watch a company for material events, brief a seller before a meeting, and track
account signals. Every provider gets the same instructions and the same output
schema. Every fact a provider returns is checked against the live web, and each
provider is scored on how many of the verified facts it found (coverage) and
how many of its own facts were right (accuracy).

Run on 2026-10-21. Judge: Claude (sonnet) through the Claude Code CLI, with a
second model (opus) re-checking a 10% sample.

## Results

Three providers ran every task: Nimble, Exa and Firecrawl. Higher is better for
combined, accuracy and coverage. Wins = tasks where the provider had the highest
combined score (ties broken by coverage).

### All 18 tasks

| Provider | Combined | Accuracy | Coverage | Wins | Avg cost / run | Median seconds |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| Nimble | 0.707 | 0.927 | 0.678 | 15/18 | $0.30 | 208 |
| Exa | 0.600 | 0.871 | 0.562 | 3/18 | $0.34 | 120 |
| Firecrawl | 0.542 | 0.909 | 0.502 | 0/18 | $0.26 | 122 |

### Finance (12 tasks)

| Provider | Combined | Accuracy | Coverage | Wins | Avg cost / run | Median seconds |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| Nimble | 0.674 | 0.916 | 0.645 | 12/12 | $0.40 | 276 |
| Exa | 0.523 | 0.856 | 0.480 | 0/12 | $0.47 | 171 |
| Firecrawl | 0.512 | 0.898 | 0.473 | 0/12 | $0.33 | 206 |

### GTM (6 tasks)

| Provider | Combined | Accuracy | Coverage | Wins | Avg cost / run | Median seconds |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| Nimble | 0.774 | 0.948 | 0.744 | 3/6 | $0.10 | 176 |
| Exa | 0.753 | 0.900 | 0.727 | 3/6 | $0.10 | 62 |
| Firecrawl | 0.601 | 0.927 | 0.559 | 0/6 | $0.10 | 74 |

### Combined score by use case (mean of 3 tasks)

| Use case | Vertical | Tier | Nimble | Exa | Firecrawl |
| :--- | :--- | :--- | :---: | :---: | :---: |
| Credit risk | finance | high | 0.701 | 0.474 | 0.420 |
| Due diligence | finance | high | 0.744 | 0.561 | 0.561 |
| KYC screening | finance | high | 0.852 | 0.694 | 0.688 |
| Portfolio monitoring | finance | medium | 0.398 | 0.363 | 0.379 |
| Account brief (meeting prep) | gtm | medium | 0.780 | 0.626 | 0.652 |
| Account signals | gtm | medium | 0.767 | 0.881 | 0.550 |

Cost is the list price per run for Nimble and Parallel, Exa's billed cost
(one KYC run billed $1.30 with its data partners), and for Firecrawl the run
credits plus paid provider credits times the Standard plan rate ($0.99 per
1,000 credits). Seconds are wall-clock from submit to result, including queue
time.

Judge agreement: on the 10% audit sample, the second model agreed with the
verdict on 78 of 83 units (94%).

### Parallel (partial)

Parallel was run on all 18 tasks, but the account ran out of credit and 8 of
the 18 requests were rejected (credit risk on Carvana and Beyond Meat, all three due
diligence tasks and all three KYC tasks). Its partial results are listed separately and are not in the
headline. The table covers the 10 tasks where all four providers were scored.

| Provider | Combined | Accuracy | Coverage | Wins | Avg cost / run | Median seconds |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| Nimble | 0.648 | 0.930 | 0.617 | 4/10 | $0.14 | 184 |
| Parallel | 0.611 | 0.970 | 0.574 | 3/10 | $0.15 | 192 |
| Exa | 0.590 | 0.812 | 0.560 | 3/10 | $0.14 | 57 |
| Firecrawl | 0.518 | 0.880 | 0.483 | 0/10 | $0.12 | 74 |

## Scope

This suite covers 6 of the 10 GTM and finance use cases we tested. The other
four (account discovery, account enrichment, decision-maker identification,
deal sourcing) are not included.

## Use cases and tasks

Six use cases, three tasks each. The full instructions and output schema for
each use case are in [`usecase_bench/tasks.json`](usecase_bench/tasks.json).

**Finance**

| Use case | Tier | Tasks |
| :--- | :--- | :--- |
| Credit risk | high | Assess the credit and counterparty risk of Spirit Airlines · Assess the credit and counterparty risk of Carvana · Assess the credit and counterparty risk of Beyond Meat |
| Due diligence | high | Write a due diligence memo on Rippling · Write a due diligence memo on Canva · Write a due diligence memo on Deel |
| KYC screening | high | Run a KYC screen on Revolut in the United Kingdom · Run a KYC screen on Wirecard in Germany · Run a KYC screen on Greensill Capital in the United Kingdom |
| Portfolio monitoring | medium | Check Intel for material events since 2026-09-21 · Check Peloton for material events since 2026-09-21 · Check Boeing for material events since 2026-09-21 |

**GTM**

| Use case | Tier | Tasks |
| :--- | :--- | :--- |
| Account brief (meeting prep) | medium | Prepare me for a meeting with Attio (attio.com) · Prepare me for a meeting with Datadog (datadoghq.com) · Prepare me for a meeting with Gong (gong.io) |
| Account signals | medium | Check Mercury (mercury.com) for changes since 2026-09-21 · Check Clay (clay.com) for changes since 2026-09-21 · Check Rippling (rippling.com) for changes since 2026-09-21 |

## Providers and price-matched tiers

Each use case runs at one tier. Tiers are matched on list price per run.

| Tier | Nimble | Exa Agent | Parallel Task | Firecrawl Agent (spark-2, dynamic credits) |
| :--- | :--- | :--- | :--- | :--- |
| medium | medium, $0.10 | medium, $0.10 | pro, $0.10 | effort medium, maxCredits 2500 (default) |
| high | high, $0.50 | high, $0.50 | ultra2x, $0.60 | effort high, maxCredits 2500 (default) |

There is no x-high tier: due diligence runs at high.

Add-ons a normal self-serve user would turn on were enabled: Exa runs with the
Exa Connect data partners listed per use case in `tasks.json`
(`exa_data_sources`), and Firecrawl runs with its provider exchange enabled.

## Method

1. **Run.** Every provider gets the same instructions and the same output
   schema for a use case, plus the task text. Parallel's schema is made strict
   (all properties required, no extra keys) because its API requires it; the
   fields are the same.
2. **Extract.** The judge turns each provider's output into units: atomic,
   checkable facts of the kind the use case asks for (for example one credit
   fact, one dated event, one KYC field). Process notes, empty fields and
   statements about what was not found are skipped.
3. **Merge.** Units from all providers for one task are merged into one
   canonical list, recording which provider asserted each unit. Conflicting
   values (two different dates for one event) stay separate. Providers are
   shown to the judge under shuffled anonymous labels.
4. **Verify.** Each canonical unit is checked once against the web by an LLM
   judge (Claude, via the Claude Code CLI, with WebSearch and WebFetch). It
   opens the cited URL first, then searches. Each unit gets true / false /
   unverifiable, whether it is in scope, and a tag that sets its weight
   (materiality, signal type, KYC group). Units are checked in batches of 5,
   with the same rules for every provider.
5. **Score.** The truth set for a task is the pool of units verified true and
   in scope, from any provider.
   - **Coverage** = the share of the pooled verified-true, in-scope units that
     the provider returned (weighted as below).
   - **Accuracy** = true / (true + false) among the provider's own units.
     Out-of-scope units count as false. Unverifiable units are left out.
   - **Combined** = F2-style score that weights coverage twice as much as
     accuracy: `5 * acc * cov / (4 * acc + cov)`.

Coverage is relative to what any provider found, so it measures who missed
what the others proved exists.

Scoring unit per use case:

| Use case | Scoring unit |
| :--- | :--- |
| Credit risk | Unit = credit fact, weighted for a lender: critical 3 (defaults, ratings, liquidity, leverage), useful 2, minor 1. |
| Due diligence | Unit = material finding, weighted for an investment committee: critical 3, useful 2, minor 1. Red-flag recall reported separately. |
| KYC screening | Unit = KYC fact, by group: registration 40% (7 fields), officers and owners 20% (capped at 5), screening findings 40%. A false sanctions or adverse-media hit weighs 3x in accuracy. |
| Portfolio monitoring | Unit = event after the since-date. Only material events count; a non-material event counts against accuracy. |
| Account brief (meeting prep) | Unit = distinct finding, weighted by usefulness to the seller: critical 3, useful 2, minor 1. |
| Account signals | Unit = event after the since-date. Buying signals (funding, leadership, expansion, hiring push, major launch, M&A, pricing) weigh 2; minor releases weigh 1. |

A second model (Claude opus) independently re-checks about 10% of units. Its
verdicts are stored as `audit` and only used to report judge agreement; they
do not change scores.

A run that failed or returned nothing is scored as empty (zero coverage), not
dropped.

## Judge note

The judge reads pages with its own WebFetch. Only when WebFetch fails, is
blocked, or the page is a PDF or a long filing, it reads the page through
`usecase_bench/fetch.py`, which uses Nimble Extract. This fetcher is used for
reading pages to verify units from every provider. It does not see which
provider asserted a unit.

## Results files

```
results/
  raw/<use_case>/<i>_<provider>.json    provider output, status, seconds, cost, run_id
  claims/<use_case>/<i>.json            extracted units per provider + merged canonical units
  verified/<use_case>/<i>.json          verdict per unit, audit sample, per-provider scores
  summary.json                          the tables above, per-task scores, judge agreement
```

In `claims/` and `verified/`, `labels` maps each provider to the anonymous
letter the judge saw, and `asserted_by` uses those letters.

## Reproduce

Requires Python 3.11+ and the [Claude Code CLI](https://docs.claude.com/en/docs/claude-code)
(`claude`, logged in) for the judge.

```bash
cd GTM-Finance-Use-Case-Benchmark
uv sync            # or: pip install -e .
cp .env.example .env   # add your keys
```

Keys: `NIMBLE_API_KEY`, `EXA_API_KEY`, `PARALLEL_API_KEY`, `FIRECRAWL_API_KEY`.
`NIMBLE_API_KEY` is also used by the judge's fallback fetcher.

To start fresh, move the shipped `results/` aside first; every step skips
files that already exist.

```bash
# 1. run providers (all, or a subset): results/raw/
python -m usecase_bench.run
python -m usecase_bench.run nimble exa firecrawl

# 2. judge, extract + merge units: results/claims/
python -m usecase_bench.judge_extract

# 3. judge, verify units on the web and score: results/verified/
python -m usecase_bench.judge_verify
python -m usecase_bench.judge_verify --fix   # re-verify units a failed batch left unchecked

# 4. aggregate: results/summary.json and the markdown tables
python -m usecase_bench.score
```

Each step takes optional use case ids (for example `fin_kyc_screening`) to run
a subset. `score.py` also re-computes every task's scores from the stored
verdicts and reports any mismatch.

Approximate cost for all 18 tasks:

| Item | Cost |
| :--- | :--- |
| Nimble | $5.40 (list) |
| Exa | about $6.20 (billed) |
| Firecrawl | about $4.60 (credits at the Standard plan rate) |
| Parallel | about $6.30 (list) |
| Judge, extract + merge | under $10 |
| Judge, verify + audit | about $17 |

`run.py` stops submitting to a provider once its spend passes a cap
(`CAP_USD`: $20 each, $60 for Firecrawl).

Results vary from run to run. The web changes, providers update their agents,
and the "since 2026-09-21" tasks depend on what has happened by the run date.
Instructions use today's date, so a later run is a different test. The
numbers above are from October 2026.

## License

Apache-2.0. Provider adapters call third-party APIs governed by those
providers' own terms.
