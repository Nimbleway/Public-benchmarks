"""Smoke test: recompute each suite's summary from its shipped results (offline) and compare with results/summary.json.

    uv run pytest      # or: python -m pytest
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(PROJECT))
# every benchmark that uses agent-bench; the first suite of each entry is the one whose summary.json is checked, the
# others are summarized with it (Finance and GTM are published with an "across both suites" view)
SUITES = [["Finance-Use-Case-Benchmark", "GTM-Use-Case-Benchmark"], ["GTM-Use-Case-Benchmark", "Finance-Use-Case-Benchmark"],
          ["Due-Diligence-Benchmark"], ["Lead-Enrichment-Benchmark"]]


def rounded(x):
    if isinstance(x, float):
        return round(x, 3)
    if isinstance(x, dict):
        return {k: rounded(v) for k, v in x.items()}
    if isinstance(x, list):
        return [rounded(v) for v in x]
    return x


@pytest.mark.parametrize("suites", SUITES, ids=lambda s: s[0])
def test_summary_matches_shipped(tmp_path, suites):
    # copy the suites so the shipped summary.json is not overwritten
    for name in suites:
        shutil.copytree(os.path.join(REPO, name), tmp_path / name, ignore=shutil.ignore_patterns("data"))
    args = [x for name in suites for x in ("--suite", str(tmp_path / name))]
    subprocess.run([sys.executable, "-m", "agent_bench.summary", *args], cwd=PROJECT, check=True, capture_output=True)
    shipped = json.load(open(os.path.join(REPO, suites[0], "results", "summary.json")))
    fresh = json.load(open(tmp_path / suites[0] / "results" / "summary.json"))
    assert rounded(fresh) == rounded(shipped)
