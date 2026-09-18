"""Base sampler that normalizes provider-specific raw responses."""

from __future__ import annotations

import contextlib
import time
from collections.abc import AsyncIterator
from contextvars import ContextVar
from typing import Any

from nimble_benchmark.metrics.latency import parse_server_timing, parse_server_timing_total_dur
from nimble_benchmark.models import (
    ProviderLatency,
    ProviderResponse,
    ProviderUsage,
    RequestStatus,
    ResponseKind,
    RetrievalChunk,
)
from nimble_benchmark.retry import CitationParseError


class BaseSampler:
    name: str

    def __init__(self, *, name: str):
        self.name = name
        self._last_headers_var: ContextVar[dict[str, str]] = ContextVar(f"{name}_last_headers", default={})
        self._last_status_var: ContextVar[RequestStatus] = ContextVar(f"{name}_last_status", default="ok")
        self._last_provider_ms_var: ContextVar[float | None] = ContextVar(f"{name}_last_provider_ms", default=None)

    @property
    def _last_headers(self) -> dict[str, str]:
        return self._last_headers_var.get()

    @_last_headers.setter
    def _last_headers(self, value: dict[str, str]) -> None:
        self._last_headers_var.set(value)

    @property
    def _last_status(self) -> RequestStatus:
        return self._last_status_var.get()

    @_last_status.setter
    def _last_status(self, value: RequestStatus) -> None:
        self._last_status_var.set(value)

    @property
    def _last_provider_ms(self) -> float | None:
        return self._last_provider_ms_var.get()

    @_last_provider_ms.setter
    def _last_provider_ms(self, value: float | None) -> None:
        self._last_provider_ms_var.set(value)

    @contextlib.asynccontextmanager
    async def _timed_provider_call(self) -> AsyncIterator[None]:
        """Time one network round trip and stash it for :meth:`sample`.

        Wrap ONLY the request itself -- opened *inside* the rate limiter gate
        and *inside* the retry loop's per-attempt body. That placement is the
        whole point: the outer ``sample`` wall clock includes limiter queue
        wait, which on a throttled lane is ~workers/rate seconds and swamps the
        upstream's actual response time (a 1 RPS lane run with 10 workers
        reported a ~10 s p50 with a ~1.5 s p50 round trip underneath it).

        The block must also cover reading the response body, since the upstream
        is still on the hook until the payload is delivered.

        Set in a ``finally`` so a failed attempt still records how long it took
        before it failed, and re-set on every attempt so the last attempt wins.
        """
        start = time.perf_counter()
        try:
            yield
        finally:
            self._last_provider_ms = round((time.perf_counter() - start) * 1000, 2)

    async def get_search_results(self, query: str) -> dict[str, Any]:
        raise NotImplementedError

    def extract_chunks(self, raw: dict[str, Any]) -> list[RetrievalChunk]:
        raise NotImplementedError

    def api_answer_available(self, raw: dict[str, Any]) -> bool:
        """True when the provider returned its own answer text.

        Only the answer-kind lanes override this; every /search lane leaves it
        ``False`` so :attr:`ProviderResponse.api_answer` stays ``None`` and the
        runner synthesizes eval-side from the chunks.
        """
        return False

    def extract_api_answer(self, raw: dict[str, Any]) -> str | None:
        return None

    def response_kind(self) -> ResponseKind:
        return "search_results"

    def extract_internal_ms(self) -> float | None:
        return parse_server_timing_total_dur(self._server_timing_header())

    def extract_stage_timings_ms(self) -> dict[str, float]:
        """Return per-stage durations parsed from the sampler's Server-Timing
        response header, excluding the ``total`` metric (already exposed via
        :meth:`extract_internal_ms`). Empty for samplers that don't emit the
        header (third-party answer APIs today)."""
        timings = parse_server_timing(self._server_timing_header())
        timings.pop("total", None)
        return timings

    def _server_timing_header(self) -> str | None:
        return self._last_headers.get("Server-Timing") or self._last_headers.get("server-timing")

    def extract_usage(self, raw: dict[str, Any]) -> ProviderUsage:
        return ProviderUsage()

    async def sample(self, query: str) -> ProviderResponse:
        start = time.perf_counter()
        self._last_status = "ok"
        # Cleared per row so a sampler that never reaches the network reports
        # ``None`` rather than inheriting the previous row's round trip.
        self._last_provider_ms = None
        try:
            raw = await self.get_search_results(query)
            status: RequestStatus = raw.pop("_status", self._last_status)
        except CitationParseError as exc:
            raw = {"error": str(exc)}
            status = "citation_parse_error"
        except Exception as exc:
            raw = {"error": str(exc)}
            status = "failed_after_retries"
        request_ms = round((time.perf_counter() - start) * 1000, 2)
        response_kind = self.response_kind()
        try:
            answer = self.extract_api_answer(raw) if self.api_answer_available(raw) else None
            chunks = self.extract_chunks(raw)
            usage = self.extract_usage(raw)
        except CitationParseError as exc:
            raw = {"error": str(exc)}
            status = "citation_parse_error"
            answer = None
            chunks = []
            usage = ProviderUsage()
        return ProviderResponse(
            provider=self.name,
            query=query,
            response_kind=response_kind,
            chunks=chunks,
            api_answer=answer,
            latency=ProviderLatency(
                request_response_time_ms=request_ms,
                internal_response_time_ms=self.extract_internal_ms(),
                provider_response_time_ms=self._last_provider_ms,
                stage_timings_ms=self.extract_stage_timings_ms(),
            ),
            status=status,
            raw=raw,
            usage=usage,
        )
