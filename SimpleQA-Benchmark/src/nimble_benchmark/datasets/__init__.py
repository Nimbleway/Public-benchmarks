"""Dataset registry."""

from nimble_benchmark.datasets.base import Dataset
from nimble_benchmark.datasets.simpleqa import SIMPLEQA, parse_gold_urls_from_metadata

DATASETS: dict[str, Dataset] = {"simpleqa": SIMPLEQA}

__all__ = ["DATASETS", "SIMPLEQA", "Dataset", "parse_gold_urls_from_metadata"]
