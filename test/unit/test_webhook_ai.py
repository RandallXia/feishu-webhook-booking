# test_webhook_ai.py — Behavior tests for AI-enabled webhook wiring (app/main.py)
#
# Tests the AI_ENABLED gate and the extended WebhookSuccessResponse:
#   - Regression lock: AI disabled → response JSON has EXACTLY 5 keys (no ai_* fields)
#   - AI enabled + succeeded → response carries ai_status/ai_record_id/ai_extracted
#   - AI enabled + failed → still 200 (never 5xx for AI reasons), original text was written
#   - 401 unauthorized → AI pipeline NOT called
#   - 422 validation error → AI pipeline NOT called
#
# Strategy:
#   - AI-disabled default comes from conftest.py (AI_ENABLED="false").
#   - AI-enabled tests mock app.state.ai_pipeline (AsyncMock → PipelineResult) and
#     app.state.feishu_client (AsyncMock) so no real network is touched.
#   - httpx.AsyncClient(transport=ASGITransport(app)) + the lifespan async
#     context manager (run directly) for ASGI integration.

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
from httpx import ASGITransport

from app.config import get_settings
from app.main import app, lifespan
from app.pipeline import PipelineResult


WEBHOOK_TOKEN = "test-webhook-token"
EXPECTED_5_KEYS = {"success", "request_id", "record_id", "book_alias", "message"}
VALID_PAYLOAD = {"original_text": "麦当劳 ¥42 微信支付 2026-08-28"}
TEST_BASE_URL = "http://testserver"


def _mock_feishu_client(record_id: str = "rec-original-001") -> AsyncMock:
    """Mock FeishuClient: update_original_text returns a record_id, no network."""
    feishu = AsyncMock()
    feishu.update_original_text = AsyncMock(return_value=record_id)
    return feishu


def _mock_pipeline(result: PipelineResult) -> AsyncMock:
    """Mock AiPipeline: run() returns the given PipelineResult."""
    pipeline = AsyncMock()
    pipeline.run = AsyncMock(return_value=result)
    return pipeline


def _enabled_settings():
    """Frozen Settings with ai_enabled flipped True (other fields unchanged).

    Settings is frozen, so per-test AI enabling replaces app.state.settings
    with a copy where ai_enabled=True. AI components themselves stay mocked
    on app.state, so the None ai_provider/ai_api_key never get exercised.
    """
    return replace(get_settings(), ai_enabled=True)


# ─── Test 1: regression lock — disabled mode yields exactly the 5 master keys ─


async def test_regression_disabled_response_has_5_keys(monkeypatch):
    """
    GIVEN AI_ENABLED is unset (monkeypatch.delenv + cache_clear)
       AND the app lifespan has constructed app.state without any ai_pipeline
    WHEN POST /v1/webhook/ocr is sent with a valid payload + valid X-Webhook-Token
    THEN the response status is 200
      AND the response JSON has EXACTLY 5 keys: success, request_id, record_id, book_alias, message
      AND no ai_* keys are present (regression lock vs master)
    """
    monkeypatch.delenv("AI_ENABLED", raising=False)
    from app.config import get_settings
    get_settings.cache_clear()

    async with lifespan(app):
        app.state.feishu_client = _mock_feishu_client()

        async with httpx.AsyncClient(transport=ASGITransport(app), base_url=TEST_BASE_URL) as client:
            response = await client.post(
                "/v1/webhook/ocr",
                json=VALID_PAYLOAD,
                headers={"X-Webhook-Token": WEBHOOK_TOKEN},
            )

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == EXPECTED_5_KEYS
    for forbidden in ("ai_status", "ai_record_id", "ai_warnings", "ai_extracted"):
        assert forbidden not in body


# ─── Test 2: AI enabled + succeeded → ai_* fields populated ─────────────────


