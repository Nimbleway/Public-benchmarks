# nimble-benchmark

**Version:** 0.1.0 | **Stack:** Python 3.12, uv, pytest, ruff | **License:** Apache-2.0

## What

Accuracy, retrieval, and latency evaluation harness for web search APIs. It runs
SimpleQA-based benchmarks, grades answers, scores retrieval quality, and writes
reproducible artifacts under `runs/`.

The harness is provider-neutral. Nimble ships as one lane among many, and every
lane — including that one — goes through the same sampler interface, the same
synthesizer, the same grader, and the same metrics. A fork that wants to benchmark
its own API adds a sampler and points `--significance-baseline` at it; nothing else
needs to change.

## Quick Start

```bash
./setup.sh
make test
make eval-quick
```

## Commands

```bash
# Development
uv sync --extra dev                         # Install runtime + dev dependencies
make lint                                   # Ruff lint + format check
make format                                 # Ruff format + autofix

# Testing
make test                                   # Unit tests only
make test-integration                       # Integration tests against live provider APIs
uv run pytest                               # Defaults to non-integration tests

# Evaluation
make eval-quick                             # 5-row smoke benchmark across ALL lanes
make eval                                   # 500-row benchmark across ALL lanes (`all_apis`)
make eval-all-preflight                     # credential check for all lanes, no API spend
uv run nimble-eval --help                   # Main benchmark CLI

# Cleanup
make clean                                  # Remove runs and local caches
```

## Architecture

```text
src/nimble_benchmark/
  cli.py                 Main `nimble-eval` CLI
  config.py              Pydantic settings from env and `.env`
  constants.py           Shared enums and the accuracy-denominator definition
  models.py              Typed row/result/usage shapes passed between stages
  preflight.py           Credential + endpoint checks before any spend
  retry.py               Retry policy and transient-error classification
  sampler_config.py      Captures each lane's exact parameterization per run
  logging_setup.py       Console + rotating DEBUG file logging (`logs/`, plus `<run-dir>/eval.log`)
  runner.py              Async benchmark runner and CSV writer
  analyzer.py            Aggregates raw run results into analyzed_results.csv
  report.py              Writes run.md / run.json from the analyzed CSVs
  price_list.py          Static vendor list prices per lane ($/1k queries), and
                          the call-count multiplier behind the Cost column.
                          Hand-maintained public pricing snapshot.
  insights.py            Writes insights.md (interpretation + prev-run diff) and errors.md (triage)
  leaderboard.py         Thin orchestrator + CLI for the leaderboard pipeline
  leaderboard_data.py    Pure data prep (run dir -> typed dataclasses + JSON snapshots)
  leaderboard_render.py  Renders structured data into HTML/Markdown fragments
  leaderboard_preview.py Snapshot + watch iteration env for fast template work
  leaderboard_html.py    Optional: renders a standalone leaderboard.html via the
                          Mintlify CLI. Needs `mintlify` on PATH; skipped otherwise.
  templates/             leaderboard_local.md, leaderboard_public.mdx (string.Template $var)
  datasets/              Dataset loaders and SimpleQA support
  samplers/              Nimble + third-party `/search` clients
                          (_http_post_base.py is the shared aiohttp-POST base class;
                           firecrawl drives a vendor SDK instead. exa/parallel/tavily
                           each pin one lane per product tier)
  metrics/               URL, LLM retrieval, latency, and significance metrics
  judging/               Answer and retrieval judging helpers
  synthesis/             Eval-side answer synthesis
  prompts/               Grader and judge prompts (`simpleqa_grader.txt` is upstream-verbatim)
tests/
  unit/                  Fast offline tests
  integration/           Real API smoke tests
  fixtures/              JSON fixtures for the offline tests
```

The CLI loads settings from `.env`, builds one or more samplers, preflights the Nimble
endpoint when a Nimble lane is in the roster (see `preflight.py` — the lanes share one
base URL, so it is one healthcheck plus one probe), runs benchmark rows concurrently,
and writes per-sampler raw CSVs plus the run summary report.

### One surface, no `--answer-source` flag

