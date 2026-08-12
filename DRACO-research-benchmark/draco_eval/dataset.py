"""Load the DRACO benchmark.

DRACO is a third-party benchmark (Perplexity, 2026) distributed on HuggingFace
as `maxkru92/draco`. This repo does NOT vendor the data — it is fetched at run
time so you always evaluate against the upstream version, and so the benchmark
authors stay the single source of truth for their own dataset.

Upstream columns:
  problem  the research query
  answer   JSON-encoded rubric: sections -> criteria -> {id, weight, requirement}
  domain   task domain (finance, law, medicine, ...)

Reference: https://arxiv.org/abs/2602.11685
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from draco_eval.judge import Rubric, parse_rubric

HF_DATASET = "maxkru92/draco"
HF_SPLIT = "test"


@dataclass(frozen=True)
class Item:
    id: str
    question: str
    domain: str
    rubric: Rubric


def load(*, cache: Path | None = None, limit: int | None = None, domains: list[str] | None = None) -> list[Item]:
    """Fetch DRACO, optionally via a local JSONL cache.

    The cache exists so a long multi-system comparison is not at the mercy of
    HuggingFace availability mid-run, and so every system in a comparison is
    provably scored against byte-identical rubrics.
    """
    rows = _read_cache(cache) if cache and cache.exists() else _fetch()
    if cache and not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text("\n".join(json.dumps(r) for r in rows))

    items = [
        Item(
            id=f"draco_{i:03d}",
            question=row["question"],
            domain=row.get("domain", "general"),
            rubric=parse_rubric(row["rubric"]),
        )
        for i, row in enumerate(rows)
    ]
    if domains:
        wanted = {d.lower() for d in domains}
        items = [it for it in items if it.domain.lower() in wanted]
    return items[:limit] if limit else items


def _read_cache(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _fetch() -> list[dict]:
    try:
        from datasets import load_dataset
    except ImportError as e:
        raise RuntimeError("DRACO is fetched from HuggingFace. Install it with: pip install 'draco-eval[data]'") from e

    ds = load_dataset(HF_DATASET, split=HF_SPLIT)
    rows = [
        {
            "question": str(row.get("problem", "")).strip(),
            "rubric": str(row.get("answer", "")),
            "domain": str(row.get("domain", "general")),
        }
        for row in ds
    ]
    rows = [r for r in rows if r["question"] and r["rubric"]]
    if not rows:
        raise RuntimeError(f"{HF_DATASET}:{HF_SPLIT} yielded no usable rows — did the upstream schema change?")
    return rows
