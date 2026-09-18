import pytest

import nimble_benchmark.samplers as samplers
from nimble_benchmark.samplers import PROVIDER_CAPABILITIES, answer_source_for_provider

# No answer-kind lane ships today; the plumbing that would route one is
# still exercised by ``test_answer_source_follows_the_lane_kind``.
ANSWER_LANES: tuple[str, ...] = ()


@pytest.mark.unit
@pytest.mark.parametrize("provider", sorted(PROVIDER_CAPABILITIES))
def test_every_lane_declares_a_kind_the_analyzer_can_partition(provider: str) -> None:
    """A lane reporting a kind outside the two-table axis makes the analyzer
    raise rather than write a summary row it has no table for."""
    capability = PROVIDER_CAPABILITIES[provider]
    assert capability.provider == provider
    assert capability.response_kind in {"search_results", "answer_with_citations", "native_answer"}


@pytest.mark.unit
@pytest.mark.parametrize("provider", sorted(PROVIDER_CAPABILITIES))
def test_answer_source_follows_the_lane_kind(provider: str) -> None:
    """The source is a property of the lane, not a per-run choice: a /search
    lane has no server-side answer to grade, and an answer lane has no ranked
    list to synthesize from."""
    capability = PROVIDER_CAPABILITIES[provider]
    expected = "synth" if capability.response_kind == "search_results" else "api"
    assert capability.answer_source == expected
    assert answer_source_for_provider(provider) == expected


@pytest.mark.unit
def test_answer_source_for_provider_rejects_unknown():
    with pytest.raises(ValueError, match="Unknown provider.*foobar"):
        answer_source_for_provider("foobar")


@pytest.mark.unit
def test_every_shipped_lane_is_a_search_lane():
    """Every lane is ``/search``, so the leaderboard's Accuracy column is
    uniformly sourced. An answer lane joining flips that, and ``report.py``'s
    Caveat text has to change with it."""
    answer_kind = {name for name, cap in PROVIDER_CAPABILITIES.items() if cap.response_kind != "search_results"}
    assert answer_kind == set(ANSWER_LANES)
    assert {cap.answer_source for cap in PROVIDER_CAPABILITIES.values()} == {"synth"}


@pytest.mark.unit
def test_removed_answer_lanes_do_not_resolve():
    """Hard cut: a stale dispatch naming a deleted lane must fail loudly
    rather than silently running a narrower roster."""
    for removed in (
        "nimble_answer",
        "exa_answer",
        "brave_answer",
        "claude_answer",
        "gemini_answer",
        "nimble_search_deep",
        "openai_answer",
        "nimble_search_lite",
        "nimble_search_fast_full_content",
        "nimble_search_cache",
        "nimble_search_you_extraction",
        "tavily_search_advanced",
        "tavily_search_ultra_fast",
        "you_search_livecrawl",
        "you_search_extraction",
    ):
        assert removed not in PROVIDER_CAPABILITIES
        with pytest.raises(ValueError, match=removed):
            samplers.expand_sampler_names([removed])


@pytest.mark.unit
def test_removed_answer_aliases_do_not_resolve():
    for removed in (
        "remote_answer_apis",
        "third_party_answer_apis",
        "all_answer_apis",
        "nimble_answer_apis",
        "nimble_apis",
        "native_llms",
        "nimble_search_apis",
        "nimble_search_depths",
        "nimble_search_content_modes",
        "nimble_search_flag_lanes",
        "you_search_generations",
        # Purged in favour of naming lanes explicitly; ``all_apis`` is the
        # only alias that survives.
        "competition_apis",
        "all_search_apis",
        "third_party_search_apis",
        "exa_search_types",
        "parallel_search_modes",
        "tavily_search_depths",
    ):
        assert removed not in samplers.ALIASES
        with pytest.raises(ValueError, match=removed):
            samplers.expand_sampler_names([removed])


@pytest.mark.unit
def test_expand_sampler_names_rejects_unknown():
    with pytest.raises(ValueError, match="foobar"):
        samplers.expand_sampler_names(["foobar"])


@pytest.mark.unit
def test_expand_sampler_names_dedups_overlapping_alias_and_literal():
    """A lane named explicitly alongside the alias that already contains it
    resolves once, in first-seen order."""
    expanded = samplers.expand_sampler_names(["exa_search_auto", "all_apis"])

    assert expanded[0] == "exa_search_auto"
    assert len(expanded) == len(set(expanded))
    assert set(expanded) == set(samplers.expand_sampler_names(["all_apis"]))


@pytest.mark.unit
def test_a_named_roster_keeps_its_given_order() -> None:
    """With the hand-listed aliases gone, a narrower roster is spelled out on
    the command line. ``expand_sampler_names`` must preserve that order, since
    it controls leaderboard row ordering."""
    expanded = samplers.expand_sampler_names(
        ["exa_search_auto", "exa_search_fast", "parallel_search_basic", "brave_search"]
    )
    assert expanded == [
        "exa_search_auto",
        "exa_search_fast",
        "parallel_search_basic",
        "brave_search",
    ]


@pytest.mark.unit
def test_parallel_lanes_pin_both_modes() -> None:
    """Parallel's ``mode`` is the customer-facing product choice, so the eval
    pins both ends as separate lanes rather than reading one from the
    environment. Name the two lanes to run that pair."""
    assert samplers.expand_sampler_names(["parallel_search_basic", "parallel_search_turbo"]) == [
        "parallel_search_basic",
        "parallel_search_turbo",
    ]
    assert samplers.PARALLEL_LANE_MODES == {
        "parallel_search_basic": "basic",
        "parallel_search_turbo": "turbo",
    }


