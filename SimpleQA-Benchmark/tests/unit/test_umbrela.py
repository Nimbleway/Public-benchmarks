"""Unit tests for the UMBRELA chunk-relevance judge.

The judge is a thin wrapper around an LLM relevance score per chunk. The
non-trivial behavior we lock down here is the per-chunk text bound — without
it, a single livecrawled page in ``extra_snippets`` (observed at 200k+
tokens) blows gpt-4o's 128k context and aborts the whole sampler.
"""

from __future__ import annotations

import asyncio

import pytest

from nimble_benchmark.judging.umbrela import (
    MAX_CHUNK_TEXT_CHARS,
    PASSAGE_PROMPT,
    _assemble_chunk_text,
    judge_chunks,
    parse_fewshot_response,
)


@pytest.mark.unit
def test_parse_fewshot_response_extracts_final_score_marker():
    assert parse_fewshot_response("Some reasoning.\n##final score: 2") == 2
    assert parse_fewshot_response("##Final Score: 3 // case-insensitive") == 3


@pytest.mark.unit
def test_parse_fewshot_response_falls_back_to_first_digit_when_marker_missing():
    assert parse_fewshot_response("score = 1 (no final marker)") == 1


@pytest.mark.unit
def test_parse_fewshot_response_returns_zero_on_unparseable_text():
    assert parse_fewshot_response("the model refused to answer") == 0


@pytest.mark.unit
def test_assemble_chunk_text_preserves_short_chunks_verbatim():
    """Short chunks (Exa/Brave-style snippets) must round-trip
    unchanged — truncation is purely a defensive bound for outliers."""
    chunk = {
        "title": "Dario Amodei",
        "url": "https://anthropic.com",
        "description": "CEO and co-founder of Anthropic.",
        "extra_snippets": ["Founded the company in 2021.", "Former OpenAI VP of Research."],
    }
    text = _assemble_chunk_text(chunk)
    assert "Dario Amodei" in text
    assert "https://anthropic.com" in text
    assert "CEO and co-founder of Anthropic." in text
    assert "Founded the company in 2021." in text
    assert "Former OpenAI VP of Research." in text
    assert len(text) < MAX_CHUNK_TEXT_CHARS


@pytest.mark.unit
def test_assemble_chunk_text_caps_huge_livecrawl_snippet():
    """Regression: livecrawl-enabled samplers attach full-page markdown to
    extra_snippets; a single such page has been observed at >200k chars.
    We cap the assembled text at MAX_CHUNK_TEXT_CHARS so the judge call
    stays well under gpt-4o's 128k token context window."""
    huge_snippet = "a" * 250_000
    chunk = {
        "title": "Wikipedia article",
        "url": "https://en.wikipedia.org/wiki/Bodmin_by-election",
        "description": "Bodmin by-election 1906.",
        "extra_snippets": [huge_snippet],
    }
    text = _assemble_chunk_text(chunk)
    assert len(text) <= MAX_CHUNK_TEXT_CHARS
    # Header content (title/url/description) must survive the truncation —
    # those are essential for the judge's call. The huge snippet is the part
    # that gets clipped.
    assert "Wikipedia article" in text
    assert "Bodmin by-election 1906." in text


@pytest.mark.unit
def test_assemble_chunk_text_truncates_crossing_snippet_rather_than_dropping():
    """When the budget runs out mid-snippet, we keep the leading content
    rather than dropping the whole snippet — the head of a livecrawled page
    is usually the most relevant part for relevance grading."""
    chunk = {
        "title": "T",
        "url": "U",
        "description": "D",
        "extra_snippets": ["x" * 5000, "y" * 5000],
    }
    text = _assemble_chunk_text(chunk)
    assert len(text) <= MAX_CHUNK_TEXT_CHARS
    # The first snippet's leading chars must be present (proves we didn't
    # drop it wholesale on budget exhaustion).
    assert "xxx" in text


@pytest.mark.unit
def test_assemble_chunk_text_skips_falsy_snippets():
    """Empty / None entries in extra_snippets must be silently filtered
    (matches the previous concat-with-newlines behavior)."""
    chunk = {
        "title": "T",
        "url": "U",
        "description": "D",
        "extra_snippets": [None, "", "real snippet", "  "],
    }
    text = _assemble_chunk_text(chunk)
    assert "real snippet" in text
    # No stray "None" string from a None entry leaking into the prompt.
    assert "None" not in text


@pytest.mark.unit
def test_assemble_chunk_text_handles_missing_keys_gracefully():
    """The assembler must not raise when the chunk dict lacks any optional
    field — extract_chunks across the samplers omits fields freely."""
    assert _assemble_chunk_text({}) == "\n\n\n"


@pytest.mark.unit
def test_assemble_chunk_text_max_chars_parameter_overrides_default():
    """Allow callers (e.g. future fine-grained policies) to pick a tighter
    bound without monkey-patching the module constant."""
    chunk = {
        "title": "T",
        "url": "U",
        "description": "D",
        "extra_snippets": ["z" * 100],
    }
    text = _assemble_chunk_text(chunk, max_chars=20)
    assert len(text) <= 20


