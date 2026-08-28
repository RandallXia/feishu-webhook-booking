# test_ai_profile_registry.py — Behavior tests for AiProfileRegistry (app/ai_profile.py)
#
# Tests AiProfileRegistry: mtime hot reload + whitelist snapshot + fail-closed:
#   1. Valid TOML + mocked list_fields returns options → snapshot has whitelists + generation=1
#   2. single_select fallback not in options → ProfileConfigError + config_valid=False
#   3. list_fields raises FeishuClientError → fail-closed (config_valid=False)
#   4. maybe_reload twice without mtime change → generation unchanged
#   5. mtime changed → reload → generation+1 + new whitelist
#   6. After config error → get_snapshot raises AiProfileRegistryUnavailableError
#   7. After config error → get_status returns dict (NON-raising) with config_valid=False + last_reload_error
#   8. /admin/config/reload response includes ai_profile sub-dict (generation/config_valid)
#   9. Registry fail-closed → POST /v1/webhook/ocr → 503 AI_PROFILE_UNAVAILABLE
#
# All FeishuClient IO is mocked (AsyncMock). TOML files written to tmp_path.
# Lock discipline: network IO happens OUTSIDE the lock; snapshot swap inside the lock.

from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from httpx import ASGITransport

from app.ai_profile import (
    AiProfile,
    AiProfileRegistry,
    AiProfileRegistryUnavailableError,
    AiProfileSnapshot,
    ProfileConfigError,
)
from app.config import get_settings
from app.feishu_client import FeishuClient, FeishuClientError
from app.main import app, lifespan


# ─── Test helpers ──────────────────────────────────────────────────────────


# Valid profile with 8 fields. prompt_header MUST appear before any [section]
# (TOML rule: a bare key after a section header attaches to that section).
_VALID_PROFILE_TOML = """\
prompt_header = "你是一个账单信息提取助手..."

[extract]
summary_field = "精简原始数据"

[bill]
app_token = "bascnXXX"
table_id = "tblXXX"

[[fields]]
ai_key = "summary"
feishu_field = "精简原始数据"
type = "text"
target = "extract"
prompt = "提炼OCR文本的关键信息摘要"

[[fields]]
ai_key = "description"
feishu_field = "消费描述"
type = "text"
target = "bill"
prompt = "一句话描述这笔消费"

[[fields]]
ai_key = "flow_type"
feishu_field = "收支类型"
type = "single_select"
fallback = "支出"
target = "bill"
prompt = "支出或收入"

[[fields]]
ai_key = "amount"
feishu_field = "金额"
type = "number"
target = "bill"
prompt = "金额"

[[fields]]
ai_key = "category"
feishu_field = "收支分类"
type = "single_select"
fallback = "其他"
target = "bill"
prompt = "消费分类"

[[fields]]
ai_key = "payment_method"
feishu_field = "支付途径"
type = "single_select"
fallback = "未知"
target = "bill"
prompt = "支付方式"

[[fields]]
ai_key = "bill_date"
feishu_field = "账单日期"
type = "date"
target = "bill"
prompt = "日期 YYYY-MM-DD"

[[fields]]
ai_key = "raw_source"
feishu_field = "原始采集账单数据"
type = "passthrough"
target = "bill"
source = "summary"
"""

WEBHOOK_TOKEN = "test-webhook-token"
ADMIN_TOKEN = "test-admin-token"
TEST_BASE_URL = "http://testserver"
VALID_PAYLOAD = {"original_text": "麦当劳 ¥42 微信支付 2026-08-28"}


def _write_profile(tmp_path: Path, content: str = _VALID_PROFILE_TOML) -> Path:
    path = tmp_path / "profile.toml"
    path.write_text(content, encoding="utf-8")
    return path


