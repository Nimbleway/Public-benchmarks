"""Sampler for Tavily POST /search (search-only, no native answer).

``include_answer`` is pinned off: the graded answer is synthesized eval-side
from the ranked chunks, so a Tavily answer would be paid-for output nothing
reads.

``search_depth`` is pinned per lane -- ``tavily_search_basic`` (``basic``) and
``tavily_search_fast`` (``fast``) -- rather than set from the environment. Both
are built from the same ``TAVILY_API_KEY`` and share one rate-limit bucket, so
running both does not multiply the request rate against the key's quota.
"""

from __future__ import annotations

import os
from typing import Any, Literal

from nimble_benchmark.models import ProviderUsage, ResponseKind, RetrievalChunk
from nimble_benchmark.retry import RetryConfig
from nimble_benchmark.samplers._http_post_base import BaseHTTPPostSampler
from nimble_benchmark.samplers._rate_limit import get_limiter

# Tavily publishes no numeric per-second cap, so the default is the
# conservative 1 RPS that fits the free tier. Both lanes share this bucket, so
# it is the aggregate ceiling rather than a per-lane one.
DEFAULT_TAVILY_RATE_PER_SECOND = 1.0
TAVILY_RATE_ENV_VAR = "TAVILY_RATE_PER_SECOND"


def _resolve_tavily_rate(rate_per_second: float | None) -> float:
    if rate_per_second is not None:
        return rate_per_second
    env = os.getenv(TAVILY_RATE_ENV_VAR)
    if env:
        return float(env)
    return DEFAULT_TAVILY_RATE_PER_SECOND


TavilyDepth = Literal["basic", "fast"]
TAVILY_DEPTHS: frozenset[str] = frozenset({"basic", "fast"})

TAVILY_SEARCH_RETRY_CONFIG = RetryConfig(max_attempts=3, initial_wait=0.5, max_wait=5.0)


class TavilySearchSampler(BaseHTTPPostSampler):
    """Search-only Tavily sampler: ``include_answer=False``, ``response_kind="search_results"``."""

    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        base_url: str = "https://api.tavily.com",
        search_depth: TavilyDepth = "basic",
        max_results: int = 10,
        timeout: float = 60.0,
        retry_config: RetryConfig | None = None,
        rate_per_second: float | None = None,
    ) -> None:
        if search_depth not in TAVILY_DEPTHS:
            raise ValueError(f"search_depth must be one of {sorted(TAVILY_DEPTHS)}")
        # One ``("tavily", api_key, rate)`` bucket shared by all four depth
        # lanes, matching Tavily's per-key quota model.
        rate = _resolve_tavily_rate(rate_per_second)
        super().__init__(
            name=name,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            timeout_env_var="TAVILY_TIMEOUT_S",
            retry_config=retry_config or TAVILY_SEARCH_RETRY_CONFIG,
            limiter=get_limiter("tavily", api_key, rate),
        )
        self.search_depth: TavilyDepth = search_depth
        self.max_results = max_results
        self.rate_per_second = rate

    def response_kind(self) -> ResponseKind:
        return "search_results"

    def _endpoint(self) -> str:
        return "/search"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _build_payload(self, query: str) -> dict[str, Any]:
        # ``search_depth`` is sent explicitly even for ``basic`` (the server
        # default) so the request on the wire records which lane produced the
        # row rather than deferring to Tavily's default of the month.
        return {
            "query": query,
            "search_depth": self.search_depth,
            "include_answer": False,
            "max_results": self.max_results,
        }

    def _validation_reject_response(self) -> dict[str, Any]:
        return {"results": []}

    def extract_chunks(self, raw: dict[str, Any]) -> list[RetrievalChunk]:
        chunks: list[RetrievalChunk] = []
        for position, result in enumerate(raw.get("results", []) or []):
            url = result.get("url", "") or ""
            if not url:
                continue
            chunks.append(
                RetrievalChunk(
                    url=url,
                    title=result.get("title", "") or "",
                    # Tavily returns one excerpt per result under ``content``;
                    # there is no second snippet field to split out, so
                    # ``extra_snippets`` stays empty rather than duplicating it.
                    description=result.get("content", "") or "",
                    extra_snippets=[],
                    position=position,
                )
            )
        return chunks

    def extract_usage(self, raw: dict[str, Any]) -> ProviderUsage:
        return ProviderUsage()
