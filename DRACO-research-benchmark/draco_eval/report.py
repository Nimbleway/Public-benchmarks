"""Aggregate result JSONL files into a comparison table."""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from draco_eval.judge import AXES

_AXIS_LABEL = {
    "factual-accuracy": "Factual accuracy",
    "breadth-and-depth-of-analysis": "Breadth & depth",
    "presentation-quality": "Presentation",
    "citation-quality": "Citation quality",
}


def load(path: Path) -> list[dict]:
    """Records from a results file, keeping only the latest run of each item.

    A resumed run re-appends any item that previously errored, so the file can
    hold more than one record per item; the last one is the live result.
    """
    latest: dict[str, dict] = {}
    for line in path.read_text().splitlines():
        if line.strip():
            record = json.loads(line)
            latest[record["item_id"]] = record
    return list(latest.values())


def summarise(records: list[dict]) -> dict:
    """Roll up one system's per-item records.

    Errored items count as 0.0 in `overall` (they are real failures) but are
    excluded from latency and cost, where they would otherwise report a
    timeout as though it were a fast, cheap answer.
    """
    ok = [r for r in records if not r.get("error")]
    latencies = [r["latency_s"] for r in ok if r.get("latency_s") is not None]
    costs = [r["cost_usd"] for r in ok if r.get("cost_usd") is not None]

    return {
        "system": records[0]["system"] if records else "?",
        "items": len(records),
        "errors": len(records) - len(ok),
        "overall": statistics.fmean([r.get("overall", 0.0) for r in records]) if records else 0.0,
        "axes": {
            axis: statistics.fmean([r.get("axes", {}).get(axis, 0.0) for r in records]) if records else 0.0
            for axis in AXES
        },
        "median_latency_s": statistics.median(latencies) if latencies else None,
        "mean_cost_usd": statistics.fmean(costs) if costs else None,
        "mean_citations": statistics.fmean([r.get("n_citations", 0) for r in ok]) if ok else 0.0,
    }


def _fmt(value: float | None, spec: str, *, prefix: str = "", suffix: str = "") -> str:
    return "—" if value is None else f"{prefix}{value:{spec}}{suffix}"


def table(summaries: list[dict]) -> str:
    """Markdown comparison table, one column per system."""
    if not summaries:
        return "no results"

    rows: list[tuple[str, list[str]]] = [
        ("Items", [str(s["items"]) for s in summaries]),
        ("Errors", [str(s["errors"]) for s in summaries]),
        ("**Overall score**", [f"**{s['overall']:.3f}**" for s in summaries]),
    ]
    rows += [(_AXIS_LABEL[axis], [f"{s['axes'][axis]:.3f}" for s in summaries]) for axis in AXES]
    rows += [
        ("Median latency", [_fmt(s["median_latency_s"], ".0f", suffix="s") for s in summaries]),
        ("Mean cost / item", [_fmt(s["mean_cost_usd"], ".3f", prefix="$") for s in summaries]),
        ("Mean citations", [f"{s['mean_citations']:.1f}" for s in summaries]),
    ]

    header = "| Metric | " + " | ".join(s["system"] for s in summaries) + " |"
    sep = "| :--- | " + " | ".join([":---:"] * len(summaries)) + " |"
    body = "\n".join(f"| {label} | " + " | ".join(cells) + " |" for label, cells in rows)

    note = (
        "\nCost is not like-for-like across vendors: some report billed price per run, "
        "some publish a list price per 1,000 requests, and some report nothing (—). "
        "Read each adapter before quoting the cost row."
    )
    return f"{header}\n{sep}\n{body}\n{note}"