async def test_enabled_succeeded_response_has_ai_fields():
    """
    GIVEN app.state.ai_pipeline is an AsyncMock returning PipelineResult(ai_status="succeeded", bill_record_id="rec123", ...)
       AND app.state.feishu_client is an AsyncMock returning a record_id for update_original_text
    WHEN POST /v1/webhook/ocr is sent with a valid payload + valid token
    THEN the response status is 200
      AND response.ai_status == "succeeded"
      AND response.ai_record_id == "rec123"
      AND response.ai_extracted contains "amount" and "category"
      AND response.record_id is still the feishu original-text record_id
    """
    extracted = {"amount": 42.0, "category": "餐饮", "flow_type": "支出", "description": "麦当劳午餐"}
    result = PipelineResult(
        ai_status="succeeded",
        bill_record_id="rec123",
        warnings=[],
        extracted=extracted,
        dedup_hit=False,
    )

    async with lifespan(app):
        app.state.feishu_client = _mock_feishu_client(record_id="rec-original-001")
        app.state.settings = _enabled_settings()
        pipeline_mock = _mock_pipeline(result)
        app.state.ai_pipeline = pipeline_mock

        async with httpx.AsyncClient(transport=ASGITransport(app), base_url=TEST_BASE_URL) as client:
            response = await client.post(
                "/v1/webhook/ocr",
                json=VALID_PAYLOAD,
                headers={"X-Webhook-Token": WEBHOOK_TOKEN},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["ai_status"] == "succeeded"
    assert body["ai_record_id"] == "rec123"
    assert body["ai_extracted"]["amount"] == 42.0
    assert body["ai_extracted"]["category"] == "餐饮"
    assert body["record_id"] == "rec-original-001"
    assert pipeline_mock.run.await_count == 1


# ─── Test 3: AI enabled + failed → still 200, original text was written ─────


async def test_enabled_ai_failure_returns_200():
    """
    GIVEN app.state.ai_pipeline is an AsyncMock returning PipelineResult(ai_status="failed", bill_record_id=None)
       AND app.state.feishu_client is an AsyncMock that succeeds for update_original_text
    WHEN POST /v1/webhook/ocr is sent with a valid payload + valid token
    THEN the response status is 200 (NOT 5xx — AI failure must not break webhook semantics)
      AND response.ai_status == "failed"
      AND response.record_id is present (original text was written before AI ran)
    """
    result = PipelineResult(
        ai_status="failed",
        bill_record_id=None,
        warnings=[],
        extracted={},
        dedup_hit=False,
    )

    async with lifespan(app):
        app.state.feishu_client = _mock_feishu_client(record_id="rec-original-007")
        app.state.settings = _enabled_settings()
        app.state.ai_pipeline = _mock_pipeline(result)

        async with httpx.AsyncClient(transport=ASGITransport(app), base_url=TEST_BASE_URL) as client:
            response = await client.post(
                "/v1/webhook/ocr",
                json=VALID_PAYLOAD,
                headers={"X-Webhook-Token": WEBHOOK_TOKEN},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["ai_status"] == "failed"
    # bill_record_id was None → response_model_exclude_none drops the key.
    assert body.get("ai_record_id") is None
    assert body["record_id"] == "rec-original-007"


# ─── Test 4: 401 unauthorized → AI pipeline NOT called ──────────────────────


async def test_unauthorized_no_ai_triggered():
    """
    GIVEN app.state.ai_pipeline is an AsyncMock spy (PipelineResult if called)
       AND the request carries NO X-Webhook-Token header
    WHEN POST /v1/webhook/ocr is sent with a valid payload
    THEN the response status is 401
      AND app.state.ai_pipeline.run was NOT called (auth fails before AI stage)
    """
    extracted = {"amount": 1.0, "category": "x", "flow_type": "y", "description": "z"}
    result = PipelineResult(
        ai_status="succeeded",
        bill_record_id="should-not-happen",
        warnings=[],
        extracted=extracted,
        dedup_hit=False,
    )

    async with lifespan(app):
        app.state.feishu_client = _mock_feishu_client()
        app.state.settings = _enabled_settings()
        pipeline_mock = _mock_pipeline(result)
        app.state.ai_pipeline = pipeline_mock

        async with httpx.AsyncClient(transport=ASGITransport(app), base_url=TEST_BASE_URL) as client:
            response = await client.post(
                "/v1/webhook/ocr",
                json=VALID_PAYLOAD,
                # No X-Webhook-Token header
            )

    assert response.status_code == 401
    assert pipeline_mock.run.await_count == 0


# ─── Test 5: 422 validation error → AI pipeline NOT called ──────────────────


async def test_validation_error_no_ai_triggered():
    """
    GIVEN app.state.ai_pipeline is an AsyncMock spy (PipelineResult if called)
       AND the request payload has an empty original_text
    WHEN POST /v1/webhook/ocr is sent with a valid X-Webhook-Token
    THEN the response status is 422
      AND app.state.ai_pipeline.run was NOT called (validation fails before AI stage)
    """
    extracted = {"amount": 1.0, "category": "x", "flow_type": "y", "description": "z"}
    result = PipelineResult(
        ai_status="succeeded",
        bill_record_id="should-not-happen",
        warnings=[],
        extracted=extracted,
        dedup_hit=False,
    )

    async with lifespan(app):
        app.state.feishu_client = _mock_feishu_client()
        app.state.settings = _enabled_settings()
        pipeline_mock = _mock_pipeline(result)
        app.state.ai_pipeline = pipeline_mock

        async with httpx.AsyncClient(transport=ASGITransport(app), base_url=TEST_BASE_URL) as client:
            response = await client.post(
                "/v1/webhook/ocr",
                json={"original_text": ""},
                headers={"X-Webhook-Token": WEBHOOK_TOKEN},
            )

    assert response.status_code == 422
    assert pipeline_mock.run.await_count == 0
