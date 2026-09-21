"""Sampler for Firecrawl ``POST /v2/search`` via the official Python SDK.

Firecrawl's search endpoint returns a ranked list of URLs + SERP snippets --
the same "search engine" surface as ``brave_search``, no
LLM in the loop. The runner synthesizes an answer eval-side from the ranked
results.

SDK, not raw HTTP
-----------------
Unlike the other search lanes this one does not subclass
:class:`~nimble_benchmark.samplers._http_post_base.BaseHTTPPostSampler`: it
drives ``AsyncFirecrawl.search()`` from ``firecrawl-py``, which owns request
shaping (snake_case -> camelCase), response parsing, and error typing. That
keeps us aligned with Firecrawl's own contract instead of pinning a hand-rolled
copy of ``/v2/search``'s payload, and it is the interface Firecrawl documents.
The cost is that the SDK's transport is ``httpx``, so the retry/limiter
skeleton the aiohttp base class provides is reimplemented here (small: one
``retry_async`` call and one limiter gate, mirroring ``brave_search``).

The SDK ships its own retry loop (``max_retries=3`` on 502 / transport errors).
We pin it to ``1`` so retries flow through :func:`retry_async` instead --
otherwise the SDK would retry *inside* our limiter gate, bypassing the QPS
ceiling exactly when the upstream is already unhappy.

Snippets only, no page scraping
-------------------------------
``search()`` can attach scraped page content per result via ``scrape_options``,
which would return :class:`firecrawl.v2.types.Document` objects carrying full
markdown. We deliberately do not request it: the leaderboard's Search APIs
table compares SERP-snippet retrieval across vendors, and a lane fed full page
bodies is not comparable to Brave/Exa on either UMBRELA relevance or
latency (and it multiplies credit spend per row). A Nimble full-content lane
is where the full-content question is asked, deliberately, as a one-knob A/B.

Rate limits
-----------
Firecrawl caps ``/search`` per minute by plan: Free 10, Hobby 100, Standard
500, Growth 5,000 (https://docs.firecrawl.dev/rate-limits). The default here is
1 req/s -- comfortably under Hobby's 100/min and usable for a 500-row run in
~8.5 minutes. It is deliberately NOT the free-tier ceiling (0.16 req/s), which
would stretch the same run past 50 minutes and drag every ``make eval``
dispatch: on a **Free** key set ``FIRECRAWL_RATE_PER_SECOND=0.16`` or the lane
will 429-storm. 429s are retried with backoff, which absorbs an occasional
overrun but cannot rescue a sustained one.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from firecrawl import AsyncFirecrawl

# Not re-exported from the package root; ``FirecrawlError`` is the base class of
# every status-carrying SDK exception (400/401/402/403/408/429/500/other), each
# of which exposes ``status_code``.
from firecrawl.v2.utils.error_handler import FirecrawlError

from nimble_benchmark.models import ProviderUsage, ResponseKind, RetrievalChunk
from nimble_benchmark.retry import RetryConfig, TransientHTTPError, retry_async
from nimble_benchmark.samplers._rate_limit import get_limiter
from nimble_benchmark.samplers.base import BaseSampler

# See the "Rate limits" section of the module docstring for why this is 1.0
# rather than the free tier's 0.16.
DEFAULT_FIRECRAWL_RATE_PER_SECOND = 1.0
FIRECRAWL_RATE_ENV_VAR = "FIRECRAWL_RATE_PER_SECOND"

FIRECRAWL_SEARCH_RETRY_CONFIG = RetryConfig(max_attempts=5, initial_wait=2.0, max_wait=10.0)

# Statuses that mean "Firecrawl refused this query and will refuse it again":
# record an empty result set instead of burning retries. 402 (out of credits)
# and 401/403 (auth) are NOT here -- those are run-level problems that must
# surface as failures so ``errors.md`` classifies them.
_VALIDATION_REJECT_STATUSES = frozenset({400, 422})
_RETRYABLE_STATUSES = frozenset({408, 425, 429}) | frozenset(range(500, 600))


def _resolve_firecrawl_rate(rate_per_second: float | None) -> float:
    """Caller arg wins; otherwise honor the env var; otherwise the default."""
    if rate_per_second is not None:
        return rate_per_second
    env = os.getenv(FIRECRAWL_RATE_ENV_VAR)
    if env:
        return float(env)
    return DEFAULT_FIRECRAWL_RATE_PER_SECOND


class FirecrawlSearchSampler(BaseSampler):
    """Search-only Firecrawl sampler.

    Returns ``response_kind="search_results"`` -- no native ``api_answer``.
    """

    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        api_url: str = "https://api.firecrawl.dev",
        num_results: int = 10,
        timeout: float = 60.0,
        retry_config: RetryConfig | None = None,
        rate_per_second: float | None = None,
    ) -> None:
        super().__init__(name=name)
        self.api_key = api_key
        # Normalized like every aiohttp sampler's ``base_url``: this value is
        # recorded in ``sampler_config_firecrawl_search.json``, so a stray
        # trailing slash would make two identically-configured runs look
        # differently parameterized in their artifacts.
        self.api_url = api_url.rstrip("/")
        # Named ``num_results`` rather than the SDK's ``limit`` on purpose: the
        # per-sampler recorded config already carries the run's row ``limit``
        # (``--limit N``), and a second key of the same name would collide in
        # ``sampler_config_firecrawl_search.json``. The ``exa_search_*`` lanes use the same
        # name for the same knob.
        self.num_results = num_results
        self._timeout_s = float(os.getenv("FIRECRAWL_TIMEOUT_S", str(timeout)))
        self.retry_config = retry_config or FIRECRAWL_SEARCH_RETRY_CONFIG
        rate = _resolve_firecrawl_rate(rate_per_second)
        self.rate_per_second = rate
        self._limiter = get_limiter("firecrawl", api_key, rate)
        self._client: AsyncFirecrawl | None = None

    def response_kind(self) -> ResponseKind:
        return "search_results"

    # ------------------------------------------------------------------
    # Request
    # ------------------------------------------------------------------

    def _get_client(self) -> AsyncFirecrawl:
        """One SDK client per sampler, built on first use.

        Firecrawl's transport already pins ``max_keepalive_connections=0``, so
        there is no idle socket pool to leak, and the SDK exposes no public
        close hook to call (the ``httpx`` client hangs off a private
        attribute). Building it lazily rather than in ``__init__`` keeps
        sampler construction -- which happens in ``build_samplers``, and in
        every unit test -- free of transport setup.
        """
        if self._client is None:
            self._client = AsyncFirecrawl(
                api_key=self.api_key,
                api_url=self.api_url,
                timeout=self._timeout_s,
                # Retry is ours: the SDK's own loop would fire inside the
                # limiter gate and bypass the per-key QPS ceiling.
                max_retries=1,
            )
        return self._client

    def _search_kwargs(self) -> dict[str, Any]:
        return {
            "limit": self.num_results,
            # Explicit rather than relying on the server default, so the
            # request recorded in ``sampler_config_firecrawl_search.json``
            # fully determines what was asked for.
            "sources": ["web"],
            # Server-side budget, in milliseconds, matched to our own
            # per-attempt timeout so a slow upstream can't outlive the row.
            "timeout": int(self._timeout_s * 1000),
        }

    async def get_search_results(self, query: str) -> dict[str, Any]:
        async def attempt() -> dict[str, Any]:
            # Acquired before every attempt, retries included -- the limiter is
            # the global QPS gate and a backoff-driven retry must not skip it.
            # Timer inside the gate so the 1 RPS default throttle stays out of
            # the measured round trip. See ``BaseSampler._timed_provider_call``.
            async with self._limiter, self._timed_provider_call():
                try:
                    data = await self._get_client().search(query, **self._search_kwargs())
                except FirecrawlError as exc:
                    return self._handle_firecrawl_error(exc)
                except ValueError as exc:
                    # Client-side request validation (empty query, bad limit).
                    # Same class of outcome as a server 400: retrying is futile.
                    self._last_status = "validation_reject"
                    return {"web": [], "error": str(exc), "_status": "validation_reject"}
                except httpx.HTTPError as exc:
                    raise TransientHTTPError(0, str(exc)) from exc
            return {"web": [_result_to_dict(result) for result in (getattr(data, "web", None) or [])]}

        return await retry_async(attempt, self.retry_config)

    def _handle_firecrawl_error(self, exc: FirecrawlError) -> dict[str, Any]:
        status = getattr(exc, "status_code", None)
        if status in _VALIDATION_REJECT_STATUSES:
            self._last_status = "validation_reject"
            return {"web": [], "error": str(exc), "_status": "validation_reject"}
        if status in _RETRYABLE_STATUSES:
            raise TransientHTTPError(status, str(exc)) from exc
        # 401 / 402 / 403 and anything unclassified: a run-level problem
        # (expired key, exhausted credits). Let it fail the row loudly so
        # ``errors.md`` groups it under auth/billing rather than burying it.
        raise exc

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def extract_chunks(self, raw: dict[str, Any]) -> list[RetrievalChunk]:
        chunks: list[RetrievalChunk] = []
        for position, result in enumerate(raw.get("web") or []):
            if not isinstance(result, dict):
                continue
            url = result.get("url", "") or ""
            if not url:
                continue
            # Unlike the ``exa_search_*`` lanes (which drop results with no summary) a
            # missing ``description`` is kept here. Exa's summary is the
            # provider's own generated payload, so its absence means the
            # content fetch failed; Firecrawl's ``description`` is a plain SERP
            # snippet, and dropping the row would silently cost the lane a
            # ranked URL on NDCG@10 / Recall@10 -- the leaderboard's headline
            # metrics. An empty snippet just scores 0 with UMBRELA, which is
            # the honest outcome.
            chunks.append(
                RetrievalChunk(
                    url=url,
                    title=result.get("title", "") or "",
                    description=result.get("description", "") or "",
                    extra_snippets=[],
                    position=position,
                )
            )
        return chunks

    def extract_usage(self, raw: dict[str, Any]) -> ProviderUsage:
        return ProviderUsage()


def _result_to_dict(result: Any) -> dict[str, Any]:
    """Normalize one SDK search result into a plain JSON-safe dict.

    ``search()`` returns ``SearchResultWeb`` pydantic models (and would return
    ``Document`` models if page scraping were requested). ``raw`` is persisted
    to CSV, so it has to be plain data either way.
    """
    if isinstance(result, dict):
        return result
    model_dump = getattr(result, "model_dump", None)
    if model_dump is not None:
        dumped = model_dump(exclude_none=True)
        if isinstance(dumped, dict):
            return dumped
    return {
        "url": getattr(result, "url", None),
        "title": getattr(result, "title", None),
        "description": getattr(result, "description", None),
        "position": getattr(result, "position", None),
    }
