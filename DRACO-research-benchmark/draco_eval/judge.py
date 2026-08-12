"""Rubric-based LLM judging for DRACO — the scoring method.

DRACO pairs each research task with an expert-curated rubric of ~40 criteria,
grouped into four axes:

  factual-accuracy              verifiable claims the response must state correctly
  breadth-and-depth-of-analysis synthesis, trade-offs, actionable guidance
  presentation-quality          terminology, format, readability, objectivity
  citation-quality              citations to primary source documents

Each criterion carries an integer weight: positive = reward, negative = penalty
(the criterion describes an error the response should NOT contain).

Scoring, per the DRACO paper:

    raw   = sum(v_i * w_i)              v_i = 1 if MET else 0
    score = clamp(raw / sum(w_i for w_i > 0), 0.0, 1.0)

The LLM is asked for exactly one binary verdict per criterion. It is never
asked to do arithmetic, weigh criteria, or produce a score — all aggregation
happens in Python, so the number is reproducible given the verdicts.

Reference: https://arxiv.org/abs/2602.11685
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field

from draco_eval.retry import with_retry

AXES = (
    "factual-accuracy",
    "breadth-and-depth-of-analysis",
    "presentation-quality",
    "citation-quality",
)

DEFAULT_JUDGE_MODEL = "anthropic:claude-sonnet-5"

#: Independent verdicts sampled per criterion, averaged. LLM judges are noisy
#: at the individual-criterion level; sampling and averaging shrinks that noise
#: by ~sqrt(n) without changing what is being measured. Three is the usual
#: sweet spot — it also breaks ties, which an even count cannot.
DEFAULT_JUDGE_SAMPLES = 3


class Criterion(BaseModel):
    id: str = ""
    weight: int
    requirement: str


class Section(BaseModel):
    id: str
    criteria: list[Criterion] = Field(default_factory=list)


class Rubric(BaseModel):
    sections: list[Section] = Field(default_factory=list)


def parse_rubric(raw: str | dict) -> Rubric:
    """Parse a rubric from the dataset's JSON-encoded `answer` column."""
    return Rubric.model_validate(json.loads(raw) if isinstance(raw, str) else raw)


class _Verdict(BaseModel):
    met: bool = Field(..., description="True if the criterion is met by the response.")
    justification: str = Field(..., description="One-sentence justification for the verdict.")


_JUDGE_PROMPT = """\
You are evaluating a deep research agent's response against a single rubric criterion.

Criterion type: {criterion_type}
{criterion_type_note}

Criterion requirement:
{requirement}

Original research query:
{question}

Agent response:
{answer}

Evaluate whether the criterion is MET or UNMET based solely on the requirement above.
""".strip()

_POSITIVE_NOTE = (
    "This is a POSITIVE criterion. "
    "Set met=true if the response satisfies the requirement; "
    "set met=false if it does not."
)
_NEGATIVE_NOTE = (
    "This is a NEGATIVE criterion (describes an error or harmful content). "
    "Set met=true if the response CONTAINS the error described; "
    "set met=false if the response avoids the error."
)


@dataclass
class AxisScore:
    score: float
    raw: float
    max_positive: int
    met: int
    total: int


@dataclass
class ItemScores:
    overall: AxisScore
    axes: dict[str, AxisScore]
    verdicts: list[dict] = field(default_factory=list)


def aggregate(verdicts: list[tuple[Criterion, float]]) -> AxisScore:
    """Apply the DRACO scoring formula to a set of judged criteria.

    Each `v` is the criterion's met-rate across the sampled verdicts: 1.0 when
    every sample said MET, 0.0 when none did, fractional when they disagreed.
    With a single sample it is just 1.0/0.0 and this is the plain formula.

    Normalising by the positive weight total (not by the signed total) is what
    makes negative criteria act as true penalties: a response that trips every
    error criterion scores below one that simply omits the rewarded content.
    """
    if not verdicts:
        return AxisScore(score=0.0, raw=0.0, max_positive=0, met=0, total=0)

    raw = sum(c.weight * v for c, v in verdicts)
    max_positive = sum(c.weight for c, _ in verdicts if c.weight > 0)
    met = sum(1 for _, v in verdicts if v >= 0.5)

    score = 0.0 if max_positive <= 0 else max(0.0, min(1.0, raw / max_positive))
    return AxisScore(score=score, raw=round(raw, 3), max_positive=max_positive, met=met, total=len(verdicts))


