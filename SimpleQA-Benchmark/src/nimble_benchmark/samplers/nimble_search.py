"""Sampler for Nimble POST /search."""

from __future__ import annotations

import contextlib
import os
from typing import Any

import aiohttp

from nimble_benchmark.models import RetrievalChunk
from nimble_benchmark.retry import RETRYABLE_STATUSES, RetryConfig, TransientHTTPError, retry_async
from nimble_benchmark.samplers._rate_limit import get_limiter
from nimble_benchmark.samplers.base import BaseSampler

# The Nimble server rate-limits per *product* -- 'search_fast' and
# 'search_lite' are separate 10 RPS buckets. Retry alone cannot fix an
# over-issuing roster: concurrent tasks back off together and re-collide, so
# rows die as failed_after_retries. Self-throttle to 9 RPS per product. The
# limiter scope includes
# search_depth so lanes on different products keep independent buckets while
# two lanes on the SAME product share one.
DEFAULT_NIMBLE_RATE_PER_SECOND = 9.0
NIMBLE_RATE_ENV_VAR = "NIMBLE_RATE_PER_SECOND"


def _resolve_nimble_rate(rate_per_second: float | None) -> float:
    """Caller arg wins; otherwise honor the env var; otherwise the safe default."""
    if rate_per_second is not None:
        return rate_per_second
    env = os.getenv(NIMBLE_RATE_ENV_VAR)
    if env:
        return float(env)
    return DEFAULT_NIMBLE_RATE_PER_SECOND


class NimbleSearchSampler(BaseSampler):
    """Search-only Nimble sampler (POST /search).

    Returns ``response_kind="search_results"``. The lane is retrieval-only:
    ``include_answer`` is pinned off server-side so /search never pays for an
    answer the eval no longer grades (every answer is synthesized eval-side).
    Uses the narrower ``RETRYABLE_STATUSES`` set intentionally — see
    :mod:`_http_post_base` module docstring for the rationale.
    """

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        search_depth: str = "lite",
        max_results: int = 10,
        full_content: bool = False,
        retry_config: RetryConfig | None = None,
        rate_per_second: float | None = None,
    ):
        super().__init__(name=name)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.search_depth = search_depth
        self.max_results = max_results
        self.full_content = full_content
        self.retry_config = retry_config or RetryConfig()
        self.rate_per_second = _resolve_nimble_rate(rate_per_second)
        # Product-scoped bucket: see module docstring on DEFAULT_NIMBLE_RATE_PER_SECOND.
        self._limiter = get_limiter(f"nimble:search_{search_depth}", api_key, self.rate_per_second)

    def _build_payload(self, query: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": query,
            "max_results": self.max_results,
            "search_depth": self.search_depth,
            # Pinned off: this harness grades eval-side synthesized answers
            # only, so requesting the server-side answer would be paid-for
            # output nothing reads.
            "include_answer": False,
        }
        # ``full_content`` defaults to false server-side, so it is sent only when
        # a lane opts in. Omitting it keeps every other lane's request
        # byte-identical, which also means a deployment predating the flag can
        # still serve them instead of rejecting an unknown field with a 400/422.
        if self.full_content:
            payload["full_content"] = True
        return payload

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def get_search_results(self, query: str) -> dict[str, Any]:
        gate = self._limiter if self._limiter is not None else contextlib.nullcontext()

        async def attempt() -> dict[str, Any]:
            # Acquired before every attempt, including retries, so a backoff
            # storm still respects the server's per-product RPS ceiling. The
            # round-trip timer opens after it, so the queue wait we impose does
            # not get charged to Nimble. See ``BaseSampler._timed_provider_call``.
            async with (
                gate,
                self._timed_provider_call(),
                aiohttp.ClientSession() as session,
                session.post(
                    f"{self.base_url}/search",
                    json=self._build_payload(query),
                    headers=self._headers(),
                ) as response,
            ):
                self._last_headers = dict(response.headers)
                if response.status in {400, 422}:
                    self._last_status = "validation_reject"
                    return {"results": [], "answer": None, "_status": "validation_reject"}
                if response.status in RETRYABLE_STATUSES:
                    raise TransientHTTPError(response.status, await response.text())
                response.raise_for_status()
                return await response.json()

        return await retry_async(attempt, self.retry_config)

    def extract_chunks(self, raw: dict[str, Any]) -> list[RetrievalChunk]:
        chunks: list[RetrievalChunk] = []
        for position, result in enumerate(raw.get("results", []) or []):
            url = result.get("url", "") or ""
            title = result.get("title", "") or ""
            description = result.get("description", "") or ""
            extra = result.get("extra_snippets") or []
            content = result.get("content") or ""
            extra_snippets = [value for value in extra if isinstance(value, str) and value]
            # ``content`` (the full scraped page, present only when
            # ``full_content=True``) folds into ``description``, not
            # ``extra_snippets``: runner.synthesize_answer formats only
            # title/url/description, so content left in extra_snippets reaches
            # the UMBRELA judge but never the answer it is scoring.
            description = f"{description}\n\n{content}".strip() if content else description
            if url or description or extra_snippets:
                chunks.append(
                    RetrievalChunk(
                        url=url,
                        title=title,
                        description=description,
                        extra_snippets=extra_snippets,
                        position=position,
                    )
                )
        return chunks
