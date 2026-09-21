"""Shared aiohttp-based POST sampler scaffolding.

Several samplers (``exa_search_*``, ``parallel_search_*``, ``brave_search``)
all follow the same skeleton: open an ``aiohttp.ClientSession``, POST a JSON payload to a
provider-specific endpoint, treat ``400``/``422`` as "validation reject" with a
provider-specific empty response shape, retry on transient statuses, wrap
network/timeout exceptions as :class:`TransientHTTPError(0)`, and capture
response headers for ``Server-Timing`` parsing.

Extracting this shared loop into one place lets each concrete sampler stay
small (provider-specific payload + headers + extraction) and means a bug fix
in the retry/error mapping lands once instead of N times. Retry is dispatched
through :func:`nimble_benchmark.retry.retry_async`, which is now a thin
shim over :class:`tenacity.AsyncRetrying` (see ``retry.py`` for details);
:mod:`synthesis.llm` uses tenacity directly via the ``@retry`` decorator.
Both styles funnel through the same tenacity engine.

Excluded from this base class:

* ``firecrawl_search`` drives the ``firecrawl-py`` SDK (``AsyncFirecrawl``);
  its lifecycle and error surface don't match the simple ``aiohttp.post``
  pattern this base owns.
* the ``nimble_search`` lanes apply the project's narrower
  :data:`nimble_benchmark.retry.RETRYABLE_STATUSES` set (``{408, 409, 425,
  429, 500, 502, 503, 504}``) intentionally — Nimble's 5xx surface is well-
  classified and we don't want to retry hot on 501/Not-Implemented or other
  non-recoverable upstream codes. The base default is broader
  (``{408, 425, 429} | 500..599``) because the third-party samplers are
  empirically flakier on a wider transient range.
"""

from __future__ import annotations

import contextlib
import os
from typing import Any

import aiohttp
from aiolimiter import AsyncLimiter

from nimble_benchmark.retry import RetryConfig, TransientHTTPError, retry_async
from nimble_benchmark.samplers.base import BaseSampler

_DEFAULT_RETRY_CONFIG = RetryConfig(max_attempts=3, initial_wait=0.5, max_wait=5.0)


class BaseHTTPPostSampler(BaseSampler):
    """Concrete-by-default base for aiohttp POST samplers.

    Subclasses MUST override:
      * :meth:`_endpoint` -- relative path appended to ``base_url`` (e.g.
        ``"/search"``, ``"/v1/search"``).
      * :meth:`_headers` -- request headers including auth.
      * :meth:`_build_payload` -- request payload; used as ``json=`` for POST or
        as ``params=`` for GET when ``get_search_results`` is overridden.
      * :meth:`_validation_reject_response` -- provider-specific empty response
        shape returned when the upstream responds with a validation-reject
        status. The :pyattr:`_status` sentinel is added by the base class.

    Subclasses MAY override:
      * :attr:`_validation_reject_statuses` -- default ``{400, 422}``.
      * :meth:`_is_retryable_status` -- default ``{408, 425, 429} | {500..599}``.

    ``BaseHTTPPostSampler`` does not add new implementations of ``response_kind``,
    ``extract_chunks`` or ``extract_usage``; concrete subclasses
    pick these up from :class:`BaseSampler` and must override ``extract_chunks`` (which
    raises ``NotImplementedError``). ``sample`` is the only method from ``BaseSampler``
    that concrete subclasses typically leave unchanged.
    """

    # Statuses the base treats as "the provider rejected the request, no point
    # retrying; persist an empty result so the runner records the row". Override
    # to a wider/narrower set when a provider has a different validation surface.
    _validation_reject_statuses: frozenset[int] = frozenset({400, 422})

    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        base_url: str,
        timeout: float = 60.0,
        timeout_env_var: str | None = None,
        retry_config: RetryConfig | None = None,
        limiter: AsyncLimiter | None = None,
    ):
        super().__init__(name=name)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        if timeout_env_var:
            self._timeout_s = float(os.getenv(timeout_env_var, str(timeout)))
        else:
            self._timeout_s = float(timeout)
        self._timeout = aiohttp.ClientTimeout(total=self._timeout_s)
        self.retry_config = retry_config or _DEFAULT_RETRY_CONFIG
        # Optional process-wide token-bucket gate. Acquired before each
        # HTTP attempt (including retries) so a backoff-driven retry storm
        # still respects the upstream's per-key QPS ceiling. ``None`` skips
        # the gate. (Every current sampler throttles -- even the Nimble lanes,
        # which sit behind a 10 RPS per-product server limit; they gate in
        # their own get_search_results since they don't subclass this base.)
        self._limiter = limiter

    # ------------------------------------------------------------------
    # Subclass contract -- override per provider.
    # ------------------------------------------------------------------

    def _endpoint(self) -> str:
        raise NotImplementedError

    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _build_payload(self, query: str) -> dict[str, Any]:
        raise NotImplementedError

    def _validation_reject_response(self) -> dict[str, Any]:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Optional overrides.
    # ------------------------------------------------------------------

    def _is_retryable_status(self, status: int) -> bool:
        return status in {408, 425, 429} or 500 <= status < 600

    # ------------------------------------------------------------------
    # Base implementation -- subclasses normally don't override.
    # ------------------------------------------------------------------

    async def get_search_results(self, query: str) -> dict[str, Any]:
        # ``contextlib.nullcontext()`` keeps the structure identical between
        # limited and unlimited samplers; the alternative would be branching
        # the whole ``async with`` block on ``self._limiter is None``.
        gate = self._limiter if self._limiter is not None else contextlib.nullcontext()

        async def attempt() -> dict[str, Any]:
            # The timer opens INSIDE the gate so the limiter queue wait -- our
            # throttle, not the provider's latency -- stays out of the measured
            # round trip. See ``BaseSampler._timed_provider_call``.
            async with gate, self._timed_provider_call():
                try:
                    async with (
                        aiohttp.ClientSession(timeout=self._timeout) as session,
                        session.post(
                            f"{self.base_url}{self._endpoint()}",
                            json=self._build_payload(query),
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


# ----------------------------------------------------------------------
# Small parse helpers shared by samplers that surface provider usage as
# floats / ints with mild coercion.
# ----------------------------------------------------------------------


def safe_int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