@pytest.mark.unit
def test_both_parallel_modes_are_published_lanes() -> None:
    """Both Parallel lanes ship in ``all_apis`` (two rows on the public table).
    They share one secret, so adding turbo cannot break a dispatch that already
    had Parallel."""
    expanded = set(samplers.expand_sampler_names(["all_apis"]))
    assert {"parallel_search_basic", "parallel_search_turbo"} <= expanded


@pytest.mark.unit
def test_exa_lanes_pin_both_search_types() -> None:
    """Exa's ``type`` is the customer-facing product choice, so the eval pins
    both ends as separate lanes rather than reading one from the environment.
    Name the two lanes to run that pair."""
    assert samplers.expand_sampler_names(["exa_search_auto", "exa_search_fast"]) == [
        "exa_search_auto",
        "exa_search_fast",
    ]
    assert samplers.EXA_LANE_TYPES == {
        "exa_search_auto": "auto",
        "exa_search_fast": "fast",
    }


@pytest.mark.unit
def test_both_exa_search_types_are_published_lanes() -> None:
    """Both Exa search lanes ship in ``all_apis`` (two rows on the public
    table). They share one secret, so adding the fast lane cannot break a
    dispatch that already had Exa."""
    expanded = set(samplers.expand_sampler_names(["all_apis"]))
    assert {"exa_search_auto", "exa_search_fast"} <= expanded


@pytest.mark.unit
def test_pre_split_exa_search_lane_is_gone() -> None:
    """Hard cut, mirroring the ``parallel_search`` -> mode-lane split: the
    unqualified lane name must not resolve, so a stale dispatch fails loudly
    instead of silently running one type."""
    assert "exa_search" not in PROVIDER_CAPABILITIES
    with pytest.raises(ValueError, match="exa_search"):
        samplers.expand_sampler_names(["exa_search"])


@pytest.mark.unit
def test_tavily_lanes_pin_both_search_depths() -> None:
    """One slice, two lanes, one knob varied -- named explicitly."""
    assert samplers.expand_sampler_names(["tavily_search_basic", "tavily_search_fast"]) == [
        "tavily_search_basic",
        "tavily_search_fast",
    ]
    assert samplers.TAVILY_LANE_DEPTHS == {
        "tavily_search_basic": "basic",
        "tavily_search_fast": "fast",
    }


@pytest.mark.unit
def test_all_apis_alias_is_the_published_set() -> None:
    """The alias must contain every third-party search competitor plus
    ``nimble_search``, which anchors the table's significance baseline. Order is
    significant — it is the published row ordering, and it follows
    ``PROVIDER_CAPABILITIES`` insertion order."""
    expanded = samplers.expand_sampler_names(["all_apis"])

    assert expanded == [
        "nimble_search",
        "exa_search_auto",
        "exa_search_fast",
        "parallel_search_basic",
        "parallel_search_turbo",
        "tavily_search_basic",
        "tavily_search_fast",
        "brave_search",
        "firecrawl_search",
    ]


@pytest.mark.unit
def test_all_apis_fills_the_leaderboard_table():
    """A full dispatch must give the paired-t test several lanes to compare
    against its baseline, all of them ``/search`` lanes."""
    expanded = samplers.expand_sampler_names(["all_apis"])
    kinds = [PROVIDER_CAPABILITIES[name].response_kind for name in expanded]

    assert kinds.count("search_results") >= 2
    assert set(kinds) == {"search_results"}
    assert "nimble_search" in expanded


@pytest.mark.unit
def test_all_apis_alias_covers_every_prod_lane():
    """``all_apis`` is the "run everything" alias behind ``make eval``, derived
    from ``PROVIDER_CAPABILITIES`` rather than hand-listed so a new sampler
    joins automatically. A hand-maintained list has dropped a lane from a full
    dispatch before.
    """
    expanded = set(samplers.expand_sampler_names(["all_apis"]))

    assert expanded == set(samplers.PROVIDER_CAPABILITIES) - samplers.EXCLUDED_SAMPLERS
    # The lane variants that are easy to forget, pinned explicitly.
    assert {"tavily_search_basic", "tavily_search_fast"} <= expanded
    # The opt-out hook still holds even though it is empty today.
    assert not expanded & samplers.EXCLUDED_SAMPLERS


@pytest.mark.unit
def test_firecrawl_search_is_a_published_lane() -> None:
    """``firecrawl_search`` is deliberately in the published set: it is swept
    into the auto-derived ``all_apis`` and therefore onto the public table.
    Running it needs a ``FIRECRAWL_API_KEY``, which is why a narrower roster is
    named lane by lane rather than aliased."""
    assert "firecrawl_search" in samplers.expand_sampler_names(["all_apis"])


@pytest.mark.unit
def test_all_apis_is_the_only_alias():
    """The alias table was deliberately collapsed to one entry: every other
    roster is named lane by lane, so the dispatched set is visible in the
    invocation instead of hidden behind a name that drifts. A new alias is a
    deliberate decision, not a convenience.
    """
    assert set(samplers.ALIASES) == {"all_apis"}
