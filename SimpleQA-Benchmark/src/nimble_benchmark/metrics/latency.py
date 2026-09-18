"""Latency parsing and aggregation.

The Nimble search server emits a ``Server-Timing`` response header attributing
wall-clock time to the discrete pipeline stages inside ``/answer``
(``plan;dur=..., search;dur=..., synthesis;dur=..., total;dur=...``).
:func:`parse_server_timing` returns every stage as a dict; the legacy
:func:`parse_server_timing_total_dur` kept for the existing
``internal_response_time_ms`` column (server-side total only).

Stage names known to the eval are listed in :data:`KNOWN_SERVER_TIMING_STAGES`
so the analyzer can aggregate consistent per-stage columns into the summary
CSV regardless of which stages happened to be present in any individual row.
A new stage (e.g. ``rerank``) added on the server will land in the raw CSV
automatically; surfacing it in :file:`run.md` requires extending this list
and the report-side consumer.
"""

from __future__ import annotations

import pandas as pd

# All non-total stages ``aggregate_latency`` will roll up into the summary
# CSV. Order matters: ``report.py`` renders the ``## Latency by stage``
# table in this column order so a reader scans plan -> search -> synthesis
# top-down. ``total`` is handled separately as ``internal_response_time_ms``.
KNOWN_SERVER_TIMING_STAGES: tuple[str, ...] = ("plan", "search", "synthesis")


def _split_unquoted(value: str, delim: str) -> list[str]:
    """Split ``value`` on ``delim``, ignoring delimiters inside quoted strings.

    Server-Timing per the W3C spec (and RFC 7230 token / quoted-string rules)
    allows ``desc="payload, with comma"`` — a naive ``str.split(',')`` would
    fracture such entries. We walk the string tracking single- and
    double-quote state so a properly quoted descriptor survives intact.
    """
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    for char in value:
        if quote is not None:
            buf.append(char)
            if char == quote:
                quote = None
            continue
        if char in ('"', "'"):
            quote = char
            buf.append(char)
            continue
        if char == delim:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(char)
    if buf:
        parts.append("".join(buf))
    return parts


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def parse_server_timing(value: str | None) -> dict[str, float]:
    """Parse a ``Server-Timing`` header into ``{metric_name: ms}``.

    Returns an empty dict when the header is absent or contains no
    ``dur=<ms>`` entries. Metric names are case-sensitive (matching the
    server's emission); duplicates resolve to the last entry seen.

    Implementation notes:
    - Splits on top-level commas / semicolons only; commas inside a quoted
      ``desc="..."`` are preserved per the W3C Server-Timing spec.
    - Entries without ``dur=`` are silently skipped (e.g. ``plan;desc="ok"``).
    - Unparseable ``dur`` values are silently skipped (no traceback for a
      one-off malformed header from a flaky upstream).
    """
    if not value:
        return {}
    out: dict[str, float] = {}
    for entry in _split_unquoted(value, ","):
        params = _split_unquoted(entry, ";")
        if not params:
            continue
        name = params[0].strip()
        if not name:
            continue
        for param in params[1:]:
            key, sep, raw_value = param.partition("=")
            if not sep or key.strip().lower() != "dur":
                continue
            try:
                out[name] = float(_unquote(raw_value))
            except ValueError:
                continue
            break
    return out


def parse_server_timing_total_dur(value: str | None) -> float | None:
    """Backward-compat helper: extract just the ``total;dur=...`` value."""
    return parse_server_timing(value).get("total")


def aggregate_latency(df: pd.DataFrame) -> dict[str, float | None]:
    """Aggregate per-row latency columns into a fixed set of summary stats.

    Computes mean / p50 / p95 for three totals (always emitted):
    ``provider_response_time_ms`` (the upstream round trip, and the column the
    leaderboard ranks on), ``request_response_time_ms`` (the harness wall
    clock, which also carries our limiter queue wait and retry backoff), and
    ``internal_response_time_ms`` (the server's own ``total;dur``). Plus the
    per-stage columns named
    ``stage_<name>_ms`` for every name in :data:`KNOWN_SERVER_TIMING_STAGES`
    (emitted as ``None`` when the sampler did not return a Server-Timing
    header for that stage).
    """
    out: dict[str, float | None] = {}
    columns = ["provider_response_time_ms", "request_response_time_ms", "internal_response_time_ms"]
    columns.extend(f"stage_{stage}_ms" for stage in KNOWN_SERVER_TIMING_STAGES)
    for column in columns:
        if column not in df:
            out[f"{column}_p50"] = None
            out[f"{column}_p95"] = None
            out[f"{column}_mean"] = None
            continue
        series = pd.to_numeric(df[column], errors="coerce").dropna()
        if series.empty:
            out[f"{column}_p50"] = None
            out[f"{column}_p95"] = None
            out[f"{column}_mean"] = None
            continue
        out[f"{column}_p50"] = round(float(series.quantile(0.50)), 2)
        out[f"{column}_p95"] = round(float(series.quantile(0.95)), 2)
        out[f"{column}_mean"] = round(float(series.mean()), 2)
    return out
