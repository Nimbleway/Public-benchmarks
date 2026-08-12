"""Nimble adapter, via the public `nimble_python` SDK.

  pip install 'draco-eval[nimble]'   ·   NIMBLE_API_KEY
  tiers: low, medium, high, x-high, max

`agents.run` starts an ad-hoc research run — no agent has to be created or
configured first, so every item is a clean, identically-configured run.
"""

from __future__ import annotations

import time

from draco_eval.retry import with_retry
from draco_eval.systems import Answer, Citation, dedupe, poll_until, require_key, require_sdk, validate_tier

TIERS = ("low", "medium", "high", "x-high", "max")
_TERMINAL = {"completed", "failed", "cancelled"}
_POLL_TIMEOUT_S = 3600
_POLL_INTERVAL_S = 5


class Nimble:
    def __init__(self, tier: str = "high") -> None:
        validate_tier(tier, TIERS, "nimble")
        AsyncNimble = require_sdk("nimble_python", "nimble").AsyncNimble

        self.name = f"nimble/{tier}"
        self.tier = tier
        self._client = AsyncNimble(api_key=require_key("NIMBLE_API_KEY", "nimble"), timeout=120)

    async def run(self, question: str) -> Answer:
        start = time.monotonic()

        run = await with_retry(
            lambda: self._client.agents.run(input=question, effort=self.tier, use_case="research"),
            label="nimble agents.run",
        )
        run_id, agent_id = run.id, run.web_search_agent_id

        state = await poll_until(
            lambda: with_retry(
                lambda: self._client.agents.runs.get(run_id, agent_id=agent_id),
                label=f"nimble runs.get({run_id})",
            ),
            is_terminal=lambda s: s.status in _TERMINAL,
            interval_s=_POLL_INTERVAL_S,
            timeout_s=_POLL_TIMEOUT_S,
            label=f"Nimble run {run_id}",
        )
        if state.status != "completed":
            # Server-side failures happen and are often transient; the reason
            # belongs in the record, and `draco-eval run` re-run retries the item.
            error = getattr(state, "error", None)
            raise RuntimeError(f"Nimble run {run_id} ended status={state.status!r}: {error or 'no reason given'}")

        result = await with_retry(
            lambda: self._client.agents.runs.result(run_id, agent_id=agent_id),
            label=f"nimble runs.result({run_id})",
        )
        latency_s = time.monotonic() - start

        payload = result if isinstance(result, dict) else result.model_dump()
        output = payload.get("output") or {}
        # The API appends its own "Source index:" block to `content`; the runner
        # strips any vendor bibliography before adding the uniform one.
        text = str(output.get("content", "") or "")

        sources = (output.get("trust") or {}).get("sources") or []
        citations = dedupe([Citation(url=s.get("url", ""), title=s.get("title", "")) for s in sources])
        # The public API reports no per-run price, so cost_usd stays None rather
        # than being guessed.
        return Answer(text=text, citations=citations, latency_s=latency_s)
