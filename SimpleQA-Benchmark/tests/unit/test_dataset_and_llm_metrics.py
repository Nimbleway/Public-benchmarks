import random
from pathlib import Path

import pandas as pd
import pytest

from nimble_benchmark.datasets.base import DEFAULT_RANDOM_STATE
from nimble_benchmark.datasets.simpleqa import SIMPLEQA, parse_gold_urls_from_metadata
from nimble_benchmark.metrics.llm_retrieval import score_grades

# Canonical row count of the SimpleQA public test set (see the SimpleQA paper
# and `openai/simple-evals` reference). Pinned as a constant so any future
# edit to `data/simpleqa_full_dataset.csv` -- de-duplication, an upstream
# refresh, a malformed-row fix -- fails CI loudly instead of silently shifting
# which 500 rows `random.Random(0).sample(rows, 500)` picks and drifting every
# published benchmark number off the canonical subset.
CANONICAL_SIMPLEQA_ROW_COUNT = 4326

# Resolve relative to the test file so the assertion works regardless of the
# pytest invocation cwd (uv run pytest vs `cd tests && pytest .` vs IDE
# runners).
_BUNDLED_SIMPLEQA_CSV = Path(__file__).resolve().parents[2] / "data" / "simpleqa_full_dataset.csv"


@pytest.mark.unit
def test_simpleqa_loader_extracts_answer_type_and_topic(tmp_path, monkeypatch):
    csv_path = tmp_path / "simpleqa.csv"
    pd.DataFrame(
        [
            {
                "problem": "Who wrote Purple Rain?",
                "answer": "Prince",
                "metadata": {"answer_type": "Person", "topic": "Music"},
            }
        ]
    ).to_csv(csv_path, index=False)
    monkeypatch.setattr(SIMPLEQA, "csv_path", str(csv_path))

    row = SIMPLEQA.load().iloc[0]

    assert row["answer_type"] == "Person"
    assert row["topic"] == "Music"
    assert "metadata" in row


@pytest.mark.unit
def test_simpleqa_loader_extracts_answer_type_and_topic_from_json_metadata(tmp_path, monkeypatch):
    csv_path = tmp_path / "simpleqa.csv"
    pd.DataFrame(
        [
            {
                "problem": "Which genre is Kind of Blue associated with?",
                "answer": "Jazz",
                "metadata": '{"answer_type":"Genre","topic":"Music","verified":true,"note":null,"archived":false}',
            }
        ]
    ).to_csv(csv_path, index=False)
    monkeypatch.setattr(SIMPLEQA, "csv_path", str(csv_path))

    row = SIMPLEQA.load().iloc[0]

    assert row["answer_type"] == "Genre"
    assert row["topic"] == "Music"


@pytest.mark.unit
def test_parse_gold_urls_from_python_literal_metadata():
    metadata = "{'topic': 'x', 'urls': ['https://a.com https://b.com']}"

    assert parse_gold_urls_from_metadata(metadata) == ["https://a.com", "https://b.com"]


@pytest.mark.unit
def test_parse_gold_urls_malformed_returns_empty():
    assert parse_gold_urls_from_metadata("not a dict") == []


@pytest.mark.unit
def test_default_random_state_is_zero_to_match_openai_simple_evals():
    """`DEFAULT_RANDOM_STATE` must be 0 -- the value used by
    `openai/simple-evals` in `simpleqa_eval.py` (`random.Random(0)`). Changing
    it silently would drift the canonical SimpleQA subset away from every
    published baseline."""
    assert DEFAULT_RANDOM_STATE == 0


@pytest.mark.unit
def test_simpleqa_load_sample_is_reproducible_across_calls(tmp_path, monkeypatch):
    """Same seed + same limit must pick the same rows on every call."""
    csv_path = tmp_path / "simpleqa.csv"
    pd.DataFrame([{"problem": f"q{i}", "answer": str(i), "metadata": "{}"} for i in range(50)]).to_csv(
        csv_path, index=False
    )
    monkeypatch.setattr(SIMPLEQA, "csv_path", str(csv_path))

    first = SIMPLEQA.load(limit=10, random_state=0)
    second = SIMPLEQA.load(limit=10, random_state=0)

    pd.testing.assert_frame_equal(first, second)


@pytest.mark.unit
def test_simpleqa_load_matches_openai_simple_evals_algorithm(tmp_path, monkeypatch):
    """Row selection must equal `random.Random(seed).sample(rows, limit)`.

    Mirrors `SimpleQAEval` in
    https://github.com/openai/simple-evals/blob/main/simpleqa_eval.py.
    Byte-for-byte equality means a `--limit N` slice in this repo evaluates on
    the same questions the canonical SimpleQA paper and the public OpenAI
    reference benchmark do, so cross-vendor accuracy numbers stay directly
    comparable instead of drifting on a private subset.

    The reference (`expected`) is constructed via the upstream iteration
    pattern -- ``[row.to_dict() for _, row in df.iterrows()]`` -- which is
    what ``simpleqa_eval.py`` literally writes, rather than re-using the
    loader's own ``to_dict(orient="records")``. That makes the test
    grounded in the canonical reference: if the loader later switches to
    ``df.itertuples`` or a pyarrow-backed read that subtly reorders rows
    (or coerces dtypes differently), the assertion fails because
    ``expected`` no longer matches ``actual``.
    """
    csv_path = tmp_path / "simpleqa.csv"
    pd.DataFrame([{"problem": f"q{i}", "answer": str(i), "metadata": "{}"} for i in range(50)]).to_csv(
        csv_path, index=False
    )
    monkeypatch.setattr(SIMPLEQA, "csv_path", str(csv_path))

    # Build the reference list the way `openai/simple-evals/simpleqa_eval.py`
    # does, not the way *this* loader does. Equality means both iteration
    # patterns produce identical row orderings at the point where
    # ``random.Random(0).sample`` selects indices.
    loaded_rows = [row.to_dict() for _, row in pd.read_csv(csv_path).iterrows()]
    expected = random.Random(0).sample(loaded_rows, 10)

    df = SIMPLEQA.load(limit=10, random_state=0)
    actual = df[["problem", "answer", "metadata"]].to_dict(orient="records")

    assert actual == expected


