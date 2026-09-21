"""Rendering layer for the leaderboard.

Turns structured :class:`~nimble_benchmark.leaderboard_data.LeaderboardData`
into Mintlify-flavored MDX fragments (and a couple of plain-Markdown
fragments). Pure functions: no I/O, no datetime, no env vars.

Mintlify quirks that are intentional here
-----------------------------------------
* **JSX-style attributes.** Mintlify's current MDX parser evaluates inline
  ``<div>`` / ``<table>`` / ``<img>`` as JSX, so we emit ``className=`` and
  ``style={{...}}`` object literals — *not* HTML ``class=`` / ``style="..."``
  strings, which React rejects at render time with "The ``style`` prop
  expects a mapping from style properties to values". CSS keys are
  camelCased (``maxWidth``, ``borderCollapse``, ``textAlign``, …).
* **Raw ``<table>`` instead of Markdown pipe tables.** Mintlify wraps every
  Markdown-rendered ``<table>`` in a ``data-table-wrapper`` that forces
  ``min-width:150px`` per cell, which makes any 6+ column leaderboard overflow
  on common viewports. Emitting an inline ``<table>`` inside an
  ``overflowX: auto`` wrapper bypasses the wrapper entirely and lets wide
  content scroll horizontally on mobile without the per-cell floor.
* **No ``&`` escaping needed.** In JSX-string attribute values an ampersand
  is literal, so favicon URLs like
  ``https://www.google.com/s2/favicons?domain=exa.ai&sz=64`` go through
  untouched (escaping them to ``&amp;`` would land the literal sequence in
  the fetched URL and break the favicon).
* **``dark:invert`` Tailwind class.** Mintlify keeps Tailwind's dark variants
  in its global stylesheet, so combining the inline ``style={{...}}`` (for
  sizing) with ``className="dark:invert"`` (for dark-mode tone) is safe.
"""

from __future__ import annotations

import re
from typing import Any

from nimble_benchmark.leaderboard_data import HeadlineRow, SourceArtifact
from nimble_benchmark.price_list import (
    PRICE_LIST_AS_OF,
    format_cost,
    search_depth_from_configs,
)

# Provider display names are surface-qualified ("Nimble Search" rather than
# bare "Nimble") so a reader scanning the table always knows which product
# surface a row measured, and the lane-variant rows (depth, type, mode) are
# further qualified in parentheses because they sit next to each other.
PROVIDER_INFO: dict[str, dict[str, Any]] = {
    # Both Exa search lanes are type-qualified: they sit next to each other in
    # the table, so an unqualified "Exa Search" row would read as "Exa in
    # general" rather than the auto tier specifically.
    "exa_search_auto": {
        "name": "Exa Search (Auto)",
        "icon": "https://www.google.com/s2/favicons?domain=exa.ai&sz=64",
    },
    "exa_search_fast": {
        "name": "Exa Search (Fast)",
        "icon": "https://www.google.com/s2/favicons?domain=exa.ai&sz=64",
    },
    # Both Parallel lanes are mode-qualified for the same reason.
    "parallel_search_basic": {
        "name": "Parallel Search (Basic)",
        "icon": "https://www.google.com/s2/favicons?domain=parallel.ai&sz=64",
    },
    "parallel_search_turbo": {
        "name": "Parallel Search (Turbo)",
        "icon": "https://www.google.com/s2/favicons?domain=parallel.ai&sz=64",
    },
    "brave_search": {
        "name": "Brave Search",
        "icon": "https://www.google.com/s2/favicons?domain=brave.com&sz=64",
    },
    "firecrawl_search": {
        "name": "Firecrawl Search",
        "icon": "https://www.google.com/s2/favicons?domain=firecrawl.dev&sz=64",
    },
    "nimble_search": {
        "name": "Nimble Search",
        "icon": "https://www.google.com/s2/favicons?domain=nimbleway.com&sz=64",
    },
    "tavily_search_basic": {
        "name": "Tavily Search (Basic)",
        "icon": "https://www.google.com/s2/favicons?domain=tavily.com&sz=64",
    },
    "tavily_search_fast": {
        "name": "Tavily Search (Fast)",
        "icon": "https://www.google.com/s2/favicons?domain=tavily.com&sz=64",
    },
}


