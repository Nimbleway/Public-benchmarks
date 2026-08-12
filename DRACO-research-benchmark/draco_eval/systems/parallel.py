"""Parallel Task API adapter.

pip install 'draco-eval[parallel]'   ·   PARALLEL_API_KEY
tiers: lite, base, core, core2x, pro, ultra, ultra2x, ultra4x, ultra8x
"""

from __future__ import annotations

import time

from draco_eval.retry import with_retry
from draco_eval.systems import Answer, Citation, dedupe, require_key, require_sdk, validate_tier

# The Task API returns no per-run cost, so cost is derived from Parallel's
# published list price (parallel.ai/pricing, USD per 1,000 requests). One eval
# item is one task run. This is retail price, not measured spend — see the
# cost caveat in the README before comparing it against a token-COGS number.
PRICE_PER_1K_USD: dict[str, float] = {
    "lite": 5.0,
    "base": 10.0,
    "core": 25.0,
    "core2x": 50.0,
    "pro": 100.0,
    "ultra": 300.0,
    "ultra2x": 600.0,
    "ultra4x": 1200.0,
    "ultra8x": 2400.0,
}
TIERS = tuple(PRICE_PER_1K_USD)

# Without an explicit task_spec the Task API defaults to an "auto" JSON schema
# and returns a terse field — e.g. {"output": "$391.035 billion"} — not a
# research report. Judged against a DRACO rubric that would score near zero on
# breadth and presentation, which would measure our misconfiguration rather
# than Parallel. Asking for a text output puts it on the same footing as the
# other systems, which produce long-form reports by default. The description is
# deliberately generic: it states the artifact type, not what to say.
_TEXT_OUTPUT_SPEC = {
    "output_schema": {
        "type": "text",
        "description": (
            "A comprehensive, well-structured research report that fully answers the question, "
            "with specific facts and figures and inline citations to the sources used."
        ),
    }
}


class Parallel:
    def __init__(self, tier: str = "core") -> None:
        validate_tier(tier, TIERS, "parallel")
        AsyncParallel = require_sdk("parallel", "parallel").AsyncParallel

        self.name = f"parallel/{tier}"
        self.tier = tier
        self._client = AsyncParallel(api_key=require_key("PARALLEL_API_KEY", "parallel"))

    async def run(self, question: str) -> Answer:
        start = time.monotonic()
        run = await with_retry(
            lambda: self._client.task_run.create(input=question, processor=self.tier, task_spec=_TEXT_OUTPUT_SPEC),
            label="parallel task_run.create",
        )
        result = await with_retry(
            lambda: self._client.task_run.result(run.run_id, api_timeout=3600),
            label=f"parallel task_run.result({run.run_id})",
        )
        latency_s = time.monotonic() - start

        output = getattr(result, "output", None)
        content = getattr(output, "content", None)
        if not isinstance(content, str):
            # A dict here means the API ignored the text task_spec and fell back
            # to a JSON schema. Stringifying it would feed Python repr to the
            # judge and quietly score a one-line value as a research report, so
            # fail the item instead.
            raise RuntimeError(
                f"Parallel returned {type(content).__name__} content "
                f"(output type={getattr(output, 'type', None)!r}); expected text."
            )

        basis = getattr(output, "basis", None) or []
        citations = dedupe(
            [
                Citation(url=getattr(c, "url", "") or "", title=getattr(c, "title", "") or "")
                for field_basis in basis
                for c in (getattr(field_basis, "citations", None) or [])
            ]
        )
        return Answer(
            text=content,
            citations=citations,
            latency_s=latency_s,
            cost_usd=PRICE_PER_1K_USD[self.tier] / 1000.0,
        )
