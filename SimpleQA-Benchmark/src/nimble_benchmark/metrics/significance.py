"""Cross-sampler statistical significance for retrieval and accuracy metrics.

Each (provider, response_kind, answer_source_used) tuple in the per-row CSVs
is treated as one "system" for significance purposes. A single configurable
baseline system is compared against every other system via a paired Student's
t-test, with Bonferroni correction applied within each metric across the
(n_systems - 1) comparisons.

For URL-binary ranking metrics we use ranx to compute per-query scores
(`evaluate(..., return_mean=False)`) so the IR semantics match the canonical
TREC formulation and the methodology. Per-row LLM
metric columns and `accuracy_score` are read directly from the raw CSVs —
ranx doesn't model LLM-graded recall or accuracy semantics.

Queries are inner-joined across systems on the `query` column; the `n_queries`
field on each output row records the shared-query count used for that pair.

Single-table significance: every lane in a run -- ``/search`` lanes and
native-LLM answer lanes alike -- is compared against one shared baseline
(``nimble_search`` by default) and the result goes into a single
``significance.csv``. There is no per-``response_kind`` split: the leaderboard
renders one table, so one baseline is the only correction family that matches
what a reader compares on the page.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Any, Final, Literal

import numpy as np
import pandas as pd
from ranx import Qrels, Run, evaluate
from scipy import stats
from statsmodels.stats.multitest import multipletests

from nimble_benchmark.constants import ACCURACY_CORRECT_RESULT, ACCURACY_DENOMINATOR_RESULTS
from nimble_benchmark.metrics.url_retrieval import normalize_url

# Multiple-testing correction method passed to ``statsmodels.multipletests``.
# Bonferroni is the historical default (matches the locked numerics on the
# leaderboard); Holm and Benjamini-Hochberg are now drop-in alternatives that
# can be requested per call without rewriting the math.
CorrectionMethod = Literal["bonferroni", "holm", "fdr_bh"]
DEFAULT_CORRECTION: CorrectionMethod = "bonferroni"

logger = logging.getLogger(__name__)


_RANX_METRICS: dict[str, str] = {
    "ndcg_at_10": "ndcg@10",
    "recall_at_10": "recall@10",
    "mrr": "mrr",
}

_DIRECT_COLUMN_METRICS: tuple[str, ...] = ("recall_at_10_llm",)

_ACCURACY_METRIC = "accuracy_score"

DEFAULT_ALPHA: Final[float] = 0.05


@dataclass(frozen=True)
class SystemKey:
    provider: str
    response_kind: str
    answer_source: str

    def __str__(self) -> str:
        return f"{self.provider}/{self.response_kind}/{self.answer_source}"


def resolve_baseline(system_keys: list[SystemKey], preferred: str | None) -> SystemKey | None:
    """Pick a baseline system.

    Priority: explicit `preferred` (full slash-joined key, then bare provider
    name) → `nimble_search` provider → first system by string sort. Returns
    None when no systems exist.

    Every lane in the run competes in one table, so one baseline anchors the
    whole comparison: ``nimble_search`` when it is in the roster, otherwise
    first-by-sort unless ``--significance-baseline`` names one explicitly.
    """
    if not system_keys:
        return None
    if preferred and preferred != "auto":
        for key in system_keys:
            if str(key) == preferred or key.provider == preferred:
                return key
        logger.warning(
            "significance baseline %r not found among %d systems; falling back to auto",
            preferred,
            len(system_keys),
        )
    for key in system_keys:
        if key.provider == "nimble_search":
            return key
    return sorted(system_keys, key=str)[0]


def compute_significance(
    *,
    raw_frames: list[pd.DataFrame],
    gold_urls_by_query: dict[str, list[str]],
    baseline: str | None = None,
    alpha: float = DEFAULT_ALPHA,
    correction: CorrectionMethod = DEFAULT_CORRECTION,
    excluded_systems: set[str] | None = None,
) -> tuple[pd.DataFrame, str | None]:
    """Run paired-t + multi-test-correction significance for every headline metric.

    Returns (significance_df, resolved_baseline_key_string_or_None). The frame
    is empty when fewer than 2 systems are present or no comparable queries
    can be found.

    Every system in ``raw_frames`` is compared against the single resolved
    baseline, regardless of ``response_kind`` -- the leaderboard renders one
    table, so the correction family is the whole roster.

    ``correction`` selects the multiple-testing method applied within each
    metric across the (n_systems - 1) pairwise comparisons. Default is
    ``"bonferroni"`` which preserves the historical leaderboard numerics;
    ``"holm"`` and ``"fdr_bh"`` (Benjamini-Hochberg) are drop-in alternatives
    that become useful as the table grows past ~5 systems and Bonferroni
    gets very conservative. The corrected p-value column on the output frame
    is always named ``p_bonferroni`` for backward compat with the
    leaderboard renderer; the actual correction method used is stamped into
    the ``correction`` column.

    ``excluded_systems`` -- a set of ``"provider/response_kind/answer_source"``
    keys that :mod:`nimble_benchmark.analyzer` flagged as unreliable
    (failure rate above the configured ceiling, or successful problems below
    the floor). These lanes are filtered out before baseline selection and
    before pairwise comparisons so a lane that surfaced only a handful of
    survivors cannot be the baseline and cannot inflate the correction's
    family size. Defaults to ``None`` (no exclusions) so callers that
    don't apply the reliability gate keep their historical behavior.

    Per-query metric vectors are always computed over rows whose
    ``request_status == "ok"`` (i.e. the same "successful subset" the
    analyzer's summary CSV averages over). This keeps the paired-t test
    consistent with the leaderboard column it's marking up: a system that
    failed 20% of its queries should be compared on the 80% it did answer,
    not penalised twice by carrying zeros from failed rows AND by getting
    a low corrected p-value built on those zeros.
    """
    if not raw_frames:
        return _empty_significance_df(), None

    combined = pd.concat(raw_frames, ignore_index=True)
    # Drop the failed rows up front so every downstream per-query map is
    # built against the successful subset. ``_per_query_*`` helpers already
    # tolerate missing columns / queries; this just narrows the universe
    # they see.
    if "request_status" in combined.columns:
        combined = combined[combined["request_status"].astype(str) == "ok"].copy()
        if combined.empty:
            return _empty_significance_df(), None
    excluded = excluded_systems or set()
    system_keys = _system_keys_from_frame(combined)
    if excluded:
        system_keys = [key for key in system_keys if str(key) not in excluded]
    if len(system_keys) < 2:
        return _empty_significance_df(), None

    baseline_key = resolve_baseline(system_keys, baseline)
    if baseline_key is None:
        return _empty_significance_df(), None

    per_system_scores: dict[str, dict[SystemKey, dict[str, float]]] = {
        metric: {} for metric in (*_RANX_METRICS, *_DIRECT_COLUMN_METRICS, _ACCURACY_METRIC)
    }
    for system in system_keys:
        for metric, query_map in _per_query_url_scores(combined, system, gold_urls_by_query).items():
            per_system_scores[metric][system] = query_map
        for metric in _DIRECT_COLUMN_METRICS:
            per_system_scores[metric][system] = _per_query_column(combined, system, metric)
        per_system_scores[_ACCURACY_METRIC][system] = _per_query_accuracy(combined, system)

    rows: list[dict[str, Any]] = []
    # Number of expected comparisons per metric — preserved as the
    # correction-vector length so that a metric where some comparisons drop
    # out (e.g. a challenger with no shared queries with the baseline) still
    # gets corrected against the full family size. This matches the historical
    # Bonferroni semantic locked by test_bonferroni_correction_scales_with_system_count.
    family_size = max(1, len(system_keys) - 1)

    for metric, by_system in per_system_scores.items():
        baseline_scores = by_system.get(baseline_key, {})
        if not baseline_scores:
            continue
        metric_rows: list[dict[str, Any]] = []
        for system in system_keys:
            if system == baseline_key:
                continue
            system_scores = by_system.get(system, {})
            if not system_scores:
                continue
            shared_queries = sorted(set(baseline_scores) & set(system_scores))
            if len(shared_queries) < 2:
                continue
            baseline_vec = np.array([baseline_scores[q] for q in shared_queries])
            system_vec = np.array([system_scores[q] for q in shared_queries])
            metric_rows.append(
                _paired_t_row(
                    metric=metric,
                    baseline_key=str(baseline_key),
                    system_key=str(system),
                    baseline_vec=baseline_vec,
                    system_vec=system_vec,
                    alpha=alpha,
                )
            )
        _apply_correction(metric_rows, family_size=family_size, method=correction, alpha=alpha)
        rows.extend(metric_rows)

    return pd.DataFrame(rows), str(baseline_key)


def _paired_t_row(
    *,
    metric: str,
    baseline_key: str,
    system_key: str,
    baseline_vec: np.ndarray,
    system_vec: np.ndarray,
    alpha: float,
) -> dict[str, Any]:
    """Build one paired-t row. ``p_bonferroni`` and ``significant`` are filled
    later by :func:`_apply_correction` once the full per-metric vector is
    known."""
    baseline_mean = float(baseline_vec.mean())
    system_mean = float(system_vec.mean())
    mean_delta = system_mean - baseline_mean

    if np.allclose(baseline_vec, system_vec):
        # ttest_rel returns NaN when the difference vector is all zeros — no
        # effect to test. Record explicitly so the report can show it cleanly.
        p_value = float("nan")
    else:
        p_value = float(stats.ttest_rel(baseline_vec, system_vec).pvalue)

    return {
        "metric": metric,
        "baseline": baseline_key,
        "system": system_key,
        "baseline_mean": round(baseline_mean, 6),
        "system_mean": round(system_mean, 6),
        "mean_delta": round(mean_delta, 6),
        "p_value": p_value,
        "p_bonferroni": float("nan"),
        "significant": False,
        "n_queries": int(baseline_vec.size),
        "alpha": alpha,
    }


def _apply_correction(
    rows: list[dict[str, Any]],
    *,
    family_size: int,
    method: CorrectionMethod,
    alpha: float,
) -> None:
    """Apply ``statsmodels.multipletests`` correction in-place across ``rows``
    for a single metric.

    The correction vector is padded to ``family_size`` so that dropped-out
    comparisons (NaN / no shared queries) still inflate the divisor. This
    preserves the legacy Bonferroni numerics: when all (n_systems - 1)
    comparisons produce a valid p-value, the corrected value equals
    ``min(1.0, p * family_size)``. NaN p-values flow through as NaN.
    """
    if not rows:
        return

    pvals = np.array([row["p_value"] for row in rows], dtype=float)
    valid_mask = ~np.isnan(pvals)
    if not valid_mask.any():
        # All comparisons returned NaN (identical vectors); nothing to correct.
        for row in rows:
            row["correction"] = method
            row["significant"] = False
        return

    # Pad the input vector to `family_size` with ones (which Bonferroni and
    # Holm both treat as "definitely not significant", so they don't change
    # the comparison's ranking but they DO count toward the multiplier /
    # rank denominator). This matches the historical semantic where the
    # Bonferroni factor was n_systems-1, not "number of comparisons that
    # happened to produce a valid p-value".
    valid_pvals = pvals[valid_mask]
    padded = np.ones(family_size, dtype=float)
    padded[: len(valid_pvals)] = valid_pvals
    _, corrected_padded, _, _ = multipletests(padded, alpha=alpha, method=method)
    corrected_valid = corrected_padded[: len(valid_pvals)]

    corrected = np.full_like(pvals, np.nan)
    corrected[valid_mask] = corrected_valid

    for row, corrected_p in zip(rows, corrected, strict=True):
        row["p_bonferroni"] = float(corrected_p) if not math.isnan(corrected_p) else float("nan")
        row["correction"] = method
        # One-sided "system beats baseline" semantic: only flag as significant
        # if the corrected p is below alpha *and* the system mean is higher.
        # Negative deltas (system loses) get a p-value but no ★.
        if math.isnan(row["p_bonferroni"]) or row["mean_delta"] <= 0:
            row["significant"] = False
        else:
            row["significant"] = row["p_bonferroni"] < alpha


def _system_keys_from_frame(df: pd.DataFrame) -> list[SystemKey]:
    needed = ("provider", "response_kind", "answer_source_used")
    if not all(col in df.columns for col in needed):
        return []
    deduped = df[list(needed)].fillna("").drop_duplicates()
    return [
        SystemKey(
            provider=str(row.provider),
            response_kind=str(row.response_kind),
            answer_source=str(row.answer_source_used),
        )
        for row in deduped.itertuples(index=False)
    ]


def _system_mask(df: pd.DataFrame, system: SystemKey) -> pd.Series:
    return (
        (df["provider"].astype(str) == system.provider)
        & (df["response_kind"].fillna("").astype(str) == system.response_kind)
        & (df["answer_source_used"].fillna("").astype(str) == system.answer_source)
    )


def _per_query_url_scores(
    df: pd.DataFrame,
    system: SystemKey,
    gold_urls_by_query: dict[str, list[str]],
) -> dict[str, dict[str, float]]:
    """Run ranx.evaluate(return_mean=False) per metric for one system."""
    if "predicted_urls" not in df.columns or "query" not in df.columns:
        return {label: {} for label in _RANX_METRICS}
    rows = df[_system_mask(df, system)]
    if rows.empty:
        return {label: {} for label in _RANX_METRICS}

    qrels_dict: dict[str, dict[str, int]] = {}
    run_dict: dict[str, dict[str, float]] = {}
    for record in rows.itertuples(index=False):
        query = str(getattr(record, "query", ""))
        if not query:
            continue
        gold_normalized = {
            normalized for normalized in (normalize_url(url) for url in gold_urls_by_query.get(query, [])) if normalized
        }
        if not gold_normalized:
            continue
        predicted_normalized = _normalized_predicted(getattr(record, "predicted_urls", None))
        if not predicted_normalized:
            continue
        qrels_dict[query] = {url: 1 for url in gold_normalized}
        # Run scores are strictly descending in rank order so ranx ranks by
        # original retrieval order regardless of any score ties downstream.
        n = len(predicted_normalized)
        run_dict[query] = {url: float(n - rank) for rank, url in enumerate(predicted_normalized)}

    if not qrels_dict:
        return {label: {} for label in _RANX_METRICS}

    qrels = Qrels(qrels_dict)
    run = Run(run_dict, name=str(system))
    # ranx's `evaluate(..., return_mean=False)` returns a 1D ndarray aligned
    # with the *qrels* iteration order, not a dict. Zip with the original
    # insertion-ordered keys so the per-query mapping survives downstream
    # paired-t alignment regardless of how ranx stores its numba dict.
    qrels_query_order = list(qrels_dict.keys())

    out: dict[str, dict[str, float]] = {}
    for label, ranx_alias in _RANX_METRICS.items():
        per_query_scores = evaluate(qrels, run, ranx_alias, return_mean=False)
        out[label] = {
            str(query): float(score) for query, score in zip(qrels_query_order, per_query_scores, strict=True)
        }
    return out


def _normalized_predicted(value: Any) -> list[str]:
    """Parse the `predicted_urls` JSON column, normalize, dedupe by first-seen."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return []
    if isinstance(value, list):
        raw = [str(item) for item in value if item]
    else:
        text = str(value).strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        raw = [str(item) for item in parsed if item]

    seen: set[str] = set()
    out: list[str] = []
    for url in raw:
        normalized = normalize_url(url)
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return out


def _per_query_column(df: pd.DataFrame, system: SystemKey, column: str) -> dict[str, float]:
    if column not in df.columns or "query" not in df.columns:
        return {}
    rows = df[_system_mask(df, system)]
    if rows.empty:
        return {}
    out: dict[str, float] = {}
    for query, value in zip(rows["query"], rows[column], strict=False):
        try:
            score = float(value)
        except (TypeError, ValueError):
            continue
        if math.isnan(score):
            continue
        out[str(query)] = score
    return out


def _per_query_accuracy(df: pd.DataFrame, system: SystemKey) -> dict[str, float]:
    if "evaluation_result" not in df.columns or "query" not in df.columns:
        return {}
    rows = df[_system_mask(df, system)]
    if rows.empty:
        return {}
    # Same denominator as ``analyzer._aggregate_accuracy``, so the paired
    # t-test tests the delta the leaderboard actually renders: a not-attempted
    # row is a 0.0 for that query, not a query dropped from the pairing.
    rows = rows[rows["evaluation_result"].isin(ACCURACY_DENOMINATOR_RESULTS)]
    if rows.empty:
        return {}
    return {
        str(query): 1.0 if value == ACCURACY_CORRECT_RESULT else 0.0
        for query, value in zip(rows["query"], rows["evaluation_result"], strict=False)
    }


def _empty_significance_df() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "metric",
            "baseline",
            "system",
            "baseline_mean",
            "system_mean",
            "mean_delta",
            "p_value",
            "p_bonferroni",
            "significant",
            "n_queries",
            "alpha",
            "correction",
        ]
    )
