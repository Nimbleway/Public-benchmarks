"""Fast iteration env for the leaderboard templates.

The full pipeline (``nimble-eval`` → ``analyzer`` → ``leaderboard``) takes
minutes per run. When iterating on Mintlify MDX styling, the data hasn't
changed — only the templates / rendering have. This tool snapshots a real run
to JSON once, then re-renders the templates against that snapshot in
sub-second time.

Workflows
---------

.. code-block:: bash

    # 1. One-time: snapshot a real run into the shared fixture.
    python -m nimble_benchmark.leaderboard_preview snapshot path/to/run_dir

    # 2. Iterate on templates/leaderboard_public.mdx — fast loop.
    python -m nimble_benchmark.leaderboard_preview render

    # 3. Re-render whenever a template or render-layer file changes.
    python -m nimble_benchmark.leaderboard_preview render --watch

    # Or point at a specific snapshot / output directory:
    python -m nimble_benchmark.leaderboard_preview render \\
        --snapshot data/leaderboard_sample.json \\
        --output _preview

The preview directory (``_preview/`` by default) holds the rendered local
Markdown and the Mintlify MDX side by side so you can diff them or open the
MDX in Mintlify's local dev server.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from nimble_benchmark.leaderboard import render_local_markdown, render_public_mdx
from nimble_benchmark.leaderboard_data import LeaderboardData, load_snapshot, prepare_leaderboard_data, save_snapshot

DEFAULT_SNAPSHOT = Path("data/leaderboard_sample.json")
DEFAULT_OUTPUT = Path("_preview")
DEFAULT_GENERATED_AT = "2026-01-01T00:00:00+00:00"

# Files the preview watcher rereads to decide whether to re-render. Keep tight
# to avoid spurious rerenders from unrelated saves.
_WATCH_ROOT = Path(__file__).resolve().parent
_WATCH_PATHS: tuple[Path, ...] = (
    _WATCH_ROOT / "templates" / "leaderboard_local.md",
    _WATCH_ROOT / "templates" / "leaderboard_public.mdx",
    _WATCH_ROOT / "leaderboard.py",
    _WATCH_ROOT / "leaderboard_render.py",
    _WATCH_ROOT / "leaderboard_data.py",
    _WATCH_ROOT / "sampler_config.py",
)


def cmd_snapshot(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    snapshot_path = Path(args.snapshot)
    # Pin the timestamp so the snapshot is reproducible across regenerations
    # (real runs vary by clock; the preview env should not).
    data = prepare_leaderboard_data(run_dir, generated_at=args.generated_at)
    save_snapshot(data, snapshot_path)
    print(f"Wrote snapshot: {snapshot_path} ({data.total_rows} rows total, {len(data.headline_rows)} lanes)")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    snapshot_path = Path(args.snapshot)
    if not snapshot_path.exists():
        print(f"No snapshot at {snapshot_path}. Run `snapshot` first or pass --snapshot.", file=sys.stderr)
        return 1
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.watch:
        return _watch_loop(snapshot_path, output_dir)
    return _render_once(snapshot_path, output_dir)


def _render_once(snapshot_path: Path, output_dir: Path) -> int:
    start = time.perf_counter()
    data = load_snapshot(snapshot_path)
    md_path = output_dir / "leaderboard.md"
    # Published MDX filename matches the docs-site page; see
    # ``nimble_benchmark.leaderboard.PUBLIC_MDX_FILENAME``.
    mdx_path = output_dir / "web-api-leaderboard.mdx"
    md_path.write_text(render_local_markdown(data), encoding="utf-8")
    mdx_path.write_text(render_public_mdx(data), encoding="utf-8")
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"Rendered {md_path} and {mdx_path} from {snapshot_path} ({elapsed_ms:.1f} ms)")
    return 0


def _watch_loop(snapshot_path: Path, output_dir: Path) -> int:
    """Poll watched files for mtime changes and re-render on edit.

    Polling (not inotify/fsevents) keeps this dependency-free; the watched set
    is small enough that the cost is invisible. Ctrl-C exits cleanly.
    """
    print(f"Watching templates + render layer; re-rendering to {output_dir}/. Ctrl-C to stop.")
    last_seen: dict[Path, float] = {}
    _render_once(snapshot_path, output_dir)
    try:
        while True:
            time.sleep(0.25)
            changed = False
            for path in (*_WATCH_PATHS, snapshot_path):
                try:
                    mtime = path.stat().st_mtime
                except FileNotFoundError:
                    continue
                if last_seen.get(path) != mtime:
                    if path in last_seen:
                        changed = True
                    last_seen[path] = mtime
            if changed:
                # Re-import on change so render_*.py edits are picked up
                # without restarting the watcher.
                _reload_render_modules()
                _render_once(snapshot_path, output_dir)
    except KeyboardInterrupt:
        print("\nstopped")
        return 0


def _reload_render_modules() -> None:
    """Re-import the render layer so template-helper edits take effect."""
    import importlib

    import nimble_benchmark.leaderboard as leaderboard
    import nimble_benchmark.leaderboard_data as data_mod
    import nimble_benchmark.leaderboard_render as render_mod

    importlib.reload(data_mod)
    importlib.reload(render_mod)
    importlib.reload(leaderboard)


def _smoke_data() -> LeaderboardData:
    """Tiny inline fixture used by ``--smoke`` so a contributor can preview
    output before any real run exists. Mirrors the dataclass shape exactly."""
    from nimble_benchmark.leaderboard_data import HeadlineRow, SourceArtifact

    return LeaderboardData(
        run_name="smoke_run_local",
        run_dir="runs/smoke_run_local",
        generated_at=DEFAULT_GENERATED_AT,
        synthesis_model="gpt-5",
        total_rows=30,  # 3 lanes x 10 problems, matching the rows below
        headline_rows=[
            HeadlineRow(
                provider_id="tavily_search_basic",
                response_kind="search_results",
                answer_source="synth",
                accuracy_score=0.68,
                ndcg_at_10=0.413,
                recall_at_10_llm=0.74,
                usage_input_tokens_mean=None,
                usage_output_tokens_mean=None,
                provider_response_time_ms_p50=2300,
                problem_count=10,
            ),
            HeadlineRow(
                provider_id="nimble_search",
                response_kind="search_results",
                answer_source="synth",
                accuracy_score=0.76,
                ndcg_at_10=0.534,
                recall_at_10_llm=0.84,
                usage_input_tokens_mean=None,
                usage_output_tokens_mean=None,
                provider_response_time_ms_p50=3200,
                problem_count=10,
                is_baseline=True,
            ),
            HeadlineRow(
                provider_id="exa_search_auto",
                response_kind="search_results",
                answer_source="synth",
                accuracy_score=0.71,
                ndcg_at_10=0.488,
                recall_at_10_llm=0.79,
                usage_input_tokens_mean=None,
                usage_output_tokens_mean=None,
                provider_response_time_ms_p50=2100,
                problem_count=10,
            ),
        ],
        sampler_configs={
            "nimble_search": {"search_depth": "fast"},
            "exa_search_auto": {"search_type": "auto", "num_results": 10, "summary": True},
        },
        reproduce_command=("uv run nimble-eval --dataset simpleqa --limit 10 --samplers nimble_search exa_search_auto"),
        leaderboard_command="python -m nimble_benchmark.leaderboard runs/smoke_run_local",
        source_artifacts=[
            SourceArtifact(name="Run directory", path="runs/smoke_run_local"),
            SourceArtifact(name="Headline CSV", path="runs/smoke_run_local/analyzed_results.csv"),
            SourceArtifact(name="Significance CSV", path="runs/smoke_run_local/significance.csv"),
        ],
        baseline_system="nimble_search/search_results/synth",
    )


def cmd_smoke(args: argparse.Namespace) -> int:
    """Render straight from the inline smoke fixture — no run dir or snapshot
    file required. Useful as a sanity check after a template edit."""
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    data = _smoke_data()
    md_path = output_dir / "leaderboard.md"
    mdx_path = output_dir / "web-api-leaderboard.mdx"
    md_path.write_text(render_local_markdown(data), encoding="utf-8")
    mdx_path.write_text(render_public_mdx(data), encoding="utf-8")
    print(f"Rendered smoke preview: {md_path} and {mdx_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m nimble_benchmark.leaderboard_preview")
    subparsers = parser.add_subparsers(dest="command", required=True)

    snap = subparsers.add_parser("snapshot", help="Capture a run directory into a JSON snapshot.")
    snap.add_argument("run_dir", type=Path, help="Path to the analyzed run directory.")
    snap.add_argument(
        "--snapshot",
        type=Path,
        default=DEFAULT_SNAPSHOT,
        help=f"Output JSON path (default: {DEFAULT_SNAPSHOT}).",
    )
    snap.add_argument(
        "--generated-at",
        default=DEFAULT_GENERATED_AT,
        help=f"Pin the generated_at timestamp for reproducible snapshots (default: {DEFAULT_GENERATED_AT}).",
    )
    snap.set_defaults(func=cmd_snapshot)

    render = subparsers.add_parser("render", help="Render templates from an existing snapshot.")
    render.add_argument(
        "--snapshot",
        type=Path,
        default=DEFAULT_SNAPSHOT,
        help=f"Input JSON snapshot (default: {DEFAULT_SNAPSHOT}).",
    )
    render.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output directory for rendered .md / .mdx (default: {DEFAULT_OUTPUT}).",
    )
    render.add_argument(
        "--watch",
        action="store_true",
        help="Stay running and re-render whenever a template or render-layer file changes.",
    )
    render.set_defaults(func=cmd_render)

    smoke = subparsers.add_parser("smoke", help="Render the inline smoke fixture (no snapshot file needed).")
    smoke.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output directory for rendered .md / .mdx (default: {DEFAULT_OUTPUT}).",
    )
    smoke.set_defaults(func=cmd_smoke)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
