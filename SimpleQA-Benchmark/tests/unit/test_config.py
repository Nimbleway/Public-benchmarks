import pytest

import nimble_benchmark.config as config
from nimble_benchmark.config import Settings, configure_openai_environment, require_openai_for_benchmark


@pytest.mark.unit
def test_settings_loads_required_keys(monkeypatch):
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    monkeypatch.setenv("NIMBLE_BASE_URL", "http://localhost:8002")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("NIMBLE_SEARCH_DEPTH", raising=False)
    settings = Settings(_env_file=None)

    assert settings.nimble_api_key.get_secret_value() == "test-key"
    assert settings.nimble_base_url == "http://localhost:8002"
    assert settings.openai_api_key is None
    assert settings.nimble_search_depth == "fast"


@pytest.mark.unit
def test_settings_allows_missing_nimble_key_until_sampler_validation(monkeypatch):
    monkeypatch.delenv("NIMBLE_API_KEY", raising=False)

    settings = Settings(_env_file=None)

    assert settings.nimble_api_key is None


@pytest.mark.unit
def test_settings_loads_optional_provider_keys(monkeypatch):
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    monkeypatch.setenv("EXA_API_KEY", "exa-test")
    monkeypatch.setenv("PARALLEL_API_KEY", "parallel-test")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "firecrawl-test")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-test")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-test")
    monkeypatch.setenv("SYNTHESIS_MODEL", "gpt-4o-mini")
    settings = Settings(_env_file=None)

    assert settings.exa_api_key.get_secret_value() == "exa-test"
    assert settings.parallel_api_key.get_secret_value() == "parallel-test"
    assert settings.firecrawl_api_key.get_secret_value() == "firecrawl-test"
    assert settings.brave_search_api_key.get_secret_value() == "brave-test"
    assert settings.tavily_api_key.get_secret_value() == "tavily-test"
    assert settings.synthesis_model == "gpt-4o-mini"


