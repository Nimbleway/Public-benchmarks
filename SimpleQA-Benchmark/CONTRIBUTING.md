# Contributing

Thanks for your interest. New provider lanes are the most valuable contribution
you can make here — a benchmark is only as useful as the set of things it
compares.

## Ground rules

**Every number you report must come from a file on disk.** If you quote a
result in an issue, a PR, or a README table, state the `--limit`,
`--random-state`, and `--synthesis-model` that produced it. Do not estimate,
interpolate, or recall a benchmark figure. A fabricated accuracy number in a
benchmark repo is the worst failure mode this project has.

**Never commit credentials.** `.env` and `runs/` are gitignored and must stay
that way. That includes code comments and test docstrings — no API keys, no
non-public hostnames, no customer data.

## Setup

```bash
./setup.sh                # checks for uv, copies .env.example -> .env, installs deps
make eval-all-preflight   # validates credentials per lane, spends nothing
make test                 # unit tests, offline, no keys needed
```

Requires Python 3.12 or 3.13 and [uv](https://docs.astral.sh/uv/).

Only `OPENAI_API_KEY` is required for a real run — it drives answer synthesis,
the SimpleQA grader, and UMBRELA judging. Every other key gates exactly one
vendor's lanes, so you can contribute against the subset you have keys for.

## Before you open a PR

```bash
make lint && make test
```

Both must pass. If your change touches a sampler, also say which lanes you ran
and at what `--limit`.

## Adding a provider

Four steps; the test suite will tell you if you missed one.

1. **Write the sampler** in `src/nimble_benchmark/samplers/your_provider.py`.
   For a JSON POST endpoint, subclass `BaseHTTPPostSampler` from
   `_http_post_base.py` and implement `_endpoint`, `_headers`, `_build_payload`,
   and `extract_chunks` — retries, throttling, and timing come free. If it needs
   a vendor SDK, subclass `BaseSampler` from `base.py` and implement
   `get_search_results` plus `extract_chunks`.
2. **Register the capability** in `PROVIDER_CAPABILITIES` in
   `samplers/__init__.py`. This alone puts the lane in `all_apis`.
3. **Construct it** in `build_samplers()`, reading its key off `Settings`.
4. **Add the key** to `config.py` and `.env.example`, and add a `PRICE_LIST`
   entry in `price_list.py` — a unit test enforces that every registered lane
   has one, so a missing price fails loudly instead of rendering as free.

Verify with `uv run nimble-eval --samplers your_provider --limit 5`, then
`make test`.

Registering the lane sweeps it into `all_apis`, which backs `make eval` — so
that run will dispatch it and require its API key. A lane that must not run by
default belongs in `EXCLUDED_SAMPLERS`, the one opt-out hook.

## Cost awareness

Eval runs spend real money on third-party APIs, and some lanes have hard monthly
caps — Brave's free tier is 2,000 requests per month total. Default to
`make eval-quick` (n=5). `--skip-llm-judge` drops the UMBRELA fan-out and is
much cheaper when you only need accuracy. `--dry-run` validates credentials
without spending anything.

## Conventions

- Python 3.12+, `uv` for dependencies, type hints on new code.
- Small, focused tests. `@pytest.mark.unit` for offline, `@pytest.mark.integration`
  for anything hitting a live API.
- Every target in the `Makefile` must also appear in its `.PHONY` line.

## Working with an AI agent

[`AGENTS.md`](AGENTS.md) is the cross-tool agent contract and
[`CLAUDE.md`](CLAUDE.md) holds the architecture notes and the reasoning behind
the metric definitions. Both are kept current — if you change behavior they
describe, update them in the same PR.

## License

By contributing you agree that your contributions are licensed under
[Apache-2.0](LICENSE), the same license as the project.
