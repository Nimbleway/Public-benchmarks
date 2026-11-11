"""Thin wrappers around the Claude Code CLI (`claude -p`, your own login) used by the judge, the answer keys and the
key audit. Each returns (parsed JSON or None/[], reported cost in USD).

  extract_call  no tools, JUDGE_MODEL                          (unit extraction, merge)
  web_call      WebSearch, WebFetch and the fetch.py command    (answer keys, audit sample)
  evidence_call web tools plus Read/Grep over evidence_cache/   (verifier agent, retry pass)
  plain_call    no tools                                        (inline verifier, grading, corrections)
"""
import json
import os
import re
import subprocess
import sys
import time

from . import ROOT

FETCH = f"{sys.executable} -m usecase_bench.fetch"
LOG = lambda *a: print(time.strftime("%H:%M:%S"), *a, flush=True)  # noqa: E731
# Judge models, pinned to the IDs the aliases resolved to when the published results were judged (Nov 2026).
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "claude-sonnet-5-5")
AUDIT_MODEL = os.environ.get("AUDIT_MODEL", "claude-opus-5-5")
BASE = ["claude", "-p", "--strict-mcp-config", "--disable-slash-commands", "--setting-sources", "project"]


def extract_call(system, user, tools=False, retries=2):
    args = BASE + ["--model", JUDGE_MODEL, "--system-prompt", system, "--output-format", "json"]
    args += ["--tools", "WebSearch,WebFetch", "--allowedTools", "WebSearch,WebFetch"] if tools else ["--tools", ""]
    cost = 0.0
    for _ in range(retries):
        p = subprocess.run(args, input=user, capture_output=True, text=True, timeout=2400, cwd=ROOT)
        try:
            env = json.loads(p.stdout)
            cost += env.get("total_cost_usd") or 0
            m = re.search(r"[\[{].*[\]}]", env.get("result") or "", re.S)
            return json.loads(m.group(0)), cost
        except Exception as e:  # noqa: BLE001
            LOG("claude parse fail", str(e)[:80], p.stderr[:150])
    return None, cost


def web_call(system, user, model=None):
    args = BASE + ["--model", model or JUDGE_MODEL, "--system-prompt", system, "--output-format", "json",
                   "--tools", "WebSearch,WebFetch,Bash", "--allowedTools", "WebSearch", "WebFetch", f"Bash({FETCH}:*)"]
    cost = 0.0
    for _ in range(2):
        p = subprocess.run(args, input=user, capture_output=True, text=True, timeout=3000, cwd=ROOT)
        try:
            env = json.loads(p.stdout)
            cost += env.get("total_cost_usd") or 0
            m = re.search(r"\[.*\]", env.get("result") or "", re.S)
            return json.loads(m.group(0)), cost
        except Exception as e:  # noqa: BLE001
            LOG("parse fail", str(e)[:80], p.stderr[:120])
    return [], cost


def evidence_call(system, user, model=None):
    """web_call plus Read/Grep, run from the benchmark folder so the verifier can search evidence_cache/."""
    args = BASE + ["--model", model or JUDGE_MODEL, "--system-prompt", system, "--output-format", "json",
                   "--tools", "WebSearch,WebFetch,Bash,Read,Grep",
                   "--allowedTools", "WebSearch", "WebFetch", "Read", "Grep", f"Bash({FETCH}:*)"]
    cost = 0.0
    for _ in range(2):
        p = subprocess.run(args, input=user, capture_output=True, text=True, timeout=3600, cwd=ROOT)
        try:
            env = json.loads(p.stdout)
            cost += env.get("total_cost_usd") or 0
            m = re.search(r"\[.*\]", env.get("result") or "", re.S)
            return json.loads(m.group(0)), cost
        except Exception as e:  # noqa: BLE001
            LOG("parse fail", str(e)[:80], p.stderr[:120])
    return [], cost


def plain_call(system, user, model=None):
    args = BASE + ["--model", model or JUDGE_MODEL, "--system-prompt", system, "--output-format", "json", "--tools", ""]
    cost = 0.0
    for _ in range(2):
        p = subprocess.run(args, input=user, capture_output=True, text=True, timeout=1800, cwd=ROOT)
        try:
            env = json.loads(p.stdout)
            cost += env.get("total_cost_usd") or 0
            m = re.search(r"\[.*\]", env.get("result") or "", re.S)
            return json.loads(m.group(0)), cost
        except Exception as e:  # noqa: BLE001
            LOG("parse fail", str(e)[:80], p.stderr[:120])
    return [], cost
