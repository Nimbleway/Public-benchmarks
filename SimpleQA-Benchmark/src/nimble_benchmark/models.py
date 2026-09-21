"""Shared normalized provider response models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

# Two surfaces: the ``/search`` lanes return a ranked result list the eval
# synthesizes an answer from, and the native-LLM lanes return a model-authored
# answer with citations. ``native_answer`` is unused today but kept as the slot
# for an answer lane that returns no citations at all.
ResponseKind = Literal["search_results", "native_answer", "answer_with_citations"]
RequestStatus = Literal["ok", "validation_reject", "failed_after_retries", "citation_parse_error"]


@dataclass(frozen=True)
class RetrievalChunk:
    url: str
    title: str
    description: str
    extra_snippets: list[str]
    position: int


@dataclass(frozen=True)
class ProviderLatency:
    # Wall-clock around the whole sampler call. This INCLUDES the client-side
    # rate-limiter queue wait and any retry backoff, so on a throttled lane it
    # is dominated by our own concurrency config (workers / rate_per_second)
    # rather than by the upstream. Kept as a harness-throughput signal; it is
    # NOT the number the leaderboard ranks on.
    request_response_time_ms: float
    internal_response_time_ms: float | None
    # The provider's own round trip: measured inside the limiter gate, around
    # one HTTP attempt only (last attempt wins), so neither queue wait nor
    # retry backoff lands in it. This is the published latency column. ``None``
    # when the row never completed a network attempt (e.g. it failed before the
    # request went out), which the aggregator drops rather than counting as 0.
    provider_response_time_ms: float | None = None
    # Optional per-stage attribution parsed from the sampler's
    # ``Server-Timing`` response header. Keys are stage names emitted by
    # the upstream (e.g. ``plan`` / ``search`` / ``synthesis`` for the
    # Nimble ``/answer`` endpoint); values are milliseconds. Empty when
    # the sampler does not emit a Server-Timing header (third-party
    # answer APIs today).
    stage_timings_ms: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class ProviderResponse:
    provider: str
    query: str
    response_kind: ResponseKind
    chunks: list[RetrievalChunk]
    # The provider's own answer text. Populated only by the answer-kind lanes;
    # ``None`` on every /search lane, whose graded answer is synthesized
    # eval-side from ``chunks`` instead.
    api_answer: str | None
    latency: ProviderLatency
    status: RequestStatus
    raw: dict[str, Any]
    usage: ProviderUsage = field(default_factory=ProviderUsage)

    def chunks_as_dicts(self) -> list[dict[str, Any]]:
        return [asdict(chunk) for chunk in self.chunks]
