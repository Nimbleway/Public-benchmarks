"""Sampler for Parallel POST /v1/search (search-only, no native answer).

Parallel's Search API returns a ranked list of URLs plus ``excerpts`` -- text
extracts its docs describe as "LLM-optimized", i.e. already condensed for
downstream model consumption rather than raw page bodies. That makes it the
closest analogue to the ``exa_search_*`` lanes' ``contents.summary``: the runner gets pure
retrieval output and synthesizes an answer eval-side from the ranked results.

Request shape (https://docs.parallel.ai/api-reference/search/search):

* ``search_queries`` is the only REQUIRED field -- ``objective`` alone is
  rejected. Parallel recommends "concise keyword search queries, 3-6 words
  each, 2-3 for best results".
* ``mode`` is ``turbo`` | ``basic`` | ``advanced``, defaulting to ``advanced``.
* ``advanced_settings.max_results`` defaults to 10, matching every other
  search lane in this eval.

Two lanes, one provider
-----------------------
Parallel's mode is not a tuning knob we pick once -- it is the product
decision a Parallel customer makes, and the modes behave like different
search engines (a richer mode buys recall, ``turbo`` buys a ~200ms p50). So
the eval registers two pinned lanes rather than one env-configurable one:
``parallel_search_basic`` (``mode=basic``) and ``parallel_search_turbo``
(``mode=turbo``). Both are built from the same ``PARALLEL_API_KEY`` and both
are published, so every dispatch that includes Parallel measures both modes
on the same slice. ``advanced`` is reachable only by constructing this sampler
directly; no lane pins it.

Query-mapping caveat
--------------------
SimpleQA rows are full natural-language questions, not 3-6 word keyword
queries, and this sampler sends the question verbatim as a single
``search_queries`` entry (and as ``objective``). That is deliberate: every
other search lane (``nimble_search``, ``exa_search_*``) also
receives the raw question, so rewriting only Parallel's input -- whether by
hand or with an LLM -- would make its row incomparable and would smuggle an
extra retrieval-assist step into one competitor's numbers. The tradeoff is
that Parallel is being measured slightly outside its documented sweet spot;
note it when reading the leaderboard.
"""

from __future__ import annotations

import os
from typing import Any, Literal

from nimble_benchmark.models import ProviderUsage, ResponseKind, RetrievalChunk
from nimble_benchmark.retry import RetryConfig
from nimble_benchmark.samplers._http_post_base import BaseHTTPPostSampler
from nimble_benchmark.samplers._rate_limit import get_limiter

ParallelMode = Literal["turbo", "basic", "advanced"]

PARALLEL_SEARCH_RETRY_CONFIG = RetryConfig(max_attempts=3, initial_wait=0.5, max_wait=5.0)

# Parallel documents a default quota of 600 requests/minute on POST /v1/search
# (https://docs.parallel.ai/getting-started/rate-limits) == 10 RPS. 9 leaves a
# margin for clock skew and for bursts aiolimiter rounds up, mirroring the Exa
# treatment of its own 10 QPS ceiling.
DEFAULT_PARALLEL_RATE_PER_SECOND = 9.0
PARALLEL_RATE_ENV_VAR = "PARALLEL_RATE_PER_SECOND"


def _resolve_parallel_rate(rate_per_second: float | None) -> float:
    """Caller arg wins; otherwise honor the env var; otherwise the safe default."""
    if rate_per_second is not None:
        return rate_per_second
    env = os.getenv(PARALLEL_RATE_ENV_VAR)
    if env:
        return float(env)
    return DEFAULT_PARALLEL_RATE_PER_SECOND


class ParallelSearchSampler(BaseHTTPPostSampler):
    """Search-only Parallel sampler.

    Returns ``response_kind="search_results"`` -- no native ``api_answer``.
    """

    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        base_url: str = "https://api.parallel.ai",
        mode: ParallelMode = "advanced",
        max_results: int = 10,
        max_chars_per_result: int | None = None,
        timeout: float = 60.0,
        retry_config: RetryConfig | None = None,
        rate_per_second: float | None = None,
    ):
        if mode not in {"turbo", "basic", "advanced"}:
            raise ValueError("mode must be one of 'turbo', 'basic', or 'advanced'")
        rate = _resolve_parallel_rate(rate_per_second)
        super().__init__(
            name=name,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            timeout_env_var="PARALLEL_TIMEOUT_S",
            retry_config=retry_config or PARALLEL_SEARCH_RETRY_CONFIG,
            limiter=get_limiter("parallel", api_key, rate),
        )
        self.mode = mode
        self.max_results = max_results
        self.max_chars_per_result = max_chars_per_result
        self.rate_per_second = rate

    def response_kind(self) -> ResponseKind:
        return "search_results"

    def _endpoint(self) -> str:
        return "/v1/search"

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _build_payload(self, query: str) -> dict[str, Any]:
        # ``search_queries`` is required; ``objective`` is sent alongside it so
        # Parallel gets the full intent rather than only the keyword form. See
        # the module docstring for why the raw question is used for both.
        advanced_settings: dict[str, Any] = {"max_results": self.max_results}
        if self.max_chars_per_result is not None:
            advanced_settings["excerpt_settings"] = {"max_chars_per_result": self.max_chars_per_result}
        return {
            "objective": query,
            "search_queries": [query],
            "mode": self.mode,
            "advanced_settings": advanced_settings,
        }

    def _validation_reject_response(self) -> dict[str, Any]:
        return {"results": []}

    def extract_chunks(self, raw: dict[str, Any]) -> list[RetrievalChunk]:
        chunks: list[RetrievalChunk] = []
        for position, result in enumerate(raw.get("results", []) or []):
            url = result.get("url", "") or ""
            if not url:
                continue
            # ``excerpts`` is a list of separate extracts from one page. They are
            # joined into ``description`` rather than split across
            # ``extra_snippets`` because the eval-side synthesizer formats only
            # ``title``/``url``/``description`` (runner.synthesize_answer),
            # so anything left in extra_snippets would be invisible to synthesis
            # while still being judged -- which would score retrieval on text the
            # answer was never allowed to use.
            excerpts = [str(excerpt) for excerpt in (result.get("excerpts") or []) if excerpt]
            description = "\n\n".join(excerpts)
            if not description:
                continue
            chunks.append(
                RetrievalChunk(
                    url=url,
                    title=result.get("title", "") or "",
                    description=description,
                    extra_snippets=[],
                    position=position,
                )
            )
        return chunks

    def extract_usage(self, raw: dict[str, Any]) -> ProviderUsage:
        return ProviderUsage()
