# AGENTS.md

Instructions for coding agents working in this repository. Humans: see [README.md](README.md); the deeper architecture notes are in [CLAUDE.md](CLAUDE.md).

## What this repo is

A benchmark harness that measures web search APIs on SimpleQA: answer accuracy, retrieval quality, and latency. Each run writes a self-contained directory under `runs/`.

It is provider-neutral by construction: every lane, Nimble's included, goes through the same sampler interface, synthesizer, grader, and metrics. Adding the API you want to measure is one file plus a registration — see [Adding a provider](#adding-a-provider).

Python 3.12+, dependencies via `uv`, tests via `pytest`, lint via `ruff`.

## Setting it up

```bash
./setup.sh                  # checks uv, copies .env.example -> .env, uv sync --extra dev
make eval-all-preflight     # validates credentials per lane, spends nothing
make eval-quick             # 5-row smoke run across all lanes
```

**Ask the user for API keys — never invent them, and never commit `.env`.** `OPENAI_API_KEY` is required for any run at all (it drives answer synthesis, the SimpleQA grader, and UMBRELA judging). Every other key gates exactly one vendor's lanes; if the user only has some, run only those lanes. `make eval-all-preflight` reports which are usable.

If a lane's key is missing, `build_samplers()` raises with the exact variable name. That error is the answer — surface it, don't work around it.

## Reporting results: the one hard rule

**Every number you report must come from a file on disk.** Read it out of `analyzed_results.csv`, `run.json`, or `run.md` in a run directory. Do not estimate, interpolate, remember, or predict a benchmark figure — a fabricated accuracy number in a benchmark repo is the worst possible failure mode here.

Three things to get right when interpreting results:

- **Accuracy measures retrieval, not a vendor's answer product.** Every shipped lane is `answer_source=synth`: graded on an answer the harness synthesized from that lane's ranked results with one fixed model. So the column is uniformly sourced today, and what it reports is how well each vendor's retrieval feeds that synthesizer — never a claim about a vendor's own answer API. The pipeline still supports an `answer_source=api` lane; if one is ever registered, an `api` row beating a `synth` row is **not** a like-for-like win and must be flagged as such.
- **Quote `provider_response_time_ms_p50` for latency, never `request_response_time_ms_p50`.** The first is the provider's round trip, timed inside the rate-limiter gate. The second is the harness wall clock and includes our own queue wait, which on a throttled lane is seconds — it is a throughput signal, not a provider measurement. If a run directory only carries the wall-clock column, it has no publishable latency number.
- **A quiet console does not mean a clean run.** Console logging defaults to `ERROR`. Check `test -s <run_dir>/errors.md` — that file is zero bytes exactly when there was nothing actionable. Read it when it isn't.

Cross-run deltas in `insights.md` are heuristic materiality thresholds, not significance tests. The only real hypothesis test is the within-run paired t-test in `significance.csv`.

## Reproducibility

`--limit N` samples with `random.Random(seed).sample(rows, N)`, matching `openai/simple-evals`. Default seed `0`. So `--limit 500` is the same 500 rows everywhere, and results are comparable across machines and dates.

Never use an unseeded subset for a number anyone will quote. If you change `--random-state` or `--synthesis-model`, say so alongside the result — `--synthesis-model` moves the accuracy of every `synth` row, so numbers across a change of it are not comparable.

## Commands

```bash
make test                   # unit tests, offline
make test-integration       # hits real APIs
make lint                   # ruff check + format check
make format                 # ruff format + autofix
make eval                   # n=500, every lane
make eval-quick             # n=5, every lane
make leaderboard            # render leaderboard.md from the newest run
uv run nimble-eval --help   # full CLI
```

Run `make lint && make test` before declaring any code change done.

## Cost and blast radius

Eval runs spend real money on real third-party APIs, and some lanes have hard monthly caps (Brave's free tier is 2,000 requests/month total).

- Default to `make eval-quick` (n=5). **Confirm with the user before any run at n≥100**, and before `make eval` (n=500 × 9 lanes).
- `--skip-llm-judge` removes the UMBRELA fan-out and is much cheaper when you only need accuracy.
- `--dry-run` validates credentials without spending anything.

Never delete or edit anything under `runs/` unless asked. Those directories are the provenance for published numbers. `make clean` deletes all of them — don't run it casually.

## Adding a provider

1. Sampler in `samplers/your_provider.py` — subclass `BaseHTTPPostSampler` (`_http_post_base.py`) for a JSON POST endpoint, implementing `_endpoint`, `_headers`, `_build_payload`, `extract_chunks`; or `BaseSampler` (`base.py`) with `get_search_results` + `extract_chunks` if it needs a vendor SDK.
2. Register in `PROVIDER_CAPABILITIES` in `samplers/__init__.py` — this alone adds it to `all_apis`.
3. Construct it in `build_samplers()`, reading its key from `Settings`.
4. Add the key to `config.py` and `.env.example`.

Verify with `uv run nimble-eval --samplers your_provider --limit 5`, then `make test`.

Registering a lane sweeps it into `all_apis` automatically, so `make eval` will dispatch it and require its API key. Do **not** add a new alias on your own initiative: the alias table is deliberately a single entry, so that every narrower roster stays spelled out in the invocation. A lane that must not run by default goes in `EXCLUDED_SAMPLERS`.

## Conventions

- Type hints on new code. Small, focused tests. `@pytest.mark.unit` for offline, `@pytest.mark.integration` for live APIs.
- Every target in the `Makefile` must also be listed in its `.PHONY` line.
- Never commit credentials, non-public hostnames, or customer data — including in code comments and test docstrings.
- Don't commit unless asked.
