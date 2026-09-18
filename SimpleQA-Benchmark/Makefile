.PHONY: install lock test test-integration lint format eval eval-quick eval-all-preflight leaderboard leaderboard-snapshot leaderboard-preview leaderboard-watch leaderboard-smoke leaderboard-html clean

install:
	uv sync --extra dev

lock:
	uv lock

test:
	uv run pytest -m unit

test-integration:
	uv run pytest -m integration

lint:
	uv run ruff check src tests && uv run ruff format --check src tests

format:
	uv run ruff format src tests && uv run ruff check --fix src tests

eval:
	uv run nimble-eval --dataset simpleqa --limit 500 --samplers all_apis

eval-quick:
	uv run nimble-eval --dataset simpleqa --limit 5 --samplers all_apis

# Credential check for every lane, no API spend. Run this before eval-all.
eval-all-preflight:
	uv run nimble-eval --samplers all_apis --dry-run


leaderboard:
	@RUN_DIR="$${RUN_DIR:-$$(ls -td runs/run_* 2>/dev/null | sed -n '1p')}"; \
	if [ -z "$$RUN_DIR" ]; then \
		echo "No run directory found. Set RUN_DIR=path/to/run."; \
		exit 1; \
	fi; \
	uv run python -m nimble_benchmark.leaderboard "$$RUN_DIR"

# Snapshot a run dir into data/leaderboard_sample.json so the
# preview/watch targets can re-render in milliseconds without rerunning eval.
leaderboard-snapshot:
	@RUN_DIR="$${RUN_DIR:-$$(ls -td runs/run_* 2>/dev/null | sed -n '1p')}"; \
	if [ -z "$$RUN_DIR" ]; then \
		echo "No run directory found. Set RUN_DIR=path/to/run."; \
		exit 1; \
	fi; \
	uv run python -m nimble_benchmark.leaderboard_preview snapshot "$$RUN_DIR"

# One-shot render of the local Markdown + Mintlify MDX into _preview/.
# Reads data/leaderboard_sample.json by default; run
# `make leaderboard-snapshot` first if it doesn't exist.
leaderboard-preview:
	uv run python -m nimble_benchmark.leaderboard_preview render

# Same as `leaderboard-preview` but stays running and re-renders whenever a
# template or render-layer file changes. Ideal for iterating on Mintlify
# MDX styling without running another eval.
leaderboard-watch:
	uv run python -m nimble_benchmark.leaderboard_preview render --watch

# Render from the tiny inline smoke fixture (no run dir / snapshot needed).
# Useful for first-time setup and sanity-checking template edits.
leaderboard-smoke:
	uv run python -m nimble_benchmark.leaderboard_preview smoke

# Capture a fully-rendered, standalone Mintlify HTML snapshot of the
# leaderboard. Boots `mintlify dev` against a temp scaffold, inlines the
# CSS bundles, and writes <RUN_DIR>/leaderboard.html (~320 KB, viewable
# locally without the dev server). Requires `mintlify` on PATH
# (`npm install -g mintlify`).
leaderboard-html:
	@RUN_DIR="$${RUN_DIR:-$$(ls -td runs/run_* 2>/dev/null | sed -n '1p')}"; \
	if [ -z "$$RUN_DIR" ]; then \
		echo "No run directory found. Set RUN_DIR=path/to/run."; \
		exit 1; \
	fi; \
	uv run python -m nimble_benchmark.leaderboard_html "$$RUN_DIR"

clean:
	rm -rf runs/ logs/ .pytest_cache .ruff_cache
