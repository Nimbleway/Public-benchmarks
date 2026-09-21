"""Tests for the static price list and the Cost column it feeds.

The price list is hand-maintained static data, so the tests that matter are the
ones that catch it drifting out of sync with the lane roster or silently
mispricing the one depth-configurable lane -- not the literal dollar values,
which are just transcribed from the vendors' published pages.
"""

import json
from datetime import date, datetime

import pandas as pd
import pytest

from nimble_benchmark.leaderboard_data import HeadlineRow
from nimble_benchmark.leaderboard_render import render_headline_table
from nimble_benchmark.price_list import (
    NIMBLE_DEPTH_USD_PER_1K,
    PRICE_LIST,
    PRICE_LIST_AS_OF,
    PRICE_SOURCES,
    LanePrice,
    format_cost,
    search_depth_from_configs,
    usd_for_calls,
    usd_per_1k_queries,
)
from nimble_benchmark.report import write_run_md
from nimble_benchmark.samplers import PROVIDER_CAPABILITIES


@pytest.mark.unit
def test_every_lane_in_the_roster_has_a_price_entry():
    """A new lane must be priced deliberately. Without this, adding a sampler
    silently renders an em dash in the Cost column that reads as "free" or
    "missing data" rather than "nobody looked up the price"."""
    assert set(PRICE_LIST) == set(PROVIDER_CAPABILITIES)


@pytest.mark.unit
@pytest.mark.parametrize("provider", sorted(PRICE_LIST))
def test_priced_lanes_carry_traceable_provenance(provider):
    """Each row keeps the vendor's own wording alongside the normalized figure
    so a reader can trace the $/1k back to a published page."""
    price = PRICE_LIST[provider]
    assert price.vendor
    assert price.tier
    assert price.list_price
    if price.usd_per_1k_queries is None:
        # An unpriced lane has to say why, or the em dash is unexplainable.
        assert price.notes
    else:
        assert price.usd_per_1k_queries > 0


@pytest.mark.unit
def test_snapshot_date_and_sources_are_present():
    """The column is a point-in-time snapshot; both the date and the pages it
    came from are part of the published claim."""
    date.fromisoformat(PRICE_LIST_AS_OF)
    assert PRICE_SOURCES
    assert all(url.startswith("https://") for url in PRICE_SOURCES)


@pytest.mark.unit
def test_pinned_lane_prices_match_their_published_tier():
    """Spot-check the pinned lanes against the vendors' published $/1k."""
    assert usd_per_1k_queries("parallel_search_turbo") == 1.00
    assert usd_per_1k_queries("parallel_search_basic") == 5.00
    assert usd_per_1k_queries("brave_search") == 5.00
    # Exa's two lanes are a latency/quality split, not a price split.
    assert usd_per_1k_queries("exa_search_auto") == usd_per_1k_queries("exa_search_fast") == 7.00
    # Tavily bills API credits: basic is 1 credit at $0.008.
    assert usd_per_1k_queries("tavily_search_basic") == 8.00


@pytest.mark.unit
def test_firecrawl_normalizes_credits_to_queries():
    """Firecrawl is credit-metered: /search costs 2 credits per 10 results, so
    1k queries burns 2k of the plan's 100k credits."""
    plan_usd, plan_credits, credits_per_query = 99.0, 100_000, 2
    expected = plan_usd / plan_credits * credits_per_query * 1_000
    assert usd_per_1k_queries("firecrawl_search") == pytest.approx(expected, abs=0.01)


@pytest.mark.unit
def test_unpublished_tier_has_no_per_query_price(monkeypatch):
    """A lane with no published per-query price renders an em dash at any call
    count rather than a fabricated figure. Exercised against a synthetic entry
    because no shipped lane carries ``usd_per_1k_queries=None`` today."""
    unpriced = LanePrice(
        vendor="Example",
        tier="Unpublished tier",
        list_price="not published",
        usd_per_1k_queries=None,
        notes="Synthetic entry; exercises the None path.",
    )
    monkeypatch.setitem(PRICE_LIST, "unpriced_lane", unpriced)
    assert usd_per_1k_queries("unpriced_lane") is None
    assert usd_for_calls("unpriced_lane", 500) is None
    assert format_cost("unpriced_lane", 500) == "—"


