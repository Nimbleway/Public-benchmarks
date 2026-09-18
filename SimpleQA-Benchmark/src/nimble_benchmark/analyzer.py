"""Aggregate raw CSVs into the run summary table.

One row per ``(provider, response_kind, answer_source)`` lane, all in a single
``analyzed_results.csv``. ``/search`` lanes and the native-LLM answer lane
(none shipped today) share the file and share one leaderboard table, so
significance is computed once against one baseline and written to a
single ``significance.csv``. The per-kind split files
(``analyzed_{answer,search}_results.csv`` / ``significance_{answer,search}.csv``)
are a hard cut and are never written.

The ``response_kind`` / ``answer_source`` columns stay on every row: they are
what tells a reader whether a lane's accuracy was graded off the model's own
answer (``api``) or an eval-side synthesis over the ranked chunks (``synth``).

Reliability gate: each lane is evaluated against two thresholds before appearing
in the leaderboard. A lane with failure_rate > DEFAULT_MAX_FAILURE_RATE is excluded
(metric mean over a handful of survivors is noise). A lane with successful_problems
< DEFAULT_MIN_SUCCESSFUL_PROBLEMS is excluded (too few rows for a meaningful
comparison). Excluded lanes still appear in the analyzed CSV with full diagnostics;
the renderer surfaces a ⚠ marker. See DEFAULT_MIN_SUCCESSFUL_PROBLEMS and
DEFAULT_MAX_FAILURE_RATE for the CLI defaults.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Final

import pandas as pd

from nimble_benchmark.constants import ACCURACY_CORRECT_RESULT, ACCURACY_DENOMINATOR_RESULTS
from nimble_benchmark.datasets import DATASETS, parse_gold_urls_from_metadata
from nimble_benchmark.metrics.latency import aggregate_latency
from nimble_benchmark.metrics.significance import compute_significance

CSV_PATTERN = re.compile(r"dataset_(?P<dataset>.+?)_raw_results_(?P<sampler>.+)\.csv$")

SUMMARY_FILENAME = "analyzed_results.csv"
SIGNIFICANCE_FILENAME = "significance.csv"

# CLI defaults for the reliability gate. ``aggregate_run`` defaults to 0 / 1.0
# so library callers and unit tests with small fixtures keep all lanes; the strict
# gate is only active on the CLI surface. See the module docstring for semantics.
DEFAULT_MIN_SUCCESSFUL_PROBLEMS: Final[int] = 10
DEFAULT_MAX_FAILURE_RATE: Final[float] = 0.95

EXCLUSION_REASON_BELOW_MIN_GATE = "below_min_successful_problems"
EXCLUSION_REASON_EXCESS_FAILURES = "excess_failure_rate"

# Per-row LLM/URL retrieval columns averaged over the successful subset only.
_RETRIEVAL_METRIC_COLUMNS: tuple[str, ...] = (
    "ndcg_at_5",
    "ndcg_at_10",
    "recall_at_5",
    "recall_at_10",
    "mrr",
    "hit_at_5",
    "hit_at_10",
    "recall_at_5_llm",
    "recall_at_10_llm",
    "hit_at_5_llm",
    "hit_at_10_llm",
)

logger = logging.getLogger(__name__)


def _ok_mask(group: pd.DataFrame) -> pd.Series:
    """Return a bool mask selecting rows whose request succeeded.

    A row is "ok" when ``request_status == "ok"`` (the column the sampler
    base stamps in :mod:`samplers.base`). When the column is absent — e.g. a
    legacy CSV written before the column existed — every row is treated as
    successful so historical artifacts keep aggregating the same way.
    """
    if "request_status" not in group:
        return pd.Series(True, index=group.index)
    return group["request_status"].astype(str) == "ok"


def _classify_exclusion(
    *,
    total_problems: int,
    successful_problems: int,
    failure_rate: float,
    min_successful_problems: int,
    max_failure_rate: float,
) -> str | None:
    """Decide whether a lane is fit for the leaderboard.

    Returns ``None`` when the lane is healthy. The failure-rate gate fires
    before the min-success gate so a totally-broken lane is labelled with
    the more specific reason instead of just "too few successes". ``total``
    being zero is treated as the failure-rate path (everything failed).
    """
    if total_problems > 0 and failure_rate > max_failure_rate:
        return EXCLUSION_REASON_EXCESS_FAILURES
    if successful_problems < min_successful_problems:
        return EXCLUSION_REASON_BELOW_MIN_GATE
    return None


def aggregate_run(
    *,
    run_dir: Path,
    dataset_name: str,
    significance_baseline: str | None = None,
    min_successful_problems: int = 0,
    max_failure_rate: float = DEFAULT_MAX_FAILURE_RATE,
) -> pd.DataFrame:
    rows = []
    raw_frames = []
    for csv_path in sorted(run_dir.glob("dataset_*_raw_results_*.csv")):
        match = CSV_PATTERN.search(csv_path.name)
        if not match or match.group("dataset") != dataset_name:
            continue
        df = pd.read_csv(csv_path)
        df = df.copy()
        df["provider"] = match.group("sampler")
        raw_frames.append(df)
        group_columns = [column for column in ("response_kind", "answer_source_used") if column in df]
        grouped = df.groupby(group_columns, dropna=False) if group_columns else [((), df)]
        for key, group in grouped:
            status = group.get("request_status", pd.Series(dtype=str))
            ok_mask = _ok_mask(group)
            ok_group = group[ok_mask]
            total_problems = int(group["query"].nunique()) if "query" in group else int(len(group))
            successful_problems = int(ok_group["query"].nunique()) if "query" in ok_group else int(len(ok_group))
            failure_count = max(0, total_problems - successful_problems)
            failure_rate = round(failure_count / total_problems, 4) if total_problems > 0 else 0.0
            excluded_reason = _classify_exclusion(
                total_problems=total_problems,
                successful_problems=successful_problems,
                failure_rate=failure_rate,
                min_successful_problems=min_successful_problems,
                max_failure_rate=max_failure_rate,
            )
            key_values = key if isinstance(key, tuple) else (key,)
            key_map = dict(zip(group_columns, key_values, strict=False))
            row = {
                "provider": match.group("sampler"),
                "dataset": dataset_name,
                "response_kind": key_map.get("response_kind"),
                "answer_source": key_map.get("answer_source_used"),
                # ``problem_count`` keeps its historical meaning -- total
                # unique queries the lane attempted -- so existing
                # leaderboard renderers stay correct. The ``successful_*``
                # and ``failure_*`` columns add the new reliability axis.
                "problem_count": total_problems,
                "successful_problem_count": successful_problems,
                "failure_count": failure_count,
                "failure_rate": failure_rate,
                "excluded_reason": excluded_reason,
                "accuracy_score": _aggregate_accuracy(group),
                "validation_reject_count": int((status == "validation_reject").sum()),
                "failed_after_retries_count": int((status == "failed_after_retries").sum()),
                "citation_parse_error_count": int((status == "citation_parse_error").sum()),
            }
            # Usage / latency / retrieval metrics are computed over the
            # successful subset only: a failed row's chunks list is empty,
            # so leaving it in the mean would silently shrink every
            # partially-failed lane's NDCG / Recall toward zero. When every
            # row failed (``ok_group`` empty) the metrics fall through to
            # ``None`` via the same path the absent-column case takes.
            metric_source = ok_group if not ok_group.empty else group.iloc[0:0]
            for metric in ["usage_input_tokens", "usage_output_tokens"]:
                row.update(_aggregate_usage(metric_source, metric))
            for metric in _RETRIEVAL_METRIC_COLUMNS:
                if metric not in metric_source:
                    row[metric] = None
                    continue
                series = pd.to_numeric(metric_source[metric], errors="coerce").dropna()
                row[metric] = round(float(series.mean()), 4) if not series.empty else None
            row.update(aggregate_latency(metric_source))
            rows.append(row)
    out = pd.DataFrame(rows)
    excluded_systems = _excluded_system_keys(out)
    out.to_csv(run_dir / SUMMARY_FILENAME, index=False)
    _write_breakdown(run_dir=run_dir, raw_frames=raw_frames, dimension="answer_type")
    _write_breakdown(run_dir=run_dir, raw_frames=raw_frames, dimension="topic")
    _write_significance(
        run_dir=run_dir,
        dataset_name=dataset_name,
        raw_frames=raw_frames,
        baseline=significance_baseline,
        excluded_systems=excluded_systems,
    )
    return out


def _excluded_system_keys(summary: pd.DataFrame) -> set[str]:
    """Slash-joined keys for every lane the reliability gate filtered out.

    Matches the ``"{provider}/{response_kind}/{answer_source}"`` shape that
    :func:`nimble_benchmark.metrics.significance.compute_significance`
    uses internally so the significance layer can drop these lanes without
    re-deriving the gate.
    """
    if summary.empty or "excluded_reason" not in summary:
        return set()
    excluded = summary[summary["excluded_reason"].notna() & (summary["excluded_reason"] != "")]
    keys: set[str] = set()
    for _, row in excluded.iterrows():
        provider = row.get("provider") or ""
        response_kind = "" if pd.isna(row.get("response_kind")) else str(row.get("response_kind") or "")
        answer_source = "" if pd.isna(row.get("answer_source")) else str(row.get("answer_source") or "")
        keys.add(f"{provider}/{response_kind}/{answer_source}")
    return keys


def _write_significance(
    *,
    run_dir: Path,
    dataset_name: str,
    raw_frames: list[pd.DataFrame],
    baseline: str | None,
    excluded_systems: set[str] | None = None,
) -> None:
    """Run significance across every lane in the run and write one CSV.

    All lanes share a single leaderboard table, so they share a single
    baseline (``nimble_search`` unless ``--significance-baseline`` overrides
    it) and a single Bonferroni family.

    ``excluded_systems`` -- the lanes the reliability gate filtered out --
    is passed straight through to :func:`compute_significance` so a
    >95%-failure lane can't be picked as the baseline and can't poison
    challenger comparisons by sitting on the system list with two surviving
    rows.

    The file is removed rather than written empty when there is nothing to
    test (fewer than two lanes, or no shared queries), so its presence is a
    reliable signal that comparisons exist.
    """
    gold_urls_by_query = _gold_urls_by_query(dataset_name)
    output_path = run_dir / SIGNIFICANCE_FILENAME
    significance_df, _ = compute_significance(
        raw_frames=raw_frames,
        gold_urls_by_query=gold_urls_by_query,
        baseline=baseline,
        excluded_systems=excluded_systems,
    )
    if significance_df.empty:
        output_path.unlink(missing_ok=True)
        return
    significance_df.to_csv(output_path, index=False)


def _gold_urls_by_query(dataset_name: str) -> dict[str, list[str]]:
    dataset = DATASETS.get(dataset_name)
    if dataset is None:
        return {}
    try:
        df = dataset.load()
    except Exception:
        logger.warning("could not load dataset %r for significance gold URLs", dataset_name)
        return {}
    if "problem" not in df.columns or "metadata" not in df.columns:
        return {}
    return {str(row.problem): parse_gold_urls_from_metadata(row.metadata) for row in df.itertuples(index=False)}


def _write_breakdown(*, run_dir: Path, raw_frames: list[pd.DataFrame], dimension: str) -> None:
    output_path = run_dir / f"analyzed_by_{dimension}.csv"
    breakdown = _aggregate_breakdown(raw_frames=raw_frames, dimension=dimension)
    if breakdown is None:
        output_path.unlink(missing_ok=True)
        return
    breakdown.to_csv(output_path, index=False)


def _aggregate_breakdown(*, raw_frames: list[pd.DataFrame], dimension: str) -> pd.DataFrame | None:
    frames = [frame for frame in raw_frames if dimension in frame]
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    valid = df[dimension].notna() & (df[dimension].astype(str).str.strip() != "")
    if not valid.any():
        return None
    df = df.loc[valid].copy()
    df[dimension] = df[dimension].astype(str).str.strip()

    rows = []
    for key, group in df.groupby(["provider", dimension], dropna=False):
        provider, value = key
        rows.append(
            {
                "provider": provider,
                dimension: value,
                "problem_count": group["query"].nunique() if "query" in group else len(group),
                "accuracy_score": _aggregate_accuracy(group),
            }
        )
    return (
        pd.DataFrame(rows)
        .sort_values(["provider", "problem_count", dimension], ascending=[True, False, True])
        .reset_index(drop=True)
    )


def _aggregate_usage(group: pd.DataFrame, metric: str) -> dict[str, float | None]:
    if metric not in group:
        return {f"{metric}_sum": None, f"{metric}_mean": None}
    series = pd.to_numeric(group[metric], errors="coerce").dropna()
    if series.empty:
        return {f"{metric}_sum": None, f"{metric}_mean": None}
    return {f"{metric}_sum": float(series.sum()), f"{metric}_mean": float(series.mean())}


def _aggregate_accuracy(group: pd.DataFrame) -> float | None:
    """Fraction of served questions the lane got right.

    Denominator is ``ACCURACY_DENOMINATOR_RESULTS`` -- every row whose request
    succeeded, ``is_not_attempted`` included. A 200 that carries no usable
    answer is a question the lane was served and did not answer, so it scores
    as a miss rather than leaving the denominator. Only ``not_evaluated``
    (the request never landed) is excluded; ``failure_rate`` covers those.
    """
    if "evaluation_result" not in group:
        return None
    graded = group[group["evaluation_result"].isin(ACCURACY_DENOMINATOR_RESULTS)]
    if graded.empty:
        return None
    return float((graded["evaluation_result"] == ACCURACY_CORRECT_RESULT).mean())


__all__ = [
    "DEFAULT_MAX_FAILURE_RATE",
    "DEFAULT_MIN_SUCCESSFUL_PROBLEMS",
    "EXCLUSION_REASON_BELOW_MIN_GATE",
    "EXCLUSION_REASON_EXCESS_FAILURES",
    "SIGNIFICANCE_FILENAME",
    "SUMMARY_FILENAME",
    "aggregate_run",
]
