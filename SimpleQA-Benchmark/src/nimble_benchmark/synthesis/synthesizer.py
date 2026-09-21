"""Eval-side answer synthesis from normalized chunks."""

from __future__ import annotations

from functools import cache

import tiktoken

from nimble_benchmark.constants import SYNTHESIS_PROMPT
from nimble_benchmark.synthesis.llm import call_llm

_FALLBACK_ENCODING = "cl100k_base"
_TRUNCATION_MARKER = "\n[truncated]"

# Context windows (tokens) for every model `--synthesis-model` accepts -- see
# ``config.SYNTHESIS_MODEL_CHOICES``. Deliberately conservative for anything
# not explicitly verified here: overestimating a window risks a hard 400 from
# the provider mid-run (an entire benchmark failing on the last row is worse
# than one truncated further than it strictly had to be), so an unlisted or
# future model falls back to gpt-4o's window rather than guessing higher.
_MODEL_CONTEXT_WINDOW_TOKENS: dict[str, int] = {
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-5": 400_000,
    "gpt-5-mini": 400_000,
    "gpt-5-thinking": 400_000,
    "gpt-5-mini-thinking": 400_000,
}
_DEFAULT_CONTEXT_WINDOW_TOKENS = 128_000

# Reserved for the system prompt, the "Query: ...\n\nSearch results:\n"
# wrapper, and the model's own response. SimpleQA answers are short, so this
# is generous headroom rather than a measured minimum.
_RESERVED_TOKENS = 4_000

# Per-result cap is dynamic, not a fixed token count -- see _bounded_context.
# Each result's share is `remaining_budget // remaining_result_count`,
# matching web-search-api-evals' `trim_results_to_model_limit`: a result
# that fits in its share only consumes what it actually used, so the freed
# tokens grow every later result's share; a result that doesn't fit is
# truncated down to exactly its share rather than dropped. No single result
# can starve the others, and none is ever fully zeroed out.


def _context_window_tokens(model: str) -> int:
    return _MODEL_CONTEXT_WINDOW_TOKENS.get(model, _DEFAULT_CONTEXT_WINDOW_TOKENS)


def max_synth_context_tokens(model: str) -> int:
    """Total result-context token budget for ``model``.

    Derived from the model's real context window rather than a fixed
    character count, so the budget scales with what the model can actually
    hold instead of silently over- or under-using it.
    """
    return max(_context_window_tokens(model) - _RESERVED_TOKENS, 0)


@cache
def _get_encoding(model: str) -> tiktoken.Encoding:
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding(_FALLBACK_ENCODING)


def _truncate_tokens(enc: tiktoken.Encoding, tokens: list[int], max_tokens: int) -> str:
    if len(tokens) <= max_tokens:
        return enc.decode(tokens)
    marker_tokens = enc.encode(_TRUNCATION_MARKER)
    if max_tokens <= len(marker_tokens):
        return enc.decode(tokens[:max_tokens])
    return enc.decode(tokens[: max_tokens - len(marker_tokens)]) + _TRUNCATION_MARKER


def _bounded_context(formatted: list[str], model: str) -> str:
    """Fill results in ranked order, each capped at a dynamic fair share of
    the model's total token budget.

    Results are visited in the order they arrive (the provider's own
    ranking) rather than web-search-api-evals' smallest-first sort -- that
    part of the earlier design choice (rank-priority over size-priority)
    stands. What changed is the per-result cap: instead of a fixed token
    ceiling, each result gets `remaining_budget // remaining_result_count`
    of whatever is left, same formula as
    `evals.processing.synthesizer_utils.trim_results_to_model_limit`. A
    result that fits inside its share only spends what it actually used,
    so the tokens it left on the table grow every later result's share.
    A result that doesn't fit is truncated down to exactly its share. No
    result is ever fully dropped as long as the budget hasn't already hit
    zero, and no single oversized result can starve the rest.
    """
    enc = _get_encoding(model)
    remaining_budget = max_synth_context_tokens(model)
    separator_tokens = len(enc.encode("\n\n"))
    parts: list[str] = []
    total = len(formatted)
    for index, result in enumerate(formatted):
        remaining_count = total - index
        cost = 0 if not parts else separator_tokens
        available = remaining_budget - cost
        if available <= 0:
            break
        fair_share = available // remaining_count
        tokens = enc.encode(str(result), disallowed_special=())
        if len(tokens) > fair_share:
            result_text = _truncate_tokens(enc, tokens, fair_share)
            tokens = enc.encode(result_text, disallowed_special=())
        else:
            result_text = str(result)
        parts.append(result_text)
        remaining_budget -= cost + len(tokens)
    return "\n\n".join(parts)


async def synth(query: str, formatted: list[str], model: str) -> str:
    context = _bounded_context(formatted, model)
    return await call_llm(
        model=model,
        system=SYNTHESIS_PROMPT,
        user=f"Query: {query}\n\nSearch results:\n{context}",
        temperature=0.0,
    )
