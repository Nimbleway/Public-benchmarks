"""Cross-sampler significance tests."""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest
from ranx import Qrels, Run, evaluate

from nimble_benchmark.metrics.significance import (
    DEFAULT_ALPHA,
    SystemKey,
    compute_significance,
    resolve_baseline,
)
from nimble_benchmark.metrics.url_retrieval import _dcg, _ndcg_at, normalize_url


def _make_frame(
    *,
    provider: str,
    response_kind: str = "search_results",
    answer_source: str = "synth",
    rows: list[dict],
) -> pd.DataFrame:
    """Build a per-row raw_results frame with the columns the analyzer expects."""
    return pd.DataFrame(
        [
            {
                "provider": provider,
                "response_kind": response_kind,
                "answer_source_used": answer_source,
                **row,
            }
            for row in rows
        ]
    )


def _row(
    *,
    query: str,
    predicted_urls: list[str],
    evaluation_result: str = "is_incorrect",
    recall_at_10_llm: float | None = None,
) -> dict:
    return {
        "query": query,
        "predicted_urls": json.dumps(predicted_urls),
        "evaluation_result": evaluation_result,
        "recall_at_10_llm": recall_at_10_llm,
    }


@pytest.mark.unit
def test_empty_when_no_raw_frames():
    df, baseline = compute_significance(raw_frames=[], gold_urls_by_query={})
    assert df.empty
    assert baseline is None


@pytest.mark.unit
def test_empty_when_single_system():
    frame = _make_frame(
        provider="solo",
        rows=[_row(query="q1", predicted_urls=["https://a.com"])],
    )
    df, baseline = compute_significance(raw_frames=[frame], gold_urls_by_query={"q1": ["https://a.com"]})
    assert df.empty
    assert baseline is None


@pytest.mark.unit
def test_identical_systems_produce_nan_pvalues_and_no_stars():
    queries = [f"q{i}" for i in range(5)]
    rows_baseline = [
        _row(query=q, predicted_urls=[f"https://{q}.com"], evaluation_result="is_correct") for q in queries
    ]
    rows_sibling = list(rows_baseline)
    gold = {q: [f"https://{q}.com"] for q in queries}
    df, baseline = compute_significance(
        raw_frames=[
            _make_frame(provider="alpha", rows=rows_baseline),
            _make_frame(provider="bravo", rows=rows_sibling),
        ],
        gold_urls_by_query=gold,
    )

    assert baseline is not None
    assert not df.empty
    for _, row in df.iterrows():
        assert math.isnan(float(row["p_value"]))
        assert math.isnan(float(row["p_bonferroni"]))
        assert row["significant"] is False or row["significant"] == 0


@pytest.mark.unit
def test_strict_dominance_is_significant():
    queries = [f"q{i}" for i in range(10)]
    gold = {q: [f"https://{q}.com"] for q in queries}
    # Baseline always misses the gold URL.
    rows_baseline = [_row(query=q, predicted_urls=["https://wrong.com", "https://other.com"]) for q in queries]
    # Challenger always returns gold at rank 0.
    rows_winner = [_row(query=q, predicted_urls=[f"https://{q}.com", "https://other.com"]) for q in queries]
    df, baseline = compute_significance(
        raw_frames=[
            _make_frame(provider="baseline_sys", rows=rows_baseline),
            _make_frame(provider="winner_sys", rows=rows_winner),
        ],
        gold_urls_by_query=gold,
        baseline="baseline_sys",
    )

    assert baseline == "baseline_sys/search_results/synth"
    ndcg_row = df[(df["metric"] == "ndcg_at_10") & (df["system"].str.startswith("winner_sys"))]
    assert len(ndcg_row) == 1
    ndcg_row = ndcg_row.iloc[0]
    assert ndcg_row["baseline_mean"] == pytest.approx(0.0)
    assert ndcg_row["system_mean"] == pytest.approx(1.0)
    assert ndcg_row["mean_delta"] == pytest.approx(1.0)
    assert ndcg_row["p_bonferroni"] < DEFAULT_ALPHA
    assert bool(ndcg_row["significant"])


@pytest.mark.unit
def test_one_sided_significance_loser_is_not_starred():
    queries = [f"q{i}" for i in range(10)]
    gold = {q: [f"https://{q}.com"] for q in queries}
    rows_strong = [_row(query=q, predicted_urls=[f"https://{q}.com"]) for q in queries]
    rows_weak = [_row(query=q, predicted_urls=["https://wrong.com"]) for q in queries]
    df, _ = compute_significance(
        raw_frames=[
            _make_frame(provider="strong", rows=rows_strong),
            _make_frame(provider="weak", rows=rows_weak),
        ],
        gold_urls_by_query=gold,
        baseline="strong",
    )

    ndcg_loser = df[(df["metric"] == "ndcg_at_10") & (df["system"].str.startswith("weak"))]
    assert len(ndcg_loser) == 1
    ndcg_loser = ndcg_loser.iloc[0]
    assert ndcg_loser["mean_delta"] < 0
    assert ndcg_loser["p_bonferroni"] < DEFAULT_ALPHA
    # Weaker system gets a p-value but no ★ — one-sided "beats baseline" semantic.
    assert not bool(ndcg_loser["significant"])


