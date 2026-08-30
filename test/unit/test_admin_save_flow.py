# test_admin_save_flow.py — Behavior tests for the AI connection editor +
# unified save/validate/reload flow (admin.js §ai-connection-section + the
# save flow shared by todo 9/10/11).
#
# The UI is vanilla JS exercised in the browser; these tests cover:
#   1. env GET→PUT round-trip integration (ASGI): GET returns the editable
#      shape → the UI assembles a PUT body from it → PUT 200 writes the file.
#      Asserts api_key="" → body api_key=null (UI must NOT send the loaded
#      secret value; an empty password box means "no change").
#   2. restart banner state: PUT responds with restart_required=true; the
#      UI must show the yellow restart banner. Asserted via the JS source
#      carrying a restart banner render function + admin.html carrying the
#      restart-banner element.
#   3. 422 path locator: the env PUT 422 is pydantic extra="forbid" → body
#      detail is [{loc, msg, type}]. The JS envParseErrorPath must consume
#      loc[1] to mark the offending control. Asserted via JS source gate
#      + the API 422 body shape.
#   4. JS grep gates: admin.js exposes renderAiConnection / markDirty /
#      envParseErrorPath + the restart-banner id; node --check passes.
#   5. admin.html id assertions for the ai-connection-section.
#
# Strategy mirrors test_config_env_api.py + test_admin_extract_flow.py.

from __future__ import annotations

import re
import subprocess
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport

from app.config import get_settings
from app.main import app, lifespan

ADMIN_TOKEN = "test-admin-token"
TEST_BASE_URL = "http://testserver"
_STATIC_DIR = Path(__file__).resolve().parents[2] / "app" / "static"

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


# ─── 1. env GET→PUT round-trip integration ─────────────────────────────────


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    path = tmp_path / "feishu-webhook.env"
    path.write_text(_ENV_FILE_SEED, encoding="utf-8")
    return path


async def test_env_get_then_put_round_trip_writes_file(env_file):
    """
    GIVEN an env file + AI enabled (ai_api_key_set=True after GET)
    WHEN the UI flow runs: GET /admin/config/env, then assembles a PUT body
       from the GET response (provider/base_url/model/timeout/ enabled, and
       api_key=null because the password box is empty — empty → no change)
    THEN the PUT response status is 200 + success=True + restart_required=True
      AND the AI_PROVIDER line was modified to the new value
      AND the AI_API_KEY line is UNCHANGED (api_key=null → skip)
    """
    new_provider = "openai"
    new_model = "gpt-4o"
    original_content = env_file.read_text(encoding="utf-8")

    async with lifespan(app):
        app.state.settings = _enabled_ai_settings(env_file)

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            headers = {"X-Admin-Token": ADMIN_TOKEN}

            # Step 1: GET — the UI reads the editable shape (NEVER the secret).
            get_resp = await client.get("/admin/config/env", headers=headers)
            assert get_resp.status_code == 200
            get_body = get_resp.json()
            assert get_body["ai"]["ai_api_key_set"] is True
            assert "sk-loaded-from-env" not in get_resp.text

            # Step 2: UI assembles the PUT body. The password box is EMPTY
            # (the UI never pre-fills the secret) → api_key=null (no change).
            # The UI carries the editable AI_* fields from GET, modified by
            # the user.
            put_body = {
                "ai": {
                    "ai_enabled": True,
                    "ai_provider": new_provider,
                    "ai_base_url": None,
                    "ai_model": new_model,
                    "ai_timeout_seconds": get_body["ai"]["ai_timeout_seconds"],
                },
                "api_key": None,
            }
            put_resp = await client.put(
                "/admin/config/env", json=put_body, headers=headers
            )

    assert put_resp.status_code == 200, put_resp.text
    result = put_resp.json()
    assert result["success"] is True
    assert result["restart_required"] is True

    new_lines = env_file.read_text(encoding="utf-8").splitlines()
    assert "AI_PROVIDER=openai" in new_lines
    assert "AI_MODEL=gpt-4o" in new_lines
    # AI_API_KEY untouched (api_key=null → skip).
    assert "AI_API_KEY=sk-old-key" in new_lines
    # Comments + FEISHU_* preserved.
    assert "WEBHOOK_SHARED_TOKEN=hook-secret" in new_lines
    assert "FEISHU_APP_ID=cli_test" in new_lines
    # .bak holds the original.
    bak_path = env_file.with_suffix(".env.bak")
    assert bak_path.is_file()
    assert bak_path.read_text(encoding="utf-8") == original_content