@pytest.mark.unit
def test_unknown_lane_prices_as_em_dash_instead_of_raising():
    """A run.md written from an older run's CSV can name a lane the price list
    no longer knows; that must not blow up the report writer."""
    assert usd_per_1k_queries("some_retired_lane") is None
    assert format_cost("some_retired_lane", 500) == "—"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("depth", "expected"),
    [("lite", 1.10), ("fast", 5.00), ("deep", 5.00)],
)
def test_depth_configurable_nimble_lane_is_priced_at_the_depth_it_ran(depth, expected):
    """``nimble_search`` follows NIMBLE_SEARCH_DEPTH, so a run pinned to lite
    must not be billed at the Search tier."""
    assert usd_per_1k_queries("nimble_search", search_depth=depth) == expected
    assert NIMBLE_DEPTH_USD_PER_1K[depth] == expected


@pytest.mark.unit
def test_depth_resolution_ignores_pinned_lanes_and_missing_configs():
    """A pinned lane's depth is part of its definition, and an absent config
    falls back to the lane's default tier rather than erroring."""
    # Pinned lane: the passed depth must not override the pinned tier price.
    assert usd_per_1k_queries("brave_search", search_depth="deep") == 5.00
    assert search_depth_from_configs("nimble_search", None) is None
    assert search_depth_from_configs("nimble_search", {}) is None
    assert search_depth_from_configs("brave_search", {"brave_search": {"count": 10}}) is None
    assert search_depth_from_configs("nimble_search", {"nimble_search": {"search_depth": "deep"}}) == "deep"
    # Unknown depth strings fall back to the lane's listed tier rather than None.
    assert usd_per_1k_queries("nimble_search", search_depth="experimental") == 5.00


@pytest.mark.unit
def test_cost_scales_linearly_with_the_call_count():
    """The whole point of the column: it's the run's spend, so doubling the
    calls doubles the cost and the unit price stays in the price list."""
    assert usd_for_calls("brave_search", 1_000) == pytest.approx(5.00)
    assert usd_for_calls("brave_search", 500) == pytest.approx(2.50)
    assert usd_for_calls("brave_search", 2_000) == pytest.approx(10.00)
    assert usd_for_calls("exa_search_auto", 500) == pytest.approx(3.50)
    assert usd_for_calls("parallel_search_turbo", 500) == pytest.approx(0.50)


@pytest.mark.unit
def test_zero_calls_costs_nothing_rather_than_rendering_as_unknown():
    """A lane that made no requests spent nothing -- distinct from a lane with
    no list price, which renders an em dash."""
    assert usd_for_calls("brave_search", 0) == 0.0
    assert format_cost("brave_search", 0) == "$0.00"


@pytest.mark.unit
@pytest.mark.parametrize(
    "calls",
    [None, "", "n/a", -1, float("nan")],
)
def test_missing_or_nonsense_call_counts_render_as_em_dash(calls):
    """``problem_count`` comes out of a CSV, so it can be blank or garbage. A
    cost we can't substantiate must render ``—``, never $0.00."""
    assert usd_for_calls("brave_search", calls) is None
    assert format_cost("brave_search", calls) == "—"


@pytest.mark.unit
def test_cost_precision_is_adaptive_so_smoke_runs_are_not_all_zero():
    """At n=5 a two-decimal total rounds every lane to $0.03 or $0.01, which
    reads as noise; sub-dime totals get four decimals instead."""
    assert format_cost("brave_search", 5) == "$0.0250"
    assert format_cost("firecrawl_search", 5) == "$0.0099"
    # A benchmark-size run stays in plain dollars and cents.
    assert format_cost("brave_search", 500) == "$2.50"
    assert format_cost("exa_search_auto", 500) == "$3.50"
    # Thousands separators keep a large run readable.
    assert format_cost("exa_search_auto", 1_000_000) == "$7,000.00"