class Judge:
    """Judges one answer against one rubric, one criterion per LLM call.

    `model` is any LangChain model identifier — "anthropic:claude-sonnet-5",
    "openai:gpt-5", "bedrock_converse:us.anthropic.claude-sonnet-4-6-...".

    Sampling params are left at the provider default: the newest reasoning
    models reject an explicit `temperature` outright, so pinning it here would
    make the default judge unusable. Verdicts are therefore not bit-reproducible
    — compare systems judged by the same model, and treat small deltas as noise.

    `samples` independent verdicts are drawn per criterion and averaged, which
    is what makes that noise tolerable. Cost scales linearly with it.
    """

    def __init__(
        self,
        model: str = DEFAULT_JUDGE_MODEL,
        *,
        concurrency: int = 8,
        samples: int = DEFAULT_JUDGE_SAMPLES,
    ) -> None:
        if samples < 1:
            raise ValueError(f"samples must be >= 1, got {samples}")
        self.model = model
        self.samples = samples
        # include_raw so a malformed tool call surfaces as parsing_error instead
        # of raising — one bad verdict must not sink the whole item's score.
        self._llm = init_chat_model(model).with_structured_output(_Verdict, include_raw=True)
        self._sem = asyncio.Semaphore(concurrency)

    async def _invoke(self, prompt: str) -> _Verdict | None:
        async def call():
            # The semaphore is acquired per attempt, not held across backoff,
            # so a sleeping retry does not occupy a concurrency slot.
            async with self._sem:
                return await self._llm.ainvoke(prompt)

        result = await with_retry(call, label=f"judge {self.model}")
        parsed = result.get("parsed") if isinstance(result, dict) else result
        return parsed if isinstance(parsed, _Verdict) else None

    async def _judge_one(
        self, axis: str, criterion: Criterion, question: str, answer: str
    ) -> tuple[str, Criterion, float, str]:
        negative = criterion.weight < 0
        prompt = _JUDGE_PROMPT.format(
            criterion_type="NEGATIVE" if negative else "POSITIVE",
            criterion_type_note=_NEGATIVE_NOTE if negative else _POSITIVE_NOTE,
            requirement=criterion.requirement,
            question=question,
            answer=answer,
        )
        # Samples run concurrently: they are independent draws, so serialising
        # them would multiply wall-clock by `samples` for no benefit.
        verdicts = await asyncio.gather(*(self._sample(prompt) for _ in range(self.samples)))
        usable = [v for v in verdicts if v is not None]
        if not usable:
            # Falls back to unmet. This biases the score DOWN rather than up,
            # and the verdict record says so, so an unparseable judge shows up
            # as an auditable line rather than free credit.
            return axis, criterion, 0.0, "judge returned no parseable verdict"

        met_rate = sum(1 for v in usable if v.met) / len(usable)
        note = usable[0].justification
        if len(usable) > 1 and 0.0 < met_rate < 1.0:
            note = f"split {sum(1 for v in usable if v.met)}/{len(usable)} MET — {note}"
        return axis, criterion, met_rate, note

    async def _sample(self, prompt: str) -> _Verdict | None:
        """One verdict draw, retried once before being written off."""
        return await self._invoke(prompt) or await self._invoke(prompt)

    async def score(self, *, question: str, answer: str, rubric: Rubric) -> ItemScores:
        """Judge every criterion once, then aggregate per axis and overall."""
        results = await asyncio.gather(
            *(
                self._judge_one(section.id, criterion, question, answer)
                for section in rubric.sections
                for criterion in section.criteria
            )
        )

        by_axis: dict[str, list[tuple[Criterion, float]]] = {axis: [] for axis in AXES}
        for axis, criterion, met_rate, _ in results:
            by_axis.setdefault(axis, []).append((criterion, met_rate))

        return ItemScores(
            # Scored over the flat criterion list, NOT as a mean of the four axis
            # scores: the axes hold different numbers of criteria and different
            # weight mass, so averaging them would silently re-weight the rubric.
            overall=aggregate([(c, met_rate) for _, c, met_rate, _ in results]),
            axes={axis: aggregate(v) for axis, v in by_axis.items()},
            verdicts=[
                {
                    "axis": axis,
                    "id": c.id,
                    "weight": c.weight,
                    "requirement": c.requirement,
                    "met": met_rate,
                    "why": why,
                }
                for axis, c, met_rate, why in results
            ],
        )
