"""Thin OpenAI chat-completions wrapper with transient-error retries.

Every OpenAI call in the eval (UMBRELA judging, SimpleQA grading, eval-side
synthesis) funnels through :func:`call_llm`, which makes this module the
right place for the two process-wide protections:

* One shared :class:`AsyncOpenAI` client with an explicitly sized httpx
  connection pool, so the multi-sampler fan-out reuses TCP+TLS connections
  instead of opening one per call.
* One process-wide concurrency gate (:func:`_openai_gate`) capping the total
  number of in-flight OpenAI requests across all callers. The runner also
  applies per-purpose semaphores (judge / grader / synth); this gate is the
  backstop that holds even if a new call site forgets to. Without it, a
  10-sampler run could stack thousands of concurrent requests onto a
  100-connection pool, and the queued requests would surface as
  ``APITimeoutError`` / ``APIConnectionError`` storms.
"""

from __future__ import annotations

import asyncio
import logging
import os
from functools import lru_cache
from weakref import WeakKeyDictionary

import httpx
from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    DefaultAsyncHttpxClient,
    InternalServerError,
    RateLimitError,
)
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

logger = logging.getLogger(__name__)

# Layered on top of the openai SDK's own retries: long n=500 runs see occasional
# transient blips (DNS, TLS, brief 502s from OpenAI) that exceed the SDK's
# default retry budget; without this an entire benchmark crashes mid-stream.
_TRANSIENT_OPENAI_ERRORS = (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError)

# Ceiling on concurrent OpenAI requests across ALL callers in this process.
# Sized just above the sum of the runner's per-purpose defaults (judge 64 +
# grader 32 + synth 16 = 112) and just below the httpx pool so a request
# admitted by the gate never queues for a connection.
DEFAULT_OPENAI_MAX_CONCURRENCY = 112
_OPENAI_MAX_CONNECTIONS = 128

# Semaphores bind to the event loop they are first awaited on, and the test
# suite creates a fresh loop per test -- key the gate by running loop so a
# stale semaphore from a dead loop can never poison a new one.
_GATES: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = WeakKeyDictionary()


OPENAI_MAX_CONCURRENCY_ENV_VAR = "OPENAI_MAX_CONCURRENCY"


def resolve_openai_concurrency(raw: str | None = None) -> int:
    """Parse ``OPENAI_MAX_CONCURRENCY`` into a usable semaphore bound.

    Defensive because the failure modes are bad out of proportion to the
    typo that causes them: the previous bare ``int(os.getenv(...))`` raised
    ``ValueError`` on the first OpenAI call for any non-numeric value, and a
    value of ``0`` produced ``asyncio.Semaphore(0)``, which never admits a
    single request -- the run hangs silently instead of failing.

    A benchmark is a long-running batch job, so an unusable value warns
    loudly and falls back to the default rather than aborting work in
    progress. Values below 1 are clamped up, since "no concurrency at all"
    is never a coherent request.
    """
    value = os.getenv(OPENAI_MAX_CONCURRENCY_ENV_VAR) if raw is None else raw
    if value is None or not str(value).strip():
        return DEFAULT_OPENAI_MAX_CONCURRENCY
    try:
        parsed = int(str(value).strip())
    except ValueError:
        logger.warning(
            "%s=%r is not an integer; falling back to %d.",
            OPENAI_MAX_CONCURRENCY_ENV_VAR,
            value,
            DEFAULT_OPENAI_MAX_CONCURRENCY,
        )
        return DEFAULT_OPENAI_MAX_CONCURRENCY
    if parsed < 1:
        logger.warning(
            "%s=%d would admit no requests and hang the run; clamping to 1.",
            OPENAI_MAX_CONCURRENCY_ENV_VAR,
            parsed,
        )
        return 1
    return parsed


def _openai_gate() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    gate = _GATES.get(loop)
    if gate is None:
        gate = asyncio.Semaphore(resolve_openai_concurrency())
        _GATES[loop] = gate
    return gate


@lru_cache(maxsize=1)
def _get_client() -> AsyncOpenAI:
    """Lazy module-level AsyncOpenAI singleton.

    Constructed once on first call so a shared httpx.AsyncClient connection
    pool is reused across the UMBRELA / grader / synth fan-out instead of
    opening a fresh TCP+TLS handshake per call. The previous per-call
    ``AsyncOpenAI()`` construction saturated the runner's NAT egress and
    produced ``httpcore.ConnectTimeout`` storms on multi-sampler runs.

    The pool is sized to :data:`_OPENAI_MAX_CONNECTIONS` (above the
    concurrency gate) so every admitted request gets a connection instead of
    silently queueing inside httpx, where the wait counts against the request
    timeout and surfaces as a spurious ``APITimeoutError``.

    Lazy so importing this module without ``OPENAI_API_KEY`` set still
    works for the URL-binary-only metric path.
    """
    return AsyncOpenAI(
        timeout=httpx.Timeout(120.0, connect=15.0),
        http_client=DefaultAsyncHttpxClient(
            limits=httpx.Limits(
                max_connections=_OPENAI_MAX_CONNECTIONS,
                max_keepalive_connections=32,
            ),
        ),
    )


def _is_gpt5_family(model: str) -> bool:
    """gpt-5, gpt-5-mini, gpt-5-codex, etc. all reject temperature != 1.

    OpenAI's chat-completions API on gpt-5 raises BadRequestError:
      "Unsupported value: 'temperature' does not support 0.0 with this
       model. Only the default (1) value is supported."

    Match the bare model id OR a provider-prefixed string ("openai/gpt-5...").
    """
    name = model.split("/", 1)[-1]
    return name.startswith("gpt-5")


@retry(
    retry=retry_if_exception_type(_TRANSIENT_OPENAI_ERRORS),
    stop=stop_after_attempt(5),
    # Randomized exponential backoff: when a whole fan-out hits a transient
    # blip at once (one upstream hiccup fails hundreds of in-flight calls),
    # jitter spreads the retries out instead of re-colliding in lockstep.
    wait=wait_random_exponential(multiplier=1, max=20),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
async def call_llm(*, model: str, user: str, system: str = "", temperature: float = 0.0) -> str:
    client = _get_client()
    # gpt-5 family rejects temperature != 1 with a BadRequestError that's not
    # in the transient-retry set, so the whole row fails. Clamp before the
    # call - if a caller actually wants gpt-5's default (1), passing 1 is a no-op.
    effective_temperature = temperature
    if _is_gpt5_family(model) and temperature != 1:
        logger.info(
            "Clamping temperature %s -> 1 for gpt-5 family model %s (model only accepts temperature=1)",
            temperature,
            model,
        )
        effective_temperature = 1
    # Gate only the request itself, not the retry sleeps -- a caller backing
    # off must not hold a slot other callers could use.
    async with _openai_gate():
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=effective_temperature,
        )
    return response.choices[0].message.content or ""