def _row(provider_id: str, **overrides) -> HeadlineRow:
    defaults = dict(
        provider_id=provider_id,
        response_kind="search_results",
        answer_source="synth",
        accuracy_score=0.8,
        ndcg_at_10=0.6,
        recall_at_10_llm=0.7,
        usage_input_tokens_mean=None,
        usage_output_tokens_mean=None,
        provider_response_time_ms_p50=750.0,
        problem_count=100,
    )
    return HeadlineRow(**{**defaults, **overrides})


@pytest.mark.unit
def test_leaderboard_headline_table_renders_a_cost_column_off_the_call_count():
    table = render_headline_table(
        [_row("exa_search_fast", problem_count=500), _row("tavily_search_fast", problem_count=500)]
    )
    assert "Cost" in table
    # 500 calls at $7.00/1k.
    assert "$3.50" in table
    # 500 calls at $8.00/1k.
    assert "$4.00" in table


@pytest.mark.unit
def test_leaderboard_cost_tracks_each_lane_own_call_count():
    """Lanes in one run can have different ``n`` (a lane dropped rows), so cost
    is computed per row rather than from a single run-level count."""
    table = render_headline_table([_row("brave_search", problem_count=500), _row("exa_search_auto", problem_count=100)])
    assert "$2.50" in table  # 500 * $5.00/1k
    assert "$0.70" in table  # 100 * $7.00/1k


@pytest.mark.unit
def test_leaderboard_cost_column_follows_the_captured_nimble_depth():
    """The Cost cell for the unpinned Nimble lane tracks the depth recorded in
    the run's sampler config, not the module default."""
    rows = [_row("nimble_search", problem_count=1_000)]
    default_table = render_headline_table(rows)
    assert "$5.00" in default_table

    lite_table = render_headline_table(rows, sampler_configs={"nimble_search": {"search_depth": "lite"}})
    assert "$1.10" in lite_table
    assert "$5.00" not in lite_table


@pytest.mark.unit
def test_run_md_cost_column_scales_with_the_rows_the_lane_actually_ran(tmp_path):
    """End-to-end through the report writer: the ``cost`` cell is driven by each
    lane's ``problem_count`` in ``analyzed_results.csv``, and the depth-priced
    Nimble lane reads its tier from the captured sampler config."""
    pd.DataFrame(
        [
            {
                "provider": "brave_search",
                "response_kind": "search_results",
                "answer_source": "synth",
                "accuracy_score": 0.80,
                "provider_response_time_ms_p50": 700.0,
                "provider_response_time_ms_p95": 1500.0,
                "problem_count": 500,
            },
            {
                "provider": "nimble_search",
                "response_kind": "search_results",
                "answer_source": "synth",
                "accuracy_score": 0.84,
                "provider_response_time_ms_p50": 400.0,
                "provider_response_time_ms_p95": 900.0,
                "problem_count": 200,
            },
        ]
    ).to_csv(tmp_path / "analyzed_results.csv", index=False)
    (tmp_path / "sampler_config_nimble_search.json").write_text(json.dumps({"search_depth": "lite"}))

    write_run_md(
        run_dir=tmp_path,
        args={"dataset": "simpleqa", "limit": 500, "synthesis_model": "gpt-4o"},
        started_at=datetime(2026, 1, 1, 0, 0, 0),
        finished_at=datetime(2026, 1, 1, 0, 1, 0),
    )

    report = (tmp_path / "run.md").read_text()
    assert "| cost |" in report
    # Brave: 500 calls at $5.00/1k. Nimble at lite depth: 200 calls at $1.10/1k.
    assert "| $2.50 |" in report
    assert "| $0.22 |" in report
