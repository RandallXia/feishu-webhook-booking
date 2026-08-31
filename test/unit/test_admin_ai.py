# test_admin_ai.py — Behavior tests for AI admin endpoints (app/main.py)
#
# Covers two admin routes added for AI observability:
#   - POST /admin/ai/test    : dry-run extraction (no Feishu writes, no dedup)
#   - GET  /admin/ai/profile : registry / profile inspection (never leaks ai_api_key)
#
# Auth mirrors reload_config exactly: X-Admin-Token + secrets.compare_digest;
# CONFIG_RELOAD_TOKEN unset → 404 RELOAD_DISABLED; bad token → 401.
#
# Strategy:
#   - conftest.py pins AI_ENABLED="false" by default, so AI-disabled tests just
#     use the lifespan default app.state (ai_registry=None).
#   - AI-enabled tests replace app.state.settings (frozen → dataclasses.replace)
#     and mock app.state.ai_registry / ai_extractor / feishu_client AFTER lifespan
#     startup, mirroring test_webhook_ai.py.
#   - httpx.AsyncClient(transport=ASGITransport(app), base_url="http://testserver")
#     with the lifespan async context manager run directly.

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import httpx
from httpx import ASGITransport

from app.ai_extractor import AiExtractorError, ExtractionResult
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
SAMPLE_TEXT = "麦当劳 ¥42 微信支付 2026-08-28"

# A 7-field profile that mirrors the production bill extraction profile:
# - summary    → extract table (passthrough of extraction.summary)
# - description → bill table (text)
# - flow_type   → bill table (single_select, fallback "支出")
# - amount      → bill table (number)
# - category    → bill table (single_select, fallback "其他")
# - payment_method → bill table (single_select, fallback "未知")
# - bill_date   → bill table (date)
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
        prompt="描述这笔交易",
    ),
    FieldSpec(
        ai_key="flow_type",
        feishu_field="收支类型",
        type="single_select",
        target="bill",
        fallback="支出",
        prompt="支出 or 收入",
    ),
    FieldSpec(
        ai_key="amount",
        feishu_field="金额",
        type="number",
        target="bill",
        prompt="交易金额",
    ),
    FieldSpec(
        ai_key="category",
        feishu_field="分类",
        type="single_select",
        target="bill",
        fallback="其他",
        prompt="交易分类",
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
        prompt="账单日期",
    ),
)

# Whitelist aligned with the fallbacks above (registry validation requires the
# fallback to be present in the options set).
_WHITELISTS: dict[str, set[str]] = {
    "收支类型": {"支出", "收入"},
    "分类": {"餐饮", "交通", "其他"},
    "支付方式": {"微信支付", "支付宝", "未知"},
}

_PROFILE = AiProfile(
    prompt_header="你是一个账单提取助手",
    summary_field="精简原始数据",
    bill_app_token="bascn-bill",
    bill_table_id="tbl-bill",
    fields=_PROFILE_FIELDS,
)


def _enabled_settings() -> "object":
    return replace(
        get_settings(),
        ai_enabled=True,
        config_reload_token=ADMIN_TOKEN,
    )


def _stub_snapshot() -> MagicMock:
    snapshot = MagicMock(spec=AiProfileSnapshot)
    snapshot.profile = _PROFILE
    snapshot.option_whitelists = _WHITELISTS
    snapshot.generation = 1
    return snapshot


def _mock_registry_healthy() -> AsyncMock:
    registry = AsyncMock()
    registry.maybe_reload = AsyncMock(return_value=None)
    registry.get_snapshot = MagicMock(return_value=_stub_snapshot())
    registry.get_status = MagicMock(
        return_value={
            "generation": 1,
            "config_valid": True,
            "last_reload_error": None,
        }
    )
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
        }
    )
    return registry


def _mock_extractor(result: ExtractionResult) -> AsyncMock:
    extractor = AsyncMock()
    extractor.extract = AsyncMock(return_value=result)
    return extractor


def _mock_extractor_error(exc: AiExtractorError) -> AsyncMock:
    extractor = AsyncMock()
    extractor.extract = AsyncMock(side_effect=exc)
    return extractor


def _mock_feishu_spy() -> AsyncMock:
    """FeishuClient spy — write methods must NEVER be called during dry-run."""
    feishu = AsyncMock()
    feishu.create_record = AsyncMock(return_value="should-not-be-called")
    feishu.update_record_field = AsyncMock(return_value="should-not-be-called")
    feishu.update_original_text = AsyncMock(return_value="should-not-be-called")
    return feishu


def _ok_extraction(**overrides) -> ExtractionResult:
    defaults = dict(
        summary="麦当劳 ¥42 微信支付 2026-08-28",
        description="麦当劳午餐",
        flow_type="支出",
        amount=42.0,
        category="餐饮",
        payment_method="微信支付",
        bill_date="2026-08-28",
    )
    defaults.update(overrides)
    return ExtractionResult(**defaults)


