"""Shared sampler-configuration rendering for the run report and leaderboard.

Each ``sampler_config_<name>.json`` written by the CLI captures the knobs the
lane ran under. The reports only surface a curated subset (the "standard preset"
agreed with the user): the 1-3 knobs per lane that materially affect retrieval
quality. Timeouts, base URLs, and country/language toggles are intentionally
omitted to keep the cell scannable.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# Per-sampler field allowlist. The order here defines the order inside the
# rendered cell, so the knob that distinguishes a lane from its siblings leads.
STANDARD_CONFIG_FIELDS: dict[str, tuple[str, ...]] = {
    "nimble_search": ("search_depth",),
    # ``search_type`` leads: it is the only difference between the two Exa
    # lanes, so the cell is what tells a reader which product tier the row
    # measured.
    "exa_search_auto": ("search_type", "num_results", "summary"),
    "exa_search_fast": ("search_type", "num_results", "summary"),
    # ``mode`` leads: it is the only difference between the two Parallel lanes,
    # so the cell is what tells a reader which product tier the row measured.
    "parallel_search_basic": ("mode", "max_results"),
    "parallel_search_turbo": ("mode", "max_results"),
    # ``rate_per_second`` is surfaced because Firecrawl's /search quota is
    # per-plan (Free 10/min .. Growth 5,000/min) and the lane's default throttle
    # sits above the free tier: the cell tells a reader whether a published row
    # was measured under a throttle their own key could sustain.
    "firecrawl_search": ("num_results", "rate_per_second"),
    # ``operators`` is load-bearing for SimpleQA, not cosmetic: Brave's default
    # (true) treats SimpleQA's quoted titles as required exact matches and
    # empties ~12% of the dataset. Surface it so a reader can tell which
    # setting a published row was measured under.
    "brave_search": ("count", "extra_snippets", "operators"),
    # ``search_depth`` is the only difference between the two Tavily lanes.
    "tavily_search_basic": ("search_depth",),
    "tavily_search_fast": ("search_depth",),
}

# Fields we never surface even if a sampler captures them. base_url, api keys,
# and timeouts are either deployment-revealing or noise for the public report.
HIDDEN_FIELDS: frozenset[str] = frozenset({"base_url", "api_key", "timeout_s", "country", "language"})


def read_sampler_configs(run_dir: Path) -> dict[str, dict[str, Any]]:
    """Load every ``sampler_config_<name>.json`` file in ``run_dir``."""
    configs: dict[str, dict[str, Any]] = {}
    for path in sorted(Path(run_dir).glob("sampler_config_*.json")):
        sampler_name = path.stem.removeprefix("sampler_config_")
        try:
            configs[sampler_name] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return configs


def _format_value(value: Any) -> str:
    """Render a single configuration value as a compact human-readable string.

    None/empty → ``(default)`` (the provider falls back to the server's choice);
    booleans → lowercase ``true``/``false``; strings/numbers pass through.
    """
    if value is None or value == "":
        return "(default)"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def format_config_cell(sampler_name: str, config: dict[str, Any]) -> str:
    """Build the ``key=value, key=value`` cell content for one sampler row.

    Wrapped in backticks so both Markdown and Mintlify HTML render it as inline
    code, which keeps the keys/values visually distinct from prose. Returns
    ``"—"`` when the sampler captures no allowlisted fields in this run.
    """
    fields: Iterable[str] = STANDARD_CONFIG_FIELDS.get(sampler_name, ())
    parts: list[str] = []
    for field in fields:
        if field in HIDDEN_FIELDS:
            continue
        if field not in config:
            # Sampler doesn't expose this field in this run; skip rather than
            # render a misleading "(default)" we can't substantiate.
            continue
        parts.append(f"{field}={_format_value(config[field])}")
    if not parts:
        return "—"
    return "`" + ", ".join(parts) + "`"


# Backwards-compatible private alias; prefer ``format_config_cell``.
_format_config_cell = format_config_cell


def render_sampler_config_table(
    run_dir: Path,
    *,
    provider_label: str = "Provider",
    use_provider_icons: bool = False,
) -> str:
    """Render the per-sampler configuration table in markdown.

    ``use_provider_icons`` is False by default so the table works in both plain
    Markdown (``run.md``) and the icon-rendering Mintlify MDX. The leaderboard
    generator passes True to reuse the existing provider-icon helper.
    """
    configs = read_sampler_configs(run_dir)
    if not configs:
        return "No `sampler_config_<name>.json` files were found for this run."

    # Local import keeps the icon-aware path optional and avoids a hard cycle
    # with the renderer (which also imports this module for the standard preset).
    if use_provider_icons:
        from nimble_benchmark.leaderboard_render import format_provider
    else:

        def format_provider(name: str) -> str:
            return f"**{name}**"

    lines = [
        f"| {provider_label} | Configuration |",
        "|---|---|",
    ]
    for sampler_name, config in configs.items():
        lines.append(f"| {format_provider(sampler_name)} | {format_config_cell(sampler_name, config)} |")
    return "\n".join(lines)