@pytest.mark.unit
def test_bundled_simpleqa_csv_has_canonical_row_count():
    """The bundled ``data/simpleqa_full_dataset.csv`` must have exactly
    :data:`CANONICAL_SIMPLEQA_ROW_COUNT` rows (the public SimpleQA test set
    count).

    Why this matters: ``--limit N`` slices use
    ``random.Random(seed).sample(rows, N)``. ``Random.sample`` picks indices
    into the row list, so if a future edit to the bundled CSV changes
    ``len(rows)``, every previously-published ``--limit 500`` accuracy number
    silently drifts onto a different subset of questions -- with no test
    failure to catch the regression.

    Hard-coding the count here forces any future CSV edit (de-dup, fix a
    malformed cell, refresh from upstream) to be an explicit, reviewed
    change rather than a silent number-shifter.
    """
    assert _BUNDLED_SIMPLEQA_CSV.exists(), (
        f"bundled SimpleQA CSV not found at {_BUNDLED_SIMPLEQA_CSV}. "
        "If you intentionally removed it, update this test."
    )
    df = pd.read_csv(_BUNDLED_SIMPLEQA_CSV)
    assert len(df) == CANONICAL_SIMPLEQA_ROW_COUNT, (
        f"bundled CSV has {len(df)} rows, expected {CANONICAL_SIMPLEQA_ROW_COUNT}. "
        "If this is an intentional refresh (e.g. upstream SimpleQA update), "
        "bump CANONICAL_SIMPLEQA_ROW_COUNT and re-run every published benchmark "
        "so accuracy numbers stay anchored to a documented subset."
    )
    # The loader reads three named columns -- a future reshape that adds /
    # drops columns is also worth catching here.
    assert list(df.columns) == ["metadata", "problem", "answer"]


@pytest.mark.unit
def test_simpleqa_load_defaults_to_canonical_seed(tmp_path, monkeypatch):
    """Omitting `random_state` must use the canonical default (0).

    Guards against regressing back to `random_state=None` (first-N slice) or
    to an unseeded `pandas.DataFrame.sample()` call, both of which existed
    earlier in the repo's history and are non-canonical."""
    csv_path = tmp_path / "simpleqa.csv"
    pd.DataFrame([{"problem": f"q{i}", "answer": str(i), "metadata": "{}"} for i in range(50)]).to_csv(
        csv_path, index=False
    )
    monkeypatch.setattr(SIMPLEQA, "csv_path", str(csv_path))

    implicit = SIMPLEQA.load(limit=5)
    explicit = SIMPLEQA.load(limit=5, random_state=0)

    pd.testing.assert_frame_equal(implicit, explicit)


@pytest.mark.unit
def test_simpleqa_load_different_seeds_pick_different_rows(tmp_path, monkeypatch):
    """Sanity check that the seed actually drives row selection.

    If sampling silently collapsed to first-N or an unseeded path, two
    different seeds would still produce the same rows -- this guards against
    that regression."""
    csv_path = tmp_path / "simpleqa.csv"
    pd.DataFrame([{"problem": f"q{i}", "answer": str(i), "metadata": "{}"} for i in range(50)]).to_csv(
        csv_path, index=False
    )
    monkeypatch.setattr(SIMPLEQA, "csv_path", str(csv_path))

    seed_zero = SIMPLEQA.load(limit=10, random_state=0)
    seed_one = SIMPLEQA.load(limit=10, random_state=1)

    assert seed_zero["problem"].tolist() != seed_one["problem"].tolist()


@pytest.mark.unit
def test_simpleqa_load_none_random_state_falls_back_to_default(tmp_path, monkeypatch):
    """Passing `random_state=None` (e.g. from a legacy caller) must behave
    identically to the canonical default rather than silently dropping back to
    unseeded or first-N sampling."""
    csv_path = tmp_path / "simpleqa.csv"
    pd.DataFrame([{"problem": f"q{i}", "answer": str(i), "metadata": "{}"} for i in range(50)]).to_csv(
        csv_path, index=False
    )
    monkeypatch.setattr(SIMPLEQA, "csv_path", str(csv_path))

    via_none = SIMPLEQA.load(limit=5, random_state=None)
    via_zero = SIMPLEQA.load(limit=5, random_state=0)

    pd.testing.assert_frame_equal(via_none, via_zero)


@pytest.mark.unit
def test_score_grades_handles_empty():
    scores = score_grades([])

    assert scores == {
        "recall_at_5_llm": 0.0,
        "recall_at_10_llm": 0.0,
        "hit_at_5_llm": 0,
        "hit_at_10_llm": 0,
    }


@pytest.mark.unit
def test_score_grades_threshold_recall():
    assert score_grades([1, 1, 1], recall_threshold=2)["recall_at_5_llm"] == 0.0
    assert score_grades([1, 2, 1], recall_threshold=2)["recall_at_5_llm"] == 1.0
