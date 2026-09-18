"""Sampler for Exa POST /search (search-only, no native answer).

Exa's ``/search`` endpoint returns a ranked list of URLs + structured page
content. No LLM is in the loop -- the runner gets pure retrieval results and
synthesizes an answer eval-side.

We request ``contents.summary`` (Exa's LLM-generated abstractive summary of
each page) rather than full ``contents.text`` so the passage fed to the
synthesizer is a condensed summary instead of the raw page body. The response
surfaces each summary as ``results[].summary``.

Two lanes, one provider
-----------------------
Exa's ``type`` is not a tuning knob we pick once -- it is the product decision
an Exa customer makes, and its ends behave like different search engines
(``auto`` is the default balance of speed and quality at ~1s; ``fast`` runs
optimized search models at ~450ms). So the eval registers two pinned lanes
rather than one env-configurable one: ``exa_search_auto`` (``type=auto``) and
``exa_search_fast`` (``type=fast``). Both are built from the same
``EXA_API_KEY`` and share one rate-limit bucket, so every dispatch that
includes Exa measures both types on the same slice without doubling the
request rate against the key's quota.

The other documented values (``instant``, ``deep-lite``, ``deep``,
``deep-reasoning``) are accepted by this class but pinned by no lane. The
legacy ``neural`` / ``keyword`` / ``hybrid`` names are deliberately *not*
accepted: Exa's current docs call them legacy terminology rather than a
supported setting for new code.

Latency caveat
--------------
``contents.summary`` puts an LLM summarization step on top of retrieval, and
that step is shared by both lanes. The lanes' measured latency is therefore
``type`` latency *plus* a roughly constant summarization cost -- the gap
between the two rows understates the ratio Exa documents for the bare search
call. See https://exa.ai/docs/reference/search-api-guide-for-coding-agents.
"""

from __future__ import annotations

import os
from typing import Any, Literal

from nimble_benchmark.models import ProviderUsage, ResponseKind, RetrievalChunk
from nimble_benchmark.retry import RetryConfig
from nimble_benchmark.samplers._http_post_base import BaseHTTPPostSampler
from nimble_benchmark.samplers._rate_limit import get_limiter

# Exa's default cap is 10 QPS (https://exa.ai/docs/reference/rate-limits).
# 9 leaves a small safety margin for clock skew and the rare burst that
# aiolimiter rounds up.
DEFAULT_EXA_RATE_PER_SECOND = 9.0
EXA_RATE_ENV_VAR = "EXA_RATE_PER_SECOND"


def _resolve_exa_rate(rate_per_second: float | None) -> float:
    """Caller arg wins; otherwise honor the env var; otherwise the safe default."""
    if rate_per_second is not None:
        return rate_per_second
    env = os.getenv(EXA_RATE_ENV_VAR)
    if env:
        return float(env)
    return DEFAULT_EXA_RATE_PER_SECOND


# The ``type`` values Exa documents for POST /search, in its own latency order
# (~250ms .. 40s). ``auto`` is the server-side default.
ExaSearchType = Literal["instant", "fast", "auto", "deep-lite", "deep", "deep-reasoning"]
EXA_SEARCH_TYPES: frozenset[str] = frozenset({"instant", "fast", "auto", "deep-lite", "deep", "deep-reasoning"})

EXA_SEARCH_RETRY_CONFIG = RetryConfig(max_attempts=3, initial_wait=0.5, max_wait=5.0)


class ExaSearchSampler(BaseHTTPPostSampler):
    """Search-only Exa sampler.

    Returns ``response_kind="search_results"`` -- no native ``api_answer``.
    """

    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        base_url: str = "https://api.exa.ai",
        search_type: ExaSearchType = "auto",
        num_results: int = 10,
        summary: bool | dict[str, Any] | None = None,
        timeout: float = 60.0,
        retry_config: RetryConfig | None = None,
        rate_per_second: float | None = None,
    ) -> None:
        if search_type not in EXA_SEARCH_TYPES:
            raise ValueError(f"search_type must be one of {sorted(EXA_SEARCH_TYPES)}")
        # Same ``("exa", api_key, rate)`` bucket as the sibling type lane so a
        # paired run shares one global 9 QPS cap,
        # matching Exa's per-key QPS quota model.
        rate = _resolve_exa_rate(rate_per_second)
        super().__init__(
            name=name,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            timeout_env_var="EXA_TIMEOUT_S",
            retry_config=retry_config or EXA_SEARCH_RETRY_CONFIG,
            limiter=get_limiter("exa", api_key, rate),
        )
        self.search_type: ExaSearchType = search_type
        self.num_results = num_results
        self.summary: bool | dict[str, Any] = summary if summary is not None else True
        self.rate_per_second = rate

    def response_kind(self) -> ResponseKind:
        return "search_results"

    def _endpoint(self) -> str:
        return "/search"

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _build_payload(self, query: str) -> dict[str, Any]:
        # ``type`` is sent explicitly even for ``auto`` (the server default) so
        # the request on the wire records which lane produced the row rather
        # than deferring to whatever Exa's default happens to be that month.
        return {
            "query": query,
            "type": self.search_type,
            "numResults": self.num_results,
            "contents": {"summary": self.summary},
        }

    def _validation_reject_response(self) -> dict[str, Any]:
        return {"results": []}

    def extract_chunks(self, raw: dict[str, Any]) -> list[RetrievalChunk]:
        chunks: list[RetrievalChunk] = []
        for position, result in enumerate(raw.get("results", []) or []):
            url = result.get("url", "") or ""
            if not url:
                continue
            title = result.get("title", "") or ""
            summary_content = result.get("summary", "") or ""
            if not summary_content:
                continue
            chunks.append(
                RetrievalChunk(
                    url=url,
                    title=title,
                    description=summary_content,
                    extra_snippets=[],
                    position=position,
                )
            )
        return chunks

    def extract_usage(self, raw: dict[str, Any]) -> ProviderUsage:
        return ProviderUsage()