Every shipped lane is a `/search` lane: `response_kind="search_results"`,
`answer_source="synth"`. None requests a server-side answer (`include_answer`
is pinned off on the Nimble and Tavily payloads); the graded answer is
synthesized eval-side from the ranked chunks by one fixed model. That is what
makes Accuracy one question asked of every vendor rather than a comparison of
nine answer products.

The answer surface is still modelled and routed on — a lane registered
`("answer_with_citations", "api")` has its citations judged as chunks and its
own answer graded directly — but no lane ships on it. There is no
`--answer-source` knob: the source follows from the lane
(`ProviderCapability.answer_source`), because a /search lane has no server-side
answer to grade and an answer lane has no ranked list to synthesize from.

**If an `api` lane is ever added, three things change together:** the Caveat
paragraph in `report.py`, the equivalent note in `leaderboard_render.py`, and
the README's Results preamble all state that accuracy is uniformly sourced.
`tests/unit/test_provider_capabilities.py::test_every_shipped_lane_is_a_search_lane`
is the tripwire.

### Leaderboard layout: one table, every lane

The analyzer writes one `analyzed_results.csv` (a row per
`(provider, response_kind, answer_source)` lane) and one `significance.csv`.
No per-kind split files: an answer lane, if registered, lands in the same table
with `response_kind` / `answer_source` telling them apart.

The leaderboard and the `run.md` Headline section render one table, **Search
APIs**, deliberately narrow — **Provider, Accuracy, Latency, Cost, n** — and
**ranked on Accuracy**. It is a summary, not the dataset: `response_kind`,
`answer_source`, `ndcg@10`, `recall@10 LLM` and `fail rate` all stay in
`analyzed_results.csv` and `run.json`.

Four things worth knowing before quoting a number:

- **Accuracy counts a non-answer as a miss.** The denominator
  (`constants.ACCURACY_DENOMINATOR_RESULTS`) is every row whose request
  succeeded — `is_correct` + `is_incorrect` + `is_not_attempted`. A 200 with
  nothing usable in it (a refusal, or zero chunks to synthesize from) scores 0
  rather than leaving the denominator. Only `not_evaluated` (the request never
  landed) is excluded; `failure_rate` covers those, so an all-failed lane
  renders as absent accuracy, not 0.0. `metrics/significance.py` uses the same
  denominator, so the ★ markers test the number the table shows.
  **This is deliberately stricter than upstream simple-evals**, which reports
  not-attempted as a third bucket and divides by attempts only — so the column
  is not comparable to a published SimpleQA figure, even though
  `prompts/simpleqa_grader.txt` is upstream-verbatim. Raw CSV labels are
  unchanged, so the upstream split is recomputable.
- **Accuracy is uniformly sourced today, and the table does not say so.** Every
  lane is a `synth` row, so the column measures retrieval quality reaching one
  fixed synthesizer, not any vendor's answer product. Registering an `api` lane
  breaks that silently, because `answer_source` is not a rendered column; the
  `run.md` Caveat section is where the distinction lives.
- **Reliability is only visible via `n`,** which renders `ok/total` whenever a
  lane had failures. The exact rate is in the CSV.
- **Cost is an estimate, not a measurement, and it scales with `n`.** See below.

### Cost: static rate card × the run's call count

`price_list.py` is a hand-copied snapshot of the vendors' **publicly published
pricing**, normalized to USD per 1,000 queries at 10 results, keyed by lane.
The Cost column is that unit price times the lane's request count
(`usd_for_calls`): what this run would have cost at list price. No billing API
is called.

- **It scales with `--limit`, so it compares down the column but never across
  runs of different `n`** — a 500-row run costs 100× a 5-row smoke run on the
  same lane. Quote the per-query rate from `price_list.py` instead. Precision is
  adaptive: under $0.10 renders four decimals so a smoke run's lanes don't all
  collapse to `$0.00`.
- **"Calls" is `problem_count`** — one per attempted dataset row, the `n`
  denominator. Two biases in opposite directions, both in the footnote: retry
  attempts are *not* counted (not recorded per row), and failed rows *are*,
  though most vendors don't bill a 5xx or a rate-limit rejection.