def _make_settings(profile_path: Path, **overrides) -> "Settings":
    """Frozen Settings for AI-enabled registry tests.

    Uses dataclasses.replace on the conftest-pinned get_settings() so all the
    base Feishu/webhook env stays valid; only the AI knobs flip on.
    """
    from app.config import Settings

    base = get_settings()
    defaults = dict(
        ai_enabled=True,
        ai_provider="openai",
        ai_api_key="test-key",
        ai_model="test-model",
        ai_profile_file=profile_path,
        ai_profile_reload_interval_seconds=0,  # tests want no throttle by default
    )
    defaults.update(overrides)
    return replace(base, **defaults)


def _make_feishu_mock(list_fields_return: dict[str, dict] | None = None,
                      list_fields_side_effect: Exception | None = None) -> AsyncMock:
    """Mock FeishuClient. Only list_fields is exercised by the registry.

    list_fields returns a dict keyed by feishu_field_name → field_def, where
    field_def has property.options[].name (single_select) — matches the real
    FeishuClient.list_fields response shape.
    """
    feishu = AsyncMock(spec=FeishuClient)
    if list_fields_side_effect is not None:
        feishu.list_fields = AsyncMock(side_effect=list_fields_side_effect)
    else:
        feishu.list_fields = AsyncMock(return_value=list_fields_return or {})
    return feishu


def _single_select_field(name: str, options: list[str]) -> dict:
    """Build a list_fields entry for a single_select field."""
    return {
        "field_name": name,
        "property": {
            "options": [{"name": opt} for opt in options],
        },
    }


def _default_list_fields() -> dict[str, dict]:
    """list_fields response matching _VALID_PROFILE_TOML single_select fields."""
    return {
        "收支类型": _single_select_field("收支类型", ["支出", "收入"]),
        "收支分类": _single_select_field("收支分类", ["餐饮", "交通", "购物", "日用", "娱乐", "医疗", "其他"]),
        "支付途径": _single_select_field("支付途径", ["微信", "支付宝", "银行卡", "现金", "信用卡", "未知"]),
    }


# ─── 1. Valid TOML + list_fields → snapshot has whitelists + generation ─────


async def test_valid_toml_with_list_fields_mock(tmp_path):
    """
    GIVEN a valid profile TOML on disk
       AND a FeishuClient whose list_fields returns options for single_select fields
    WHEN AiProfileRegistry.load_initial() is awaited
    THEN get_snapshot() returns an AiProfileSnapshot with generation=1
      AND option_whitelists contains the single_select feishu_field names
      AND each whitelist set matches the list_fields response options
    """
    path = _write_profile(tmp_path)
    settings = _make_settings(path)
    feishu = _make_feishu_mock(_default_list_fields())

    registry = AiProfileRegistry(settings, feishu)
    await registry.load_initial()

    snapshot = registry.get_snapshot()
    assert isinstance(snapshot, AiProfileSnapshot)
    assert snapshot.generation == 1
    assert isinstance(snapshot.profile, AiProfile)
    assert snapshot.profile.bill_app_token == "bascnXXX"
    # All three single_select feishu_fields present in whitelists
    assert "收支类型" in snapshot.option_whitelists
    assert "收支分类" in snapshot.option_whitelists
    assert "支付途径" in snapshot.option_whitelists
    # Values match the mocked options
    assert snapshot.option_whitelists["收支类型"] == {"支出", "收入"}
    assert "餐饮" in snapshot.option_whitelists["收支分类"]
    # Non-single_select fields are NOT in whitelists (whitelist = options only)
    assert "金额" not in snapshot.option_whitelists
    assert "消费描述" not in snapshot.option_whitelists
    feishu.list_fields.assert_awaited_once_with("bascnXXX", "tblXXX")


# ─── 2. single_select fallback not in options → fail-closed ────────────────


