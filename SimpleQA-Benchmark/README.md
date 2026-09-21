# SimpleQA Benchmark

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue.svg)](https://www.python.org/)

An open benchmark harness for **web search APIs**.

Point it at a set of providers, and it runs a SimpleQA slice through each one, grades every answer with the official SimpleQA classifier, scores retrieval quality with both URL-binary and LLM-judged metrics, measures latency, runs significance tests, and writes everything to a reproducible run directory you can diff, publish, or throw away.

Built by [Nimble](https://www.nimbleway.com). Nimble is one lane among many here — the harness is provider-neutral, every number it prints is reproducible from a seed, and adding a competitor takes one file.

- **One command, every provider.** 9 lanes across 6 vendors ship out of the box.
- **Reproducible by construction.** `--limit N` uses the same seeded sampler as [`openai/simple-evals`](https://github.com/openai/simple-evals/blob/main/simpleqa_eval.py), so `--limit 500` picks the identical SimpleQA subset on every machine, every run.
- **Four metric families in one pass** — answer accuracy, URL retrieval (NDCG/Recall/MRR), LLM-judged retrieval ([UMBRELA](https://arxiv.org/abs/2406.06519)), and latency (round-trip p50/p95, measured inside the client-side rate limiter so the harness's own throttling never inflates a provider's number, plus server-reported internal timings where the vendor exposes them).
- **Comparable by construction.** Every lane is measured the same way: the same ranked-results interface, the same synthesizer, the same grader, so the Accuracy column is one question asked of every vendor.
- **Every failure is accounted for.** Failed rows are excluded from accuracy rather than scored as wrong, counted in `failure_rate`, and triaged by cause in `errors.md` — which is zero bytes exactly when the run was clean.

## Results

SimpleQA, n=500, seed 0 (`--limit 500 --random-state 0`), synthesized with
`gpt-4o`, graded with the upstream SimpleQA classifier, `nimble_search` at
`search_depth=standard`. Every figure is read out of a run's
`analyzed_results.csv` — none is hand-entered.

`runs/` is gitignored, so it does not ship with the repo. The seed is what makes
these numbers checkable instead: `--limit 500 --random-state 0` draws the
identical 500 rows on any machine.

| Provider | Accuracy | Latency p50 | Cost | n |
| --- |---------:|------------:| ---: | ---: |
| `parallel_search_basic` |    95.8% |    1,569 ms | $2.50 | 499/500 |
| `nimble_search` |    95.4% |      817 ms | $2.50 | 500 |
| `exa_search_auto` |    89.2% |    3,924 ms | $3.50 | 500 |
| `parallel_search_turbo` |    88.1% |      482 ms | $0.50 | 495/500 |
| `exa_search_fast` |    86.6% |    3,022 ms | $3.50 | 500 |
| `firecrawl_search` |    86.4% |    1,584 ms | $0.99 | 500 |
| `brave_search` |    85.2% |    1,058 ms | $2.50 | 500 |
| `tavily_search_basic` |    82.0% |    2,174 ms | $4.00 | 499/500 |
| `tavily_search_fast` |    72.4% |      937 ms | $4.00 | 500 |

Regenerate or extend:

```bash
make eval          # n=500 across every lane
make leaderboard   # renders <RUN_DIR>/leaderboard.md from the newest run
```

**Accuracy measures retrieval quality, not a vendor's answer product.** Every
lane here is a `/search` lane: the harness takes its ranked results and
synthesizes one answer from them with a single fixed model, then grades that. So
the column reports *how well each vendor's retrieval feeds one synthesizer* —
which is what makes the lanes comparable to each other, and what makes the
number not a claim about any vendor's own answer API. The columns measured
identically for every lane — `ndcg_at_10`, `recall_at_10`, `recall_at_10_llm`,
latency — are all in `analyzed_results.csv`.

**Accuracy counts a non-answer as a miss.** The denominator is every question whose request succeeded. A lane that returns HTTP 200 with nothing usable in it — a refusal, or zero results to synthesize an answer from — is graded `is_not_attempted` and scores 0 for that question rather than dropping out of the denominator: the call reached the provider, so failing to answer costs the same as answering wrong. Only rows whose request never landed (`not_evaluated`) are excluded, and those are counted by `failure_rate` instead. Upstream [simple-evals](https://github.com/openai/simple-evals) reports not-attempted as a separate third bucket and divides by attempts only, so **this column is deliberately stricter than a published SimpleQA accuracy figure and should not be quoted against one** — the grader prompt is upstream-verbatim, the aggregation is not. Per-row `evaluation_result` labels are unchanged in the raw CSVs, so the upstream split is recomputable.

**Cost is an estimate of what the run would have cost at list price, not a measured bill.** It is the lane's request count — one call per attempted dataset row, the `n` denominator — times a hand-copied snapshot of that vendor's *published per-query price* for the lane's product tier, held as static data in [`src/nimble_benchmark/price_list.py`](src/nimble_benchmark/price_list.py). No billing API is called.

Three things follow. **It scales with `--limit`**, so it compares down the column but never across runs of different `n` — quote the per-query rate from `price_list.py` for that. **It miscounts in both directions, slightly:** retry attempts aren't recorded per row so they aren't charged for, while failed rows are charged for even though most vendors don't bill a 5xx or a rate-limit rejection. And **prices go stale** — `PRICE_LIST_AS_OF` records the snapshot date, `PRICE_SOURCES` the pages it came from, and both tables footnote the date.

The figure excludes overage beyond 10 results (Parallel and Exa each add ~$1/1k extra results), Exa's page summaries and Deep Search tiers, and volume or annual discounts; Firecrawl is credit-metered and plan-gated, so its rate normalizes the month-to-month Standard plan (2 credits per 10 results) to stay comparable with the pay-as-you-go lanes. A `—` in the Cost column means the lane has **no published per-query list price** rather than missing data — the harness renders an em dash rather than inventing a rate. No lane in the table is in that state today. Because cost and accuracy come from different places, a cheap row is not a good row — read the two columns together.

**Latency is the round trip, measured under load.** The published column is `provider_response_time_ms_p50`, timed inside the client-side rate limiter so the harness's own throttling and retry backoff are excluded. `request_response_time_ms_*` sits alongside it in the CSV — the wall clock *including* the limiter queue wait, a throughput signal rather than a provider measurement. Each lane is measured at whatever concurrency its rate limit permitted, so these are response times under load, not idle single-request latencies.

## How it works

For each question in the SimpleQA slice, and for each lane:

1. **Query** the provider once, timing the round trip and recording any server-reported timing.
2. **Extract chunks** — a ranked list of URL + text, normalized from whatever shape the vendor returns.
3. **Synthesize an answer** from the top chunks (`--synthesis-model`, default `gpt-4o`). Same model for every lane, so the only thing that varies is the retrieval feeding it.
4. **Grade** the answer against SimpleQA's gold target with the official A/B/C classifier — correct / incorrect / not attempted.
5. **Score retrieval** two ways: URL-binary NDCG/Recall/MRR against the gold source URL, and UMBRELA per-chunk relevance judging by an LLM.
6. **Aggregate**, then run a paired t-test of every lane against one baseline lane, Bonferroni-corrected across the family.

Nothing is cached between runs and no lane sees another lane's output.

## Setup

### With an AI agent

This repo ships an [`AGENTS.md`](AGENTS.md) and a [`CLAUDE.md`](CLAUDE.md), so a coding agent can set it up unattended. Open the repo in Claude Code, Cursor, Codex, or similar, and paste:

```text
Set up this benchmark repo and prove it works:
1. Run ./setup.sh
2. Ask me for whichever API keys are needed, then write them into .env.
   OPENAI_API_KEY is mandatory (it drives synthesis, grading and judging).
   Every other key gates exactly one vendor's lanes.
3. Run `make eval-all-preflight` and tell me which lanes are credentialed.
4. Run `make eval-quick` limited to the lanes that passed preflight.
5. Confirm `test -s errors.md` in the new runs/ dir is empty, then summarize
   the accuracy and latency per lane from analyzed_results.csv.
Don't fabricate any numbers - every figure must come from a file on disk.
```

### Manually

```bash
git clone https://github.com/Nimbleway/Public-benchmarks.git
cd Public-benchmarks/SimpleQA-Benchmark
./setup.sh          # checks for uv, copies .env.example -> .env, installs deps
```

Then put your keys in `.env` and verify:

```bash
make eval-all-preflight   # validates credentials for every lane, spends nothing
make eval-quick           # 5-row smoke run across all lanes
```

Requires **Python 3.12 or 3.13** and [**uv**](https://docs.astral.sh/uv/). `uv` will fetch a suitable Python if you don't have one.

### API keys

Only `OPENAI_API_KEY` is required. Every other key gates exactly one vendor's lanes — omit it and run the lanes you have keys for.

| Variable | Gates | Notes |
| --- | --- | --- |
| `OPENAI_API_KEY` | **Every run** | Drives answer synthesis, the SimpleQA grader, and UMBRELA judging. It gates no lane of its own. |
| `NIMBLE_API_KEY` | `nimble_search` | [Get one](https://www.nimbleway.com). |
| `EXA_API_KEY` | `exa_search_auto`, `exa_search_fast` | Both lanes share one 9 RPS bucket. |
| `PARALLEL_API_KEY` | `parallel_search_basic`, `parallel_search_turbo` | Both lanes share one 9 RPS bucket. |
| `TAVILY_API_KEY` | `tavily_search_basic`, `tavily_search_fast` | Both lanes share one 1 RPS bucket. Tavily publishes no QPS cap, so that default is conservative — raise it with `TAVILY_RATE_PER_SECOND`. Both depths cost 1 API credit per call against a 1,000/month free tier. |
| `BRAVE_SEARCH_API_KEY` | `brave_search` | Free tier is 1 req/s and 2,000/month — the tightest ceiling here. |
| `FIRECRAWL_API_KEY` | `firecrawl_search` | Lane self-throttles to 1 req/s (fits Hobby+). On a Free key set `FIRECRAWL_RATE_PER_SECOND=0.16`. |

Everything else has a code default — API-key and model settings on `Settings` in `src/nimble_benchmark/config.py`, the per-lane `*_RATE_PER_SECOND` and `*_TIMEOUT_S` overrides in the sampler modules that read them. [`.env.example`](.env.example) lists them all; uncomment a line only to override one.

## Running evaluations

```bash
make eval                    # n=500 across every lane (`all_apis`)
make eval-quick              # n=5 smoke run, same lanes
make eval-all-preflight      # credential check for every lane, no API spend
make leaderboard             # render leaderboard.md from the newest run
uv run nimble-eval --help    # full CLI
```

Or drive the CLI directly:

```bash
uv run nimble-eval --samplers all_apis --limit 500              # everything
uv run nimble-eval --samplers nimble_search brave_search        # just two lanes
uv run nimble-eval --samplers exa_search_auto exa_search_fast --limit 50   # Exa auto vs fast
uv run nimble-eval --samplers all_apis --limit 1000             # publish-quality
uv run nimble-eval --samplers all_apis --limit 20 --skip-llm-judge   # cheap: no UMBRELA
```

### Options worth knowing

| Flag | Default | Why you'd change it |
| --- | --- | --- |
| `--samplers` | `nimble_search` | Lane names or an alias (see below). |
| `--limit` | `500` | Rows to sample. Seeded, so a given `N` is always the same rows. |
| `--random-state` | `0` | Draw a different reproducible subset. `0` matches `openai/simple-evals`. |
| `--synthesis-model` | `gpt-4o` | The synthesizer behind every search lane's accuracy. Changing it moves all of them — re-baseline, don't diff across it. |
| `--skip-llm-judge` | off | Skip UMBRELA. Much cheaper; URL retrieval metrics still computed. |
| `--skip-grader` | off | Skip accuracy grading when you only care about retrieval. |
| `--max-concurrent-tasks` | `40` | Raise until the provider rate-limits or p95 flattens; lower for a rate-limited vendor. |
| `--significance-baseline` | `auto` | The single lane every other lane is tested against. `auto` picks `nimble_search`. |
| `--log-level` | `ERROR` | Console verbosity. `INFO` restores per-lane progress commentary; both log files capture DEBUG regardless. |
| `--run-dir` | new dir | Write artifacts into an explicit directory. Overwrites raw results already there. |
| `--dry-run` | off | Validate credentials and exit. |

## Output

Every run writes a self-contained directory under `runs/`:

```text
runs/run_<timestamp>_benchmark_simpleqa_<lanes>_n<N>/
├── analyzed_results.csv          # one row per lane, 46 columns - the file to analyze
├── significance.csv              # paired t-test vs the baseline lane
├── analyzed_by_topic.csv         # accuracy sliced by SimpleQA topic
├── analyzed_by_answer_type.csv   # accuracy sliced by answer type
├── run.md                        # human-readable report: headline table, wrong-answer samples, caveats
├── run.json                      # machine-readable summary + the run's resolved arguments
├── insights.md                   # what changed vs the previous run
├── errors.md                     # failure triage, grouped by cause. EMPTY when the run was clean
├── eval.log                      # full DEBUG log for this run
├── failures.json                 # every failed row with its error
├── dataset_simpleqa_raw_results_<lane>.csv   # per-row raw output, one file per lane
└── sampler_config_<lane>.json    # exact parameterization of each lane, for provenance
```

`make leaderboard` adds `leaderboard.md` to that directory afterwards (and `make leaderboard-html` a `leaderboard.html`); neither is written by the run itself.

`test -s runs/<run>/errors.md` is a one-shot health check — the file is zero bytes when there was nothing actionable. Check it rather than trusting a quiet console: the console defaults to `ERROR` so a fully-failing lane can't drown the run in warnings.

## Providers

Every lane is a `/search` lane: it returns a ranked result list, the harness
synthesizes one answer from the top chunks with a single fixed model, and that
answer is what gets graded. No lane is asked for a server-side answer, which is
what keeps the Accuracy column comparable across vendors.

| Lane | Provider |
| --- | --- |
| `nimble_search` | Nimble `/search`, depth from `NIMBLE_SEARCH_DEPTH` (default `fast`) |
| `exa_search_auto` | Exa `/search`, `type=auto`, with `contents.summary` |
| `exa_search_fast` | Exa `/search`, `type=fast` — the low-latency tier |
| `parallel_search_basic` | Parallel `/v1/search`, `mode=basic` |
| `parallel_search_turbo` | Parallel `/v1/search`, `mode=turbo` — the low-latency tier |
| `tavily_search_basic` | Tavily `/search`, `search_depth=basic` (1 API credit) |
| `tavily_search_fast` | Tavily `/search`, `search_depth=fast` — the low-latency tier. 1 API credit, same as `basic` |
| `brave_search` | Brave Web Search. `operators` off by default: Brave's default reads SimpleQA's quoted titles as required exact matches and empties ~12% of the dataset |
| `firecrawl_search` | Firecrawl `/v2/search` via the official `firecrawl-py` SDK. Snippets only, so it stays comparable to the other SERP lanes |

Where a vendor exposes a speed/quality tier as a product choice (Exa's `type`,
Parallel's `mode`, Tavily's `search_depth`), the harness pins **each** end as a
separate lane rather than reading one from the environment — so every run
measures the tradeoff instead of silently picking a side. `nimble_search` is the
one exception: its depth follows `NIMBLE_SEARCH_DEPTH`, and the Cost column
prices it against the depth the run actually recorded.

### Aliases

There is exactly one:

| Alias | Contents |
| --- | --- |
| `all_apis` | Every lane. Derived from `PROVIDER_CAPABILITIES`, so it grows automatically when a sampler is added. Backs `make eval`. |

Any narrower roster is named lane by lane — `--samplers exa_search_auto
exa_search_fast` — which keeps the dispatched set visible in the invocation
rather than hidden behind a name that drifts. `--samplers` preserves the order
you give, and that order controls leaderboard row ordering.

## Adding a search provider

Four steps, and the test suite tells you if you missed one.

1. **Write the sampler** in `src/nimble_benchmark/samplers/your_provider.py`. If it's a JSON POST endpoint, subclass `BaseHTTPPostSampler` from `_http_post_base.py` and implement `_endpoint`, `_headers`, `_build_payload`, and `extract_chunks`. Retries, throttling, and timing come free. If it needs a vendor SDK instead, subclass `BaseSampler` from `base.py` and implement `get_search_results` plus `extract_chunks`.
2. **Register the capability** in `PROVIDER_CAPABILITIES` in `samplers/__init__.py`:
   `"your_provider": ProviderCapability("your_provider", "search_results", "synth")`. This alone puts it in `all_apis`.
3. **Construct it** in `build_samplers()` in the same file, reading its key off `Settings`.
4. **Add the key** to `config.py` and `.env.example`.

Then `uv run nimble-eval --samplers your_provider --limit 5` to verify, and `make test`. The pipeline also has an answer-lane path for a provider that returns its own answer with citations rather than a result list — register it `"answer_with_citations"` / `"api"` in step 2 — but no lane ships on it today, and such a row's accuracy is **not** comparable to the synthesized rows above.

Registering the lane puts it in `all_apis`, so `make eval` will dispatch it and require its API key. A lane that must not run by default belongs in `EXCLUDED_SAMPLERS`.

## Development

```bash
make test               # unit tests (offline, fast)
make test-integration   # smoke tests against real APIs
make lint               # ruff check + format check
make format             # ruff format + autofix
make clean              # remove runs/ logs/ and caches
```

Conventions: Python 3.12+, `uv` for dependencies, type hints on new code, small focused tests. Mark offline tests `@pytest.mark.unit` and anything hitting a live API `@pytest.mark.integration`. [`CLAUDE.md`](CLAUDE.md) documents the architecture and the reasoning behind the design decisions.

## Contributing

Issues and pull requests welcome — new providers especially. See
[CONTRIBUTING.md](CONTRIBUTING.md) for setup, the four-step guide to adding a
provider lane, and the cost rules. Run `make lint && make test` before opening a
PR, and mention which lanes you ran if the change touches a sampler.

Benchmark results are only as good as their provenance. If you report a number, say which run directory it came from and what `--limit`, `--random-state`, and `--synthesis-model` produced it.

Participation is governed by our [Code of Conduct](CODE_OF_CONDUCT.md).
Security reports go through
[GitHub's private vulnerability reporting](SECURITY.md), not public issues.

## License

Copyright 2026 Nimble. Licensed under Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).

The bundled SimpleQA test set (`data/simpleqa_full_dataset.csv`) and the SimpleQA grader prompt are taken verbatim from [`openai/simple-evals`](https://github.com/openai/simple-evals) and remain under its MIT license — © 2024 OpenAI. Provenance, checksum, and the full license text are in [`data/README.md`](data/README.md).

Third-party APIs are subject to their own terms of service. You are responsible for complying with the terms of any provider you benchmark, including the terms governing publication of performance comparisons.
