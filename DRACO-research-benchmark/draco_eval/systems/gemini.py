"""Gemini adapter, via the Interactions API's Deep Research agent.

Uses the `deep-research-*-preview` agent, not `generate_content` + the
google_search tool — the agent runs its own multi-step research loop and
decides when to search, so this measures Gemini's research product rather
than a search API glued to a synthesis prompt we wrote.

  pip install 'draco-eval[gemini]'   ·   GEMINI_API_KEY
  tiers: preview, max

Agent identifiers are dated previews and shift as Google ships new ones; check
ai.google.dev/gemini-api/docs/deep-research for the current ones before
assuming these still exist.
"""

from __future__ import annotations

import time

from draco_eval.retry import with_retry
from draco_eval.systems import Answer, Citation, dedupe, poll_until, require_key, require_sdk, validate_tier

_AGENTS = {
    "preview": "deep-research-preview-04-2026",
    "max": "deep-research-max-preview-04-2026",
}
TIERS = tuple(_AGENTS)
_PENDING = {"queued", "in_progress"}
_POLL_TIMEOUT_S = 3600
_POLL_INTERVAL_S = 10


class Gemini:
    def __init__(self, tier: str = "preview") -> None:
        validate_tier(tier, TIERS, "gemini")
        Client = require_sdk("google.genai", "gemini").Client

        self.name = f"gemini/{tier}"
        self.tier = tier
        self._client = Client(api_key=require_key("GEMINI_API_KEY", "gemini"))

    async def run(self, question: str) -> Answer:
        start = time.monotonic()
        interaction = await with_retry(
            lambda: self._client.aio.interactions.create(agent=_AGENTS[self.tier], input=question, background=True),
            label="gemini interactions.create",
        )
        interaction = await poll_until(
            lambda: with_retry(
                lambda: self._client.aio.interactions.get(interaction.id),
                label=f"gemini interactions.get({interaction.id})",
            ),
            is_terminal=lambda i: i.status not in _PENDING,
            interval_s=_POLL_INTERVAL_S,
            timeout_s=_POLL_TIMEOUT_S,
            label=f"Gemini interaction {interaction.id}",
        )
        latency_s = time.monotonic() - start

        if interaction.status != "completed":
            err = getattr(interaction, "error", None) or f"status={interaction.status}"
            raise RuntimeError(f"Gemini interaction {interaction.id} did not complete: {err}")

        text = getattr(interaction, "output_text", None)
        if not text:
            # Fallback for SDK versions where the Deep Research agent doesn't
            # populate the output_text convenience property.
            steps = interaction.steps or []
            text = steps[-1].content[0].text if steps and getattr(steps[-1], "content", None) else ""

        citations = dedupe(
            [
                Citation(url=getattr(a, "url", "") or "", title=getattr(a, "title", "") or "")
                for step in (interaction.steps or [])
                if getattr(step, "type", None) == "model_output"
                for block in (getattr(step, "content", None) or [])
                for a in (getattr(block, "annotations", None) or [])
                if getattr(a, "type", None) == "url_citation"
            ]
        )
        # Deep Research is billed per search/tool call the agent makes, not a
        # flat per-run price, so cost_usd stays None rather than being estimated.
        return Answer(text=str(text or ""), citations=citations, latency_s=latency_s)
