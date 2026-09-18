"""LLM-judged retrieval metrics over UMBRELA grades."""

from __future__ import annotations

_ZERO_SCORES_LLM: dict[str, float | int] = {
    "recall_at_5_llm": 0.0,
    "recall_at_10_llm": 0.0,
    "hit_at_5_llm": 0,
    "hit_at_10_llm": 0,
}


def score_grades(grades: list[int], recall_threshold: int = 2) -> dict[str, float | int]:
    if not grades:
        return dict(_ZERO_SCORES_LLM)

    scores: dict[str, float | int] = {}
    for k in (5, 10):
        hit = 1 if any(grade >= recall_threshold for grade in grades[:k]) else 0
        scores[f"recall_at_{k}_llm"] = float(hit)
        scores[f"hit_at_{k}_llm"] = hit
    return scores