@pytest.mark.unit
def test_bonferroni_correction_scales_with_system_count():
    queries = [f"q{i}" for i in range(20)]
    gold = {q: [f"https://{q}.com"] for q in queries}
    rows_baseline = [_row(query=q, predicted_urls=["https://wrong.com"]) for q in queries]
    rows_winner = [_row(query=q, predicted_urls=[f"https://{q}.com"]) for q in queries]
    rows_winner2 = [_row(query=q, predicted_urls=[f"https://{q}.com"]) for q in queries]
    rows_winner3 = [_row(query=q, predicted_urls=[f"https://{q}.com"]) for q in queries]

    df, _ = compute_significance(
        raw_frames=[
            _make_frame(provider="baseline_sys", rows=rows_baseline),
            _make_frame(provider="winner_a", rows=rows_winner),
            _make_frame(provider="winner_b", rows=rows_winner2),
            _make_frame(provider="winner_c", rows=rows_winner3),
        ],
        gold_urls_by_query=gold,
        baseline="baseline_sys",
    )

    ndcg_winners = df[
        (df["metric"] == "ndcg_at_10") & (df["system"].str.startswith(("winner_a", "winner_b", "winner_c")))
    ]
    assert len(ndcg_winners) == 3
    for _, row in ndcg_winners.iterrows():
        # 3 non-baseline systems → Bonferroni factor 3.
        raw_p = float(row["p_value"])
        corrected = float(row["p_bonferroni"])
        assert corrected == pytest.approx(min(1.0, raw_p * 3))


@pytest.mark.unit
def test_inner_join_on_query_drops_unshared():
    gold = {f"q{i}": [f"https://{i}.com"] for i in range(5)}
    rows_full = [_row(query=f"q{i}", predicted_urls=[f"https://{i}.com"]) for i in range(5)]
    rows_partial = [_row(query=f"q{i}", predicted_urls=[f"https://{i}.com"]) for i in range(3)]
    df, _ = compute_significance(
        raw_frames=[
            _make_frame(provider="full_sys", rows=rows_full),
            _make_frame(provider="partial_sys", rows=rows_partial),
        ],
        gold_urls_by_query=gold,
        baseline="full_sys",
    )
    ndcg = df[df["metric"] == "ndcg_at_10"].iloc[0]
    assert int(ndcg["n_queries"]) == 3  # intersection of queries


@pytest.mark.unit
def test_accuracy_metric_computed_from_evaluation_result():
    queries = [f"q{i}" for i in range(10)]
    # Baseline correct half the time, challenger always correct.
    rows_baseline = [
        _row(
            query=q,
            predicted_urls=["https://x.com"],
            evaluation_result="is_correct" if i % 2 == 0 else "is_incorrect",
        )
        for i, q in enumerate(queries)
    ]
    rows_winner = [_row(query=q, predicted_urls=["https://x.com"], evaluation_result="is_correct") for q in queries]
    df, _ = compute_significance(
        raw_frames=[
            _make_frame(provider="baseline_sys", rows=rows_baseline),
            _make_frame(provider="winner_sys", rows=rows_winner),
        ],
        gold_urls_by_query={q: ["https://x.com"] for q in queries},
        baseline="baseline_sys",
    )

    acc = df[df["metric"] == "accuracy_score"].iloc[0]
    assert acc["baseline_mean"] == pytest.approx(0.5)
    assert acc["system_mean"] == pytest.approx(1.0)
    assert bool(acc["significant"])


@pytest.mark.unit
def test_significance_accuracy_scores_not_attempted_as_zero():
    """The paired t-test uses the same denominator as the rendered table.

    A not-attempted row is a 0.0 for that query, not a query dropped from the
    pairing -- otherwise the ★ markers would test a different accuracy from
    the one the leaderboard shows. Baseline: 5 correct, 5 not-attempted -> 0.5.
    Winner: 10 correct -> 1.0. All 10 queries stay paired.
    """
    queries = [f"q{i}" for i in range(10)]
    rows_baseline = [
        _row(
            query=q,
            predicted_urls=["https://x.com"],
            evaluation_result="is_correct" if i % 2 == 0 else "is_not_attempted",
        )
        for i, q in enumerate(queries)
    ]
    rows_winner = [_row(query=q, predicted_urls=["https://x.com"], evaluation_result="is_correct") for q in queries]
    df, _ = compute_significance(
        raw_frames=[
            _make_frame(provider="baseline_sys", rows=rows_baseline),
            _make_frame(provider="winner_sys", rows=rows_winner),
        ],
        gold_urls_by_query={q: ["https://x.com"] for q in queries},
        baseline="baseline_sys",
    )

    acc = df[df["metric"] == "accuracy_score"].iloc[0]
    assert acc["baseline_mean"] == pytest.approx(0.5)
    assert acc["system_mean"] == pytest.approx(1.0)
    assert int(acc["n_queries"]) == 10, "not-attempted rows must stay in the pairing"