# Style tokens as Python dicts. Kept module-level so the iteration env can
# eyeball them in one place when tuning the table look. Keys are camelCased
# to match React's CSS-property contract.
#
# ``width: 100%`` fills the available content area so the headline table no
# longer floats in a corner with empty space to its right. The horizontal
# scroll wrapper (``WRAPPER_STYLE``) then handles overflow on the wider
# breakdown tables — its scrollbar is explicit (``scrollbarWidth: thin``)
# rather than relying on macOS overlay scrollbars that only appear on hover.
TABLE_OUTER_STYLE: dict[str, str] = {
    "width": "100%",
    "borderCollapse": "collapse",
    "tableLayout": "auto",
    "fontSize": "13px",
    "lineHeight": "1.4",
    "margin": "0.75em 0",
}

# Headers wrap by default so multi-word columns like "Science and technology"
# stack vertically instead of dragging the whole table off the viewport. Data
# cells keep ``nowrap`` because their values (``$0.0435``, ``18.3k/1.1k tok``,
# ``96.0%``) read as a single token.
TABLE_TH_STYLE_BASE: dict[str, str] = {
    "padding": "6px 10px",
    "borderBottom": "1px solid rgba(127,127,127,0.3)",
    "fontWeight": "600",
    "whiteSpace": "normal",
    "verticalAlign": "bottom",
}

TABLE_TD_STYLE_BASE: dict[str, str] = {
    "padding": "6px 10px",
    "borderBottom": "1px solid rgba(127,127,127,0.12)",
    "whiteSpace": "nowrap",
    "verticalAlign": "middle",
}

# Mintlify's global ``img`` rule sets ``display: block; margin: 0 auto`` to
# auto-center content images, which would push the favicon onto its own line
# above the provider name. We sidestep that by rendering the badge as an
# ``inline-flex`` ``<span>`` so the icon + name share a baseline regardless of
# the global rule.
PROVIDER_BADGE_STYLE: dict[str, str] = {
    "display": "inline-flex",
    "alignItems": "center",
    "gap": "6px",
}

PROVIDER_ICON_STYLE: dict[str, str] = {
    "width": "18px",
    "height": "18px",
    "borderRadius": "3px",
    "flexShrink": "0",
    "margin": "0",
}

# Scroll wrapper: visible-thin scrollbar so a horizontally scrollable table is
# discoverable on the dark Mintlify theme (overlay scrollbars disappear on
# macOS until you hover).
WRAPPER_STYLE: dict[str, str] = {
    "overflowX": "auto",
    "scrollbarWidth": "thin",
    "WebkitOverflowScrolling": "touch",
}


def jsx_style(props: dict[str, str]) -> str:
    """Render a CSS-prop dict as a JSX ``style={{...}}`` attribute literal.

    Keys are emitted verbatim (we keep camelCase at the source), values are
    JSON-encoded so an embedded quote can't break out of the JSX expression.
    """
    parts = [f'"{key}": "{_escape_jsx_string(value)}"' for key, value in props.items()]
    return "style={{" + ", ".join(parts) + "}}"


