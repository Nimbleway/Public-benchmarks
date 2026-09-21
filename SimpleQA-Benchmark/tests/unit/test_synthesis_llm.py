"""Unit tests for the OpenAI synthesis wrapper."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from openai import APITimeoutError

from nimble_benchmark.synthesis.llm import (
    DEFAULT_OPENAI_MAX_CONCURRENCY,
    _get_client,
    _is_gpt5_family,
    _openai_gate,
    call_llm,
    resolve_openai_concurrency,
)


@pytest.fixture(autouse=True)
def _reset_openai_client_cache() -> None:
    # ``_get_client`` memoizes the AsyncOpenAI instance via lru_cache so the
    # production code reuses one httpx connection pool. Tests patch the
    # ``AsyncOpenAI`` class symbol per-case; without resetting the cache the
    # second test in the file would reuse the *first* test's mocked instance
    # and the new patch would be invisible.
    _get_client.cache_clear()


class TestIsGpt5Family:
    def test_bare_gpt5(self) -> None:
        assert _is_gpt5_family("gpt-5") is True

    def test_gpt5_mini(self) -> None:
        assert _is_gpt5_family("gpt-5-mini") is True

    def test_gpt5_codex(self) -> None:
        assert _is_gpt5_family("gpt-5-codex") is True

    def test_dotted_gpt5_point_releases(self) -> None:
        # Probed live against /v1/chat/completions: every one of these rejects
        # temperature=0 with "Only the default (1) value is supported", so the
        # startswith("gpt-5") rule is correct for the whole 5.x line -- adding
        # a 5.x entry to SYNTHESIS_MODEL_CHOICES needs no change here.
        assert _is_gpt5_family("gpt-5.5") is True
        assert _is_gpt5_family("gpt-5.5-pro") is True
        assert _is_gpt5_family("gpt-5.6-sol") is True

    def test_provider_prefixed(self) -> None:
        # Strings that come through with an "openai/" prefix should match too.
        assert _is_gpt5_family("openai/gpt-5") is True
        assert _is_gpt5_family("openai/gpt-5-mini") is True

    def test_non_gpt5_models(self) -> None:
        assert _is_gpt5_family("gpt-4o") is False
        assert _is_gpt5_family("gpt-4o-mini") is False
        assert _is_gpt5_family("gpt-4.1") is False
        assert _is_gpt5_family("o1") is False
        assert _is_gpt5_family("claude-haiku-4-5") is False


def _mock_response(content: str = "ok") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
    )


class TestCallLLMTemperatureClamp:
    """REGRESSION GUARD: the gpt-5 family rejects an explicit temperature.

    Before this fix, every `gpt-5` synthesis call raised BadRequestError
    "Unsupported value: 'temperature' does not support 0.0 with this model"
    and every eval row failed with "Row failed for sampler=...". Callers still
    ask for temperature=0 to get deterministic
    synthesis, so the clamp has to happen here rather than at each call site.
    """

    @pytest.mark.asyncio
    async def test_gpt5_clamps_temperature_to_1(self) -> None:
        mock_create = AsyncMock(return_value=_mock_response("hello"))
        # Patch the AsyncOpenAI() constructor so we don't hit the network
        # and can inspect the kwargs handed to chat.completions.create.
        with patch("nimble_benchmark.synthesis.llm.AsyncOpenAI") as mock_client_cls:
            mock_client_cls.return_value.chat.completions.create = mock_create
            result = await call_llm(
                model="gpt-5",
                user="hi",
                system="be terse",
                temperature=0.0,  # caller asks for deterministic
            )

        assert result == "hello"
        kwargs = mock_create.await_args.kwargs
        # The clamp rewrote 0.0 -> 1 before the wire call.
        assert kwargs["temperature"] == 1
        assert kwargs["model"] == "gpt-5"

    @pytest.mark.asyncio
    async def test_gpt5_mini_also_clamped(self) -> None:
        mock_create = AsyncMock(return_value=_mock_response("hello"))
        with patch("nimble_benchmark.synthesis.llm.AsyncOpenAI") as mock_client_cls:
            mock_client_cls.return_value.chat.completions.create = mock_create
            await call_llm(model="gpt-5-mini", user="hi", temperature=0.0)

        assert mock_create.await_args.kwargs["temperature"] == 1

    @pytest.mark.asyncio
    async def test_gpt5_temperature_already_1_passes_through(self) -> None:
        mock_create = AsyncMock(return_value=_mock_response("hello"))
        with patch("nimble_benchmark.synthesis.llm.AsyncOpenAI") as mock_client_cls:
            mock_client_cls.return_value.chat.completions.create = mock_create
            await call_llm(model="gpt-5", user="hi", temperature=1)

        assert mock_create.await_args.kwargs["temperature"] == 1

    @pytest.mark.asyncio
    async def test_non_gpt5_model_temperature_unchanged(self) -> None:
        """Other models (gpt-4o, claude-*, etc.) accept temperature=0.0 fine."""
        mock_create = AsyncMock(return_value=_mock_response("hello"))
        with patch("nimble_benchmark.synthesis.llm.AsyncOpenAI") as mock_client_cls:
            mock_client_cls.return_value.chat.completions.create = mock_create
            await call_llm(model="gpt-4o", user="hi", temperature=0.0)

        assert mock_create.await_args.kwargs["temperature"] == 0.0

    @pytest.mark.asyncio
    async def test_non_gpt5_model_with_custom_temperature(self) -> None:
        mock_create = AsyncMock(return_value=_mock_response("hello"))
        with patch("nimble_benchmark.synthesis.llm.AsyncOpenAI") as mock_client_cls:
            mock_client_cls.return_value.chat.completions.create = mock_create
            await call_llm(model="gpt-4o-mini", user="hi", temperature=0.7)

        assert mock_create.await_args.kwargs["temperature"] == 0.7


class TestCallLLMRetry:
    """Lock in the @retry-decorator contract: transient errors retry, exhaustion
    reraises the original exception (not a tenacity RetryError), and the policy
    only retries the transient-set (BadRequest etc. propagate immediately)."""

    @staticmethod
    def _timeout() -> APITimeoutError:
        # APITimeoutError needs a request; build a throwaway one.
        return APITimeoutError(request=httpx.Request("POST", "https://x"))

    @pytest.mark.asyncio
    async def test_retries_then_succeeds(self) -> None:
        mock_create = AsyncMock(side_effect=[self._timeout(), self._timeout(), _mock_response("ok")])
        with (
            patch("nimble_benchmark.synthesis.llm.AsyncOpenAI") as mock_client_cls,
            patch("asyncio.sleep", new=AsyncMock(return_value=None)),
        ):
            mock_client_cls.return_value.chat.completions.create = mock_create
            assert await call_llm(model="gpt-4o", user="hi") == "ok"
            assert mock_create.await_count == 3

    @pytest.mark.asyncio
    async def test_exhaustion_reraises_original_exception(self) -> None:
        # `reraise=True` means the caller sees APITimeoutError directly, with no
        # tenacity.RetryError wrapper — keeps tracebacks short in the runner.
        mock_create = AsyncMock(side_effect=self._timeout())
        with (
            patch("nimble_benchmark.synthesis.llm.AsyncOpenAI") as mock_client_cls,
            patch("asyncio.sleep", new=AsyncMock(return_value=None)),
            pytest.raises(APITimeoutError),
        ):
            mock_client_cls.return_value.chat.completions.create = mock_create
            await call_llm(model="gpt-4o", user="hi")
        assert mock_create.await_count == 5  # stop_after_attempt(5)

    @pytest.mark.asyncio
    async def test_non_transient_error_does_not_retry(self) -> None:
        mock_create = AsyncMock(side_effect=ValueError("nope"))
        with (
            patch("nimble_benchmark.synthesis.llm.AsyncOpenAI") as mock_client_cls,
            pytest.raises(ValueError),
        ):
            mock_client_cls.return_value.chat.completions.create = mock_create
            await call_llm(model="gpt-4o", user="hi")
        assert mock_create.await_count == 1


@pytest.mark.unit
class TestResolveOpenAIConcurrency:
    """`OPENAI_MAX_CONCURRENCY` feeds an asyncio.Semaphore directly, so a bad
    value used to be worse than a typo: a non-integer raised ValueError on the
    first OpenAI call, and `0` produced Semaphore(0), which admits no requests
    and hangs the whole run silently.
    """

    def test_unset_uses_default(self, monkeypatch):
        monkeypatch.delenv("OPENAI_MAX_CONCURRENCY", raising=False)
        assert resolve_openai_concurrency() == DEFAULT_OPENAI_MAX_CONCURRENCY

    def test_valid_value_is_honored(self, monkeypatch):
        monkeypatch.setenv("OPENAI_MAX_CONCURRENCY", "16")
        assert resolve_openai_concurrency() == 16

    def test_surrounding_whitespace_is_tolerated(self, monkeypatch):
        monkeypatch.setenv("OPENAI_MAX_CONCURRENCY", "  24  ")
        assert resolve_openai_concurrency() == 24

    @pytest.mark.parametrize("value", ["abc", "8.5", "twelve", "1e3"])
    def test_non_integer_falls_back_instead_of_crashing(self, value):
        # A long benchmark must not die on the first OpenAI call because of a
        # typo in .env -- warn and use the default.
        assert resolve_openai_concurrency(value) == DEFAULT_OPENAI_MAX_CONCURRENCY

    @pytest.mark.parametrize("value", ["0", "-1", "-50"])
    def test_values_below_one_are_clamped_not_deadlocked(self, value):
        # Semaphore(0) never admits a request; Semaphore(-1) raises. Both are
        # worse than simply running single-threaded.
        assert resolve_openai_concurrency(value) == 1

    def test_empty_string_uses_default(self):
        assert resolve_openai_concurrency("") == DEFAULT_OPENAI_MAX_CONCURRENCY
        assert resolve_openai_concurrency("   ") == DEFAULT_OPENAI_MAX_CONCURRENCY

    @pytest.mark.asyncio
    async def test_gate_is_usable_after_a_bad_env_value(self, monkeypatch):
        """End-to-end guard: a garbage value must still yield a working gate."""
        monkeypatch.setenv("OPENAI_MAX_CONCURRENCY", "not-a-number")
        gate = _openai_gate()
        await asyncio.wait_for(gate.acquire(), timeout=1.0)
        gate.release()

    @pytest.mark.asyncio
    async def test_gate_does_not_hang_on_zero(self, monkeypatch):
        monkeypatch.setenv("OPENAI_MAX_CONCURRENCY", "0")
        gate = _openai_gate()
        await asyncio.wait_for(gate.acquire(), timeout=1.0)
        gate.release()