async def test_single_select_fallback_not_in_options_raises(tmp_path):
    """
    GIVEN a valid profile TOML with a single_select field whose fallback="支出"
       AND a FeishuClient whose list_fields returns options NOT containing "支出"
    WHEN AiProfileRegistry.load_initial() is awaited
    THEN ProfileConfigError is raised
      AND get_status()["config_valid"] is False
    """
    path = _write_profile(tmp_path)
    settings = _make_settings(path)
    # 收支类型 only has "收入" — fallback "支出" is NOT in options
    bad_fields = {
        "收支类型": _single_select_field("收支类型", ["收入"]),
        "收支分类": _single_select_field("收支分类", ["其他"]),
        "支付途径": _single_select_field("支付途径", ["未知"]),
    }
    feishu = _make_feishu_mock(bad_fields)

    registry = AiProfileRegistry(settings, feishu)
    with pytest.raises(ProfileConfigError, match="收支类型"):
        await registry.load_initial()

    status = registry.get_status()
    assert status["config_valid"] is False
    assert status["last_reload_error"] is not None


# ─── 3. list_fields network failure → fail-closed ───────────────────────────


async def test_list_fields_failure_fail_closed(tmp_path):
    """
    GIVEN a valid profile TOML
       AND a FeishuClient whose list_fields raises FeishuClientError
    WHEN AiProfileRegistry.load_initial() is awaited
    THEN FeishuClientError propagates (re-raised by _load_snapshot)
      AND get_status()["config_valid"] is False
      AND get_status()["last_reload_error"] is not None
    """
    path = _write_profile(tmp_path)
    settings = _make_settings(path)
    feishu = _make_feishu_mock(
        list_fields_side_effect=FeishuClientError(
            "network down", stage="list_fields"
        )
    )

    registry = AiProfileRegistry(settings, feishu)
    with pytest.raises(FeishuClientError):
        await registry.load_initial()

    status = registry.get_status()
    assert status["config_valid"] is False
    assert status["last_reload_error"] is not None


# ─── 4. mtime unchanged → maybe_reload is a no-op ───────────────────────────


async def test_mtime_unchanged_no_reload(tmp_path):
    """
    GIVEN a registry that has loaded an initial snapshot (generation=1)
       AND the profile file mtime has NOT changed
    WHEN maybe_reload() is awaited twice in quick succession
    THEN generation remains 1
      AND list_fields is NOT called again after the initial load
    """
    path = _write_profile(tmp_path)
    settings = _make_settings(path)
    feishu = _make_feishu_mock(_default_list_fields())

    registry = AiProfileRegistry(settings, feishu)
    await registry.load_initial()
    assert registry.get_snapshot().generation == 1
    initial_call_count = feishu.list_fields.await_count

    await registry.maybe_reload()
    await registry.maybe_reload()

    assert registry.get_snapshot().generation == 1
    assert feishu.list_fields.await_count == initial_call_count


# ─── 5. mtime changed → reload → generation+1 + new whitelist ───────────────


async def test_mtime_changed_reloads(tmp_path):
    """
    GIVEN a registry that has loaded an initial snapshot (generation=1)
       AND the profile file is rewritten with a new single_select fallback
       AND the file mtime changes (newer)
    WHEN maybe_reload() is awaited (after the reload interval elapses)
    THEN generation becomes 2
      AND get_snapshot().option_whitelists reflects the new options
    """
    path = _write_profile(tmp_path)
    settings = _make_settings(path)
    feishu = _make_feishu_mock(_default_list_fields())

    registry = AiProfileRegistry(settings, feishu)
    await registry.load_initial()
    assert registry.get_snapshot().generation == 1
    assert "支出" in registry.get_snapshot().option_whitelists["收支类型"]

    # Flip fallback 支出 → 收入 in the TOML, and drop "支出" from list_fields
    # so the new fallback "收入" is valid (options=["收入"]).
    new_toml = _VALID_PROFILE_TOML.replace(
        'feishu_field = "收支类型"\ntype = "single_select"\nfallback = "支出"',
        'feishu_field = "收支类型"\ntype = "single_select"\nfallback = "收入"',
    )
    new_fields = _default_list_fields()
    new_fields["收支类型"] = _single_select_field("收支类型", ["收入"])
    feishu.list_fields = AsyncMock(return_value=new_fields)

    path.write_text(new_toml, encoding="utf-8")
    # Force mtime strictly newer (covers filesystems with coarse mtime resolution)
    new_mtime = time.time() + 5
    os.utime(path, (new_mtime, new_mtime))

    await registry.maybe_reload()

    snapshot = registry.get_snapshot()
    assert snapshot.generation == 2
    assert snapshot.option_whitelists["收支类型"] == {"收入"}
    assert "支出" not in snapshot.option_whitelists["收支类型"]


