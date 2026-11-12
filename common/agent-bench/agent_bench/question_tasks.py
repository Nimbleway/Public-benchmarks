"""Build a tasks.json that runs a sample of a question set through this harness, one job of single questions.

    python -m agent_bench.question_tasks Due-Diligence-Benchmark dd_question "Due diligence questions" 50
    python -m agent_bench.question_tasks Lead-Enrichment-Benchmark le_question "Lead enrichment questions" 50

Reads <set>/data/questions.csv, samples N questions in proportion to each category's size (largest remainder, drawn
with random.Random(SEED)), and writes <set>/tasks.json. The job is "Answer a current question with sources"
(fin_grounded_answer in Finance-Use-Case-Benchmark/tasks.json): its instructions, output schema, unit, judge tag and
tier, taken from there rather than copied. A question set has no answer keys, so answer_key is false. The judge also
tags each fact "answers" (does it answer the question?), so a long answer's extra true facts count towards
thoroughness, not the headline score (coverage "answers"). Then run every command with --suite <set>.
"""
import csv
import json
import os
import random
import sys

from .config import REPO, suite_path

TEMPLATE = "fin_grounded_answer"
TEMPLATE_SUITE = os.path.join(REPO, "Finance-Use-Case-Benchmark")
SEED = 2026
ANSWERS_SPEC = (', "answers": true|false (true = the fact states the answer to the question asked, or part of it; '
                'false = context or other facts about the entity)')
SCORING_NOTE = (" No answer key: the set is unlabeled. Headline score: coverage of the pooled true facts that answer the "
                "question (answers = true). Thoroughness: coverage of all pooled true facts. Both use the same accuracy.")


def sample(rows, n):
    """n questions, each category's share of them as close as possible to its share of the set."""
    rng = random.Random(SEED)
    by = {}
    for row in rows:
        by.setdefault(row["category"], []).append(row["question"])
    exact = {c: n * len(q) / len(rows) for c, q in sorted(by.items())}
    take = {c: int(x) for c, x in exact.items()}
    for c in sorted(exact, key=lambda c: take[c] - exact[c])[: n - sum(take.values())]:
        take[c] += 1
    return [q for c in sorted(by) for q in rng.sample(by[c], take[c])]


def main(folder, job_id, name, n):
    folder = suite_path(folder)
    with open(os.path.join(folder, "data", "questions.csv"), newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    questions = sample(rows, n)
    template = next(u for u in json.load(open(os.path.join(TEMPLATE_SUITE, "tasks.json"))) if u["id"] == TEMPLATE)
    task = {**template, "id": job_id, "name": name, "vertical": os.path.basename(os.path.abspath(folder)),
            "inputs": questions, "answer_key": False,
            # a true fact that does not answer the question is context: it neither costs accuracy nor adds coverage
            "unit": {**template["unit"], "verify_rule": "true if the fact is correct and current as of the run date."},
            "judge_tag": {**template["judge_tag"], "spec": template["judge_tag"]["spec"] + ANSWERS_SPEC},
            "scoring_rule": template["scoring_rule"] + SCORING_NOTE}
    path = os.path.join(folder, "tasks.json")
    json.dump([task], open(path, "w"), indent=1, ensure_ascii=False)
    print(f"{path}: {n} of {len(rows)} questions, seed {SEED}")


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]))
