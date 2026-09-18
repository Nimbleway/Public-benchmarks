"""Prompt constants and default model names.

``SIMPLEQA_ANSWER_GRADER_TEMPLATE`` is loaded verbatim from
``prompts/simpleqa_grader.txt`` (vendored from ``openai/simple-evals``,
MIT) so the ``A``/``B``/``C`` grade distribution matches the canonical
SimpleQA methodology. The text lives in a resource file rather than a
string literal here to keep the provenance ``diff``-able against the
upstream source.

The *grades* are upstream; the *aggregation* is not. See
``ACCURACY_DENOMINATOR_RESULTS`` below -- this harness folds
not-attempted into the accuracy denominator as a miss, where upstream
reports it as a separate bucket, so the leaderboard's accuracy column is
deliberately stricter than a published SimpleQA figure and should not be
quoted against one.

Source: https://github.com/openai/simple-evals/blob/main/simpleqa_eval.py
Upstream license: MIT — see ``data/LICENSE.simple-evals`` and ``data/README.md``.
"""

from nimble_benchmark.prompts import load_prompt

SIMPLEQA_ANSWER_GRADER_TEMPLATE = load_prompt("simpleqa_grader")

SYNTHESIS_PROMPT = """Answer the query using only the provided search results."""

# Fallback defaults used only when no Settings object is available (e.g. in
# unit tests that import constants directly). The CLI default for synthesis
# is SYNTHESIS_MODEL_CHOICES[0] in config.py ("gpt-4o"), which takes
# precedence over this value at runtime.
SYNTHESIS_MODEL = "gpt-4o"
GRADER_MODEL = "gpt-4o"
LLM_JUDGE_MODEL = "gpt-4o-2024-08-06"

# ---------------------------------------------------------------------------
# The accuracy contract.
#
# ``evaluation_result`` on a raw row carries one of four labels: the three
# SimpleQA grades (``is_correct`` / ``is_incorrect`` / ``is_not_attempted``,
# see ``judging.grader.GRADE_MAP``) plus ``not_evaluated`` for a row the grader
# never saw.
#
# ACCURACY_DENOMINATOR_RESULTS is the denominator: every row where the request
# reached the provider and came back 200. ``is_not_attempted`` is IN it --
# a lane that answers "I don't know", or returns a 200 with nothing usable in
# it, has failed to answer a question it was served, and scores the same as
# answering it wrong. This departs from upstream simple-evals, which reports
# not-attempted as its own third bucket and computes accuracy over attempts
# only; the labels are unchanged, so the upstream split is still recoverable
# from ``evaluation_result`` in the raw CSVs.
#
# ``not_evaluated`` stays out: the request itself failed, so there is no
# provider answer to score. That row is counted by ``failure_rate`` instead,
# and a lane failing enough of them is dropped from the ranking by the
# reliability gate in ``analyzer``.
ACCURACY_DENOMINATOR_RESULTS: frozenset[str] = frozenset({"is_correct", "is_incorrect", "is_not_attempted"})
ACCURACY_CORRECT_RESULT = "is_correct"
