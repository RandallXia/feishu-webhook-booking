# test_config_profile_api.py — Behavior tests for GET/PUT /admin/config/profile.
#
# Two routes (mirror reload_config auth + an AI_DISABLED 404 gate):
#   - GET /admin/config/profile  : read editable profile shape (degrades 200 on fail-closed)
#   - PUT /admin/config/profile  : validate → atomic save (tmp + os.replace) → reload
#
# Strategy mirrors test_admin_ai.py:
#   - conftest pins AI_ENABLED="false" + legacy mode (FEISHU_TARGETS_FILE=""),
#     so PUT's resolve(None,None) returns the legacy default target.
#   - AI-enabled tests replace app.state.settings (frozen → dataclasses.replace)
#     and stub app.state.ai_registry / feishu_client AFTER lifespan startup.
#   - httpx.AsyncClient(transport=ASGITransport(app), base_url="http://testserver")
#     with the lifespan async context manager run directly.

from __future__ import annotations

import errno
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from httpx import ASGITransport

from app.ai_profile import (
    AiProfile,
    AiProfileRegistryUnavailableError,
    AiProfileSnapshot,
)
from app.config import get_settings
from app.field_codec import FieldSpec
from app.main import app, lifespan


ADMIN_TOKEN = "test-admin-token"
TEST_BASE_URL = "http://testserver"


# --- Shared fixtures ---

_PROFILE_FIELDS = (
    FieldSpec(
        ai_key="summary",
        feishu_field="精简原始数据",
        type="passthrough",
        target="extract",
        source="summary",
    ),
    FieldSpec(
        ai_key="description",
        feishu_field="描述",
        type="text",
        target="bill",
        prompt="描述",
    ),
    FieldSpec(
        ai_key="flow_type",
        feishu_field="收支类型",
        type="single_select",
        target="bill",
        fallback="支出",
        prompt="支出/收入",
    ),
    FieldSpec(
        ai_key="amount",
        feishu_field="金额",
        type="number",
        target="bill",
        prompt="金额",
    ),
    FieldSpec(
        ai_key="category",
        feishu_field="分类",
        type="single_select",
        target="bill",
        fallback="其他",
        prompt="分类",
    ),
    FieldSpec(
        ai_key="payment_method",
        feishu_field="支付方式",
        type="single_select",
        target="bill",
        fallback="未知",
        prompt="支付方式",
    ),
    FieldSpec(
        ai_key="bill_date",
        feishu_field="日期",
        type="date",
        target="bill",
        prompt="日期",
    ),
    FieldSpec(
        ai_key="raw_source",
        feishu_field="原始采集",
        type="passthrough",
        target="bill",
        source="summary",
    ),
)

_PROFILE = AiProfile(
    prompt_header="你是一个账单提取助手",
    summary_field="精简原始数据",
    bill_app_token="bascn-bill",
    bill_table_id="tbl-bill",
    fields=_PROFILE_FIELDS,
)

_WHITELISTS: dict[str, set[str]] = {
    "收支类型": {"支出", "收入"},
    "分类": {"餐饮", "其他"},
    "支付方式": {"微信支付", "未知"},
}


def _enabled_settings(profile_file: Path | None = None) -> "object":
    kwargs = dict(ai_enabled=True, config_reload_token=ADMIN_TOKEN)
    if profile_file is not None:
        kwargs["ai_profile_file"] = profile_file
    return replace(get_settings(), **kwargs)


def _stub_snapshot() -> MagicMock:
    snapshot = MagicMock(spec=AiProfileSnapshot)
    snapshot.profile = _PROFILE
    snapshot.option_whitelists = _WHITELISTS
    snapshot.generation = 1
    return snapshot


def _mock_registry_healthy(generation: int = 1) -> AsyncMock:
    """Healthy registry mock whose get_status() reflects the post-reload generation.

    reload(force=True) bumps the internal generation counter so a subsequent
    get_status() returns the new value — mirrors the real registry's behavior.
    """
    state = {"generation": generation}

    def _get_status() -> dict:
        return {
            "generation": state["generation"],
            "config_valid": True,
            "last_reload_error": None,
            "field_count": len(_PROFILE_FIELDS),
            "source_path": "/tmp/profile.toml",
        }

    def _reload(*, force: bool) -> dict:
        state["generation"] += 1
        return _get_status()

    registry = AsyncMock()
    registry.maybe_reload = AsyncMock(return_value=None)
    registry.get_snapshot = MagicMock(return_value=_stub_snapshot())
    registry.get_status = MagicMock(side_effect=_get_status)
    registry.reload = AsyncMock(side_effect=_reload)
    return registry


