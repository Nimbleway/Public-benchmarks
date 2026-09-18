"""UMBRELA-style chunk relevance judge.

The passage-variant template is vendored verbatim from the canonical
Castorini ``umbrela`` reference (``qrel_zeroshot_bing.yaml``,
Upadhyay & Lin 2024, arXiv:2406.06519) so the 0-3 grades we hand to
LLM-judged recall use the same grade definitions as the published UMBRELA
work. Drift this prompt and the LLM-judged scores stop being comparable.

Source: https://github.com/castorini/umbrela/blob/main/src/umbrela/prompts/prompt_templates/qrel_zeroshot_bing.yaml

The URL-only variant is a project extension (UMBRELA itself has no
URL-only template). It reuses the canonical 0-3 grade definitions so a
sampler that only returns ``url`` + ``title`` (no snippets) gets graded
on the same anchor as the passage-variant samplers, with the obvious
caveat that grade 3 ("dedicated to the query and contains the exact
answer") is essentially unreachable without a passage to inspect.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Final

from openai import BadRequestError, PermissionDeniedError

from nimble_benchmark.prompts import load_prompt
from nimble_benchmark.synthesis.llm import call_llm

logger = logging.getLogger(__name__)

PASSAGE_PROMPT = load_prompt("umbrela_passage")
URL_ONLY_PROMPT = load_prompt("umbrela_url_only")


MAX_CHUNK_TEXT_CHARS: Final[int] = 4000

# OpenAI returns 403 PermissionDeniedError (and occasionally 400
# BadRequestError with a ``policy_violation`` / ``content_policy_violation`` /
# ``moderation_blocked`` code) when the moderation stack refuses to score a
# specific passage -- usually a false positive on a benign query that just
# happens to brush a content classifier. Tenacity in
# ``synthesis.llm.call_llm`` deliberately does not retry these (they are not
# transient), so before this guard a single refused chunk would propagate up
# ``asyncio.gather`` -> ``response_to_rows`` -> ``run_one_sampler``, fail the
# row, and -- because the runner aggregates failures into a final
# ``RuntimeError`` -- abort a 35-minute, ~1000-row benchmark for one judge
# refusal. Surface the refusal as grade 0 (the same default
# ``parse_fewshot_response`` returns for unparseable text) so the row stays in
# the CSV and the run still produces a leaderboard.
#
# We deliberately do NOT catch every ``BadRequestError`` -- wrong model name,
# prompt-too-long, malformed schema, or empty messages all raise 400 too, and
# silently grading those as 0 would mask config bugs across an entire run.
# Refusal classification therefore goes through ``_is_judge_refusal`` which
# inspects the SDK's ``code`` / ``body['error']['code']`` / message text and
# only matches the known moderation reasons listed below.
_MODERATION_REFUSAL_CODES: Final[frozenset[str]] = frozenset(
    {
        "policy_violation",
        "content_policy_violation",
        "moderation_blocked",
        "content_filter",
    }
)
_MODERATION_REFUSAL_HINTS: Final[tuple[str, ...]] = (
    "policy_violation",
    "content_policy",
    "moderation",
    "content filter",
)


def _is_judge_refusal(exc: BaseException) -> bool:
    """True iff ``exc`` is an OpenAI moderation refusal we should swallow.

    ``PermissionDeniedError`` (403) is always a refusal in this code path -- we
    only call the judge model with a fixed UMBRELA prompt against a benign
    chunk, so a 403 here is moderation, not a credential or org problem.
    ``BadRequestError`` (400) is only a refusal when the error code or message
    text matches the known moderation markers; every other 400 must re-raise so
    a config bug surfaces immediately instead of poisoning the run with silent
    zero grades.
    """
    if isinstance(exc, PermissionDeniedError):
        return True
    if not isinstance(exc, BadRequestError):
        return False
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code in _MODERATION_REFUSAL_CODES:
        return True
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else None
        body_code = err.get("code") if err else None
        if isinstance(body_code, str) and body_code in _MODERATION_REFUSAL_CODES:
            return True
    message = getattr(exc, "message", None) or str(exc)
    lowered = message.lower()
    return any(hint in lowered for hint in _MODERATION_REFUSAL_HINTS)


def parse_fewshot_response(response: str) -> int:
    match = re.search(r"##final score:\s*([0-3])", response, flags=re.IGNORECASE)
    if not match:
        match = re.search(r"\b([0-3])\b", response)
    return int(match.group(1)) if match else 0


def _assemble_chunk_text(chunk: dict, *, max_chars: int = MAX_CHUNK_TEXT_CHARS) -> str:
    """Build the text shown to the relevance judge for one chunk, capped at
    ``max_chars`` so a single livecrawled page can't blow the judge's context
    budget.

    Title/url/description are kept in full (they are short by construction).
    extra_snippets are concatenated until the remaining budget is exhausted;
    the last snippet that crosses the boundary is truncated rather than
    dropped wholesale so the judge still sees its leading content.
    """
    header = f"{chunk.get('title', '')}\n{chunk.get('url', '')}\n{chunk.get('description', '')}\n"
    snippets = [snippet for snippet in (chunk.get("extra_snippets") or []) if snippet]
    if not snippets:
        return header[:max_chars]

    remaining = max(0, max_chars - len(header))
    if remaining == 0:
        return header[:max_chars]

    appended: list[str] = []
    used = 0
    for snippet in snippets:
        snippet_str = str(snippet)
        # +1 for the joining newline (none for the first snippet).
        sep_cost = 0 if not appended else 1
        budget = remaining - used - sep_cost
        if budget <= 0:
            break
        if len(snippet_str) <= budget:
            appended.append(snippet_str)
            used += sep_cost + len(snippet_str)
            continue
        # Truncate the snippet that crosses the boundary instead of dropping
        # it — the leading text is usually the most relevant part of a
        # livecrawled markdown page.
        appended.append(snippet_str[:budget])
        used += sep_cost + budget
        break

    return header + "\n".join(appended)


async def judge_chunks(
    *,
    query: str,
    chunks: list[dict],
    model: str,
    variant: str = "passage",
    semaphore: asyncio.Semaphore | None = None,
) -> list[int]:
    """Judge each chunk's relevance to ``query`` and return a list of 0-3 grades.

    Chunks are judged concurrently via ``asyncio.gather`` so a 10-chunk row
    completes in ~one round-trip instead of ten serial calls. Pass ``semaphore``
    (typically the runner-side ``judge_sem``) to bound concurrent OpenAI calls
    across all rows in flight; without it, every chunk in every row launches
    immediately, which can blow OpenAI's per-minute rate limit on big runs.
    """
    template = PASSAGE_PROMPT if variant == "passage" else URL_ONLY_PROMPT

    async def _grade_one(chunk: dict) -> int:
        passage = _assemble_chunk_text(chunk)
        user_message = template.format(query=query, passage=passage)
        try:
            if semaphore is not None:
                async with semaphore:
                    response = await call_llm(model=model, system="", user=user_message, temperature=0.0)
            else:
                response = await call_llm(model=model, system="", user=user_message, temperature=0.0)
        except (PermissionDeniedError, BadRequestError) as exc:
            if not _is_judge_refusal(exc):
                # Non-moderation 400s (wrong model, prompt too long, malformed
                # schema, ...) are real bugs -- re-raise so the runner records
                # the failure instead of silently grading the chunk as 0.
                raise
            logger.warning(
                "Judge refused chunk (query=%r url=%r model=%s): %s; grading as 0",
                query,
                chunk.get("url"),
                model,
                exc,
            )
            return 0
        return parse_fewshot_response(response)

    if not chunks:
        return []
    # ``return_exceptions=True`` so every sibling task settles before we act
    # on a failure. A bare ``gather`` re-raises the first exception while the
    # remaining tasks are still running; their late failures then surface as
    # "Task exception was never retrieved" / "unhandled exception during
    # asyncio.run() shutdown" noise instead of a diagnosable error. The row
    # still fails (the first real exception is re-raised below) so the runner
    # records it -- but only after the fan-out has fully drained.
    results = await asyncio.gather(*(_grade_one(chunk) for chunk in chunks), return_exceptions=True)
    errors = [result for result in results if isinstance(result, BaseException)]
    if errors:
        logger.error(
            "Judging failed for %d/%d chunk(s) (query=%r model=%s): %r",
            len(errors),
            len(results),
            query,
            model,
            errors[0],
        )
        raise errors[0]
    return [result for result in results if isinstance(result, int)]