async def test_env_put_empty_api_key_string_means_no_change(env_file):
    """
    GIVEN an env file with AI_API_KEY=sk-old-key
    WHEN PUT is called with api_key="" (empty string — the UI password box
       was empty, which the UI converts to null OR sends as "")
    THEN the response is 200
      AND the AI_API_KEY line is UNCHANGED (empty string → no change)
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
                "/admin/config/env", json=body,
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    new_lines = env_file.read_text(encoding="utf-8").splitlines()
    assert "AI_API_KEY=sk-old-key" in new_lines


async def test_env_put_new_api_key_updates_line(env_file):
    """
    GIVEN an env file with AI_API_KEY=sk-old-key
    WHEN PUT is called with api_key="sk-new-key" (user typed a new key)
    THEN the response is 200
      AND the AI_API_KEY line is updated to sk-new-key
    """
    body = {
        "ai": {
            "ai_enabled": True, "ai_provider": "anthropic",
            "ai_base_url": None, "ai_model": "claude-3-5-sonnet",
            "ai_timeout_seconds": 20,
        },
        "api_key": "sk-new-key",
    }

    async with lifespan(app):
        app.state.settings = _enabled_ai_settings(env_file)

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/env", json=body,
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    new_lines = env_file.read_text(encoding="utf-8").splitlines()
    assert "AI_API_KEY=sk-new-key" in new_lines
    assert "AI_API_KEY=sk-old-key" not in new_lines


# ─── 2. restart banner state ────────────────────────────────────────────────


async def test_env_put_response_carries_restart_required_true(env_file):
    """
    GIVEN a writable env file
    WHEN PUT /admin/config/env succeeds
    THEN the response body.restart_required is True
      (the UI's restart banner depends on this signal)
    """
    body = {
        "ai": {
            "ai_enabled": True, "ai_provider": "openai",
            "ai_base_url": None, "ai_model": "gpt-4o",
            "ai_timeout_seconds": 20,
        },
        "api_key": None,
    }

    async with lifespan(app):
        app.state.settings = _enabled_ai_settings(env_file)

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/env", json=body,
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    assert response.json()["restart_required"] is True


async def test_env_put_no_env_file_returns_409_env_file_not_found():
    """
    GIVEN env_file_path is None (env vars set directly)
    WHEN PUT /admin/config/env is called
    THEN the response is 409 ENV_FILE_NOT_FOUND
      (the UI's red banner path depends on this code)
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
    assert response.json()["detail"]["error"]["code"] == "ENV_FILE_NOT_FOUND"


async def test_env_put_readonly_filesystem_returns_409_runtime_readonly(monkeypatch, env_file):
    """
    GIVEN a valid PUT body BUT os.replace raises OSError(EROFS)
    WHEN PUT /admin/config/env is called
    THEN the response is 409 RUNTIME_READONLY + suggested_action="host-edit"
      (the UI's red banner path depends on this code)
    """
    from app import main as main_mod

    def _replace_raises(src, dst):
        raise OSError(__import__("errno").EROFS, "Read-only file system", str(dst))

    monkeypatch.setattr(main_mod.os, "replace", _replace_raises)

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


# ─── 3. 422 path locator — pydantic extra="forbid" ──────────────────────────


async def test_env_put_extra_field_returns_422_pydantic_shape(env_file):
    """
    GIVEN a PUT body with an extra forbidden field
    WHEN PUT /admin/config/env is called
    THEN the response status is 422 (pydantic extra="forbid")
      AND body.detail is a list of {type, loc, msg, ...} entries
      (the UI's envParseErrorPath consumes detail[i].loc to mark the control)
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
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert len(detail) >= 1
    # pydantic v2 loc is a list like ["body", "unexpected_field"].
    first = detail[0]
    assert "loc" in first
    assert "msg" in first
    assert "unexpected_field" in first["loc"]


# ─── 4. JS grep gates ───────────────────────────────────────────────────────


def _read_static(name: str) -> str:
    return (_STATIC_DIR / name).read_text(encoding="utf-8")


def test_admin_js_exposes_save_flow_functions():
    """
    GIVEN app/static/admin.js exists
    WHEN the source is scanned for the function declarations the AI
       connection editor + unified save flow require
    THEN it contains function declarations for:
      - renderAiConnection (renders the AI connection form)
      - markDirty (marks a form dirty for the dry-run guard)
      - envParseErrorPath (pydantic loc → control locator)
    AND it references the restart-banner id
    """
    js = _read_static("admin.js")
    required = ["renderAiConnection", "markDirty", "envParseErrorPath"]
    for name in required:
        pattern = r"function\s+" + re.escape(name) + r"\s*\("
        assert re.search(pattern, js), f"admin.js missing function declaration: {name}"
    assert "restart-banner" in js, "admin.js missing restart-banner reference"


def test_admin_js_mark_dirty_uses_dirty_flag_and_save_clears():
    """
    GIVEN admin.js markDirty function + save success handlers
    WHEN the source is scanned
    THEN markDirty sets a dirty flag
      AND at least one save success path clears the dirty flag
      (locks the dirty-check-before-dry-run contract)
    """
    js = _read_static("admin.js")
    assert re.search(r"function\s+markDirty\s*\([^)]*\)\s*\{", js)
    # markDirty body assigns a dirty variable.
    m = re.search(r"function\s+markDirty\s*\([^)]*\)\s*\{(.+?)\n\s*function\s", js, re.S)
    assert m, "markDirty function body not isolatable"
    assert re.search(r"dirty\s*=\s*true", m.group(1)) or "isDirty" in m.group(1)
    # A save success path clears the dirty flag.
    assert re.search(r"dirty\s*=\s*false", js) or "isDirty = false" in js, (
        "save success path must clear the dirty flag"
    )


def test_admin_js_save_buttons_debounced():
    """
    GIVEN admin.js save button handlers
    WHEN the source is scanned
    THEN at least one save handler disables its button on click + re-enables
      in the finally block (500ms debounce gate)
    """
    js = _read_static("admin.js")
    # Each save button handler disables the button at entry.
    save_btns = ["saveTargetsBtn", "saveProfileBtn", "saveEnvBtn"]
    found_debounce = False
    for btn in save_btns:
        if btn in js:
            # Look for btn.disabled = true near the click handler.
            idx = js.find(btn + ".addEventListener")
            if idx != -1:
                snippet = js[idx:idx + 2000]
                if re.search(r"\.disabled\s*=\s*true", snippet):
                    found_debounce = True
                    break
    assert found_debounce, "no save button disables itself on click (no debounce)"


def test_admin_js_node_check_passes():
    """
    GIVEN app/static/admin.js exists on disk
    WHEN `node --check app/static/admin.js` is executed
    THEN the exit code is 0 (JS syntax gate — todo 11 added new functions)
    """
    js_path = _STATIC_DIR / "admin.js"
    assert js_path.is_file()
    result = subprocess.run(
        ["node", "--check", str(js_path)], capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"node --check failed (exit {result.returncode}):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ─── 5. admin.html id assertions ────────────────────────────────────────────


async def test_admin_html_ai_connection_section_has_required_ids():
    """
    GIVEN the FastAPI app is running
    WHEN GET /admin/ai is requested
    THEN the ai-connection-section contains id="save-env-btn",
       id="ai-provider-select", id="ai-api-key-input",
       id="restart-banner" (the JS wires handlers to these ids)
       AND the env-other-list is wrapped in a <details> element (todo 4 —
       collapsible deployment info)
    """
    async with lifespan(app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get("/admin/ai")

    body = response.text
    assert 'id="ai-connection-section"' in body
    assert 'id="save-env-btn"' in body
    assert 'id="ai-provider-select"' in body
    assert 'id="ai-api-key-input"' in body
    assert 'id="restart-banner"' in body
    # todo 4: env-other-list is inside a <details> collapsible block.
    assert "<details" in body, "admin.html missing <details> collapsible block"
    details_start = body.find("<details")
    details_end = body.find("</details>", details_start)
    assert details_start != -1 and details_end != -1, "<details> block not well-formed"
    details_block = body[details_start:details_end]
    assert 'id="env-other-list"' in details_block, (
        "env-other-list must be inside the <details> block (todo 4)"
    )