# --- Test 1: POST /admin/ai/test without X-Admin-Token → 401 ---


async def test_post_test_no_token_returns_401():
    """
    GIVEN CONFIG_RELOAD_TOKEN is set to "test-admin-token" and AI is enabled
    WHEN POST /admin/ai/test is sent with NO X-Admin-Token header
    THEN the response status is 401
      AND the error code is UNAUTHORIZED
    """
    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.ai_registry = _mock_registry_healthy()
        app.state.ai_extractor = _mock_extractor(_ok_extraction())
        app.state.feishu_client = _mock_feishu_spy()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.post(
                "/admin/ai/test",
                json={"text": SAMPLE_TEXT},
            )

    assert response.status_code == 401
    body = response.json()
    assert body["detail"]["error"]["code"] == "UNAUTHORIZED"


# --- Test 2: POST /admin/ai/test with CONFIG_RELOAD_TOKEN unset → 404 RELOAD_DISABLED ---


async def test_post_test_reload_disabled_returns_404(monkeypatch):
    """
    GIVEN CONFIG_RELOAD_TOKEN is unset (monkeypatch settings.config_reload_token=None)
    WHEN POST /admin/ai/test is sent with any X-Admin-Token
    THEN the response status is 404
      AND the error code is RELOAD_DISABLED
    """
    async with lifespan(app):
        settings = replace(get_settings(), config_reload_token=None)
        app.state.settings = settings
        app.state.ai_registry = _mock_registry_healthy()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.post(
                "/admin/ai/test",
                json={"text": SAMPLE_TEXT},
                headers={"X-Admin-Token": "anything"},
            )

    assert response.status_code == 404
    body = response.json()
    assert body["detail"]["error"]["code"] == "RELOAD_DISABLED"


# --- Test 3: POST /admin/ai/test with AI_ENABLED=false → 404 AI_DISABLED ---