@pytest.mark.unit
def test_missing_llm_column_skipped_other_metrics_kept():
    queries = [f"q{i}" for i in range(5)]
    gold = {q: [f"https://{q}.com"] for q in queries}
    rows_with_llm = [
        _row(
            query=q,
            predicted_urls=[f"https://{q}.com"],
            recall_at_10_llm=1.0,
        )
        for q in queries
    ]
    rows_without_llm = [_row(query=q, predicted_urls=[f"https://{q}.com"]) for q in queries]
    df, _ = compute_significance(
        raw_frames=[
            _make_frame(provider="with_llm", rows=rows_with_llm),
            _make_frame(provider="no_llm", rows=rows_without_llm),
        ],
        gold_urls_by_query=gold,
        baseline="with_llm",
    )
    # URL metrics still produced for both systems.
    assert not df[df["metric"] == "ndcg_at_10"].empty
    # LLM recall row skipped because the challenger has only NaN values.
    assert df[df["metric"] == "recall_at_10_llm"].empty


@pytest.mark.unit
def test_resolve_baseline_prefers_nimble_search():
    keys = [
        SystemKey("exa_search_auto", "search_results", "synth"),
        SystemKey("brave_search", "search_results", "synth"),
        SystemKey("nimble_search", "search_results", "synth"),
    ]
    assert resolve_baseline(keys, None).provider == "nimble_search"


@pytest.mark.unit
def test_resolve_baseline_falls_back_to_first_sorted_when_no_nimble():
    keys = [
        SystemKey("zeta", "search_results", "synth"),
        SystemKey("alpha", "search_results", "synth"),
    ]
    assert resolve_baseline(keys, None).provider == "alpha"


@pytest.mark.unit
def test_resolve_baseline_explicit_full_key_wins():
    keys = [
        SystemKey("nimble_search", "search_results", "synth"),
        SystemKey("exa_search_auto", "search_results", "synth"),
    ]
    chosen = resolve_baseline(keys, "nimble_search/search_results/synth")
    assert chosen.provider == "nimble_search"


@pytest.mark.unit
def test_resolve_baseline_unknown_falls_back_to_auto():
    keys = [
        SystemKey("exa_search_auto", "search_results", "synth"),
        SystemKey("nimble_search", "search_results", "synth"),
    ]
    chosen = resolve_baseline(keys, "totally_made_up_sampler")
    assert chosen.provider == "nimble_search"


@pytest.mark.unit
def test_ranx_url_ndcg_matches_manual_implementation():
    """Catch drift between ranx's NDCG@10 and `metrics.url_retrieval._ndcg_at`."""
    predicted = ["https://a.com", "https://b.com", "https://c.com", "https://d.com"]
    gold = {"https://a.com", "https://c.com", "https://e.com"}  # one missed gold

    predicted_normalized = [normalize_url(url) for url in predicted]
    gold_normalized = {normalize_url(url) for url in gold}
    manual_ndcg = _ndcg_at(predicted_normalized, gold_normalized, k=10)

    qrels = Qrels({"q": {url: 1 for url in gold_normalized}})
    n = len(predicted_normalized)
    run = Run(
        {"q": {url: float(n - rank) for rank, url in enumerate(predicted_normalized)}},
        name="test",
    )
    ranx_ndcg = float(evaluate(qrels, run, "ndcg@10"))

    assert ranx_ndcg == pytest.approx(manual_ndcg, abs=1e-6)


@pytest.mark.unit
def test_dcg_helper_matches_log_definition():
    """Sanity check that the manual DCG matches a textbook log_2(rank + 2) sum."""
    relevance = [1, 0, 1]
    expected = 1 / np.log2(2) + 0 + 1 / np.log2(4)
    assert _dcg(relevance) == pytest.approx(expected)


@pytest.mark.unit
def test_resolve_baseline_prefers_nimble_search_over_alphabetically_earlier_lane():
    """``nimble_search`` anchors the table, so the auto baseline must land on
    it rather than an alphabetically earlier third party."""
    keys = [
        SystemKey(provider="brave_search", response_kind="search_results", answer_source="synth"),
        SystemKey(provider="nimble_search", response_kind="search_results", answer_source="synth"),
    ]
    assert resolve_baseline(keys, None).provider == "nimble_search"