# ─── 6. After config error → get_snapshot raises ────────────────────────────


async def test_get_snapshot_raises_when_invalid(tmp_path):
    """
    GIVEN a registry whose load_initial() failed due to a fallback-not-in-options error
    WHEN get_snapshot() is called
    THEN AiProfileRegistryUnavailableError is raised
    """
    path = _write_profile(tmp_path)
    settings = _make_settings(path)
    bad_fields = {
        "收支类型": _single_select_field("收支类型", ["收入"]),  # fallback 支出 not in
        "收支分类": _single_select_field("收支分类", ["其他"]),
        "支付途径": _single_select_field("支付途径", ["未知"]),
    }
    feishu = _make_feishu_mock(bad_fields)

    registry = AiProfileRegistry(settings, feishu)
    with pytest.raises(ProfileConfigError):
        await registry.load_initial()

    with pytest.raises(AiProfileRegistryUnavailableError):
        registry.get_snapshot()


# ─── 7. get_status returns diagnostics (NON-raising) when invalid ────────────


async def test_get_status_returns_diagnostics_when_invalid(tmp_path):
    """
    GIVEN a registry whose load_initial() failed (config_valid=False)
    WHEN get_status() is called
    THEN a dict is returned (no raise)
      AND dict["config_valid"] is False
      AND dict["last_reload_error"] is not None
      AND dict["generation"] is present
    """
    path = _write_profile(tmp_path)
    settings = _make_settings(path)
    bad_fields = {
        "收支类型": _single_select_field("收支类型", ["收入"]),
        "收支分类": _single_select_field("收支分类", ["其他"]),
        "支付途径": _single_select_field("支付途径", ["未知"]),
    }
    feishu = _make_feishu_mock(bad_fields)

    registry = AiProfileRegistry(settings, feishu)
    with pytest.raises(ProfileConfigError):
        await registry.load_initial()

    # NON-raising accessor — used by admin/health endpoints in fail-closed state
    status = registry.get_status()
    assert isinstance(status, dict)
    assert status["config_valid"] is False
    assert status["last_reload_error"] is not None
    assert "generation" in status


# ─── 8. /admin/config/reload response includes ai_profile sub-dict ──────────


