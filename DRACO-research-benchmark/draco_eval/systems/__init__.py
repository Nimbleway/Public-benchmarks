"""The System contract: what a research system must provide to be evaluated.

Implement `System` and your agent is comparable against every other adapter in
this repo on identical rubrics, an identical judge, and identical aggregation.

Fairness rules the built-in adapters follow, and yours should too:

  1. One question in, one final answer out. No per-item prompt tuning, no
     retries-for-quality, no rubric visible to the system under test.
  2. Cite through `Answer.citations`. The judge only ever sees text, so the
     runner appends a uniform Sources block — if you inline your own bespoke
     bibliography instead, the citation-quality axis stops being comparable.
  3. `latency_s` is your system's own wall clock (submit -> final answer),
     excluding judging.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Citation:
    url: str
    title: str = ""


@dataclass
class Answer:
    text: str
    citations: list[Citation] = field(default_factory=list)
    latency_s: float = 0.0
    #: Vendor-billed price where the API reports one. NOT comparable across
    #: systems that report different things (retail price vs. token COGS) —
    #: the report labels the source rather than pretending it is like-for-like.
    cost_usd: float | None = None


class System(Protocol):
    name: str

    async def run(self, question: str) -> Answer: ...


def validate_tier(tier: str, tiers: tuple[str, ...], system: str) -> None:
    if tier not in tiers:
        raise ValueError(f"Unknown {system} effort {tier!r}. Valid: {', '.join(tiers)}")


async def poll_until[T](
    fetch: Callable[[], Awaitable[T]],
    *,
    is_terminal: Callable[[T], bool],
    interval_s: float,
    timeout_s: float,
    label: str = "run",
) -> T:
    """Poll `fetch` until it returns a terminal state, or raise past the deadline.

    Every polling adapter (Nimble, Gemini) needs the same shape — sleep, fetch,
    check, repeat, bounded so a stalled vendor task cannot hang the whole run.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        result = await fetch()
        if is_terminal(result):
            return result
        if time.monotonic() > deadline:
            raise TimeoutError(f"{label} unfinished after {timeout_s:.0f}s")
        await asyncio.sleep(interval_s)


def require_key(env_var: str, system: str) -> str:
    key = os.getenv(env_var)
    if not key:
        raise RuntimeError(f"{env_var} is not set — required by the {system} adapter.")
    return key


def require_sdk(module: str, extra: str):
    """Import a vendor SDK, or explain which extra installs it."""
    try:
        return importlib.import_module(module)
    except ImportError as e:
        raise RuntimeError(f"The {extra} adapter needs `{module}`: pip install 'draco-eval[{extra}]'") from e


def sources_block(citations: list[Citation]) -> str:
    """Render citations uniformly for the judge.

    Every system's answer text gets this same block appended, so the
    citation-quality axis measures which sources a system chose, not how
    prettily its SDK happens to format a bibliography.
    """
    if not citations:
        return ""
    lines = ["", "Sources:"]
    lines += [f"[{i}] {c.title or c.url} — {c.url}" for i, c in enumerate(citations, start=1)]
    return "\n".join(lines)


#: A trailing bibliography heading: "## Sources", "**References:**", "Source index:".
_BIBLIOGRAPHY_HEADING = re.compile(
    r"\n[ \t]*#{0,6}[ \t]*\**(?:sources?|references?|source index)\**[ \t]*:?[ \t]*\n",
    re.IGNORECASE,
)


def strip_trailing_bibliography(text: str) -> str:
    """Drop a vendor-appended source list from the end of an answer.

    Some systems append their own bibliography to the prose while also
    returning citations structurally. The runner adds a uniform block, so
    leaving the vendor's in place would give that system two.

    Only strips when every line under the heading carries a link, so a prose
    section that happens to be titled "References" survives.
    """
    matches = list(_BIBLIOGRAPHY_HEADING.finditer(text))
    if not matches:
        return text
    head, tail = text[: matches[-1].start()], text[matches[-1].end() :]
    lines = [ln for ln in tail.splitlines() if ln.strip()]
    if not lines or not all("http" in ln for ln in lines):
        return text
    return head.rstrip().removesuffix("---").rstrip()


def dedupe(citations: list[Citation]) -> list[Citation]:
    seen: set[str] = set()
    out: list[Citation] = []
    for c in citations:
        if c.url and c.url not in seen:
            seen.add(c.url)
            out.append(c)
    return out