@pytest.mark.unit
def test_compute_significance_compares_every_lane_against_the_search_baseline():
    baseline_frame = _make_frame(
        provider="nimble_search",
        rows=[
            _row(query="q1", predicted_urls=["https://a.com"]),
            _row(query="q2", predicted_urls=["https://b.com"]),
        ],
    )
    challenger_a = _make_frame(
        provider="exa_search_auto",
        rows=[
            _row(query="q1", predicted_urls=["https://a.com"]),
            _row(query="q2", predicted_urls=["https://x.com"]),
        ],
    )
    challenger_b = _make_frame(
        provider="brave_search",
        rows=[
            _row(query="q1", predicted_urls=["https://y.com"]),
            _row(query="q2", predicted_urls=["https://b.com"]),
        ],
    )
    gold = {"q1": ["https://a.com"], "q2": ["https://b.com"]}

    df, baseline = compute_significance(
        raw_frames=[baseline_frame, challenger_a, challenger_b],
        gold_urls_by_query=gold,
    )

    assert baseline == "nimble_search/search_results/synth"
    assert set(df["system"]) == {
        "exa_search_auto/search_results/synth",
        "brave_search/search_results/synth",
    }


@pytest.mark.unit
def test_holm_correction_is_less_conservative_than_bonferroni():
    """Holm-Bonferroni step-down: the largest p still gets multiplied by 1,
    while Bonferroni multiplies every p by family_size. For 3 challengers,
    the smallest p*3 (Bonferroni) >= smallest p*3 (Holm) at rank 1, but
    the largest p*1 (Holm) < largest p*3 (Bonferroni). Easy regression
    test: a borderline-p challenger that Bonferroni would suppress, Holm
    flags. We just assert the corrected p-value is no greater than the
    Bonferroni value (Holm dominates Bonferroni on every comparison).
    """
    queries = [f"q{i}" for i in range(20)]
    gold = {q: [f"https://{q}.com"] for q in queries}
    rows_baseline = [_row(query=q, predicted_urls=["https://wrong.com"]) for q in queries]
    rows_winner = [_row(query=q, predicted_urls=[f"https://{q}.com"]) for q in queries]
    rows_winner2 = [_row(query=q, predicted_urls=[f"https://{q}.com"]) for q in queries]
    rows_winner3 = [_row(query=q, predicted_urls=[f"https://{q}.com"]) for q in queries]

    bonf_df, _ = compute_significance(
        raw_frames=[
            _make_frame(provider="baseline_sys", rows=rows_baseline),
            _make_frame(provider="winner_a", rows=rows_winner),
            _make_frame(provider="winner_b", rows=rows_winner2),
            _make_frame(provider="winner_c", rows=rows_winner3),
        ],
        gold_urls_by_query=gold,
        baseline="baseline_sys",
        correction="bonferroni",
    )
    holm_df, _ = compute_significance(
        raw_frames=[
            _make_frame(provider="baseline_sys", rows=rows_baseline),
            _make_frame(provider="winner_a", rows=rows_winner),
            _make_frame(provider="winner_b", rows=rows_winner2),
            _make_frame(provider="winner_c", rows=rows_winner3),
        ],
        gold_urls_by_query=gold,
        baseline="baseline_sys",
        correction="holm",
    )

    bonf_ndcg = bonf_df[bonf_df["metric"] == "ndcg_at_10"].set_index("system")["p_bonferroni"]
    holm_ndcg = holm_df[holm_df["metric"] == "ndcg_at_10"].set_index("system")["p_bonferroni"]
    assert list(bonf_ndcg.index) == list(holm_ndcg.index)
    for sys_key in bonf_ndcg.index:
        # Holm dominates Bonferroni: corrected_holm ≤ corrected_bonferroni for every comparison.
        assert holm_ndcg.loc[sys_key] <= bonf_ndcg.loc[sys_key] + 1e-9, (
            f"Holm should be at least as powerful as Bonferroni on {sys_key}: "
            f"holm={holm_ndcg.loc[sys_key]}, bonf={bonf_ndcg.loc[sys_key]}"
        )
    # Correction method is stamped on every row so the leaderboard can disclose it.
    assert (bonf_df["correction"] == "bonferroni").all()
    assert (holm_df["correction"] == "holm").all()


@pytest.mark.unit
def test_compute_significance_returns_empty_with_a_single_system():
    """Nothing to compare against: one lane produces no pairwise rows."""
    search_only = _make_frame(
        provider="nimble_search",
        rows=[_row(query="q1", predicted_urls=["https://a.com"])],
    )
    df, baseline = compute_significance(
        raw_frames=[search_only],
        gold_urls_by_query={"q1": ["https://a.com"]},
    )
    assert df.empty
    assert baseline is None
