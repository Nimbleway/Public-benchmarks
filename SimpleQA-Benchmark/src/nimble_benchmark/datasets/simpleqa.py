"""SimpleQA dataset helpers."""

from __future__ import annotations

import ast
import json
import logging

import pandas as pd

from nimble_benchmark.datasets.base import DEFAULT_RANDOM_STATE, Dataset
from nimble_benchmark.judging.grader import evaluate_single_simpleqa

logger = logging.getLogger(__name__)


def _parse_metadata(metadata: str | dict) -> dict:
    if isinstance(metadata, str):
        try:
            parsed = json.loads(metadata)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(metadata)
            except (ValueError, SyntaxError):
                logger.warning("bad_simpleqa_metadata")
                return {}
    elif isinstance(metadata, dict):
        parsed = metadata
    else:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_gold_urls_from_metadata(metadata: str | dict) -> list[str]:
    parsed = _parse_metadata(metadata)
    urls = parsed.get("urls", []) or []
    out: list[str] = []
    for url_value in urls:
        if isinstance(url_value, str):
            out.extend(part.strip() for part in url_value.split() if part.strip())
    return out


class SimpleQADataset(Dataset):
    def load(
        self,
        limit: int | None = None,
        random_state: int | None = DEFAULT_RANDOM_STATE,
    ) -> pd.DataFrame:
        df = super().load(limit=limit, random_state=random_state)
        if "metadata" not in df:
            return df
        metadata = df["metadata"].apply(_parse_metadata)
        for column in ("answer_type", "topic"):
            extracted = metadata.apply(lambda value, column=column: value.get(column))
            if column in df:
                df[column] = df[column].where(df[column].notna(), extracted)
            else:
                df[column] = extracted
        return df


SIMPLEQA = SimpleQADataset(
    name="simpleqa",
    csv_path="data/simpleqa_full_dataset.csv",
    grader=evaluate_single_simpleqa,
)