- **It goes stale silently.** `PRICE_LIST_AS_OF` is the snapshot date and
  `PRICE_SOURCES` the pages it came from; re-check before publishing. The
  footnote under both tables carries the date.
- **`—` means "no per-query list price", not missing data.** Such a lane carries
  `usd_per_1k_queries=None` rather than a fabricated figure; no shipped lane does
  today. A missing or unparseable `problem_count` also renders `—` rather than
  `$0.00`, which would read as free.
- **Excluded from the figure:** overage beyond 10 results (Parallel and Exa both
  charge ~$1/1k extra), Exa's page summaries and Deep Search tiers, and
  volume/annual discounts. Firecrawl is credit-metered and plan-gated, so its row
  normalizes the month-to-month Standard plan (2 credits per 10 results) to stay
  comparable with the pay-as-you-go rows.
- **Every lane in `PROVIDER_CAPABILITIES` must have a `PRICE_LIST` entry** — a
  unit test enforces set equality, so a new lane fails loudly instead of
  rendering an em dash that reads as free.
- **`nimble_search` is the one depth-configurable lane,** so its price resolves
  against the `search_depth` captured in `sampler_config_nimble_search.json`
  (lite → Lite tier, fast/deep → Search tier). Every other lane's tier is pinned
  by the lane definition.

### Which latency column to quote

Two per-row latency columns, one publishable:

- **`provider_response_time_ms` — the published Latency column.** Timed around a
  single HTTP attempt, *inside* the rate-limiter gate, so neither the
  client-side throttle nor retry backoff lands in it. Rolls up to
  `provider_response_time_ms_{p50,p95,mean}`.
- **`request_response_time_ms` — harness wall clock, do not publish.** Includes
  the limiter queue wait, roughly `workers / rate_per_second` seconds on a
  throttled lane. Retained only as a throughput signal.

On a throttled lane the queue wait dominates: publish the wall clock and every
rate-limited lane converges on roughly `_SAMPLER_RATE_HEADROOM_S` (`runner.py`)
however fast the provider answered, so a 1 RPS lane and a 9 RPS lane report the
same latency. Any rendered latency figure must come from
`provider_response_time_ms`. The `run.md` "Latency by stage" table nests all
three totals — server `total;dur` ≤ provider round trip ≤ client wall clock — so
the gap stays visible.

One caveat survives the right column: the round trip is measured *under load*,
at whatever concurrency each lane's rate limit allowed
(`effective_sampler_concurrency`), which differs across lanes. It is "response
time at that concurrency", not single-request latency.

**One significance baseline for the whole table.** `auto` resolves to
`nimble_search`, falling back to first-by-sort when that lane isn't in the
roster; `--significance-baseline <provider>` pins it. A fork measuring its own
API should point this at its own lane. Every lane sits in the same Bonferroni
family, which is what makes the ★ markers comparable down the column.

The by-answer-type and by-topic accuracy breakdowns are computed but not
rendered: the analyzer writes `analyzed_by_answer_type.csv` /
`analyzed_by_topic.csv` and lists both under Source Artifacts, so accuracy can
be re-sliced by category without widening the headline table.

**`all_apis` is the only alias.** It is derived from `PROVIDER_CAPABILITIES`
minus `EXCLUDED_SAMPLERS`, so new samplers are swept in automatically and no
hand-maintained "run everything" list can go stale — that failure once dropped a
lane from a full n=500 run. It backs `make eval`. A lane that must NOT run by
default belongs in `EXCLUDED_SAMPLERS` (empty today).

The hand-listed rosters (`competition_apis`, `all_search_apis`,
`third_party_search_apis`, and the one-knob pairs) were removed deliberately: a
narrower roster is now spelled out lane by lane on the command line, so the
dispatched set is visible in the invocation instead of behind a name that
drifts. `expand_sampler_names` preserves the given order, and that order is what
controls leaderboard row ordering.
`tests/unit/test_provider_capabilities.py::test_all_apis_is_the_only_alias` is
the tripwire, and the removed names are pinned in
`test_removed_answer_aliases_do_not_resolve` so a stale dispatch fails loudly
rather than silently running a different roster.

