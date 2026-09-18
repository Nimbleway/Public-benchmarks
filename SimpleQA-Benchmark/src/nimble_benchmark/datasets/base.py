"""Dataset model."""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import pandas as pd

GraderFn = Callable[[str, str, str], Awaitable[dict]]


# Default sampling seed matches OpenAI's canonical SimpleQA implementation in
# `openai/simple-evals` (`simpleqa_eval.py`), which seeds a fresh
# `random.Random(0)` and calls `rng.sample(examples, num_examples)`. Reusing
# both the seed and the stdlib RNG keeps a `--limit N` slice reproducible
# across runs and identical to the canonical subset that every other
# SimpleQA-based benchmark in the field reports against.
DEFAULT_RANDOM_STATE = 0


@dataclass
class Dataset:
    name: str
    csv_path: str
    grader: GraderFn

    def load(
        self,
        limit: int | None = None,
        random_state: int | None = DEFAULT_RANDOM_STATE,
    ) -> pd.DataFrame:
        """Load the dataset CSV, optionally sampling ``limit`` rows.

        Sampling mirrors ``SimpleQAEval`` in ``openai/simple-evals``:

            rng = random.Random(seed)
            examples = rng.sample(rows, limit)

        Using the Python stdlib RNG (not ``pandas.DataFrame.sample`` /
        ``numpy``) is deliberate -- it guarantees the exact same row subset as
        the canonical simple-evals reference when ``seed=0``, so accuracy
        numbers stay directly comparable with published SimpleQA results.

        ``random_state=None`` is treated as the default seed so callers can
        pass through an ``Optional[int]`` from argparse without re-introducing
        unseeded, non-reproducible sampling.
        """
        df = pd.read_csv(self.csv_path)
        if limit is None or limit >= len(df):
            return df.reset_index(drop=True)
        seed = DEFAULT_RANDOM_STATE if random_state is None else random_state
        rows = df.to_dict(orient="records")
        sampled = random.Random(seed).sample(rows, limit)
        return pd.DataFrame(sampled).reset_index(drop=True)
