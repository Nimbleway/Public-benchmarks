"""Settings loaded from env + .env."""

import os
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

SYNTHESIS_MODEL_CHOICES: tuple[str, ...] = (
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-5",
    "gpt-5-mini",
    "gpt-5-thinking",
    "gpt-5-mini-thinking",
    "gpt-5.5",
    "gpt-5.6-sol",
)
"""Models the workflow exposes for `synthesis_model`. The first entry is the default.

``gpt-4o`` leads deliberately, and the ordering is load-bearing: both the
``Settings`` default and the ``--synthesis-model`` CLI default read entry ``[0]``.

The gpt-5 family REJECTS ``temperature != 1`` with a ``BadRequestError``, so
``synthesis.llm.call_llm`` clamps the synthesizer's ``temperature=0.0`` up to
``1``. That makes eval-side synthesis non-deterministic: the same chunks can
yield different answers across runs, so two runs over the same SimpleQA subset
disagree on accuracy from sampling noise alone. Since the whole Search APIs
table is graded off synthesized answers, a gpt-5 default silently traded
reproducibility -- the thing the published leaderboard is for -- for a stronger
synthesizer. ``gpt-4o`` honors ``temperature=0.0``. Pick a gpt-5.x entry
explicitly if you want the stronger model and accept the variance.

The clamp is deliberate, not a workaround to remove: the ``BadRequestError``
is not in ``call_llm``'s transient-retry set, so without the clamp every
synthesis row of the run fails outright. Clamped-and-noisy beats zero rows.

Every ``gpt-5.x`` entry here was probed directly against
``/v1/chat/completions`` and all of them reject ``temperature=0`` with
"Only the default (1) value is supported" -- ``gpt-5``, ``gpt-5-mini``,
``gpt-5.5``, and ``gpt-5.6-sol`` alike. The ``startswith("gpt-5")`` rule in
``synthesis.llm._is_gpt5_family`` is therefore accurate for this whole list,
and the reproducibility caveat above applies to every one of them, not just
the bare ``gpt-5`` aliases.

``-pro`` models (``gpt-5.4-pro``, ``gpt-5.5-pro``) are deliberately absent.
They are OpenAI models, but ``/v1/chat/completions`` -- the only endpoint
``synthesis.llm.call_llm`` speaks -- 404s them with "This is not a chat
model", and this harness has no server-routed answer path that could use them
instead, so listing one would only offer a choice that can never complete a
run. :func:`synthesis_model_supports_eval_side_synthesis` rejects them too, so
a ``SYNTHESIS_MODEL`` env value naming one fails at run start rather than
404-ing every synthesized row.

Every graded answer in this harness is synthesized eval-side through the
OpenAI client, so the list is OpenAI-only -- the Anthropic entries went away
with the server-side ``/answer`` lanes that were the only thing that could
have used them. ``--synthesis-model`` rejects anything outside this tuple at
parse time; a ``SYNTHESIS_MODEL`` env value that slips past it (the settings
field is an unconstrained ``str``) is caught at run start by
``synthesis_model_supports_eval_side_synthesis`` with an actionable message.
"""


def synthesis_model_supports_eval_side_synthesis(model: str) -> bool:
    """True for models the eval-side OpenAI synthesizer can call.

    Two independent reasons a model can fail here:

    * It is not an OpenAI model at all (a ``claude-*`` id, say, which nothing
      in this harness can route).
    * It IS an OpenAI model but is not served on ``/v1/chat/completions``,
      which is the only endpoint :func:`synthesis.llm.call_llm` speaks. The
      ``*-pro`` reasoning models answer such requests with a 404 "This is not
      a chat model" -- confirmed against ``gpt-5.2-pro``, ``gpt-5.4-pro``, and
      ``gpt-5.5-pro``. Catching them here turns a run that would fail every
      synthesized row into an upfront CLI error.
    """
    if model.endswith("-pro"):
        return False
    return model.startswith(("gpt-", "o1", "o3", "o4"))