@pytest.mark.unit
def test_settings_drops_removed_lane_secrets(monkeypatch):
    """The removed lanes' credentials are gone. ``extra="ignore"`` means a
    stale ``.env`` still loads -- the field just no longer exists."""
    for stale in ("CLAUDE_API_KEY", "BRAVE_ANSWER_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.setenv(stale, "stale")
    settings = Settings(_env_file=None)

    for removed in (
        "claude_api_key",
        "brave_answer_api_key",
        "nimble_local_api_key",
        "nimble_local_base_url",
        "claude_answer_model",
        "gemini_api_key",
        "gemini_answer_model",
        "you_api_key",
        "openai_answer_model",
    ):
        assert not hasattr(settings, removed), removed


@pytest.mark.unit
def test_require_credentials_for_samplers_names_the_missing_variable(monkeypatch):
    """Each lane gates exactly one vendor credential, and the error names the
    environment variable rather than making the caller guess."""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    settings = Settings(_env_file=None)

    with pytest.raises(RuntimeError) as exc_info:
        config.require_credentials_for_samplers(settings, ["tavily_search_basic", "tavily_search_fast"])

    message = str(exc_info.value)
    assert "tavily_search_basic: TAVILY_API_KEY" in message
    assert "tavily_search_fast: TAVILY_API_KEY" in message


@pytest.mark.unit
def test_require_openai_passes_when_set(monkeypatch):
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test")
    settings = Settings(_env_file=None)

    require_openai_for_benchmark(settings)


@pytest.mark.unit
def test_require_openai_fails_when_missing(monkeypatch):
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(_env_file=None)

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        require_openai_for_benchmark(settings)


@pytest.mark.unit
def test_require_credentials_for_samplers_exa_missing(monkeypatch):
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    settings = Settings(_env_file=None)

    with pytest.raises(RuntimeError, match="EXA_API_KEY"):
        config.require_credentials_for_samplers(settings, ["exa_search_auto"])


@pytest.mark.unit
def test_require_credentials_for_samplers_nimble_search_requires_api_key(monkeypatch):
    monkeypatch.setenv("NIMBLE_API_KEY", "")
    monkeypatch.setenv("NIMBLE_BASE_URL", "http://localhost:8002")
    settings = Settings(_env_file=None)

    with pytest.raises(RuntimeError) as exc_info:
        config.require_credentials_for_samplers(settings, ["nimble_search"])

    message = str(exc_info.value)
    assert "nimble_search" in message
    assert "NIMBLE_API_KEY" in message


@pytest.mark.unit
def test_require_credentials_aggregates_all_missing(monkeypatch):
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    settings = Settings(_env_file=None)

    with pytest.raises(RuntimeError) as exc_info:
        config.require_credentials_for_samplers(
            settings, ["exa_search_fast", "parallel_search_turbo", "firecrawl_search"]
        )

    message = str(exc_info.value)
    assert "exa_search_fast: EXA_API_KEY" in message
    assert "parallel_search_turbo: PARALLEL_API_KEY" in message
    assert "firecrawl_search: FIRECRAWL_API_KEY" in message


@pytest.mark.unit
def test_configure_openai_environment_exports_loaded_secret(monkeypatch):
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test")
    settings = Settings(_env_file=None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    configure_openai_environment(settings)

    assert __import__("os").environ["OPENAI_API_KEY"] == "openai-test"


@pytest.mark.unit
def test_settings_ships_public_lane_url_default():
    """The prod base URL must be available without any env wiring so CI can
    rely on it without setting workflow env vars."""
    settings = Settings(_env_file=None)

    assert settings.nimble_base_url == "https://sdk.nimbleway.com/v2"


@pytest.mark.unit
def test_settings_default_synthesis_model_first_in_choices():
    from nimble_benchmark.config import SYNTHESIS_MODEL_CHOICES

    settings = Settings(_env_file=None)
    assert settings.synthesis_model == SYNTHESIS_MODEL_CHOICES[0]


@pytest.mark.unit
def test_default_synthesis_model_is_pinned_to_gpt_4o():
    """Adding models to the choices list must never move the default.

    Both ``Settings.synthesis_model`` and ``--synthesis-model`` read entry
    ``[0]``, so a new entry inserted at the front would silently re-point every
    default-args run at a different synthesizer -- and every gpt-5.x
    alternative is temperature-clamped, i.e. non-reproducible. Pin the literal.
    """
    from nimble_benchmark.config import SYNTHESIS_MODEL_CHOICES

    assert SYNTHESIS_MODEL_CHOICES[0] == "gpt-4o"


@pytest.mark.unit
@pytest.mark.parametrize(
    "model,supported",
    [
        ("gpt-5", True),
        ("gpt-5-mini", True),
        ("gpt-4o", True),
        ("o1-mini", True),
        ("o3-mini", True),
        ("gpt-5.5", True),
        ("gpt-5.6-sol", True),
        ("claude-haiku-4-5", False),
        ("claude-sonnet-4-5-thinking", False),
        # -pro models are OpenAI models but 404 on /v1/chat/completions
        # ("This is not a chat model"), which is the only endpoint the
        # eval-side synthesizer speaks. Rejecting them here costs one CLI
        # error instead of one failed API call per synthesized row.
        ("gpt-5.5-pro", False),
        ("gpt-5.4-pro", False),
    ],
)
def test_synthesis_model_supports_eval_side_synthesis(model, supported):
    from nimble_benchmark.config import synthesis_model_supports_eval_side_synthesis

    assert synthesis_model_supports_eval_side_synthesis(model) is supported


@pytest.mark.unit
def test_require_credentials_for_samplers_brave_missing(monkeypatch):
    monkeypatch.setenv("NIMBLE_API_KEY", "test-key")
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    settings = Settings(_env_file=None)

    with pytest.raises(RuntimeError, match="BRAVE_SEARCH_API_KEY"):
        config.require_credentials_for_samplers(settings, ["brave_search"])


@pytest.mark.unit
def test_every_synthesis_choice_supports_eval_side_synthesis():
    """The choices tuple must never advertise a model the run can't call.

    Unlike the upstream harness this one has no server-routed answer path, so
    a choice the eval-side synthesizer can't reach (a ``-pro`` model, an
    Anthropic id) would only offer a selection that fails every run at start.
    """
    from nimble_benchmark.config import (
        SYNTHESIS_MODEL_CHOICES,
        synthesis_model_supports_eval_side_synthesis,
    )

    unusable = [m for m in SYNTHESIS_MODEL_CHOICES if not synthesis_model_supports_eval_side_synthesis(m)]
    assert unusable == []
