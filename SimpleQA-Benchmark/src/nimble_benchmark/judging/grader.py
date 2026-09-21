"""SimpleQA A/B/C answer grader."""

from __future__ import annotations

import re

from nimble_benchmark.constants import GRADER_MODEL, SIMPLEQA_ANSWER_GRADER_TEMPLATE
from nimble_benchmark.synthesis.llm import call_llm

GRADE_MAP = {"A": "is_correct", "B": "is_incorrect", "C": "is_not_attempted"}
_grader_model = GRADER_MODEL


def set_grader_model(model: str) -> None:
    global _grader_model
    _grader_model = model


async def evaluate_single_simpleqa(question: str, target: str, predicted: str) -> dict:
    response = await call_llm(
        model=_grader_model,
        user=SIMPLEQA_ANSWER_GRADER_TEMPLATE.format(question=question, target=target, predicted_answer=predicted),
        temperature=0.0,
    )
    match = re.search(r"\b([ABC])\b", response or "")
    grade = match.group(1) if match else "C"
    return {"grade": grade, "score_name": GRADE_MAP[grade], "is_correct": grade == "A"}
