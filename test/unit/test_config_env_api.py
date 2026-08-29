# test_config_env_api.py — Behavior tests for GET/PUT /admin/config/env.
#
# Two routes (mirror reload_config auth + _require_admin_auth):
#   - GET /admin/config/env  : read AI connection settings (NEVER returns secret values)
#   - PUT /admin/config/env  : line-based env file edit (atomic save)
#
# PUT is line-based: reads the existing file, replaces/adds/deletes AI_* lines,
# preserves all other lines (comments + FEISHU_* + WEBHOOK_*) byte-for-byte.
# env_file_path None (env vars set directly, no file) → 409 ENV_FILE_NOT_FOUND.
#
# Strategy mirrors test_config_profile_api.py:
#   - conftest pins FEISHU_ENV_FILE="" (so _detect_env_file_path probes .env,
#     which doesn't exist in the workspace → None). Tests that need a file
#     monkeypatch settings.env_file_path to a tmp file.
#   - httpx.AsyncClient(transport=ASGITransport(app), base_url="http://testserver")
#     with the lifespan async context manager run directly.

from __future__ import annotations

import errno
import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from httpx import ASGITransport

from app.config import PROJECT_ROOT, get_settings
from app.main import app, lifespan


ADMIN_TOKEN = "test-admin-token"
TEST_BASE_URL = "http://testserver"

# A realistic env file with comments, FEISHU_* lines, and partial AI_* lines.
_ENV_FILE_SEED = """\
# Feishu webhook service env
# Edit AI_* below to configure the extraction pipeline

WEBHOOK_SHARED_TOKEN=hook-secret
FEISHU_APP_ID=cli_test
FEISHU_APP_SECRET=fs-secret

# AI section
AI_ENABLED=false
AI_PROVIDER=anthropic
AI_API_KEY=sk-old-key
AI_MODEL=claude-3-5-sonnet
"""


def _enabled_ai_settings(env_file: Path | None) -> object:
    return replace(
        get_settings(),
        config_reload_token=ADMIN_TOKEN,
        ai_enabled=True,
        ai_provider="anthropic",
        ai_api_key="sk-loaded-from-env",
        ai_model="claude-3-5-sonnet",
        ai_base_url=None,
        ai_timeout_seconds=20,
        env_file_path=env_file,
    )


def _disabled_ai_settings(env_file: Path | None) -> object:
    return replace(
        get_settings(),
        config_reload_token=ADMIN_TOKEN,
        env_file_path=env_file,
    )


# ─── config.py: _detect_env_file_path unit tests ──────────────────────────
#
# conftest pins FEISHU_ENV_FILE="" so _path_env returns None; the probe then
# falls back to PROJECT_ROOT/.env (absent in workspace → None). Tests use
# monkeypatch to control both inputs.


def test_detect_env_file_path_feishu_env_file_set_and_exists(tmp_path, monkeypatch):
    """
    GIVEN FEISHU_ENV_FILE points to an existing tmp file
    WHEN get_settings() is called
    THEN settings.env_file_path == that tmp file
    """
    env_file = tmp_path / "custom.env"
    env_file.write_text("AI_ENABLED=false\n", encoding="utf-8")
    monkeypatch.setenv("FEISHU_ENV_FILE", str(env_file))
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.env_file_path == env_file


def test_detect_env_file_path_feishu_env_file_unset_but_dotenv_exists(tmp_path, monkeypatch):
    """
    GIVEN FEISHU_ENV_FILE is empty AND PROJECT_ROOT/.env exists
    WHEN get_settings() is called
    THEN settings.env_file_path == PROJECT_ROOT/.env
    """
    dotenv = tmp_path / ".env"
    dotenv.write_text("AI_ENABLED=false\n", encoding="utf-8")
    monkeypatch.setenv("FEISHU_ENV_FILE", "")
    monkeypatch.setattr("app.config.PROJECT_ROOT", tmp_path)
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.env_file_path == dotenv