class Settings(BaseSettings):
    # Secrets — no in-code defaults; supplied via env / GitHub Secrets.
    nimble_api_key: SecretStr | None = None
    # Gates no lane of its own, but every run needs it: grader, UMBRELA judge
    # and answer synthesis all go through it. See
    # ``require_openai_for_benchmark``.
    openai_api_key: SecretStr | None = None
    exa_api_key: SecretStr | None = None
    parallel_api_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None
    firecrawl_api_key: SecretStr | None = None
    brave_search_api_key: SecretStr | None = None

    # The public Nimble URL ships as a code default so workflows don't have to
    # set it. Override locally only when benchmarking a different host.
    nimble_base_url: str = "https://sdk.nimbleway.com/v2"

    # Non-secret config knobs. All have defaults so CI never has to set them as
    # env vars; override per-run via CLI flags or workflow inputs.
    # ``standard`` is the server's current name for this tier and ``fast`` the
    # deprecated alias it still accepts; ``deep`` means ``lite`` plus
    # ``full_content``. Legacy spellings stay accepted so existing .env files
    # keep working.
    nimble_search_depth: Literal["lite", "fast", "standard", "deep"] = "fast"
    # There is deliberately no ``parallel_search_mode`` field: Parallel's mode is
    # pinned per lane (``parallel_search_basic`` / ``parallel_search_turbo``,
    # see ``samplers.PARALLEL_LANE_MODES``) so every run measures both ends of
    # the product. ``extra="ignore"`` below means a stale ``PARALLEL_SEARCH_MODE``
    # left in a local .env is quietly ignored rather than raising.
    judge_model: str = "gpt-4o-2024-08-06"
    synthesis_model: str = SYNTHESIS_MODEL_CHOICES[0]
    grader_model: str = "gpt-4o"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


def require_openai_for_benchmark(settings: Settings) -> None:
    if settings.openai_api_key is None:
        raise RuntimeError(
            "OPENAI_API_KEY is required for benchmark runs because the harness grades answers "
            "and runs UMBRELA retrieval judging in-band. Set it in .env or as an environment variable."
        )


def require_credentials_for_samplers(settings: Settings, sampler_names: list[str]) -> None:
    missing: list[tuple[str, str]] = []

    def add_missing(sampler_name: str, env_name: str) -> None:
        missing.append((sampler_name, env_name))

    def has_secret(secret: SecretStr | None) -> bool:
        return secret is not None and bool(secret.get_secret_value().strip())

    def has_value(value: str | None) -> bool:
        return value is not None and bool(value.strip())

    for sampler_name in sampler_names:
        if sampler_name == "nimble_search":
            if not has_value(settings.nimble_base_url):
                add_missing(sampler_name, "NIMBLE_BASE_URL")
            if not has_secret(settings.nimble_api_key):
                add_missing(sampler_name, "NIMBLE_API_KEY")
        elif sampler_name in ("exa_search_auto", "exa_search_fast"):
            # Both Exa type lanes are billed against the same key; listed
            # literally rather than imported from ``samplers`` because that
            # module imports this one.
            if not has_secret(settings.exa_api_key):
                add_missing(sampler_name, "EXA_API_KEY")
        elif sampler_name in ("parallel_search_basic", "parallel_search_turbo"):
            # Both Parallel mode lanes are billed against the same key; listed
            # literally rather than imported from ``samplers`` because that
            # module imports this one.
            if not has_secret(settings.parallel_api_key):
                add_missing(sampler_name, "PARALLEL_API_KEY")
        elif sampler_name in ("tavily_search_basic", "tavily_search_fast"):
            # Listed literally rather than imported from ``samplers``: that
            # module imports this one.
            if not has_secret(settings.tavily_api_key):
                add_missing(sampler_name, "TAVILY_API_KEY")
        elif sampler_name == "firecrawl_search":
            if not has_secret(settings.firecrawl_api_key):
                add_missing(sampler_name, "FIRECRAWL_API_KEY")
        elif sampler_name == "brave_search":
            if not has_secret(settings.brave_search_api_key):
                add_missing(sampler_name, "BRAVE_SEARCH_API_KEY")

    if missing:
        missing_lines = "\n".join(f"- {sampler_name}: {env_name}" for sampler_name, env_name in missing)
        raise RuntimeError(f"Missing credentials for selected samplers:\n{missing_lines}")


def configure_openai_environment(settings: Settings) -> None:
    """Expose .env-loaded OpenAI keys to SDKs that read os.environ directly."""
    if settings.openai_api_key is not None:
        os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key.get_secret_value())
