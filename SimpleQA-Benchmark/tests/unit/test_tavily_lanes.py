"""Offline tests for the Tavily depth lanes."""

from __future__ import annotations

import pytest

from nimble_benchmark.price_list import PRICE_LIST, usd_per_1k_queries
from nimble_benchmark.samplers import (
    PROVIDER_CAPABILITIES,
    TAVILY_LANE_DEPTHS,
)
from nimble_benchmark.samplers.tavily_search import TavilySearchSampler


@pytest.mark.unit
def test_tavily_payload_pins_depth_and_never_requests_an_answer() -> None:
    """``include_answer`` is pinned off: the lane is retrieval-only and its
    graded answer is synthesized eval-side."""
    sampler = TavilySearchSampler(name="tavily_search_fast", api_key="tk", search_depth="fast")
    assert sampler._build_payload("messi age") == {
        "query": "messi age",
        "search_depth": "fast",
        "include_answer": False,
        "max_results": 10,
    }
    assert sampler._headers()["Authorization"] == "Bearer tk"


@pytest.mark.unit
def test_tavily_rejects_unknown_depth() -> None:
    for bad_depth in ("deep", "turbo", "auto", "advanced", "ultra-fast", ""):
        with pytest.raises(ValueError, match="search_depth must be one of"):
            TavilySearchSampler(name="tavily_search_basic", api_key="tk", search_depth=bad_depth)


@pytest.mark.unit
def test_tavily_lanes_share_one_rate_limit_bucket() -> None:
    """Both lanes on one key must not take two independent throttles: they
    share Tavily's per-key quota."""
    samplers = [
        TavilySearchSampler(name=name, api_key="same-key", search_depth=depth)
        for name, depth in TAVILY_LANE_DEPTHS.items()
    ]
    limiters = {id(sampler._limiter) for sampler in samplers}
    assert len(limiters) == 1


@pytest.mark.unit
def test_tavily_extract_chunks_maps_content_to_description() -> None:
    sampler = TavilySearchSampler(name="tavily_search_basic", api_key="tk")
    chunks = sampler.extract_chunks(
        {
            "results": [
                {"url": "https://a.example", "title": "A", "content": "passage a"},
                {"url": "", "title": "no url", "content": "dropped"},
            ]
        }
    )
    assert [(chunk.url, chunk.description, chunk.position) for chunk in chunks] == [
        ("https://a.example", "passage a", 0)
    ]


@pytest.mark.unit
@pytest.mark.parametrize("lane", list(TAVILY_LANE_DEPTHS))
def test_tavily_lanes_are_registered_and_priced(lane: str) -> None:
    """A lane missing from either map renders as absent rather than failing
    loudly, so both are asserted per lane."""
    assert lane in PROVIDER_CAPABILITIES
    assert PROVIDER_CAPABILITIES[lane].answer_source == "synth"
    assert PROVIDER_CAPABILITIES[lane].response_kind == "search_results"
    assert lane in PRICE_LIST


@pytest.mark.unit
def test_both_tavily_depths_price_at_one_credit() -> None:
    """Both depths cost 1 API credit, so both normalize to $8.00 / 1k. Equal
    figures read off the same published table, not one copied from the other."""
    assert usd_per_1k_queries("tavily_search_basic") == 8.00
    assert usd_per_1k_queries("tavily_search_fast") == 8.00