async def test_admin_config_reload_response_has_ai_profile_key(tmp_path, monkeypatch):
    """
    GIVEN app lifespan started with AI enabled and a valid registry
       AND CONFIG_RELOAD_TOKEN is set
    WHEN POST /admin/config/reload is sent with the correct X-Admin-Token
    THEN response status is 200
      AND response JSON contains an "ai_profile" sub-dict
      AND ai_profile has keys generation, config_valid, last_reload_error
    """
    path = _write_profile(tmp_path)
    # CONFIG_RELOAD_TOKEN must be set BEFORE get_settings() is called by lifespan.
    monkeypatch.setenv("CONFIG_RELOAD_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_MODEL", "test-model")
    monkeypatch.setenv("AI_PROFILE_FILE", str(path))
    monkeypatch.setenv("AI_PROFILE_RELOAD_INTERVAL_SECONDS", "0")
    # Legacy target env so TargetRegistry.load_initial doesn't blow up
    monkeypatch.setenv("FEISHU_TARGETS_FILE", "")
    monkeypatch.setenv("FEISHU_APP_TOKEN", "tok")
    monkeypatch.setenv("FEISHU_TABLE_ID", "tbl")
    monkeypatch.setenv("FEISHU_RECORD_ID", "rec")
    get_settings.cache_clear()

    feishu = _make_feishu_mock(_default_list_fields())

    # Lifespan overwrites app.state.feishu_client with a real FeishuClient, so
    # patch the AiProfileRegistry symbol in main to inject our mock instead.
    import app.main as main_mod
    real_cls = main_mod.AiProfileRegistry

    def _factory(settings, _real_feishu):
        return real_cls(settings, feishu)

    monkeypatch.setattr(main_mod, "AiProfileRegistry", _factory)

    async with lifespan(app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.post(
                "/admin/config/reload",
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    body = response.json()
    assert "ai_profile" in body
    ai_status = body["ai_profile"]
    assert ai_status["config_valid"] is True
    assert ai_status["generation"] >= 1
    assert "last_reload_error" in ai_status


# ─── 9. Registry fail-closed → POST webhook → 503 ───────────────────────────


async def test_webhook_503_when_registry_unavailable(tmp_path, monkeypatch):
    """
    GIVEN app lifespan started with AI enabled
       AND app.state.ai_registry is fail-closed (config_valid=False)
    WHEN POST /v1/webhook/ocr is sent with valid token + payload
    THEN response status is 503
      AND response error.code == "AI_PROFILE_UNAVAILABLE"
    """
    path = _write_profile(tmp_path)
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setenv("AI_MODEL", "test-model")
    monkeypatch.setenv("AI_PROFILE_FILE", str(path))
    monkeypatch.setenv("AI_PROFILE_RELOAD_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("FEISHU_TARGETS_FILE", "")
    monkeypatch.setenv("FEISHU_APP_TOKEN", "tok")
    monkeypatch.setenv("FEISHU_TABLE_ID", "tbl")
    monkeypatch.setenv("FEISHU_RECORD_ID", "rec")
    get_settings.cache_clear()

    # Mock for the original-text write (called by ingest_ocr before the AI stage).
    feishu = _make_feishu_mock(_default_list_fields())
    feishu.update_original_text = AsyncMock(return_value="rec-original-001")

    # A second mock that returns single_select options MISSING "支出" — used to
    # build a fail-closed registry. load_initial will raise ProfileConfigError,
    # but _load_snapshot sets _config_valid=False before re-raising, so after
    # the catch the registry instance is in fail-closed state.
    bad_feishu = _make_feishu_mock({
        "收支类型": _single_select_field("收支类型", ["收入"]),
        "收支分类": _single_select_field("收支分类", ["其他"]),
        "支付途径": _single_select_field("支付途径", ["未知"]),
    })

    import app.main as main_mod
    real_cls = main_mod.AiProfileRegistry

    # Lifespan needs a registry that builds without raising, then we swap it
    # for a fail-closed one after startup. Use the valid-mock for lifespan.
    def _factory(settings, _real_feishu):
        return real_cls(settings, feishu)

    monkeypatch.setattr(main_mod, "AiProfileRegistry", _factory)

    async with lifespan(app):
        # Build a separately-constructed fail-closed registry against bad_feishu.
        # load_initial raises ProfileConfigError (caught) but leaves _config_valid=False.
        fail_closed = real_cls(app.state.settings, bad_feishu)
        with pytest.raises(ProfileConfigError):
            await fail_closed.load_initial()
        # Swap in the fail-closed registry — get_snapshot() will now raise.
        app.state.ai_registry = fail_closed
        # Original-text write still uses the valid feishu mock.
        app.state.feishu_client = feishu
        # Pipeline mock — should NOT be reached (503 short-circuits before pipeline.run)
        app.state.ai_pipeline = AsyncMock()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.post(
                "/v1/webhook/ocr",
                json=VALID_PAYLOAD,
                headers={"X-Webhook-Token": WEBHOOK_TOKEN},
            )

    assert response.status_code == 503
    body = response.json()
    # FastAPI wraps HTTPException(detail=...) under the "detail" key.
    assert body["detail"]["error"]["code"] == "AI_PROFILE_UNAVAILABLE"
    # Pipeline was never called — the 503 short-circuits before the AI stage
    assert app.state.ai_pipeline.run.await_count == 0
