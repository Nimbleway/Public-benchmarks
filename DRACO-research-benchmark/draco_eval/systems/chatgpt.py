"""ChatGPT adapter, via the Responses API's native web_search tool.

Not OpenAI's separate Deep Research product (the o3/o4-mini-deep-research
models) — this is the flagship chat model doing its own tool-use loop over
web_search, deciding itself when and how many times to search.

  pip install 'draco-eval[chatgpt]'   ·   OPENAI_API_KEY
  tiers: gpt-5.5, gpt-5.5-pro
"""

from __future__ import annotations

import time

from draco_eval.retry import with_retry
from draco_eval.systems import Answer, Citation, dedupe, require_key, require_sdk, validate_tier

TIERS = ("gpt-5.5", "gpt-5.5-pro")

# Without an explicit instruction the model answers a bare fact ("Paris."), not
# a research report — scoring near zero on breadth and citation-quality axes
# that measure our prompt, not ChatGPT's search. Same fix, same wording, as
# Parallel's _TEXT_OUTPUT_SPEC (systems/parallel.py): configuration parity,
# not a hint about the answer.
_REPORT_INSTRUCTION = (
    "Answer the question with a comprehensive, well-structured research report, "
    "with specific facts and figures and inline citations to the sources you used."
)


class ChatGPT:
    def __init__(self, tier: str = "gpt-5.5") -> None:
        validate_tier(tier, TIERS, "chatgpt")
        AsyncOpenAI = require_sdk("openai", "chatgpt").AsyncOpenAI

        self.name = f"chatgpt/{tier}"
        self.tier = tier
        self._client = AsyncOpenAI(api_key=require_key("OPENAI_API_KEY", "chatgpt"))

    async def run(self, question: str) -> Answer:
        start = time.monotonic()
        resp = await with_retry(
            lambda: self._client.responses.create(
                model=self.tier,
                input=[
                    {"role": "developer", "content": [{"type": "input_text", "text": _REPORT_INSTRUCTION}]},
                    {"role": "user", "content": [{"type": "input_text", "text": question}]},
                ],
                tools=[{"type": "web_search_preview"}],
            ),
            label="chatgpt responses.create",
        )
        latency_s = time.monotonic() - start

        citations = dedupe(
            [
                Citation(url=getattr(a, "url", "") or "", title=getattr(a, "title", "") or "")
                for item in (resp.output or [])
                for block in (getattr(item, "content", None) or [])
                for a in (getattr(block, "annotations", None) or [])
                if getattr(a, "type", None) == "url_citation"
            ]
        )
        # The Responses API bills by token usage, not a per-run dollar figure,
        # so cost_usd stays None rather than being estimated from a price table.
        return Answer(text=str(resp.output_text or ""), citations=citations, latency_s=latency_s)
