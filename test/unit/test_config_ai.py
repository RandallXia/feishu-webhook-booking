# test_config_ai.py — Behavior tests for AI config env parsing
#
# conftest.py pins AI_ENABLED=false and AI_PROFILE_FILE="" at module level.
# Each test MUST monkeypatch env vars AND call get_settings.cache_clear()
# before calling get_settings().

from pathlib import Path

import pytest


def test_ai_enabled_defaults_to_false_when_unset(monkeypatch):
    # Given: No AI_ENABLED is set (remove conftest pin)
    monkeypatch.delenv("AI_ENABLED", raising=False)
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.delenv("AI_MODEL", raising=False)
    monkeypatch.delenv("AI_PROFILE_FILE", raising=False)

    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    # Then: ai_enabled is False, and all AI connection fields are None
    assert settings.ai_enabled is False
    assert settings.ai_provider is None
    assert settings.ai_api_key is None
    assert settings.ai_model is None
    assert settings.ai_profile_file is None


def test_ai_enabled_true_with_complete_env(monkeypatch):
    # Given: Full AI environment with openai provider
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_API_KEY", "sk-xxx")
    monkeypatch.setenv("AI_MODEL", "gpt-4o")
    monkeypatch.setenv("AI_BASE_URL", "https://api.openai.com")
    monkeypatch.setenv("AI_PROFILE_FILE", "/etc/ai-profiles.toml")
    monkeypatch.setenv("AI_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("AI_PROFILE_RELOAD_INTERVAL_SECONDS", "15")
    monkeypatch.setenv("AI_DEDUP_TTL_SECONDS", "600")

    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    # Then: All AI fields parse to their expected values
    assert settings.ai_enabled is True
    assert settings.ai_provider == "openai"
    assert settings.ai_api_key == "sk-xxx"
    assert settings.ai_model == "gpt-4o"
    assert settings.ai_base_url == "https://api.openai.com"
    assert settings.ai_profile_file == Path("/etc/ai-profiles.toml")
    assert settings.ai_timeout_seconds == 30
    assert settings.ai_profile_reload_interval_seconds == 15
    assert settings.ai_dedup_ttl_seconds == 600


def test_ai_enabled_true_missing_provider_raises(monkeypatch):
    # Given: AI_ENABLED=true, but AI_PROVIDER is not set
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.setenv("AI_API_KEY", "sk-xxx")
    monkeypatch.setenv("AI_MODEL", "gpt-4o")
    monkeypatch.setenv("AI_PROFILE_FILE", "/tmp/test.toml")

    from app.config import get_settings

    get_settings.cache_clear()

    # When / Then: RuntimeError is raised
    with pytest.raises(RuntimeError, match="AI_PROVIDER"):
        get_settings()


def test_ai_invalid_provider_value_raises(monkeypatch):
    # Given: AI_ENABLED=true, AI_PROVIDER=openai2 (not a valid value)
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_PROVIDER", "openai2")
    monkeypatch.setenv("AI_API_KEY", "sk-xxx")
    monkeypatch.setenv("AI_MODEL", "gpt-4o")
    monkeypatch.setenv("AI_PROFILE_FILE", "/tmp/test.toml")

    from app.config import get_settings

    get_settings.cache_clear()

    # When / Then: RuntimeError with message indicating valid providers
    with pytest.raises(RuntimeError, match="anthropic.*openai"):
        get_settings()


def test_ai_timeout_seconds_defaults_to_20(monkeypatch):
    # Given: AI_ENABLED=true, but AI_TIMEOUT_SECONDS is not set
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("AI_API_KEY", "sk-test")
    monkeypatch.setenv("AI_MODEL", "claude-3")
    monkeypatch.setenv("AI_PROFILE_FILE", "/tmp/test.toml")
    monkeypatch.delenv("AI_TIMEOUT_SECONDS", raising=False)

    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    # Then: ai_timeout_seconds is 20
    assert settings.ai_timeout_seconds == 20


def test_ai_enabled_true_missing_profile_file_raises(monkeypatch):
    # Given: AI_ENABLED=true, but AI_PROFILE_FILE is not set
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("AI_API_KEY", "sk-test")
    monkeypatch.setenv("AI_MODEL", "claude-3")
    monkeypatch.delenv("AI_PROFILE_FILE", raising=False)

    from app.config import get_settings

    get_settings.cache_clear()

    # When / Then: RuntimeError with message indicating missing AI_PROFILE_FILE
    with pytest.raises(RuntimeError, match="AI_PROFILE_FILE"):
        get_settings()


def test_ai_enabled_boolean_parsing_variants(monkeypatch):
    from app.config import get_settings

    # Given: AI_ENABLED=1
    monkeypatch.setenv("AI_ENABLED", "1")
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_API_KEY", "sk-x")
    monkeypatch.setenv("AI_MODEL", "gpt-4")
    monkeypatch.setenv("AI_PROFILE_FILE", "/tmp/test.toml")
    get_settings.cache_clear()
    # Then: ai_enabled is True
    assert get_settings().ai_enabled is True

    # Given: AI_ENABLED=true
    monkeypatch.setenv("AI_ENABLED", "true")
    get_settings.cache_clear()
    # Then: ai_enabled is True
    assert get_settings().ai_enabled is True

    # Given: AI_ENABLED=TRUE (case insensitive)
    monkeypatch.setenv("AI_ENABLED", "TRUE")
    get_settings.cache_clear()
    # Then: ai_enabled is True
    assert get_settings().ai_enabled is True

    # Given: AI_ENABLED=no
    monkeypatch.setenv("AI_ENABLED", "no")
    get_settings.cache_clear()
    # Then: ai_enabled is False
    assert get_settings().ai_enabled is False


def test_ai_enabled_false_ignores_missing_ai_env(monkeypatch):
    # Given: AI_ENABLED=false (conftest default), no other AI env vars set
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.delenv("AI_MODEL", raising=False)
    # AI_PROFILE_FILE is set to "" by conftest, which is fine when disabled

    from app.config import get_settings

    get_settings.cache_clear()

    # When / Then: No RuntimeError; ai_enabled is False; fields are None/defaults
    settings = get_settings()
    assert settings.ai_enabled is False
    assert settings.ai_provider is None
    assert settings.ai_api_key is None
    assert settings.ai_model is None
    assert settings.ai_base_url is None
    assert settings.ai_profile_file is None
    assert settings.ai_timeout_seconds == 20
    assert settings.ai_profile_reload_interval_seconds == 10
    assert settings.ai_dedup_ttl_seconds == 300


def test_ai_dedup_ttl_seconds_defaults_to_300(monkeypatch):
    # Given: AI_ENABLED=true, but AI_DEDUP_TTL_SECONDS is not set
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("AI_API_KEY", "sk-test")
    monkeypatch.setenv("AI_MODEL", "claude-3")
    monkeypatch.setenv("AI_PROFILE_FILE", "/tmp/test.toml")
    monkeypatch.delenv("AI_DEDUP_TTL_SECONDS", raising=False)

    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    # Then: ai_dedup_ttl_seconds is 300
    assert settings.ai_dedup_ttl_seconds == 300


def test_ai_profile_reload_interval_seconds_defaults_to_10(monkeypatch):
    # Given: AI_ENABLED=true, but AI_PROFILE_RELOAD_INTERVAL_SECONDS is not set
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("AI_API_KEY", "sk-test")
    monkeypatch.setenv("AI_MODEL", "claude-3")
    monkeypatch.setenv("AI_PROFILE_FILE", "/tmp/test.toml")
    monkeypatch.delenv("AI_PROFILE_RELOAD_INTERVAL_SECONDS", raising=False)

    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    # Then: ai_profile_reload_interval_seconds is 10
    assert settings.ai_profile_reload_interval_seconds == 10


def test_bool_env_helper_truthy_values(monkeypatch):
    from app.config import _bool_env

    # Given: env var set to truthy values
    for val in ("1", "true", "yes", "on"):
        monkeypatch.setenv("_TEST_BOOL", val)
        # When: _bool_env is called
        # Then: Returns True
        assert _bool_env("_TEST_BOOL") is True

    # Given: env var set to truthy values in uppercase
    for val in ("TRUE", "YES", "ON"):
        monkeypatch.setenv("_TEST_BOOL", val)
        # When: _bool_env is called
        # Then: Returns True (case insensitive)
        assert _bool_env("_TEST_BOOL") is True

    # Given: env var set to falsy values
    for val in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("_TEST_BOOL", val)
        # When: _bool_env is called
        # Then: Returns False
        assert _bool_env("_TEST_BOOL") is False

    # Given: env var not set
    monkeypatch.delenv("_TEST_BOOL", raising=False)
    # When: _bool_env is called with default=False
    # Then: Returns the default (False)
    assert _bool_env("_TEST_BOOL") is False
    # When: _bool_env is called with default=True
    # Then: Returns True
    assert _bool_env("_TEST_BOOL", default=True) is True