def test_detect_env_file_path_neither_exists_returns_none(monkeypatch):
    """
    GIVEN FEISHU_ENV_FILE is empty AND PROJECT_ROOT has no .env
    WHEN get_settings() is called
    THEN settings.env_file_path is None
    """
    empty_root = Path("/tmp/nonexistent-project-root-for-test")
    monkeypatch.setenv("FEISHU_ENV_FILE", "")
    monkeypatch.setattr("app.config.PROJECT_ROOT", empty_root)
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.env_file_path is None


def test_detect_env_file_path_feishu_env_file_set_but_missing_falls_back_to_dotenv(tmp_path, monkeypatch):
    """
    GIVEN FEISHU_ENV_FILE points to a NON-existent path AND PROJECT_ROOT/.env exists
    WHEN get_settings() is called
    THEN settings.env_file_path == PROJECT_ROOT/.env (fallback)
    """
    dotenv = tmp_path / ".env"
    dotenv.write_text("AI_ENABLED=false\n", encoding="utf-8")
    monkeypatch.setenv("FEISHU_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setattr("app.config.PROJECT_ROOT", tmp_path)
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.env_file_path == dotenv


# ─── GET /admin/config/env ─────────────────────────────────────────────────


async def test_get_env_ai_enabled_happy_no_secret_leak(tmp_path):
    """
    GIVEN ai_enabled=True with a loaded AI_API_KEY="sk-loaded-from-env"
       AND env_file_path points to a tmp file
    WHEN GET /admin/config/env is called with valid X-Admin-Token
    THEN the response status is 200
      AND body.env_file is the tmp path string
      AND body.ai.ai_api_key_set is True (boolean, NOT the key value)
      AND body.ai has ai_enabled/ai_provider/ai_base_url/ai_model/ai_timeout_seconds
      AND body.other has *_set booleans for the 3 secrets
      AND body.restart_required is True
      AND the raw response text does NOT contain "sk-loaded-from-env"
    """
    env_file = tmp_path / "feishu-webhook.env"
    env_file.write_text(_ENV_FILE_SEED, encoding="utf-8")

    async with lifespan(app):
        app.state.settings = _enabled_ai_settings(env_file)

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get(
                "/admin/config/env",
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["env_file"] == str(env_file)
    assert body["restart_required"] is True

    ai = body["ai"]
    assert ai["ai_enabled"] is True
    assert ai["ai_provider"] == "anthropic"
    assert ai["ai_model"] == "claude-3-5-sonnet"
    assert ai["ai_base_url"] is None
    assert ai["ai_timeout_seconds"] == 20
    assert ai["ai_api_key_set"] is True

    other = body["other"]
    assert set(other.keys()) == {
        "feishu_app_id", "feishu_app_secret_set",
        "webhook_shared_token_set", "config_reload_token_set",
    }
    assert other["feishu_app_id"] == "test-app-id"
    assert other["feishu_app_secret_set"] is True
    assert other["webhook_shared_token_set"] is True

    # CRITICAL: no secret value ever leaks in the response body.
    assert "sk-loaded-from-env" not in response.text
    assert "fs-secret" not in response.text
    assert "hook-secret" not in response.text


async def test_get_env_ai_disabled_shows_unset_fields(tmp_path):
    """
    GIVEN ai_enabled=False (conftest default) + env_file_path set
    WHEN GET /admin/config/env is called
    THEN body.ai.ai_enabled is False
      AND body.ai.ai_provider is null + ai_api_key_set is False
    """
    env_file = tmp_path / "feishu-webhook.env"
    env_file.write_text(_ENV_FILE_SEED, encoding="utf-8")

    async with lifespan(app):
        app.state.settings = _disabled_ai_settings(env_file)

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get(
                "/admin/config/env",
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["ai"]["ai_enabled"] is False
    assert body["ai"]["ai_provider"] is None
    assert body["ai"]["ai_api_key_set"] is False


async def test_get_env_no_env_file_returns_null_path():
    """
    GIVEN env_file_path is None (env vars set directly, no file)
    WHEN GET /admin/config/env is called
    THEN body.env_file is null (GET still 200; PUT will 409)
    """
    async with lifespan(app):
        app.state.settings = _disabled_ai_settings(None)

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get(
                "/admin/config/env",
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    assert response.json()["env_file"] is None


async def test_get_env_no_token_returns_401():
    """
    GIVEN CONFIG_RELOAD_TOKEN is set
    WHEN GET /admin/config/env is called with NO X-Admin-Token
    THEN the response status is 401 UNAUTHORIZED
    """
    async with lifespan(app):
        app.state.settings = _disabled_ai_settings(None)

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get("/admin/config/env")

    assert response.status_code == 401
    assert response.json()["detail"]["error"]["code"] == "UNAUTHORIZED"


async def test_get_env_reload_disabled_returns_404():
    """
    GIVEN CONFIG_RELOAD_TOKEN is unset
    WHEN GET /admin/config/env is called
    THEN the response status is 404 RELOAD_DISABLED
    """
    async with lifespan(app):
        app.state.settings = replace(get_settings(), config_reload_token=None)

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get(
                "/admin/config/env",
                headers={"X-Admin-Token": "anything"},
            )

    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "RELOAD_DISABLED"


# ─── PUT /admin/config/env ─────────────────────────────────────────────────


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    path = tmp_path / "feishu-webhook.env"
    path.write_text(_ENV_FILE_SEED, encoding="utf-8")
    return path


async def test_put_env_happy_modify_add_delete_preserves_others(env_file):
    """
    GIVEN an env file with comments + FEISHU_* + partial AI_* lines
    WHEN PUT /admin/config/env is called with:
      - ai_provider="openai" (modify existing AI_PROVIDER line)
      - ai_base_url="https://relay.example.com/v1" (add — line absent)
      - ai_model="gpt-4o" (modify existing AI_MODEL line)
      - ai_timeout_seconds=30 (add — line absent)
      - ai_enabled=True (modify existing)
      - api_key="sk-new-key" (modify existing AI_API_KEY)
    THEN the response status is 200 + success=True + restart_required=True
      AND a .bak file was created with the ORIGINAL content
      AND comment lines + WEBHOOK_* + FEISHU_* lines are byte-identical
      AND AI_PROVIDER/AI_MODEL/AI_API_KEY/AI_ENABLED lines were replaced
      AND AI_BASE_URL/AI_TIMEOUT_SECONDS lines were appended
    """
    original_content = env_file.read_text(encoding="utf-8")
    original_lines = original_content.splitlines()

    body = {
        "ai": {
            "ai_enabled": True,
            "ai_provider": "openai",
            "ai_base_url": "https://relay.example.com/v1",
            "ai_model": "gpt-4o",
            "ai_timeout_seconds": 30,
        },
        "api_key": "sk-new-key",
    }

    async with lifespan(app):
        app.state.settings = _enabled_ai_settings(env_file)

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/env",
                json=body,
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["success"] is True
    assert result["restart_required"] is True

    # .bak holds the original
    bak_path = env_file.with_suffix(".env.bak")
    assert bak_path.is_file()
    assert bak_path.read_text(encoding="utf-8") == original_content

    new_content = env_file.read_text(encoding="utf-8")
    new_lines = new_content.splitlines()

    # Comments + WEBHOOK_* + FEISHU_* preserved byte-identical (in order).
    preserved_markers = [
        "# Feishu webhook service env",
        "# Edit AI_* below to configure the extraction pipeline",
        "WEBHOOK_SHARED_TOKEN=hook-secret",
        "FEISHU_APP_ID=cli_test",
        "FEISHU_APP_SECRET=fs-secret",
        "# AI section",
    ]
    for marker in preserved_markers:
        assert marker in new_lines, f"preserved line missing: {marker}"

    # AI lines updated.
    assert "AI_ENABLED=true" in new_lines
    assert "AI_PROVIDER=openai" in new_lines
    assert "AI_MODEL=gpt-4o" in new_lines
    assert "AI_API_KEY=sk-new-key" in new_lines
    # New AI lines appended.
    assert "AI_BASE_URL=https://relay.example.com/v1" in new_lines
    assert "AI_TIMEOUT_SECONDS=30" in new_lines

    # Old values gone.
    assert "AI_PROVIDER=anthropic" not in new_lines
    assert "AI_API_KEY=sk-old-key" not in new_lines
    assert "AI_MODEL=claude-3-5-sonnet" not in new_lines


async def test_put_env_null_provider_deletes_line(env_file):
    """
    GIVEN an env file with an AI_PROVIDER line
    WHEN PUT /admin/config/env is called with ai_provider=null
    THEN the response is 200
      AND the AI_PROVIDER line is DELETED from the file
      AND other AI_* lines are unchanged
    """
    body = {
        "ai": {
            "ai_enabled": False,
            "ai_provider": None,  # delete
            "ai_base_url": None,
            "ai_model": "claude-3-5-sonnet",
            "ai_timeout_seconds": 20,
        },
        "api_key": None,  # no change to AI_API_KEY
    }

    async with lifespan(app):
        app.state.settings = _enabled_ai_settings(env_file)

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/env",
                json=body,
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    new_lines = env_file.read_text(encoding="utf-8").splitlines()
    assert not any(ln.startswith("AI_PROVIDER=") for ln in new_lines)
    # AI_API_KEY untouched (api_key=null → skip)
    assert "AI_API_KEY=sk-old-key" in new_lines
    # AI_MODEL untouched
    assert "AI_MODEL=claude-3-5-sonnet" in new_lines


async def test_put_env_empty_api_key_leaves_line_unchanged(env_file):
    """
    GIVEN an env file with AI_API_KEY=sk-old-key
    WHEN PUT is called with api_key="" (empty string)
    THEN the response is 200
      AND the AI_API_KEY line is UNCHANGED (still sk-old-key)
    """
    body = {
        "ai": {
            "ai_enabled": False,
            "ai_provider": "anthropic",
            "ai_base_url": None,
            "ai_model": "claude-3-5-sonnet",
            "ai_timeout_seconds": 20,
        },
        "api_key": "",
    }

    async with lifespan(app):
        app.state.settings = _enabled_ai_settings(env_file)

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/env",
                json=body,
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    new_lines = env_file.read_text(encoding="utf-8").splitlines()
    assert "AI_API_KEY=sk-old-key" in new_lines


async def test_put_env_special_char_values_round_trip(env_file):
    """
    GIVEN an env file
    WHEN PUT is called with values containing #, =, and spaces (realistic for
       base URLs with query fragments and api keys with embedded equals)
    THEN the response is 200
      AND re-reading the written file with config.py's quote-strip semantics
          recovers the EXACT original values (round-trip lock)

    Note: config.py:36's loader strips ONE layer of matching surrounding quotes
    but does NOT unescape internal \" — so values containing literal " or \\
    are inherently un-representable in this loader format. The realistic
    special chars for AI config (URL fragments, query params, api keys with
    =) DO round-trip correctly via quote-wrapping.
    """
    special_base_url = "https://x.example.com/v1?token=abc#frag me"
    special_model = "gpt-4o-mini-2024-07-18"
    body = {
        "ai": {
            "ai_enabled": True,
            "ai_provider": "openai",
            "ai_base_url": special_base_url,
            "ai_model": special_model,
            "ai_timeout_seconds": 20,
        },
        "api_key": "sk-key=with=equals",
    }

    async with lifespan(app):
        app.state.settings = _enabled_ai_settings(env_file)

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/env",
                json=body,
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200

    written = env_file.read_text(encoding="utf-8")
    parsed: dict[str, str] = {}
    for raw_line in written.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        parsed[key] = value

    assert parsed["AI_BASE_URL"] == special_base_url
    assert parsed["AI_MODEL"] == special_model
    assert parsed["AI_API_KEY"] == "sk-key=with=equals"


async def test_put_env_no_env_file_returns_409():
    """
    GIVEN env_file_path is None (env vars set directly)
    WHEN PUT /admin/config/env is called
    THEN the response status is 409 ENV_FILE_NOT_FOUND
      AND the message mentions FEISHU_ENV_FILE or .env
    """
    async with lifespan(app):
        app.state.settings = _enabled_ai_settings(None)

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/env",
                json={
                    "ai": {
                        "ai_enabled": True, "ai_provider": "openai",
                        "ai_base_url": None, "ai_model": "gpt-4o",
                        "ai_timeout_seconds": 20,
                    },
                    "api_key": "sk-x",
                },
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 409
    body = response.json()["detail"]
    assert body["error"]["code"] == "ENV_FILE_NOT_FOUND"
    assert "FEISHU_ENV_FILE" in body["error"]["message"] or ".env" in body["error"]["message"]


async def test_put_env_readonly_filesystem_returns_409(monkeypatch, env_file):
    """
    GIVEN a valid PUT body BUT os.replace raises OSError(EROFS)
    WHEN PUT /admin/config/env is called
    THEN the response status is 409 RUNTIME_READONLY
      AND response.suggested_action == "host-edit"
      AND the env file is UNCHANGED
    """
    from app import main as main_mod

    def _replace_raises(src, dst):
        raise OSError(errno.EROFS, "Read-only file system", str(dst))

    monkeypatch.setattr(main_mod.os, "replace", _replace_raises)

    original_content = env_file.read_text(encoding="utf-8")

    async with lifespan(app):
        app.state.settings = _enabled_ai_settings(env_file)

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/env",
                json={
                    "ai": {
                        "ai_enabled": True, "ai_provider": "openai",
                        "ai_base_url": None, "ai_model": "gpt-4o",
                        "ai_timeout_seconds": 20,
                    },
                    "api_key": "sk-x",
                },
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 409
    body = response.json()["detail"]
    assert body["error"]["code"] == "RUNTIME_READONLY"
    assert body["error"]["suggested_action"] == "host-edit"
    assert env_file.read_text(encoding="utf-8") == original_content


async def test_put_env_no_token_returns_401(env_file):
    """
    GIVEN CONFIG_RELOAD_TOKEN is set + env_file_path set
    WHEN PUT /admin/config/env is called with NO X-Admin-Token
    THEN the response status is 401 UNAUTHORIZED
    """
    async with lifespan(app):
        app.state.settings = _enabled_ai_settings(env_file)

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/env",
                json={
                    "ai": {
                        "ai_enabled": True, "ai_provider": "openai",
                        "ai_base_url": None, "ai_model": "gpt-4o",
                        "ai_timeout_seconds": 20,
                    },
                    "api_key": "sk-x",
                },
            )

    assert response.status_code == 401
    assert response.json()["detail"]["error"]["code"] == "UNAUTHORIZED"


async def test_put_env_reload_disabled_returns_404(env_file):
    """
    GIVEN CONFIG_RELOAD_TOKEN is unset
    WHEN PUT /admin/config/env is called
    THEN the response status is 404 RELOAD_DISABLED
    """
    async with lifespan(app):
        app.state.settings = replace(
            get_settings(),
            config_reload_token=None,
            env_file_path=env_file,
        )

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/env",
                json={
                    "ai": {
                        "ai_enabled": True, "ai_provider": "openai",
                        "ai_base_url": None, "ai_model": "gpt-4o",
                        "ai_timeout_seconds": 20,
                    },
                    "api_key": "sk-x",
                },
                headers={"X-Admin-Token": "anything"},
            )

    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "RELOAD_DISABLED"


async def test_put_env_extra_field_rejected_returns_422(env_file):
    """
    GIVEN a PUT body with an extra forbidden field
    WHEN PUT /admin/config/env is called
    THEN the response status is 422 (pydantic extra="forbid")
    """
    async with lifespan(app):
        app.state.settings = _enabled_ai_settings(env_file)

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/env",
                json={
                    "ai": {
                        "ai_enabled": True, "ai_provider": "openai",
                        "ai_base_url": None, "ai_model": "gpt-4o",
                        "ai_timeout_seconds": 20,
                    },
                    "api_key": "sk-x",
                    "unexpected_field": "boom",
                },
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 422