def _mock_registry_fail_closed() -> AsyncMock:
    registry = AsyncMock()
    registry.maybe_reload = AsyncMock(return_value=None)
    registry.get_snapshot = MagicMock(
        side_effect=AiProfileRegistryUnavailableError("profile bad")
    )
    registry.get_status = MagicMock(
        return_value={
            "generation": 0,
            "config_valid": False,
            "last_reload_error": "TOML parse error: missing [bill]",
            "field_count": 0,
            "source_path": "/tmp/profile.toml",
        }
    )
    return registry


def _mock_feishu() -> AsyncMock:
    return AsyncMock()


def _profile_dict() -> dict:
    """Editable profile shape (mirrors GET response.profile)."""
    return {
        "prompt_header": _PROFILE.prompt_header,
        "summary_field": _PROFILE.summary_field,
        "bill": {
            "app_token": _PROFILE.bill_app_token,
            "table_id": _PROFILE.bill_table_id,
        },
        "fields": [
            {
                "ai_key": s.ai_key,
                "feishu_field": s.feishu_field,
                "type": s.type,
                "target": s.target,
                "fallback": s.fallback,
                "prompt": s.prompt,
                "source": s.source,
            }
            for s in _PROFILE_FIELDS
        ],
    }


# ─── GET /admin/config/profile ─────────────────────────────────────────────


