"""Sampler for Brave Search API (web search results).

Brave's ``/res/v1/web/search`` returns a ranked list of URLs + snippets per
query -- the canonical "search engine" interface, no LLM in the loop. It is a
plain JSON-over-HTTP **GET**, so :meth:`get_search_results` is overridden to
send ``params=`` instead of a JSON body while still reusing
:class:`BaseHTTPPostSampler`'s session / retry / error-mapping skeleton.

Two behaviours below were surfaced by measuring this lane against SimpleQA.
Both matter for correctness, not style:

SimpleQA quirk (``operators=False``)
    Brave defaults ``operators=true``, which treats a ``"quoted phrase"`` as a
    required exact match and ``site:`` / ``-foo`` as filters. SimpleQA wraps
    article, book, and song titles in straight quotes, and the exact phrase
    rarely appears verbatim on any indexed page -- so Brave returned an empty
    result set for roughly 12% of the dataset. Defaulting ``operators=False``
    recovers those rows. Do not "clean this up" without re-measuring the empty
    result rate.

Brave 422 quirk
    Brave answers ``422`` for some long or validation-sensitive queries. The
    base class's default ``_validation_reject_statuses`` ({400, 422}) already
    treats that as an empty result set rather than retrying, which is what we
    want -- Brave will not accept the query no matter how many times we ask.
    ``429`` and ``5xx`` *are* retried. ``X-RateLimit-Reset`` is captured in
    ``_last_headers`` but not used to schedule delays; the token-bucket limiter
    prevents sustained 429 storms instead.

Quota warning
    Brave's free tier hard-caps at 1 request/second with a 2,000 request/month
    allowance, so one ``--limit 500`` run costs ~8.5 minutes of wall clock for
    this lane alone and burns a quarter of the monthly free allowance. Set
    ``BRAVE_RATE_PER_SECOND`` on a paid plan to lift the throttle.
"""

from __future__ import annotations

import contextlib
import os
from typing import Any

import aiohttp

from nimble_benchmark.models import ProviderUsage, ResponseKind, RetrievalChunk
from nimble_benchmark.retry import RetryConfig, TransientHTTPError, retry_async
from nimble_benchmark.samplers._http_post_base import BaseHTTPPostSampler
from nimble_benchmark.samplers._rate_limit import get_limiter

# Brave free tier: 1 req/sec hard cap
# (https://api-dashboard.search.brave.com/app/subscriptions/subscribe).
# We self-throttle to that ceiling, so retry's job is only to absorb genuine
# transient blips -- not to act as a load-shedding gate against our own
# over-issued QPS. Retry stays generous because the monthly quota window can
# still surprise us.
DEFAULT_BRAVE_RATE_PER_SECOND = 1.0
BRAVE_RATE_ENV_VAR = "BRAVE_RATE_PER_SECOND"

BRAVE_SEARCH_RETRY_CONFIG = RetryConfig(max_attempts=5, initial_wait=2.0, max_wait=10.0)


def _resolve_brave_rate(rate_per_second: float | None) -> float:
    """Caller arg wins; otherwise honor the env var; otherwise the free-tier cap."""
    if rate_per_second is not None:
        return rate_per_second
    env = os.getenv(BRAVE_RATE_ENV_VAR)
    if env:
        return float(env)
    return DEFAULT_BRAVE_RATE_PER_SECOND


class BraveSearchSampler(BaseHTTPPostSampler):
    """Search-only Brave sampler.

    Returns ``response_kind="search_results"`` -- no native ``api_answer``.
    The runner synthesizes an answer eval-side from the ranked results.
    """

    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        base_url: str = "https://api.search.brave.com",
        count: int = 10,
        extra_snippets: bool = True,
        operators: bool = False,
        timeout: float = 60.0,
        retry_config: RetryConfig | None = None,
        rate_per_second: float | None = None,
    ):
        rate = _resolve_brave_rate(rate_per_second)
        super().__init__(
            name=name,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            timeout_env_var="BRAVE_TIMEOUT_S",
            retry_config=retry_config or BRAVE_SEARCH_RETRY_CONFIG,
            limiter=get_limiter("brave", api_key, rate),
        )
        self.count = count
        self.extra_snippets = extra_snippets
        self.operators = operators
        self.rate_per_second = rate

    def response_kind(self) -> ResponseKind:
        return "search_results"

    def _endpoint(self) -> str:
        return "/res/v1/web/search"

    def _headers(self) -> dict[str, str]:
        return {
            "X-Subscription-Token": self.api_key,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        }

    def _build_payload(self, query: str) -> dict[str, Any]:
        # Brave's GET endpoint takes query params, not a JSON body, so this dict
        # is passed as ``params=`` by ``get_search_results``. Booleans are sent
        # as lowercase strings because aiohttp will not encode Python bools.
        return {
            "q": query,
            "count": self.count,
            "extra_snippets": str(self.extra_snippets).lower(),
            "operators": str(self.operators).lower(),
        }

    def _validation_reject_response(self) -> dict[str, Any]:
        return {"web": {"results": []}}

    async def get_search_results(self, query: str) -> dict[str, Any]:
        # Overrides the base class because Brave's web-search endpoint is a GET
        # with query params, not a POST with a JSON body. The body mirrors the
        # base class except for the HTTP verb and ``params=`` vs ``json=``.
        gate = self._limiter if self._limiter is not None else contextlib.nullcontext()

        async def attempt() -> dict[str, Any]:
            # Acquire BEFORE the network call (including retries): the limiter
            # is the global QPS gate, and retry must not bypass it just because
            # we already paid the wait once.
            # Timer inside the gate: Brave's 1 RPS free tier makes the limiter
            # wait the single largest term in the outer wall clock, and it is
            # ours, not Brave's. See ``BaseSampler._timed_provider_call``.
            async with gate, self._timed_provider_call():
                try:
                    async with (
                        aiohttp.ClientSession(timeout=self._timeout) as session,
                        session.get(
                            f"{self.base_url}{self._endpoint()}",
                            params=self._build_payload(query),
                            headers=self._headers(),
                        ) as response,
                    ):
                        self._last_headers = dict(response.headers)
                        if response.status in self._validation_reject_statuses:
                            self._last_status = "validation_reject"
                            return {**self._validation_reject_response(), "_status": "validation_reject"}
                        if self._is_retryable_status(response.status):
                            raise TransientHTTPError(response.status, await response.text())
                        response.raise_for_status()
                        return await response.json()
                except aiohttp.ClientResponseError:
                    raise
                except (aiohttp.ClientError, TimeoutError) as exc:
                    raise TransientHTTPError(0, str(exc)) from exc

        return await retry_async(attempt, self.retry_config)

    def extract_chunks(self, raw: dict[str, Any]) -> list[RetrievalChunk]:
        chunks: list[RetrievalChunk] = []
        for position, result in enumerate(raw.get("web", {}).get("results", []) or []):
            url = result.get("url", "") or ""
            if not url:
                continue
            title = result.get("title", "") or ""
            description = result.get("description", "") or ""
            raw_extras = result.get("extra_snippets") or []
            extra_snippets = [str(snippet) for snippet in raw_extras if isinstance(snippet, str) and snippet]
            if not (description or extra_snippets):
                continue
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

    def extract_usage(self, raw: dict[str, Any]) -> ProviderUsage:
        return ProviderUsage()