@pytest.mark.unit
@pytest.mark.asyncio
async def test_judge_chunks_uses_truncated_text(monkeypatch):
    """End-to-end: ``judge_chunks`` must pass the truncated assembled text
    to ``call_llm``, not the raw multi-megabyte concat. We assert the user
    payload size to lock the regression."""
    huge = "h" * 250_000
    chunks = [
        {
            "title": "row 1",
            "url": "https://example.com/1",
            "description": "desc 1",
            "extra_snippets": [huge],
        },
        {
            "title": "row 2",
            "url": "https://example.com/2",
            "description": "desc 2",
            "extra_snippets": ["short"],
        },
    ]
    captured_user_payloads: list[str] = []

    async def fake_call_llm(*, model, system, user, temperature):
        captured_user_payloads.append(user)
        return "##final score: 2"

    monkeypatch.setattr("nimble_benchmark.judging.umbrela.call_llm", fake_call_llm)

    grades = await judge_chunks(query="q", chunks=chunks, model="gpt-4o")

    assert grades == [2, 2]
    assert len(captured_user_payloads) == 2
    # Every payload must be safely bounded. The canonical UMBRELA template
    # (PASSAGE_PROMPT) wraps the bounded passage with ~1.2 KB of grade-
    # definition scaffolding, so the total payload size = template overhead
    # + bounded passage. Computing the bound dynamically prevents drift if
    # the canonical prompt is ever re-vendored.
    template_overhead = len(PASSAGE_PROMPT.format(query="q", passage=""))
    payload_ceiling = template_overhead + MAX_CHUNK_TEXT_CHARS + 32
    for payload in captured_user_payloads:
        assert len(payload) <= payload_ceiling, f"payload too long: {len(payload)} chars (ceiling {payload_ceiling})"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_judge_chunks_runs_in_parallel(monkeypatch):
    """Per-chunk grading must fan out via asyncio.gather — the legacy serial
    for-loop dominated wall-clock for n=500 runs (10 chunks x ~1.5 s each).
    Locking parallelism in: 10 chunks taking ~80 ms each must complete in
    ~well under 200 ms total, not 800 ms.
    """
    chunks = [{"title": f"c{i}", "url": f"https://x/{i}", "description": "d"} for i in range(10)]

    async def slow_call_llm(*, model, system, user, temperature):
        await asyncio.sleep(0.08)
        return "##final score: 1"

    monkeypatch.setattr("nimble_benchmark.judging.umbrela.call_llm", slow_call_llm)

    loop = asyncio.get_running_loop()
    t0 = loop.time()
    grades = await judge_chunks(query="q", chunks=chunks, model="gpt-4o")
    elapsed = loop.time() - t0

    assert grades == [1] * 10
    # Serial would take ~0.8 s; parallel completes in ~one round-trip.
    assert elapsed < 0.4, f"judge_chunks took {elapsed:.2f}s — expected parallel fan-out"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_judge_chunks_respects_semaphore(monkeypatch):
    """When a bounded ``semaphore`` is supplied, only ``N`` concurrent OpenAI
    calls are in flight at once across all chunks. We track the live-call
    counter to verify the cap is enforced.
    """
    in_flight = 0
    peak_in_flight = 0
    chunks = [{"title": f"c{i}", "url": f"https://x/{i}", "description": "d"} for i in range(20)]

    async def slow_call_llm(*, model, system, user, temperature):
        nonlocal in_flight, peak_in_flight
        in_flight += 1
        peak_in_flight = max(peak_in_flight, in_flight)
        try:
            await asyncio.sleep(0.05)
            return "##final score: 2"
        finally:
            in_flight -= 1

    monkeypatch.setattr("nimble_benchmark.judging.umbrela.call_llm", slow_call_llm)

    sem = asyncio.Semaphore(3)
    grades = await judge_chunks(query="q", chunks=chunks, model="gpt-4o", semaphore=sem)

    assert grades == [2] * 20
    assert peak_in_flight <= 3, f"semaphore cap of 3 violated; saw {peak_in_flight} concurrent calls"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_judge_chunks_returns_empty_for_empty_chunks():
    """No chunks → no LLM calls, return ``[]`` cleanly so callers that pass
    through a sampler-side empty SERP don't hit a no-op gather edge case.
    """
    grades = await judge_chunks(query="q", chunks=[], model="gpt-4o")
    assert grades == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_judge_chunks_treats_openai_403_as_grade_zero(monkeypatch):
    """Regression: in run 26547968711 a single ``openai.PermissionDeniedError``
    (HTTP 403) from the judge on one chunk of one query in a third-party lane
    propagated up through ``asyncio.gather`` and aborted a 35-minute,
    1000-row SimpleQA benchmark. The judge must absorb moderation refusals
    as grade 0 (the same default ``parse_fewshot_response`` returns for
    unparseable text) so one refused chunk no longer kills the row -- and
    therefore the run.
    """
    import httpx
    from openai import PermissionDeniedError

    chunks = [
        {"title": "ok", "url": "https://x/ok", "description": "d"},
        {"title": "refused", "url": "https://x/refused", "description": "d"},
        {"title": "ok2", "url": "https://x/ok2", "description": "d"},
    ]

    async def maybe_refusing_call_llm(*, model, system, user, temperature):
        if "refused" in user:
            raise PermissionDeniedError(
                message="Error code: 403",
                response=httpx.Response(
                    403, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
                ),
                body={"error": {"code": "moderation_blocked"}},
            )
        return "##final score: 2"

    monkeypatch.setattr("nimble_benchmark.judging.umbrela.call_llm", maybe_refusing_call_llm)

    grades = await judge_chunks(query="q", chunks=chunks, model="gpt-4o-mini")

    assert grades == [2, 0, 2], "refused chunk must grade as 0; siblings must keep their real grades"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_judge_chunks_treats_openai_400_policy_violation_as_grade_zero(monkeypatch):
    """``BadRequestError`` (HTTP 400) with a moderation/policy reason behaves
    identically to the 403 path -- a refusal, not a transient error -- and
    must not abort the row.
    """
    import httpx
    from openai import BadRequestError

    chunks = [{"title": "refused", "url": "https://x/refused", "description": "d"}]

    async def refusing_call_llm(*, model, system, user, temperature):
        raise BadRequestError(
            message="Error code: 400 - policy_violation",
            response=httpx.Response(400, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions")),
            body={"error": {"code": "policy_violation"}},
        )

    monkeypatch.setattr("nimble_benchmark.judging.umbrela.call_llm", refusing_call_llm)

    grades = await judge_chunks(query="q", chunks=chunks, model="gpt-4o-mini")

    assert grades == [0]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_judge_chunks_still_raises_on_non_refusal_errors(monkeypatch):
    """The refusal-swallow path must be narrow: a truly unexpected exception
    (e.g. our prompt template breaking, a programming bug) must still
    propagate so the runner can record the failure and the operator can fix
    it. Only the two known content-moderation error types are absorbed.
    """
    chunks = [{"title": "t", "url": "u", "description": "d"}]

    async def boom_call_llm(*, model, system, user, temperature):
        raise RuntimeError("unexpected judge bug")

    monkeypatch.setattr("nimble_benchmark.judging.umbrela.call_llm", boom_call_llm)

    with pytest.raises(RuntimeError, match="unexpected judge bug"):
        await judge_chunks(query="q", chunks=chunks, model="gpt-4o-mini")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_judge_chunks_propagates_non_moderation_400_so_config_bugs_surface(monkeypatch):
    """The refusal swallow must not extend to *every* ``BadRequestError`` --
    a wrong model name, a prompt that exceeds the context window, a malformed
    schema, or empty messages all raise HTTP 400 from OpenAI. Silently grading
    those as 0 would mask a config bug across an entire ~1000-row run and
    publish a leaderboard built on noise. Only moderation-coded 400s are
    absorbed; everything else re-raises so the runner records the failure
    immediately.
    """
    import httpx
    from openai import BadRequestError

    chunks = [{"title": "t", "url": "u", "description": "d"}]

    async def model_not_found_call_llm(*, model, system, user, temperature):
        raise BadRequestError(
            message="Error code: 400 - The model `gpt-9000` does not exist or you do not have access to it.",
            response=httpx.Response(400, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions")),
            body={"error": {"code": "model_not_found", "message": "The model `gpt-9000` does not exist"}},
        )

    monkeypatch.setattr("nimble_benchmark.judging.umbrela.call_llm", model_not_found_call_llm)

    with pytest.raises(BadRequestError, match="model"):
        await judge_chunks(query="q", chunks=chunks, model="gpt-9000")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_judge_chunks_drains_all_siblings_before_raising(monkeypatch):
    """When one chunk's grading fails, ``judge_chunks`` must let every sibling
    task settle before re-raising. A bare ``asyncio.gather`` re-raises while
    siblings are still in flight; their late failures then leak as
    "unhandled exception during asyncio.run() shutdown" noise (observed with
    ``openai.APIConnectionError`` on n=500 multi-sampler runs).
    """
    import asyncio

    chunks = [{"title": f"t{i}", "url": f"u{i}", "description": "d"} for i in range(4)]
    settled = []

    async def flaky_call_llm(*, model, system, user, temperature):
        index = len(settled)
        settled.append(index)
        await asyncio.sleep(0.01 * index)
        if "t0" in user or "t2" in user:
            raise RuntimeError(f"connection error for call {index}")
        return "##final score: 2"

    monkeypatch.setattr("nimble_benchmark.judging.umbrela.call_llm", flaky_call_llm)

    with pytest.raises(RuntimeError, match="connection error"):
        await judge_chunks(query="q", chunks=chunks, model="gpt-4o")

    # All four chunk tasks ran to completion (none abandoned mid-flight).
    assert len(settled) == 4