async def test_post_test_ai_disabled_returns_404():
    """
    GIVEN CONFIG_RELOAD_TOKEN is set BUT AI_ENABLED=false (conftest default)
       AND app.state.ai_registry is None
    WHEN POST /admin/ai/test is sent with a valid X-Admin-Token
    THEN the response status is 404
      AND the error code is AI_DISABLED
    """
    async with lifespan(app):
        # config_reload_token set but ai_enabled stays False (conftest default)
        settings = replace(get_settings(), config_reload_token=ADMIN_TOKEN)
        app.state.settings = settings

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.post(
                "/admin/ai/test",
                json={"text": SAMPLE_TEXT},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 404
    body = response.json()
    assert body["detail"]["error"]["code"] == "AI_DISABLED"


# --- Test 4: POST /admin/ai/test happy dry-run → 200, no Feishu writes ---


async def test_post_test_happy_dry_run():
    """
    GIVEN app.state.settings has ai_enabled=True + config_reload_token set
       AND app.state.ai_registry is mocked (get_snapshot returns a valid snapshot)
       AND app.state.ai_extractor is mocked (extract returns ExtractionResult)
       AND app.state.feishu_client is an AsyncMock spy
    WHEN POST /admin/ai/test is sent with valid X-Admin-Token and body {"text": "..."}
    THEN the response status is 200
      AND response.ai_status == "succeeded"
      AND response.extracted has all 7 ExtractionResult fields
      AND response.bill_fields is present
      AND response.summary_writeback has field + value
      AND feishu_client.create_record was NOT called
      AND feishu_client.update_record_field was NOT called
    """
    extraction = _ok_extraction()
    feishu_spy = _mock_feishu_spy()

    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.ai_registry = _mock_registry_healthy()
        app.state.ai_extractor = _mock_extractor(extraction)
        app.state.feishu_client = feishu_spy

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.post(
                "/admin/ai/test",
                json={"text": SAMPLE_TEXT},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["ai_status"] == "succeeded"

    extracted = body["extracted"]
    for key in (
        "summary",
        "description",
        "flow_type",
        "amount",
        "category",
        "payment_method",
        "bill_date",
    ):
        assert key in extracted, f"missing extracted field: {key}"
    assert extracted["amount"] == 42.0
    assert extracted["category"] == "餐饮"

    assert body["bill_fields"]
    assert body["bill_fields"]["金额"] == 42.0
    assert body["bill_fields"]["分类"] == "餐饮"

    assert body["summary_writeback"]["field"] == "精简原始数据"
    assert body["summary_writeback"]["value"] == extraction.summary

    assert feishu_spy.create_record.await_count == 0
    assert feishu_spy.update_record_field.await_count == 0
    assert feishu_spy.update_original_text.await_count == 0


# --- Test 5: POST /admin/ai/test with AI extract failure → 200 ai_status=failed ---


async def test_post_test_ai_failure_returns_200():
    """
    GIVEN app.state.ai_extractor.extract raises AiExtractorError
    WHEN POST /admin/ai/test is sent with valid X-Admin-Token and body
    THEN the response status is 200 (dry-run never 5xx for AI failures)
      AND response.ai_status == "failed"
      AND response.error is a string carrying the failure message
      AND feishu_client.create_record was NOT called
    """
    feishu_spy = _mock_feishu_spy()
    extractor_err = AiExtractorError("upstream timeout", stage="request")

    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.ai_registry = _mock_registry_healthy()
        app.state.ai_extractor = _mock_extractor_error(extractor_err)
        app.state.feishu_client = feishu_spy

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.post(
                "/admin/ai/test",
                json={"text": SAMPLE_TEXT},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["ai_status"] == "failed"
    assert "upstream timeout" in body["error"]
    assert feishu_spy.create_record.await_count == 0
    assert feishu_spy.update_record_field.await_count == 0


# --- Test 6: GET /admin/ai/profile healthy → 200 full body, no ai_api_key leak ---


async def test_get_profile_healthy():
    """
    GIVEN app.state.settings has ai_enabled=True + config_reload_token set
       AND app.state.ai_registry is mocked with a valid snapshot + healthy status
    WHEN GET /admin/ai/profile is sent with valid X-Admin-Token
    THEN the response status is 200
      AND response.ai_enabled == true
      AND response.profile is populated (summary_field, bill, fields)
      AND response.whitelists is populated
      AND response.registry is populated
      AND the serialized response body does NOT contain "ai_api_key"
    """
    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.ai_registry = _mock_registry_healthy()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get(
                "/admin/ai/profile",
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["ai_enabled"] is True
    base_settings = get_settings()
    assert body["provider"] == base_settings.ai_provider
    assert body["model"] == base_settings.ai_model
    assert body["timeout_seconds"] == base_settings.ai_timeout_seconds
    assert body["dedup_ttl_seconds"] == base_settings.ai_dedup_ttl_seconds

    profile = body["profile"]
    assert profile["summary_field"] == "精简原始数据"
    assert profile["bill"]["app_token"] == "bascn-bill"
    assert profile["bill"]["table_id"] == "tbl-bill"
    assert len(profile["fields"]) == len(_PROFILE_FIELDS)

    whitelists = body["whitelists"]
    # option sets serialized as sorted lists (Unicode codepoint order)
    assert whitelists["分类"] == sorted({"餐饮", "交通", "其他"})
    assert whitelists["支付方式"] == sorted({"微信支付", "支付宝", "未知"})

    registry = body["registry"]
    assert registry["config_valid"] is True
    assert registry["generation"] == 1

    # CRITICAL: never leak the API key
    assert "ai_api_key" not in response.text


# --- Test 7: GET /admin/ai/profile fail-closed → 200 degraded (NOT 503) ---


async def test_get_profile_fail_closed_returns_200_degraded():
    """
    GIVEN app.state.ai_registry.get_snapshot raises AiProfileRegistryUnavailableError
       AND app.state.ai_registry.get_status returns config_valid=false + last_reload_error
    WHEN GET /admin/ai/profile is sent with valid X-Admin-Token
    THEN the response status is 200 (NEITHER 500 NOR 503)
      AND response.profile is null
      AND response.whitelists is null
      AND response.registry.config_valid == false
      AND response.registry.last_reload_error is present
    """
    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.ai_registry = _mock_registry_fail_closed()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get(
                "/admin/ai/profile",
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["profile"] is None
    assert body["whitelists"] is None
    assert body["registry"]["config_valid"] is False
    assert body["registry"]["last_reload_error"]


# --- Test 8: POST /admin/ai/test with unwhitelisted category → warning + fallback ---


async def test_get_profile_unwhitelisted_option_warning():
    """
    GIVEN app.state.ai_extractor returns category="未知分类" (not in whitelist)
       AND the category FieldSpec has a fallback "其他" that IS in the whitelist
    WHEN POST /admin/ai/test is sent with valid X-Admin-Token and body
    THEN the response status is 200
      AND response.warnings contains an "option fallback" record for the category field
      AND response.bill_fields for the category reflects the fallback value
    """
    extraction = _ok_extraction(category="未知分类")
    feishu_spy = _mock_feishu_spy()

    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.ai_registry = _mock_registry_healthy()
        app.state.ai_extractor = _mock_extractor(extraction)
        app.state.feishu_client = feishu_spy

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.post(
                "/admin/ai/test",
                json={"text": SAMPLE_TEXT},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["ai_status"] == "succeeded"

    warnings = body["warnings"]
    assert any("option fallback" in w and "分类" in w for w in warnings)
    assert body["bill_fields"]["分类"] == "其他"
    assert feishu_spy.create_record.await_count == 0
    assert feishu_spy.update_record_field.await_count == 0
