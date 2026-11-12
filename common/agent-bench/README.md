# agent-bench

The shared runner and judge for benchmarks of web research agents. A
benchmark that uses it is a folder with a `tasks.json` (the jobs and their
inputs) and a `results/` folder; agent-bench runs every provider on every
task, judges each returned fact against the pages the providers cited and the
live web, and writes the scores and tables.

How it measures, and why: [METHOD.md](METHOD.md).

Benchmarks in this repo that use it:

| Benchmark | Jobs | Tasks |
| :--- | :--- | :--- |
| [Finance use cases](../../Finance-Use-Case-Benchmark) | 8 finance research jobs | 40 |
| [GTM use cases](../../GTM-Use-Case-Benchmark) | meeting prep, signal tracking | 10 |
| [Due diligence questions](../../Due-Diligence-Benchmark) | one question per task | 50 of 300 |
| [Lead enrichment questions](../../Lead-Enrichment-Benchmark) | one question per task | 50 of 235 |

Using it is optional: a benchmark can bring its own code instead (see
[DRACO](../../DRACO-research-benchmark) and
[SimpleQA](../../SimpleQA-Benchmark)).

## Setup

Requires Python 3.11+ and the [Claude Code CLI](https://docs.claude.com/en/docs/claude-code)
(`claude`, logged in) for the Claude Opus 5.5 arm and the judge.

```bash
cd common/agent-bench
uv sync                 # or: pip install -e .
cp .env.example .env    # add your keys
```

Keys: `NIMBLE_API_KEY`, `EXA_API_KEY`, `PARALLEL_API_KEY`,
`FIRECRAWL_API_KEY`, `OPENAI_API_KEY` (only for the providers you run). The
judge's page fetcher uses the Nimble, Exa and Firecrawl keys. Optional
settings are listed in [`.env.example`](.env.example).

## Commands

Run from `common/agent-bench`. Every command takes the benchmark as
`--suite <folder>`: a path, or a folder name at the repo root.

Recompute a benchmark's tables from its shipped results (offline, no keys
needed):

```bash
uv run python -m agent_bench.summary --suite Lead-Enrichment-Benchmark
```

The finance and GTM suites are published together, with an "across both
suites" table; give both, the one to write first:

```bash
uv run python -m agent_bench.summary --suite Finance-Use-Case-Benchmark --suite GTM-Use-Case-Benchmark
```

Run the test (recomputes every benchmark's summary in a temp copy and
compares it with the shipped `results/summary.json`):

```bash
uv run pytest
```

To rerun a benchmark, move its shipped `results/` aside first; every step
skips files that already exist.

```bash
S=Finance-Use-Case-Benchmark

# 1. run providers (all, or a subset): results/raw/
uv run python -m agent_bench.run --suite $S
uv run python -m agent_bench.run --suite $S nimble opus

# 2. judge: extract, merge, fetch evidence, verify, retry, audit, score: results/verified/
uv run python -m agent_bench.judge --suite $S

# 3. corrections: duplicate merge + deal-sourcing ownership rule (in place, once per task)
uv run python -m agent_bench.corrections --suite $S

# 4. answer keys and grading, for jobs with answer keys: results/answer_keys/
uv run python -m agent_bench.answer_keys --suite $S

# 5. stale-key audit, apply, re-grade
uv run python -m agent_bench.key_audit --suite $S
uv run python -m agent_bench.key_audit --suite $S apply
uv run python -m agent_bench.answer_keys --suite $S

# 6. aggregate: results/summary.json, prints the tables
uv run python -m agent_bench.summary --suite $S
```

Each step takes optional job ids (for example `fin_kyc_screening`) to run a
subset. `BENCH_TODAY=YYYY-MM-DD` sets the date put into the instructions and
judge prompts. `run` stops submitting to a provider once its spend passes a
cap (`CAP_<PROVIDER>` in USD). The judge's page cache is
`common/agent-bench/evidence_cache/`, shared by all benchmarks and not
shipped.

## Results files

```
<benchmark>/results/
  raw/<job>/<i>_<provider>.json        provider output, status, seconds, cost, cost basis, run_id, usage
  verified/<job>/<i>.json              merged units, verdict per unit, audit sample, corrections_v2, per-provider scores
  answer_keys/<job>/<i>.json           key questions and answers, key_original/audit where changed, grades per provider
  key_audit/<job>/<i>.json             stale-key audit verdict per disputed key item
  summary.json                         the README tables, per-task scores and runs, judge agreement
```

Provider file names: `nimble`, `exa`, `parallel`, `firecrawl`, `gpt`, `opus`.
In `verified/` and `answer_keys/`, `labels` maps each provider to the
anonymous letter the judge saw; `asserted_by`, `variants` and
`label_verdicts` use those letters.

## tasks.json

A list with one entry per job. Everything the run and the judge need is in
the entry, so a new benchmark picks its scoring here without changing code.

| Field | What it is |
| :--- | :--- |
| `id`, `name` | job id (results folders use it) and display name |
| `vertical` | `finance` or `gtm` for the use-case suites; otherwise the benchmark's folder name |
| `tier` | `medium` or `high`: the effort or processor each provider runs at |
| `instructions`, `output_schema` | what every provider is given, the same for all |
| `inputs` | the task texts; one task per input |
| `exa_data_sources` | Exa Connect data partners turned on for this job |
| `unit` | what one judged unit is (`name`, `definition`, `verify_rule`) |
| `judge_tag` | the tags the judge adds to each unit (`spec`) and the one that sets its weight (`key`: `materiality`, `signal`, `group` or `criteria`) |
| `metric` | `F1` (decision jobs) or `F2` (list and event jobs) |
| `coverage` | `all` (every true unit counts) or `answers` (only units tagged as answering the question; see [METHOD.md](METHOD.md#answer-only-coverage-coverage-answers)) |
| `answer_key` | `true` to write and grade fixed answer keys for this job |
| `scoring_rule` | the scoring rule in words, for readers (the code reads `metric`, `judge_tag.key` and `coverage`) |

### A new question set

A folder with `data/questions.csv` (`question`, `category`) can be turned
into a one-job benchmark: `question_tasks` samples N questions in proportion
to each category (fixed seed) and writes `<folder>/tasks.json`, built from the
finance suite's "Answer a current question with sources" job with
`"coverage": "answers"` and no answer key.

```bash
uv run python -m agent_bench.question_tasks Due-Diligence-Benchmark dd_question "Due diligence questions" 50
```

Then run the steps above with `--suite Due-Diligence-Benchmark`, and add the
benchmark to the test's `SUITES` in [`tests/test_summary.py`](tests/test_summary.py).

## Licensing

Code: Apache-2.0. Provider adapters call third-party APIs governed by those
providers' own terms. No third-party page content is redistributed: the
judge's page cache is not shipped.