def _escape_jsx_string(value: str) -> str:
    """Minimal JSX-string escape: handle the two characters that would break
    out of a double-quoted attribute value."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def format_provider(
    provider_id: str,
    *,
    is_baseline: bool = False,
    excluded_reason: str | None = None,
) -> str:
    """Render an icon + bold provider name as a single inline-flex badge.

    Wrapping the ``<img>`` + ``<strong>`` in a ``<span>`` with
    ``display: inline-flex; align-items: center`` keeps the favicon next to
    the provider name even when Mintlify's global stylesheet sets
    ``img { display: block }`` to auto-center content images. Emits proper
    JSX (``className=``, ``style={{...}}``) so Mintlify's MDX parser doesn't
    reject the resulting cell at render time. Markdown is not processed
    inside JSX so we use ``<strong>`` rather than ``**…**``.

    When ``is_baseline`` is true, an inline ``◯`` marker is appended after the
    provider name so the row is visually identifiable as the significance
    baseline without forcing a separate column.

    When ``excluded_reason`` is non-empty, a ⚠ marker is appended (with the
    reason as the ``title`` attribute) so a reader scanning the table
    immediately sees that the row's metric numbers are based on a sample
    truncated by the reliability gate. Pairs with the standalone "Excluded
    lanes" section in run.md / report.py.
    """
    info = PROVIDER_INFO.get(provider_id)
    baseline_marker = ' <span title="Significance baseline">◯</span>' if is_baseline else ""
    excluded_marker = (
        f' <span title="Excluded by reliability gate: {excluded_reason}">⚠</span>' if excluded_reason else ""
    )
    if not info:
        return f"<strong>{provider_id}</strong>{baseline_marker}{excluded_marker}"
    icon = info["icon"]
    name = info["name"]
    badge_style = jsx_style(PROVIDER_BADGE_STYLE)
    icon_style = jsx_style(PROVIDER_ICON_STYLE)
    class_attr = ' className="dark:invert"' if info.get("invert_dark") else ""
    return (
        f"<span {badge_style}>"
        f'<img src="{icon}" alt="{name}"{class_attr} {icon_style} />'
        f"<strong>{name}</strong>"
        f"</span>{baseline_marker}{excluded_marker}"
    )


# --- table primitives -----------------------------------------------------


def _th(text: str, align: str = "left") -> str:
    style = jsx_style({**TABLE_TH_STYLE_BASE, "textAlign": align})
    return f"<th {style}>{text}</th>"


def _td(text: str, align: str = "left") -> str:
    style = jsx_style({**TABLE_TD_STYLE_BASE, "textAlign": align})
    return f"<td {style}>{text}</td>"


def html_table(headers: list[tuple[str, str]], rows: list[list[tuple[str, str]]]) -> str:
    """Emit a compact JSX table.

    ``headers`` and each row entry are ``(value, align)`` tuples; ``align``
    is ``left`` / ``right`` / ``center``. The outer wrapper has
    ``overflowX: auto`` so wide tables scroll horizontally on narrow
    viewports without dragging the column header off-screen.
    """
    head = "".join(_th(text, align) for text, align in headers)
    body_rows = ["<tr>" + "".join(_td(text, align) for text, align in row) + "</tr>" for row in rows]
    body = "\n".join(body_rows)
    wrapper_style = jsx_style(WRAPPER_STYLE)
    table_style = jsx_style(TABLE_OUTER_STYLE)
    return (
        f"<div {wrapper_style}>\n"
        f"<table {table_style}>\n"
        f"<thead><tr>{head}</tr></thead>\n"
        f"<tbody>\n{body}\n</tbody>\n"
        "</table>\n"
        "</div>"
    )


def inline_code_to_html(text: str) -> str:
    """Promote Markdown inline-code spans (``\\`x\\```) to ``<code>x</code>`` so
    they render correctly inside JSX tables (MDX does not process Markdown
    inside JSX subtrees).

    Curly braces inside the code body are escaped to HTML entities so MDX's
    acorn pass does not try to parse them as JSX expressions. A sampler
    config cell like ``text={'maxCharacters': 20000}`` (Python dict repr)
    is a valid JS *block statement* opener, not an object literal, and
    acorn rejects the unexpected ``:`` -- that broke the n=500 leaderboard
    HTML render in run 26539110340.
    """

    def _escape(match: re.Match[str]) -> str:
        body = match.group(1).replace("{", "&#123;").replace("}", "&#125;")
        return f"<code>{body}</code>"

    return re.sub(r"`([^`]+)`", _escape, text)


# --- section renderers ----------------------------------------------------


def _sort_rows(rows: list[HeadlineRow], *, primary_metric: str) -> list[HeadlineRow]:
    """Sort rows in-place-friendly by the table's headline metric desc.

    Rows with missing metric values sort last so the table's top is always
    populated. Ties broken by ``provider_id`` then ``answer_source`` for
    stable rendering across runs.
    """
    return sorted(
        rows,
        key=lambda row: (
            getattr(row, primary_metric) is None,
            -(getattr(row, primary_metric) or 0.0),
            row.provider_id,
            row.answer_source,
        ),
    )


def render_headline_table(
    rows: list[HeadlineRow],
    *,
    sampler_configs: dict[str, dict[str, Any]] | None = None,
) -> str:
    """The leaderboard's single table. Headline metric: Accuracy.

    Every lane in the run lives here, ranked on Accuracy.

    Deliberately narrow: Provider, Accuracy, Latency, Cost, n.

    ``Cost`` is what this run's requests to the lane would cost at the vendor's
    published rate: the lane's static per-query list price from
    :mod:`nimble_benchmark.price_list` times its request count
    (``problem_count``, the ``n`` denominator). It therefore scales with
    ``--limit`` -- two runs at different sizes are not cost-comparable, and the
    unit price is the thing to quote when they need to be. A lane whose tier
    has no published per-query price renders ``—``.
    ``sampler_configs`` is optional and used only to price the
    depth-configurable ``nimble_search`` lane at the tier it actually ran at.

    The retired columns and where their data still lives --

    * ``System`` (``response_kind · answer_source``), ``NDCG@10``,
      ``Recall@10``: every value is still in ``analyzed_results.csv`` and
      ``run.json``. Every shipped lane is ``synth`` today, so Accuracy is
      uniformly sourced; that stops being true the moment an ``api`` lane
      joins the table, and ``answer_source`` in the CSV is what says which.
    * ``Fail rate``: the ``n`` column still renders ``ok/total`` whenever a
      lane had failures, so a partially-failed lane is still visible; the
      exact rate is in the CSV.
    * ``Usage`` (input/output token mean): only answer lanes report it, so
      every ``/search`` provider rendered ``—``.

    Returns an empty string when ``rows`` is empty so the surrounding template
    can omit the section heading entirely.
    """
    if not rows:
        return ""
    headers: list[tuple[str, str]] = [
        ("Provider", "left"),
        ("Accuracy", "right"),
        ("Latency", "right"),
        ("Cost", "right"),
        ("n", "right"),
    ]
    body_rows: list[list[tuple[str, str]]] = []
    for row in _sort_rows(rows, primary_metric="accuracy_score"):
        body_rows.append(
            [
                (
                    format_provider(
                        row.provider_id,
                        is_baseline=row.is_baseline,
                        excluded_reason=row.excluded_reason,
                    ),
                    "left",
                ),
                (
                    format_percent_with_marker(row.accuracy_score, significant=row.accuracy_score_significant),
                    "right",
                ),
                (format_latency(row.provider_response_time_ms_p50), "right"),
                (
                    format_cost(
                        row.provider_id,
                        row.problem_count,
                        search_depth=search_depth_from_configs(row.provider_id, sampler_configs),
                    ),
                    "right",
                ),
                (_format_n_column(row), "right"),
            ]
        )
    return html_table(headers, body_rows)


def _format_n_column(row: HeadlineRow) -> str:
    """Render the ``n`` cell as ``ok/total`` when the analyzer emitted the
    reliability columns. Falls back to the historical bare-total form for
    fully-healthy or legacy runs so the column stays narrow."""
    total = row.problem_count
    successful = row.successful_problem_count
    if successful is None or (row.failure_count or 0) == 0:
        return format_count(total)
    return f"{format_count(successful)}/{format_count(total)}"


def render_cost_footnote() -> str:
    """One-line provenance note for the Cost column.

    The column is an estimate built from a static rate card rather than a
    measurement, so it always carries its own caveat: what it multiplies, that
    it scales with the run size, the snapshot date of the prices, and that
    ``—`` means "no per-query list price" rather than missing data.
    """
    return (
        '<p style={{"fontSize": "12px", "marginTop": "0.25em"}}>'
        "<em>Cost</em> is this run's spend at list price: the lane's request count "
        "(<code>n</code>'s total) times the vendor's published per-query rate for that product tier "
        f"(price snapshot of {PRICE_LIST_AS_OF}). It scales with the run size, so it is comparable "
        "down the column but not across runs of different <code>n</code>. Retry attempts are not "
        "counted and failed requests are, and the figure excludes extra-result, page-summary, and "
        "volume-discount pricing -- it is an estimate, not an invoice. <em>—</em> means the lane has "
        "no per-query list price (the native-LLM answer lane is billed on tokens)."
        "</p>"
    )


def render_significance_footnote(*, baseline_system: str | None, alpha: float | None) -> str:
    """One-line marker legend rendered under the leaderboard table.

    Returns an empty string when there's no significance data so the section
    collapses cleanly for single-system runs.
    """
    if not baseline_system:
        return ""
    alpha_text = f"p < {alpha:.2f}" if alpha is not None else "Bonferroni-corrected p"
    return (
        '<p style={{"fontSize": "12px", "marginTop": "0.25em"}}>'
        f"◯ <em>significance baseline</em>: <code>{baseline_system}</code>. "
        f"★ <em>significantly better than the baseline</em> on this metric "
        f"({alpha_text}, paired Student's t-test, Bonferroni-corrected within metric)."
        "</p>"
    )


def render_system_significance_details(rows: list[HeadlineRow], *, heading: str = "Per-system significance") -> str:
    """System-level breakdown table for the leaderboard.

    Each ``HeadlineRow`` now corresponds to exactly one system, so this
    renderer just emits one table row per system that has significance data
    (i.e. is not the baseline and has at least one paired comparison).
    Returns nothing when no system has data.

    Always-visible rather than ``<details>``-collapsed because Mintlify's MDX
    parser silently drops ``<details>`` blocks that wrap JSX-styled tables
    (the kind we use everywhere else for layout reasons).

    The dedicated ``sig`` ★/— column was retired -- the same ★ marker is
    already stamped on the headline table next to the metric value, so
    duplicating it here just widened the table. The Δ and Bonferroni p columns
    remain because they're the actual signal a reader needs to decide whether
    a headline ★ is a real win or a marginal one.

    This tracks whatever the headline table shows, so it covers Accuracy only.
    The NDCG@10 / Recall@10 Δ + p columns went when those metrics left the
    headline; they're still in ``significance.csv`` for anyone drilling in.
    """
    detail_rows = [row for row in rows if row.system_significance]
    if not detail_rows:
        return ""

    headers: list[tuple[str, str]] = [
        ("System", "left"),
        ("Accuracy Δ", "right"),
        ("Accuracy p (Bonf.)", "right"),
    ]
    body_rows: list[list[tuple[str, str]]] = []
    for row in detail_rows:
        system = row.system_significance
        assert system is not None  # filtered above; helps the type checker
        body_rows.append(
            [
                (f"<code>{system.system}</code>", "left"),
                (format_delta(system.accuracy_score_delta), "right"),
                (format_p_value(system.accuracy_score_p_bonferroni), "right"),
            ]
        )
    return f"## {heading}\n\n{html_table(headers, body_rows)}"


def render_leaderboard_section(
    *,
    heading: str,
    rows: list[HeadlineRow],
    baseline_system: str | None,
    alpha: float | None,
    significance_heading: str,
    sampler_configs: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Compose the leaderboard table section (heading + table + footnotes + details).

    Returns an empty string when ``rows`` is empty so the surrounding template
    collapses cleanly. Used by both the local Markdown and public MDX
    renderers via ``leaderboard.py``.
    """
    if not rows:
        return ""
    table = render_headline_table(rows, sampler_configs=sampler_configs)
    if not table:
        return ""
    footnote = render_significance_footnote(baseline_system=baseline_system, alpha=alpha)
    details = render_system_significance_details(rows, heading=significance_heading)
    parts = [f"## {heading}", "", table, "", render_cost_footnote()]
    if footnote:
        parts.extend(["", footnote])
    if details:
        parts.extend(["", details])
    return "\n".join(parts) + "\n"


