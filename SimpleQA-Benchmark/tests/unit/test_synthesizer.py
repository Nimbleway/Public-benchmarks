"""Unit tests for eval-side answer synthesis prompt assembly."""

from __future__ import annotations

import pytest

from nimble_benchmark.synthesis.synthesizer import (
    _get_encoding,
    max_synth_context_tokens,
    synth,
)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_synth_bounds_oversized_search_context(monkeypatch):
    """Regression: full-text search providers can return enough page content
    to exceed the synthesis model's context window before synthesis gets a
    chance to run."""
    captured: dict[str, str] = {}

    async def fake_call_llm(**kwargs):
        captured["user"] = kwargs["user"]
        return "answer"

    monkeypatch.setattr("nimble_benchmark.synthesis.synthesizer.call_llm", fake_call_llm)

    model = "gpt-4o-mini"
    huge_result = "[Huge](https://example.com)\n" + ("a" * 250_000)
    answer = await synth("query", [huge_result for _ in range(10)], model)

    assert answer == "answer"
    enc = _get_encoding(model)
    # The wrapper text ("Query: ...\n\nSearch results:\n") sits outside the
    # bounded context proper, so allow a small margin over the pure budget.
    assert len(enc.encode(captured["user"])) <= max_synth_context_tokens(model) + 50
    assert "[Huge](https://example.com)" in captured["user"]


@pytest.mark.unit
def test_synth_budget_scales_with_model_context_window():
    """The token budget tracks the model's real context window rather than a
    single fixed number, so a bigger-window model gets more result context."""
    assert max_synth_context_tokens("gpt-4o") == 128_000 - 4_000
    assert max_synth_context_tokens("gpt-4o-mini") == 128_000 - 4_000
    assert max_synth_context_tokens("gpt-5") == 400_000 - 4_000
    # An unrecognized/future model falls back to the conservative gpt-4o
    # floor rather than guessing a larger window and risking a hard 400.
    assert max_synth_context_tokens("some-future-unlisted-model") == 128_000 - 4_000


@pytest.mark.unit
@pytest.mark.asyncio
async def test_synth_gives_every_result_a_fair_share_none_dropped(monkeypatch):
    """web-search-api-evals parity: no result is ever fully dropped. Ten
    equally oversized results each get an equal ~1/10 share of the budget
    and all ten survive, truncated rather than zeroed out."""
    captured: dict[str, str] = {}

    async def fake_call_llm(**kwargs):
        captured["user"] = kwargs["user"]
        return "answer"

    monkeypatch.setattr("nimble_benchmark.synthesis.synthesizer.call_llm", fake_call_llm)

    model = "gpt-4o-mini"
    big_result_template = "[Result {i}](https://example.com/{i})\n" + ("word " * 40_000)
    results = [big_result_template.format(i=i) for i in range(10)]

    await synth("query", results, model)

    for i in range(10):
        assert f"example.com/{i}" in captured["user"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_synth_oversized_result_does_not_starve_ranked_results_after_it(monkeypatch):
    """No single oversized result can consume the whole budget: a huge
    first-ranked result is truncated down to its fair share, leaving the
    smaller lower-ranked results after it fully intact."""
    captured: dict[str, str] = {}

    async def fake_call_llm(**kwargs):
        captured["user"] = kwargs["user"]
        return "answer"

    monkeypatch.setattr("nimble_benchmark.synthesis.synthesizer.call_llm", fake_call_llm)

    model = "gpt-4o-mini"
    huge_first = "[Big](https://example.com/big)\n" + ("word " * 200_000)
    small_rest = ["[Small](https://example.com/small)\nshort text"] * 4
    results = [huge_first, *small_rest]

    await synth("query", results, model)

    assert "example.com/big" in captured["user"]
    assert captured["user"].count("example.com/small") == 4