## Key Files

```text
pyproject.toml                         Package metadata, dependencies, pytest markers, console scripts
Makefile                               Canonical install, test, lint, eval, and clean commands
ruff.toml                              Python lint and format policy
.env.example                           Public configuration template
data/simpleqa_full_dataset.csv         Bundled SimpleQA-style evaluation data
src/nimble_benchmark/cli.py            Main benchmark command
tests/conftest.py                      Shared test setup
README.md                              Public-facing docs: setup, providers, metrics, output layout
AGENTS.md                              Cross-tool agent contract (cost limits, no-fabrication rule)
```

## Configuration

Copy `.env.example` to `.env` and fill in secrets locally. Never commit `.env`, generated `runs/`, or API keys.

| Variable | Required | Description |
| --- | --- | --- |
| `NIMBLE_API_KEY` | For `nimble_search` | API key for the configured Nimble-compatible API under test. |
| `NIMBLE_BASE_URL` | No | Nimble-compatible API base URL, defaulting to prod `https://sdk.nimbleway.com/v2`. |
| `OPENAI_API_KEY` | Yes | Required by every run: eval-side answer synthesis, answer grading, and UMBRELA retrieval judging all go through it. It gates no lane of its own. |
| `OPENAI_MAX_CONCURRENCY` | No | Process-wide cap on concurrent OpenAI requests across judge + grader + synthesis, default 112 (kept under the shared httpx pool of 128). |
| `NIMBLE_SEARCH_DEPTH` | No | Search depth for the `nimble_search` lane: `lite`, `standard`, `fast`, or `deep`. `standard` is the server's current name for the Search tier and `fast` its deprecated alias; `deep` now means `lite` plus `full_content`. This is the one depth-configurable lane — every other vendor's tiers are pinned per lane — so the Cost column resolves its price against the depth the run recorded. |
| `NIMBLE_RATE_PER_SECOND` | No | Client-side throttle for the Nimble search lane, default 9 (server caps search products at 10 RPS). The limiter bucket is scoped per product, i.e. per `search_depth`. |
| `JUDGE_MODEL` | No | Model for retrieval judging. |
| `SYNTHESIS_MODEL` | No | Model used to synthesize every lane's graded answer. OpenAI models only. Changing it moves the Accuracy of every row, so re-baseline rather than diffing across it. |
| `GRADER_MODEL` | No | Model for answer grading. |
| `EXA_API_KEY` | For `exa_search_auto`, `exa_search_fast` | Exa API key. Powers both search-type lanes (`type=auto` / `type=fast`), which share one rate-limit bucket. The type is pinned per lane — there is no `EXA_SEARCH_TYPE` knob. |
| `EXA_RATE_PER_SECOND` | No | Throttle shared by both Exa lanes, default 9 (Exa's documented cap is 10 QPS). |
| `PARALLEL_API_KEY` | For `parallel_search_basic`, `parallel_search_turbo` | Parallel Search API key. Powers both mode lanes, which share one rate-limit bucket. |
| `PARALLEL_RATE_PER_SECOND` | No | Throttle shared by both Parallel lanes, default 9 (Parallel's documented cap is 600/min per key). |
| `TAVILY_API_KEY` | For `tavily_search_basic`, `tavily_search_fast` | Tavily API key. Powers both depth lanes, which share one rate-limit bucket and cost 1 API credit each. The depth is pinned per lane — there is no `TAVILY_SEARCH_DEPTH` knob. |
| `TAVILY_RATE_PER_SECOND` | No | Throttle shared by both Tavily lanes, default 1. Tavily publishes no numeric QPS cap, so this is a conservative default rather than a documented ceiling. |
| `FIRECRAWL_API_KEY` | For `firecrawl_search` | Firecrawl API key, driven through `AsyncFirecrawl.search()` from `firecrawl-py`. |
| `FIRECRAWL_RATE_PER_SECOND` | No | Throttle for `firecrawl_search`, default 1 (fits Hobby's 100/min and up). The Free plan allows 10/min — set `0.16` there. |
| `BRAVE_SEARCH_API_KEY` | For `brave_search` | Brave Web Search subscription token. Free tier is 1 req/sec, 2,000/month. |
| `BRAVE_RATE_PER_SECOND` | No | Lifts the 1 RPS free-tier throttle on a paid Brave plan. |

## Conventions

Use Python 3.12+ and UV for all dependency management. Use type hints for new Python code, prefer small focused tests, mark fast offline tests with `@pytest.mark.unit`, and reserve `@pytest.mark.integration` for tests that hit a live provider API. Every target in the `Makefile` must also be listed in its `.PHONY` line.

**Nothing committed may contain credentials or private infrastructure detail** — no API keys, no non-public hostnames or environment names, no customer data. That applies to code comments and test docstrings as much as to config. `.env` and `runs/` are gitignored and must stay that way.

**Never fabricate a benchmark number.** Every figure reported must be read out of a file on disk (`analyzed_results.csv`, `run.json`, `run.md`) in a run directory. See [AGENTS.md](AGENTS.md) for the full agent contract, including the confirm-before-spending rules for large runs.

### Logging

`nimble-eval` logs ERROR to the console and full DEBUG to a rotating per-invocation file under `logs/` (tune with `--log-level` / `--log-dir`). Once the run directory exists, every record is also mirrored to `<run-dir>/eval.log`, so each `runs/run_*` folder is a self-contained debugging artifact next to its CSVs. Noisy HTTP libraries (httpx, openai, aiohttp, ...) are pinned to WARNING.

The console is the only **filtered** sink, defaulting to ERROR because a lane failing every row (expired key, exhausted quota) otherwise prints one WARNING per question and drowns the run. Both log files still capture DEBUG, and `errors.md` is derived from the run's CSVs rather than log records, so it is byte-identical at any `--log-level`. What the default costs you on the console is the progress commentary and the `This run had issues — see errors.md` pointer: **check `test -s errors.md` after a run instead of trusting a quiet console.** Pass `--log-level INFO` for the commentary.

### Run digests: insights.md + errors.md

Every run writes two interpretation artifacts next to `run.md` (see `insights.py`):

- **`insights.md`** — what the run *means*: lane roster changes and metric movements against **the previous run in the results directory, whatever question slice it sampled**, reliability and coverage gaps, latency outliers, tested significance results, and token usage. When the two runs' `(dataset, limit, random_state)` differ (an n=5 smoke run against an n=500 benchmark, say), the deltas are still rendered but carry a warning that they mix provider change with sample change — treat those as a lead to re-test on a matched slice, not as a result. Cross-run deltas are heuristic materiality thresholds in either case, **not** significance tests; the only real hypothesis test is the within-run lane-vs-baseline paired t-test surfaced from `significance.csv`.
- **`errors.md`** — triage for whoever picks the run up next. Failures are grouped by *cause class* (auth / billing / quota / config / validation / rate-limit / transient / unclassified) with concrete remediation, because the HTTP status alone misleads: Parallel's monthly quota message says "rate limit" while being a monthly allowance no retry can clear. **The file is empty (zero bytes) when the run had no actionable issues**, so `test -s errors.md` is a one-shot health check. A run being too small to clear the ranking gate is context, not an issue, and does not make the file non-empty.

Both are written last and can never fail a run — a writer exception is logged and swallowed. Classification rules live in `insights._CLASSIFIER_RULES` and are grounded in error strings actually observed in `runs/`; add a rule when an `Unclassified` entry recurs.

### SimpleQA sampling

`--limit N` slices use `random.Random(seed).sample(rows, N)` -- the exact algorithm and stdlib RNG used by [openai/simple-evals](https://github.com/openai/simple-evals/blob/main/simpleqa_eval.py)'s `SimpleQAEval`. The default seed is `0`, matching that reference, so a bare `nimble-eval --limit 500` picks the same SimpleQA subset every published benchmark reports against. Pass `--random-state <int>` to draw a different reproducible subset; never use unseeded sampling for ranked or shipped numbers.
