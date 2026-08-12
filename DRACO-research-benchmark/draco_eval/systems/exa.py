"""Exa Agent API adapter.

Uses the Agent API (beta), not plain search, so Exa runs the same
end-to-end research flow as every other system here.

  pip install 'draco-eval[exa]'   ·   EXA_API_KEY
  tiers: low, medium, high, xhigh, auto
"""

from __future__ import annotations

import time

from draco_eval.retry import with_retry
from draco_eval.systems import Answer, Citation, dedupe, require_key, require_sdk, validate_tier

TIERS = ("low", "medium", "high", "xhigh", "auto")
_BETA = "agent-2026-05-07"


class Exa:
    def __init__(self, tier: str = "auto") -> None:
        validate_tier(tier, TIERS, "exa")
        AsyncExa = require_sdk("exa_py", "exa").AsyncExa

        self.name = f"exa/{tier}"
        self.tier = tier
        self._client = AsyncExa(api_key=require_key("EXA_API_KEY", "exa"))

    async def run(self, question: str) -> Answer:
        start = time.monotonic()
        run = await with_retry(
            lambda: self._client.beta.agent.runs.create(betas=[_BETA], query=question, effort=self.tier),
            label="exa runs.create",
        )
        run = await with_retry(
            lambda: self._client.beta.agent.runs.poll_until_finished(
                run.id, betas=[_BETA], poll_interval=2000, timeout_ms=3_600_000
            ),
            label=f"exa poll({run.id})",
        )
        latency_s = time.monotonic() - start

        if run.status != "completed":
            err = getattr(getattr(run, "error", None), "message", None) or f"status={run.status}"
            raise RuntimeError(f"Exa agent run {run.id} did not complete: {err}")

        output = run.output
        citations = dedupe(
            [
                Citation(url=getattr(c, "url", "") or "", title=getattr(c, "title", "") or "")
                for entry in (getattr(output, "grounding", None) or [])
                for c in (getattr(entry, "citations", None) or [])
            ]
        )
        cost = getattr(getattr(run, "cost_dollars", None), "total", None)
        return Answer(
            text=str(getattr(output, "text", "") or ""),
            citations=citations,
            latency_s=latency_s,
            cost_usd=float(cost) if isinstance(cost, (int, float)) and cost > 0 else None,
        )