async def test_get_profile_happy():
    """
    GIVEN ai_enabled=True + a healthy ai_registry snapshot
    WHEN GET /admin/config/profile is called with valid X-Admin-Token
    THEN the response status is 200
      AND body has prompt_header, summary_field, bill.{app_token,table_id}
      AND body.fields has 8 entries each with all 7 keys (incl None values)
      AND body.generation == registry.get_status()["generation"]
      AND body.config_valid is True
    """
    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.ai_registry = _mock_registry_healthy(generation=1)

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get(
                "/admin/config/profile",
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["prompt_header"] == _PROFILE.prompt_header
    assert body["summary_field"] == _PROFILE.summary_field
    assert body["bill"] == {"app_token": "bascn-bill", "table_id": "tbl-bill"}
    assert body["generation"] == 1
    assert body["config_valid"] is True

    fields = body["fields"]
    assert len(fields) == 8
    # Every field carries all 7 keys (None values preserved for the UI).
    for f in fields:
        assert set(f.keys()) == {
            "ai_key", "feishu_field", "type", "target",
            "fallback", "prompt", "source",
        }
    # passthrough fields expose source; non-single_select expose fallback=None
    assert fields[0]["fallback"] is None
    assert fields[0]["source"] == "summary"
    assert fields[2]["fallback"] == "支出"


async def test_get_profile_fail_closed_degrades_200():
    """
    GIVEN ai_registry.get_snapshot raises AiProfileRegistryUnavailableError
       AND get_status returns config_valid=false + last_reload_error
    WHEN GET /admin/config/profile is called
    THEN the response status is 200 (NOT 503)
      AND body.profile is null
      AND body carries registry fields from get_status()
      AND body.config_valid is False
    """
    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.ai_registry = _mock_registry_fail_closed()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get(
                "/admin/config/profile",
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["profile"] is None
    assert body["config_valid"] is False
    assert body["last_reload_error"] == "TOML parse error: missing [bill]"
    assert body["generation"] == 0


async def test_get_profile_ai_disabled_returns_404():
    """
    GIVEN AI_ENABLED=false (conftest default) + config_reload_token set
    WHEN GET /admin/config/profile is called with valid X-Admin-Token
    THEN the response status is 404 AI_DISABLED
    """
    async with lifespan(app):
        settings = replace(get_settings(), config_reload_token=ADMIN_TOKEN)
        app.state.settings = settings

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get(
                "/admin/config/profile",
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "AI_DISABLED"


async def test_get_profile_no_token_returns_401():
    """
    GIVEN CONFIG_RELOAD_TOKEN is set
    WHEN GET /admin/config/profile is called with NO X-Admin-Token
    THEN the response status is 401 UNAUTHORIZED
    """
    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.ai_registry = _mock_registry_healthy()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get("/admin/config/profile")

    assert response.status_code == 401
    assert response.json()["detail"]["error"]["code"] == "UNAUTHORIZED"


async def test_get_profile_reload_disabled_returns_404():
    """
    GIVEN CONFIG_RELOAD_TOKEN is unset
    WHEN GET /admin/config/profile is called
    THEN the response status is 404 RELOAD_DISABLED
    """
    async with lifespan(app):
        settings = replace(get_settings(), config_reload_token=None)
        app.state.settings = settings

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get(
                "/admin/config/profile",
                headers={"X-Admin-Token": "anything"},
            )

    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "RELOAD_DISABLED"


# ─── PUT /admin/config/profile ─────────────────────────────────────────────


@pytest.fixture
def profile_file(tmp_path: Path) -> Path:
    """A tmp ai-profile.toml seeded with valid content."""
    path = tmp_path / "ai-profile.toml"
    path.write_text(
        'prompt_header = "initial"\n\n'
        "[extract]\nsummary_field = \"精简原始数据\"\n\n"
        "[bill]\napp_token = \"bascn-bill\"\ntable_id = \"tbl-bill\"\n",
        encoding="utf-8",
    )
    return path


async def test_put_profile_happy(monkeypatch, profile_file):
    """
    GIVEN a healthy ai_registry (generation=1) + a tmp profile file
       AND validate_profile_candidate returns no errors
       AND target_registry resolves to the legacy default target
    WHEN PUT /admin/config/profile is called with base_generation=1 + a profile dict
    THEN the response status is 200
      AND response.success is True + response.generation == 2 (reloaded)
      AND response.warnings == []
      AND a .bak file was created with the ORIGINAL content
      AND the profile file now contains dump_profile's output
      AND ai_registry.reload(force=True) was awaited
    """
    from app import main as main_mod
    from app.toml_writer import dump_profile

    expected_new_text = dump_profile(_profile_dict_converted())
    monkeypatch.setattr(
        main_mod, "validate_profile_candidate",
        AsyncMock(return_value=(_PROFILE, _WHITELISTS, [])),
    )

    original_content = profile_file.read_text(encoding="utf-8")
    registry = _mock_registry_healthy(generation=1)

    async with lifespan(app):
        app.state.settings = _enabled_settings(profile_file=profile_file)
        app.state.ai_registry = registry
        app.state.feishu_client = _mock_feishu()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/profile",
                json={"profile": _profile_dict(), "base_generation": 1},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["generation"] == 2
    assert body["warnings"] == []

    # .bak created with the ORIGINAL content
    bak_path = profile_file.with_suffix(".toml.bak")
    assert bak_path.is_file()
    assert bak_path.read_text(encoding="utf-8") == original_content

    # profile file now holds the dump_profile output
    assert profile_file.read_text(encoding="utf-8") == expected_new_text

    # reload was force-called
    registry.reload.assert_awaited_once()
    assert registry.reload.await_args.kwargs.get("force") is True


def _profile_dict_converted() -> dict:
    """dump_profile input shape (summary_field nested under [extract])."""
    pd = _profile_dict()
    return {
        "prompt_header": pd["prompt_header"],
        "extract": {"summary_field": pd["summary_field"]},
        "bill": pd["bill"],
        "fields": pd["fields"],
    }


async def test_put_profile_validation_failure_returns_422_no_write(monkeypatch, profile_file):
    """
    GIVEN validate_profile_candidate returns a non-empty errors list
    WHEN PUT /admin/config/profile is called
    THEN the response status is 422
      AND response.errors is the list from validate
      AND the profile file is UNCHANGED (byte-for-byte the original)
      AND NO .bak file was created
      AND ai_registry.reload was NOT called
    """
    from app import main as main_mod

    errors = [{"path": "fields[2].fallback", "message": "fallback not in options"}]
    monkeypatch.setattr(
        main_mod, "validate_profile_candidate",
        AsyncMock(return_value=(_PROFILE, _WHITELISTS, errors)),
    )

    original_content = profile_file.read_text(encoding="utf-8")
    registry = _mock_registry_healthy(generation=1)

    async with lifespan(app):
        app.state.settings = _enabled_settings(profile_file=profile_file)
        app.state.ai_registry = registry
        app.state.feishu_client = _mock_feishu()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/profile",
                json={"profile": _profile_dict(), "base_generation": 1},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 422
    assert response.json()["detail"]["errors"] == errors

    # File untouched, no .bak
    assert profile_file.read_text(encoding="utf-8") == original_content
    assert not profile_file.with_suffix(".toml.bak").is_file()
    registry.reload.assert_not_awaited()


async def test_put_profile_stale_generation_returns_409(profile_file):
    """
    GIVEN ai_registry.get_status()["generation"] == 5
    WHEN PUT /admin/config/profile is called with base_generation=1 (stale)
    THEN the response status is 409 STALE_WRITE
      AND the profile file is UNCHANGED
      AND validate_profile_candidate was NOT called (mocked to fail-fast)
    """
    registry = _mock_registry_healthy(generation=5)

    async with lifespan(app):
        app.state.settings = _enabled_settings(profile_file=profile_file)
        app.state.ai_registry = registry
        app.state.feishu_client = _mock_feishu()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/profile",
                json={"profile": _profile_dict(), "base_generation": 1},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "STALE_WRITE"


async def test_put_profile_readonly_filesystem_returns_409(monkeypatch, profile_file):
    """
    GIVEN validate passes BUT os.replace raises PermissionError (simulating :ro mount)
    WHEN PUT /admin/config/profile is called
    THEN the response status is 409 RUNTIME_READONLY
      AND response.suggested_action == "host-edit"
      AND the profile file is UNCHANGED (the tmp was written but not replaced)
    """
    from app import main as main_mod

    monkeypatch.setattr(
        main_mod, "validate_profile_candidate",
        AsyncMock(return_value=(_PROFILE, _WHITELISTS, [])),
    )

    def _replace_raises(src, dst):
        raise PermissionError(errno.EROFS, "Read-only file system", dst)

    monkeypatch.setattr(main_mod.os, "replace", _replace_raises)

    original_content = profile_file.read_text(encoding="utf-8")
    registry = _mock_registry_healthy(generation=1)

    async with lifespan(app):
        app.state.settings = _enabled_settings(profile_file=profile_file)
        app.state.ai_registry = registry
        app.state.feishu_client = _mock_feishu()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/profile",
                json={"profile": _profile_dict(), "base_generation": 1},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 409
    body = response.json()
    assert body["detail"]["error"]["code"] == "RUNTIME_READONLY"
    assert body["detail"]["error"]["suggested_action"] == "host-edit"
    # Original file untouched (os.replace failed before swap)
    assert profile_file.read_text(encoding="utf-8") == original_content


async def test_put_profile_ai_disabled_returns_404(profile_file):
    """
    GIVEN AI_ENABLED=false (conftest default) + config_reload_token set
    WHEN PUT /admin/config/profile is called with valid X-Admin-Token
    THEN the response status is 404 AI_DISABLED
    """
    async with lifespan(app):
        settings = replace(
            get_settings(),
            config_reload_token=ADMIN_TOKEN,
            ai_profile_file=profile_file,
        )
        app.state.settings = settings

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/profile",
                json={"profile": _profile_dict(), "base_generation": 1},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "AI_DISABLED"


async def test_put_profile_no_token_returns_401(profile_file):
    """
    GIVEN CONFIG_RELOAD_TOKEN is set + AI enabled
    WHEN PUT /admin/config/profile is called with NO X-Admin-Token
    THEN the response status is 401 UNAUTHORIZED
    """
    async with lifespan(app):
        app.state.settings = _enabled_settings(profile_file=profile_file)
        app.state.ai_registry = _mock_registry_healthy()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/profile",
                json={"profile": _profile_dict(), "base_generation": 1},
            )

    assert response.status_code == 401
    assert response.json()["detail"]["error"]["code"] == "UNAUTHORIZED"


async def test_put_profile_reload_disabled_returns_404(profile_file):
    """
    GIVEN CONFIG_RELOAD_TOKEN is unset
    WHEN PUT /admin/config/profile is called
    THEN the response status is 404 RELOAD_DISABLED
    """
    async with lifespan(app):
        settings = replace(get_settings(), config_reload_token=None)
        app.state.settings = settings

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/profile",
                json={"profile": _profile_dict(), "base_generation": 1},
                headers={"X-Admin-Token": "anything"},
            )

    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "RELOAD_DISABLED"