def format_percent_with_marker(
    value: float | str | None,
    *,
    significant: bool = False,
) -> str:
    """Percent formatter that appends ★ when the cell is significant.

    The star is space-separated from the number so layouts can still measure
    cell widths. Accuracy is the only headline metric left, so this is the
    only marker-stamping formatter the table needs; the NDCG/Recall decimal
    variant went with those columns.
    """
    text = format_percent(value)
    return f"{text} ★" if significant and text != "—" else text


def format_delta(value: float | str | None) -> str:
    """Signed three-decimal delta — used by the per-system breakdown column."""
    number = _to_float(value)
    if number is None:
        return "—"
    return f"{number:+.3f}"


def format_p_value(value: float | str | None) -> str:
    """Render a p-value compactly: scientific notation below 1e-4, four-decimal
    otherwise. Empty p-values render as ``—`` so identical-vector rows (NaN)
    don't show ``nan`` in published docs."""
    number = _to_float(value)
    if number is None:
        return "—"
    if number < 1e-4:
        return f"{number:.2e}"
    return f"{number:.4f}"


def render_sampler_config_table(configs: dict[str, dict[str, Any]]) -> str:
    """HTML-table version of the Sampler Configuration block.

    Uses the curated standard preset from
    :mod:`nimble_benchmark.sampler_config` (single source of truth for the
    field allowlist) and pipes the rows through :func:`html_table` so this
    section inherits the same compact, content-width styling as the headline
    table.
    """
    # Local import to avoid a cycle: sampler_config also imports the provider
    # formatter for its plain-Markdown helper.
    from nimble_benchmark.sampler_config import format_config_cell

    if not configs:
        return "<p>No <code>sampler_config_&lt;name&gt;.json</code> files were found for this run.</p>"
    headers: list[tuple[str, str]] = [("Provider", "left"), ("Configuration", "left")]
    rows: list[list[tuple[str, str]]] = []
    for sampler_name, config in configs.items():
        rows.append(
            [
                (format_provider(sampler_name), "left"),
                (inline_code_to_html(format_config_cell(sampler_name, config)), "left"),
            ]
        )
    return html_table(headers, rows)


