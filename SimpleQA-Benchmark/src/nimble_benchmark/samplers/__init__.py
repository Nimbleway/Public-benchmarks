"""Sampler factory and provider capabilities.

Every shipped lane is a ``/search`` lane: its graded answer is synthesized
eval-side from the ranked chunks, so the lanes are comparable -- each measures
the retrieval it feeds to one fixed synthesizer.

The answer-lane surface is modelled but unpopulated: a lane registered
``("answer_with_citations", "api")`` is graded on its own answer instead. There
is no ``--answer-source`` knob; the source follows from
:attr:`ProviderCapability.answer_source`.

Alias groups (``ALIASES``)
--------------------------
``all_apis`` — the one alias, and every lane in it. Derived from
    ``PROVIDER_CAPABILITIES`` instead of hand-listed, so a newly added sampler
    joins automatically and no "run everything" list can silently go stale.
    This is the alias behind ``make eval``. Excludes only
    ``EXCLUDED_SAMPLERS``.

Any narrower roster is spelled out lane by lane on the command line, which
keeps the dispatched set visible in the invocation rather than hidden behind a
name that drifts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import SecretStr

from nimble_benchmark.config import Settings
from nimble_benchmark.models import ResponseKind
from nimble_benchmark.samplers.base import BaseSampler
from nimble_benchmark.samplers.brave_search import BraveSearchSampler
from nimble_benchmark.samplers.exa_search import ExaSearchSampler, ExaSearchType
from nimble_benchmark.samplers.firecrawl_search import FirecrawlSearchSampler
from nimble_benchmark.samplers.nimble_search import NimbleSearchSampler
from nimble_benchmark.samplers.parallel_search import ParallelMode, ParallelSearchSampler
from nimble_benchmark.samplers.tavily_search import TavilyDepth, TavilySearchSampler

# Where a lane's graded answer comes from. ``api`` is the provider's own
# answer text; ``synth`` is eval-side synthesis over the ranked chunks. This
# is a property of the lane, not a per-run choice -- a /search lane has no
# server-side answer to grade, and an answer lane has no ranked list to
# synthesize from. Every lane shipped today is ``synth``.
AnswerSource = Literal["api", "synth"]


@dataclass(frozen=True)
class ProviderCapability:
    provider: str
    response_kind: ResponseKind
    answer_source: AnswerSource


PROVIDER_CAPABILITIES: dict[str, ProviderCapability] = {
    "nimble_search": ProviderCapability("nimble_search", "search_results", "synth"),
    # Exa ships two lanes rather than one env-configurable one: ``type`` is the
    # customer-facing product choice (quality vs ~450ms), so both ends are
    # measured on every dispatch. ``exa_search_auto`` is pinned to ``type=auto``
    # (Exa's own default), ``exa_search_fast`` to ``type=fast``; both are built
    # from the same EXA_API_KEY and share one rate-limit bucket.
    "exa_search_auto": ProviderCapability("exa_search_auto", "search_results", "synth"),
    "exa_search_fast": ProviderCapability("exa_search_fast", "search_results", "synth"),
    # Parallel ships two lanes rather than one env-configurable one: ``mode`` is
    # the customer-facing product choice (recall vs ~200ms p50), so both ends are
    # measured on every dispatch. ``parallel_search_basic`` is pinned to
    # ``mode=basic``, ``parallel_search_turbo`` to ``mode=turbo``; both are
    # built from the same PARALLEL_API_KEY and share one rate-limit bucket.
    "parallel_search_basic": ProviderCapability("parallel_search_basic", "search_results", "synth"),
    "parallel_search_turbo": ProviderCapability("parallel_search_turbo", "search_results", "synth"),
    # Depth pinned per lane, as with Exa's types. See ``tavily_search``.
    "tavily_search_basic": ProviderCapability("tavily_search_basic", "search_results", "synth"),
    "tavily_search_fast": ProviderCapability("tavily_search_fast", "search_results", "synth"),
    "brave_search": ProviderCapability("brave_search", "search_results", "synth"),
    "firecrawl_search": ProviderCapability("firecrawl_search", "search_results", "synth"),
}


# Exa lane -> pinned ``type``. The single source of truth for which Exa search
# types the eval runs: the factory, the credential check
# (``config.require_credentials_for_samplers``) and the tests all read it, so
# adding a type lane means adding one entry here plus its
# ``PROVIDER_CAPABILITIES`` row.
EXA_LANE_TYPES: dict[str, ExaSearchType] = {
    "exa_search_auto": "auto",
    "exa_search_fast": "fast",
}


# Parallel lane -> pinned ``mode``. The single source of truth for which
# Parallel modes the eval runs: the factory, the credential check
# (``config.require_credentials_for_samplers``) and the tests all read it, so
# adding a mode lane means adding one entry here plus its
# ``PROVIDER_CAPABILITIES`` row.
PARALLEL_LANE_MODES: dict[str, ParallelMode] = {
    "parallel_search_basic": "basic",
    "parallel_search_turbo": "turbo",
}


# Tavily lane -> pinned ``search_depth``. The factory, the credential check and
# the tests all read this, so a new depth lane means one entry here plus its
# ``PROVIDER_CAPABILITIES`` row.
TAVILY_LANE_DEPTHS: dict[str, TavilyDepth] = {
    "tavily_search_basic": "basic",
    "tavily_search_fast": "fast",
}


# Lanes that must never be swept into a "run everything" alias. Currently
# empty. Kept as the single opt-out hook so a lane that should not be run by
# default can be excluded from ``all_apis`` with a one-line edit;
# ``cli`` imports it rather than keeping its own copy.
EXCLUDED_SAMPLERS: frozenset[str] = frozenset()


def _all_sampler_names() -> list[str]:
    """Every lane, derived from :data:`PROVIDER_CAPABILITIES`.

    Deliberately computed rather than hand-listed: a hand-maintained "run
    everything" list silently goes stale the moment a sampler is added, which
    is exactly how a lane went missing from a full n=500 run once already.
    Deriving it means a new entry in ``PROVIDER_CAPABILITIES`` joins
    ``all_apis`` automatically; a lane that should NOT be swept in belongs in
    :data:`EXCLUDED_SAMPLERS`.
    """
    return [name for name in PROVIDER_CAPABILITIES if name not in EXCLUDED_SAMPLERS]


ALIASES: dict[str, list[str]] = {
    # The only alias: every lane, derived from ``PROVIDER_CAPABILITIES`` rather
    # than hand-listed, so a newly registered sampler joins automatically.
    # Backs ``make eval``. A lane that must NOT be swept in belongs in
    # ``EXCLUDED_SAMPLERS``.
    "all_apis": _all_sampler_names(),
}


def expand_sampler_names(names: list[str]) -> list[str]:
    expanded: list[str] = []
    seen: set[str] = set()
    for name in names:
        resolved_names = ALIASES.get(name, [name])
        for resolved_name in resolved_names:
            if resolved_name not in PROVIDER_CAPABILITIES:
                raise ValueError(f"Unknown sampler: {resolved_name}")
            if resolved_name not in seen:
                seen.add(resolved_name)
                expanded.append(resolved_name)
    return expanded


def answer_source_for_provider(provider: str) -> AnswerSource:
    """Where this lane's graded answer comes from.

    Fixed per lane rather than chosen per run: a /search lane has no
    server-side answer to grade and an answer lane has no ranked list to
    synthesize from, so there is exactly one correct source for each.
    """
    capability = PROVIDER_CAPABILITIES.get(provider)
    if capability is None:
        raise ValueError(f"Unknown provider or sampler: {provider}")
    return capability.answer_source


def build_samplers(*, settings: Settings, sampler_names: list[str]) -> list[BaseSampler]:
    """Construct samplers for the requested provider list.

    Every lane here is retrieval-only: none of them requests a server-side
    answer, because their graded answer is synthesized eval-side from the
    ranked chunks.
    """
    sampler_names = expand_sampler_names(sampler_names)

    samplers: list[BaseSampler] = []
    for name in sampler_names:
        if name == "nimble_search":
            api_key = _required_secret_value(settings.nimble_api_key, "NIMBLE_API_KEY")
            samplers.append(
                NimbleSearchSampler(
                    name="nimble_search",
                    base_url=settings.nimble_base_url,
                    api_key=api_key,
                    search_depth=settings.nimble_search_depth,
                )
            )
        elif name in TAVILY_LANE_DEPTHS:
            tavily_search_key = _optional_secret_value(settings.tavily_api_key)
            if tavily_search_key is None:
                raise ValueError(f"{name} requires TAVILY_API_KEY")
            samplers.append(
                TavilySearchSampler(
                    name=name,
                    api_key=tavily_search_key,
                    # Pinned per lane, not read from the environment: the two
                    # lanes exist precisely so both depths are always measured,
                    # and an env override would collapse them onto one depth
                    # while still writing two rows.
                    search_depth=TAVILY_LANE_DEPTHS[name],
                )
            )
        elif name in EXA_LANE_TYPES:
            exa_search_key = _optional_secret_value(settings.exa_api_key)
            if exa_search_key is None:
                raise ValueError(f"{name} requires EXA_API_KEY")
            samplers.append(
                ExaSearchSampler(
                    name=name,
                    api_key=exa_search_key,
                    # Pinned per lane, not read from the environment: the two
                    # lanes exist precisely so both types are always measured,
                    # and an env override would collapse them onto one type
                    # while still writing two rows.
                    search_type=EXA_LANE_TYPES[name],
                )
            )
        elif name in PARALLEL_LANE_MODES:
            parallel_search_key = _optional_secret_value(settings.parallel_api_key)
            if parallel_search_key is None:
                raise ValueError(f"{name} requires PARALLEL_API_KEY")
            samplers.append(
                ParallelSearchSampler(
                    name=name,
                    api_key=parallel_search_key,
                    # Pinned per lane, not read from the environment: the two
                    # lanes exist precisely so both modes are always measured,
                    # and an env override would collapse them onto one mode
                    # while still writing two rows.
                    mode=PARALLEL_LANE_MODES[name],
                )
            )
        elif name == "firecrawl_search":
            firecrawl_search_key = _optional_secret_value(settings.firecrawl_api_key)
            if firecrawl_search_key is None:
                raise ValueError("firecrawl_search requires FIRECRAWL_API_KEY")
            samplers.append(FirecrawlSearchSampler(name="firecrawl_search", api_key=firecrawl_search_key))
        elif name == "brave_search":
            brave_search_key = _optional_secret_value(settings.brave_search_api_key)
            if brave_search_key is None:
                raise ValueError("brave_search requires BRAVE_SEARCH_API_KEY")
            samplers.append(BraveSearchSampler(name="brave_search", api_key=brave_search_key))
        else:
            raise ValueError(f"Unknown sampler: {name}")
    return samplers


def _optional_value(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _optional_secret_value(secret: SecretStr | None) -> str | None:
    if secret is None:
        return None
    value = secret.get_secret_value()
    stripped = value.strip()
    return stripped or None


def _required_secret_value(secret: SecretStr | None, env_name: str) -> str:
    value = _optional_secret_value(secret)
    if value is None:
        raise ValueError(f"{env_name} is required")
    return value


__all__ = [
    "ALIASES",
    "EXA_LANE_TYPES",
    "EXCLUDED_SAMPLERS",
    "PARALLEL_LANE_MODES",
    "PROVIDER_CAPABILITIES",
    "TAVILY_LANE_DEPTHS",
    "AnswerSource",
    "BraveSearchSampler",
    "ExaSearchSampler",
    "FirecrawlSearchSampler",
    "NimbleSearchSampler",
    "ParallelSearchSampler",
    "ProviderCapability",
    "TavilySearchSampler",
    "answer_source_for_provider",
    "build_samplers",
    "expand_sampler_names",
]