def render_source_artifacts_table(artifacts: list[SourceArtifact]) -> str:
    """Plain Markdown table — surfaced under a section heading and read as
    prose, not as a styled component, so we don't pay the HTML-table tax.

    When an artifact carries a ``url`` (CI runs supply
    ``LEADERBOARD_WORKFLOW_RUN_URL`` so the row points at the workflow
    run's Artifacts panel), the path is rendered as a Markdown link
    around inline code so the viewer can click straight through to the
    download. Local runs omit the URL and the path stays plain inline
    code.
    """
    lines = ["| Artifact | Path |", "| --- | --- |"]
    for artifact in artifacts:
        if artifact.url:
            lines.append(f"| {artifact.name} | [`{artifact.path}`]({artifact.url}) |")
        else:
            lines.append(f"| {artifact.name} | `{artifact.path}` |")
    return "\n".join(lines)


# --- value formatters -----------------------------------------------------


def format_percent(value: float | str | None) -> str:
    number = _to_float(value)
    if number is None:
        return "—"
    return f"{number * 100:.1f}%"


def format_latency(value: float | str | None) -> str:
    """Compact latency: sub-second in ms, ≥1 s in seconds with one decimal.

    Keeps the leaderboard headline narrow so the table fits common viewports
    without horizontal scroll.
    """
    number = _to_float(value)
    if number is None:
        return "—"
    if number < 1000:
        return f"{number:.0f}ms"
    return f"{number / 1000:.1f}s"


def format_count(value: float | str | None) -> str:
    number = _to_float(value)
    if number is None:
        return "—"
    return f"{number:,.0f}"


def _to_float(value: float | str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
