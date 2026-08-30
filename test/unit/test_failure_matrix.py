# test_failure_matrix.py — Cross-module AI failure matrix (ASGI integration)
#
# 9 scenarios spanning the full request path (route → registry → pipeline →
# feishu_client → field_codec) under failure / fallback conditions:
#   1. AI timeout (AiExtractorError stage="request") → 200, failed, original written, no bill
#   2. AI missing required key → same as 1
#   3. Unwhitelisted single_select value → bill created with fallback, dirty value never serialized
#   4. Bad bill_date format → bill created with today (Shanghai midnight), date fallback warning
#   5. Duplicate text within TTL → duplicate, AI NOT re-called, create_record called once
#   6. TTL expiry → full chain reruns (AI called again, create_record count=2)
#   7. AI disabled → response has exactly 5 master keys (no ai_*)
#   8. Profile registry fail-closed → 503 AI_PROFILE_UNAVAILABLE, then recovers to 200
#   9. client_token deterministic across two non-deduped calls (sha256(alias:text)[:32] → UUID format)
#
# Strategy:
#   - Scenarios 1,2,5,6: mock ai_pipeline.run to return specific PipelineResult values
#     (assert on response shape + call counts on app.state.feishu_client).
#   - Scenarios 3,4,9: use a REAL AiPipeline with mocked ai_extractor + feishu_client so
#     encode_fields runs and bill_fields / client_token flow into create_record call args.
#   - Scenario 7: AI disabled (monkeypatch.delenv AI_ENABLED), no AI mock.
#   - Scenario 8: mock ai_registry.get_snapshot to raise AiProfileRegistryUnavailableError.
#
# ASGI pattern reused from test_webhook_ai.py: httpx.AsyncClient(transport=ASGITransport(app))
# + `async with lifespan(app):`. AI components mocked on app.state AFTER lifespan startup.

from __future__ import annotations

import dataclasses
import hashlib
import re
from dataclasses import replace
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import httpx
import pytest
from httpx import ASGITransport

from app.ai_extractor import AiExtractorError, ExtractionResult
from app.ai_profile import (
    AiProfile,
    AiProfileRegistryUnavailableError,
)
from app.config import get_settings
from app.feishu_client import FeishuClient
from app.field_codec import FieldSpec
from app.main import app, lifespan
from app.pipeline import AiPipeline, PipelineResult


WEBHOOK_TOKEN = "test-webhook-token"
TEST_BASE_URL = "http://testserver"
VALID_PAYLOAD = {"original_text": "麦当劳 ¥42 微信支付 2026-08-28"}
_SHANGHAI = ZoneInfo("Asia/Shanghai")


# ─── Shared mock helpers ────────────────────────────────────────────────────


def _enabled_settings():
    """Frozen Settings with ai_enabled flipped True (other fields unchanged)."""
    return replace(get_settings(), ai_enabled=True)


def _mock_feishu_client(record_id: str = "rec-original-001") -> AsyncMock:
    """Mock FeishuClient: update_original_text returns a record_id, no network.

    create_record / update_record_field also mocked so a real AiPipeline can
    drive them in scenarios 3/4/9 without touching the network.
    """
    feishu = AsyncMock(spec=FeishuClient)
    feishu.update_original_text = AsyncMock(return_value=record_id)
    feishu.update_record_field = AsyncMock(return_value=record_id)
    feishu.create_record = AsyncMock(return_value="bill-rec-001")
    return feishu


def _mock_registry(snapshot=None) -> MagicMock:
    """Mock AiProfileRegistry: maybe_reload async no-op, get_snapshot returns stub.

    If `snapshot` is None a default stub with .profile + .option_whitelists is
    built — only the shape matters because mocked ai_pipeline.run ignores it.
    """
    from app.ai_profile import AiProfileSnapshot

    registry = MagicMock()
    registry.maybe_reload = AsyncMock(return_value=None)
    if snapshot is None:
        snapshot = MagicMock()
        snapshot.profile = _make_profile()
        snapshot.option_whitelists = _make_whitelists()
    registry.get_snapshot = MagicMock(return_value=snapshot)
    return registry


def _mock_pipeline_run(result: PipelineResult) -> AsyncMock:
    """Mock AiPipeline where run() returns the given PipelineResult (route-level)."""
    pipeline = AsyncMock()
    pipeline.run = AsyncMock(return_value=result)
    return pipeline


# ─── Real-pipeline helpers (scenarios 3, 4, 9) ──────────────────────────────


def _make_extraction(**kwargs) -> ExtractionResult:
    defaults = dict(
        summary="麦当劳 ¥42 微信支付",
        description="麦当劳午餐",
        flow_type="支出",
        amount=42.0,
        category="餐饮",
        payment_method="微信",
        bill_date="2026-08-28",
    )
    defaults.update(kwargs)
    return ExtractionResult(**defaults)


def _make_profile() -> AiProfile:
    return AiProfile(
        prompt_header="你是一个账单信息提取助手...",
        summary_field="精简原始数据",
        bill_app_token="bill-app-token",
        bill_table_id="bill-table-id",
        fields=(
            FieldSpec(
                ai_key="summary",
                feishu_field="精简原始数据",
                type="text",
                target="extract",
                prompt="提炼摘要",
            ),
            FieldSpec(
                ai_key="description",
                feishu_field="消费描述",
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
                prompt="收支",
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
                fallback="其他",
                prompt="支付",
            ),
            FieldSpec(
                ai_key="bill_date",
                feishu_field="日期",
                type="date",
                target="bill",
                prompt="日期",
            ),
        ),
    )


def _make_whitelists() -> dict[str, set[str]]:
    return {
        "收支类型": {"支出", "收入"},
        "分类": {"餐饮", "交通", "其他"},
        "支付方式": {"微信", "支付宝", "其他"},
    }


def _real_pipeline(feishu_mock: AsyncMock, extraction: ExtractionResult) -> AiPipeline:
    """Build a REAL AiPipeline against a mocked extractor + feishu.

    The extractor returns the given ExtractionResult; field_codec.encode_fields
    runs for real so bill_fields / client_token reach feishu_mock.create_record.
    """
    settings = _enabled_settings()
    extractor = MagicMock()
    extractor.extract = AsyncMock(return_value=extraction)
    return AiPipeline(settings, extractor, feishu_mock)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app), base_url=TEST_BASE_URL)


def _post(client: httpx.AsyncClient, payload: dict | None = None):
    return client.post(
        "/v1/webhook/ocr",
        json=payload or VALID_PAYLOAD,
        headers={"X-Webhook-Token": WEBHOOK_TOKEN},
    )


# ─── Scenario 1: AI timeout → 200, failed, original written, no bill ────────


async def test_ai_timeout_returns_200_failed():
    """
    GIVEN app.state.ai_pipeline is mocked to return PipelineResult(ai_status="failed")
       (simulating an AiExtractorError(stage="request") caught inside the real pipeline)
       AND app.state.feishu_client.update_original_text succeeds
    WHEN POST /v1/webhook/ocr is sent with a valid payload + token
    THEN the response status is 200 (AI failure must not break webhook semantics)
      AND response.ai_status == "failed"
      AND feishu.update_original_text WAS called exactly once (original text written)
      AND feishu.create_record was NOT called (mocked pipeline skips bill creation)
    """
    result = PipelineResult(
        ai_status="failed",
        bill_record_id=None,
        warnings=[],
        extracted={},
        dedup_hit=False,
    )

    async with lifespan(app):
        feishu = _mock_feishu_client(record_id="rec-original-007")
        app.state.feishu_client = feishu
        app.state.settings = _enabled_settings()
        app.state.ai_pipeline = _mock_pipeline_run(result)
        app.state.ai_registry = _mock_registry()

        async with _client() as client:
            response = await _post(client)

    assert response.status_code == 200
    body = response.json()
    assert body["ai_status"] == "failed"
    assert body["record_id"] == "rec-original-007"
    assert feishu.update_original_text.await_count == 1
    assert feishu.create_record.await_count == 0


# ─── Scenario 2: AI missing required key → 200, failed, original written ────


async def test_ai_missing_required_key_returns_200_failed():
    """
    GIVEN app.state.ai_pipeline is mocked to return PipelineResult(ai_status="failed")
       (simulating an AiExtractorError(stage="validate", missing required key) caught
       inside the real pipeline — the route sees only the failed result)
       AND app.state.feishu_client.update_original_text succeeds
    WHEN POST /v1/webhook/ocr is sent with a valid payload + token
    THEN the response status is 200
      AND response.ai_status == "failed"
      AND feishu.update_original_text WAS called exactly once
      AND feishu.create_record was NOT called
    """
    result = PipelineResult(
        ai_status="failed",
        bill_record_id=None,
        warnings=[],
        extracted={},
        dedup_hit=False,
    )

    async with lifespan(app):
        feishu = _mock_feishu_client(record_id="rec-original-008")
        app.state.feishu_client = feishu
        app.state.settings = _enabled_settings()
        app.state.ai_pipeline = _mock_pipeline_run(result)
        app.state.ai_registry = _mock_registry()

        async with _client() as client:
            response = await _post(client)

    assert response.status_code == 200
    body = response.json()
    assert body["ai_status"] == "failed"
    assert body["record_id"] == "rec-original-008"
    assert feishu.update_original_text.await_count == 1
    assert feishu.create_record.await_count == 0


# ─── Scenario 3: unwhitelisted single_select → fallback, dirty value not serialized


async def test_unwhitelisted_single_select_uses_fallback():
    """
    GIVEN a REAL AiPipeline whose extractor returns ExtractionResult(category="餐饮 ")
       (trailing space — NOT in the whitelist {"餐饮","交通","其他"})
       AND the registry snapshot exposes option_whitelists with 分类={"餐饮","交通","其他"}, fallback="其他"
    WHEN POST /v1/webhook/ocr is sent with a valid payload + token
    THEN the response status is 200
      AND response.ai_status == "succeeded" (bill WAS created despite the fallback)
      AND response.ai_warnings contains a string mentioning "option fallback"
      AND feishu.create_record received bill_fields where 分类 == "其他"
      AND the dirty value "餐饮 " does NOT appear anywhere in the serialized create_record body
    """
    extraction = _make_extraction(category="餐饮 ")
    feishu = _mock_feishu_client(record_id="rec-original-009")
    pipeline = _real_pipeline(feishu, extraction)

    async with lifespan(app):
        app.state.feishu_client = feishu
        app.state.settings = _enabled_settings()
        app.state.ai_pipeline = pipeline
        app.state.ai_registry = _mock_registry()

        async with _client() as client:
            response = await _post(client)

    assert response.status_code == 200
    body = response.json()
    assert body["ai_status"] == "succeeded"
    assert body["ai_record_id"] == "bill-rec-001"
    assert any("option fallback" in w for w in body["ai_warnings"])

    assert feishu.create_record.await_count == 1
    bill_fields = feishu.create_record.call_args.args[0]
    assert bill_fields["分类"] == "其他"
    # Pollution-prevention: the dirty value must not leak into any serialized field.
    for _field, value in bill_fields.items():
        assert "餐饮 " not in str(value)


# ─── Scenario 4: bad bill_date → today (Shanghai midnight), date fallback warning


async def test_bad_date_falls_back_to_today():
    """
    GIVEN a REAL AiPipeline whose extractor returns ExtractionResult(bill_date="2026/08/28")
       (wrong format — encode_fields expects YYYY-MM-DD)
    WHEN POST /v1/webhook/ocr is sent with a valid payload + token
    THEN the response status is 200
      AND response.ai_status == "succeeded"
      AND response.ai_warnings contains a string mentioning "date fallback"
      AND feishu.create_record received bill_fields where 日期 == today's Shanghai midnight ms timestamp
    """
    extraction = _make_extraction(bill_date="2026/08/28")
    feishu = _mock_feishu_client(record_id="rec-original-010")
    pipeline = _real_pipeline(feishu, extraction)

    async with lifespan(app):
        app.state.feishu_client = feishu
        app.state.settings = _enabled_settings()
        app.state.ai_pipeline = pipeline
        app.state.ai_registry = _mock_registry()

        async with _client() as client:
            response = await _post(client)

    assert response.status_code == 200
    body = response.json()
    assert body["ai_status"] == "succeeded"
    assert any("date fallback" in w for w in body["ai_warnings"])

    assert feishu.create_record.await_count == 1
    bill_fields = feishu.create_record.call_args.args[0]
    today_shanghai_midnight = datetime.now(_SHANGHAI).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    expected_ms = int(today_shanghai_midnight.timestamp() * 1000)
    assert bill_fields["日期"] == expected_ms


# ─── Scenario 5: duplicate text within TTL → duplicate, AI not re-called ────


async def test_duplicate_text_within_ttl_returns_duplicate():
    """
    GIVEN a REAL AiPipeline whose extractor returns a valid ExtractionResult
       AND time.time is frozen so the second call stays inside ai_dedup_ttl_seconds
    WHEN POST /v1/webhook/ocr is sent TWICE with the SAME original_text
    THEN the first response.ai_status == "succeeded"
      AND the second response.ai_status == "duplicate"
      AND the AI extractor was NOT called on the second request (call count stays at 1)
      AND feishu.create_record was called exactly once total (only the first, real run)
    """
    extraction = _make_extraction()
    feishu = _mock_feishu_client(record_id="rec-original-011")
    pipeline = _real_pipeline(feishu, extraction)

    import app.pipeline as pipeline_mod

    base_time = [1000.0]
    original_time = pipeline_mod.time.time

    def fake_time():
        return base_time[0]

    pipeline_mod.time.time = fake_time
    try:
        async with lifespan(app):
            app.state.feishu_client = feishu
            app.state.settings = _enabled_settings()
            app.state.ai_pipeline = pipeline
            app.state.ai_registry = _mock_registry()

            async with _client() as client:
                first = await _post(client)
                # Advance only 10s — well inside the 300s dedup TTL.
                base_time[0] = 1000.0 + 10.0
                second = await _post(client)
    finally:
        pipeline_mod.time.time = original_time

    assert first.status_code == 200
    assert first.json()["ai_status"] == "succeeded"
    assert second.status_code == 200
    assert second.json()["ai_status"] == "duplicate"

    # Dedup contract: the extractor was called once (first run only); the second
    # run short-circuited at the dedup check before reaching extract().
    assert pipeline._extractor.extract.await_count == 1
    assert feishu.create_record.await_count == 1


# ─── Scenario 6: TTL expiry → full chain reruns ─────────────────────────────


async def test_ttl_expiry_reruns_full_chain():
    """
    GIVEN a REAL AiPipeline that successfully processes a text once (dedup recorded)
       AND app.pipeline.time.time is monkeypatched forward past ai_dedup_ttl_seconds
    WHEN the same original_text is POSTed a second time
    THEN the second response.ai_status == "succeeded" (NOT "duplicate")
      AND the extractor was called twice (full chain reran)
      AND feishu.create_record was called twice total
    """
    extraction = _make_extraction()
    feishu = _mock_feishu_client(record_id="rec-original-012")
    pipeline = _real_pipeline(feishu, extraction)

    import app.pipeline as pipeline_mod

    base_time = [1000.0]
    original_time = pipeline_mod.time.time

    def fake_time():
        return base_time[0]

    pipeline_mod.time.time = fake_time
    try:
        async with lifespan(app):
            app.state.feishu_client = feishu
            app.state.settings = _enabled_settings()
            app.state.ai_pipeline = pipeline
            app.state.ai_registry = _mock_registry()

            async with _client() as client:
                first = await _post(client)
                # Advance past TTL (default 300s) → dedup window expired.
                base_time[0] = 1000.0 + 301.0
                second = await _post(client)
    finally:
        pipeline_mod.time.time = original_time

    assert first.status_code == 200
    assert first.json()["ai_status"] == "succeeded"
    assert second.status_code == 200
    assert second.json()["ai_status"] == "succeeded"

    assert pipeline._extractor.extract.await_count == 2
    assert feishu.create_record.await_count == 2


# ─── Scenario 7: AI disabled → response has no ai_* keys ────────────────────


async def test_disabled_response_has_no_ai_keys(monkeypatch):
    """
    GIVEN AI_ENABLED is unset (monkeypatch.delenv + cache_clear)
       AND the app lifespan constructed app.state without any ai_pipeline
    WHEN POST /v1/webhook/ocr is sent with a valid payload + token
    THEN the response status is 200
      AND the response JSON has EXACTLY 5 keys: success, request_id, record_id, book_alias, message
      AND no ai_* keys are present (regression lock vs master)
    """
    monkeypatch.delenv("AI_ENABLED", raising=False)
    get_settings.cache_clear()

    async with lifespan(app):
        app.state.feishu_client = _mock_feishu_client(record_id="rec-original-013")

        async with _client() as client:
            response = await _post(client)

    assert response.status_code == 200
    body = response.json()
    expected_keys = {"success", "request_id", "record_id", "book_alias", "message"}
    assert set(body.keys()) == expected_keys
    for forbidden in ("ai_status", "ai_record_id", "ai_warnings", "ai_extracted"):
        assert forbidden not in body


# ─── Scenario 8: profile registry fail-closed → 503, then recovers ─────────


async def test_profile_hot_reload_bad_toml_returns_503():
    """
    GIVEN app.state.ai_registry.get_snapshot raises AiProfileRegistryUnavailableError
       (simulating a bad TOML hot-reload that fail-closed the registry)
    WHEN POST /v1/webhook/ocr is sent with a valid payload + token
    THEN the response status is 503
      AND the error code is "AI_PROFILE_UNAVAILABLE"
      AND feishu.update_original_text WAS called (the original-text write is pre-AI)
    WHEN ai_registry.get_snapshot is then mocked to succeed again
       AND a second request is sent
    THEN the second response status is 200 (registry self-healed)
    """
    succeeded = PipelineResult(
        ai_status="succeeded",
        bill_record_id="bill-rec-002",
        warnings=[],
        extracted={"amount": 1.0, "category": "餐饮", "flow_type": "支出", "description": "x"},
        dedup_hit=False,
    )

    async with lifespan(app):
        feishu = _mock_feishu_client(record_id="rec-original-014")
        app.state.feishu_client = feishu
        app.state.settings = _enabled_settings()
        app.state.ai_pipeline = _mock_pipeline_run(succeeded)

        registry = MagicMock()
        registry.maybe_reload = AsyncMock(return_value=None)
        registry.get_snapshot = MagicMock(
            side_effect=AiProfileRegistryUnavailableError("registry fail-closed")
        )
        app.state.ai_registry = registry

        async with _client() as client:
            first = await _post(client)

            # Registry self-heals: get_snapshot now returns a valid snapshot.
            registry.get_snapshot = MagicMock(return_value=MagicMock(
                profile=_make_profile(),
                option_whitelists=_make_whitelists(),
            ))
            second = await _post(client)

    assert first.status_code == 503
    first_body = first.json()
    # FastAPI nests HTTPException detail under body["detail"].
    assert first_body["detail"]["error"]["code"] == "AI_PROFILE_UNAVAILABLE"
    assert feishu.update_original_text.await_count >= 1

    assert second.status_code == 200
    assert second.json()["ai_status"] == "succeeded"


# ─── Scenario 9: client_token deterministic across two non-deduped calls ────


async def test_client_token_deterministic():
    """
    GIVEN a REAL AiPipeline processing the SAME original_text + alias twice
       (with app.pipeline.time.time monkeypatched past TTL between calls so dedup
       does NOT short-circuit the second call)
    WHEN both POSTs are sent
    THEN both responses are 200 + ai_status="succeeded"
      AND feishu.create_record was called twice total
      AND both create_record calls received the SAME client_token
      THEN both client_tokens are valid UUIDs derived from the same hash
    """
    extraction = _make_extraction()
    feishu = _mock_feishu_client(record_id="rec-original-015")
    pipeline = _real_pipeline(feishu, extraction)

    import app.pipeline as pipeline_mod

    base_time = [1000.0]
    original_time = pipeline_mod.time.time

    def fake_time():
        return base_time[0]

    pipeline_mod.time.time = fake_time
    try:
        async with lifespan(app):
            app.state.feishu_client = feishu
            app.state.settings = _enabled_settings()
            app.state.ai_pipeline = pipeline
            app.state.ai_registry = _mock_registry()

            async with _client() as client:
                first = await _post(client)
                base_time[0] = 1000.0 + 301.0
                second = await _post(client)
    finally:
        pipeline_mod.time.time = original_time

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["ai_status"] == "succeeded"
    assert second.json()["ai_status"] == "succeeded"

    assert feishu.create_record.await_count == 2
    token_a = feishu.create_record.call_args_list[0].args[3]
    token_b = feishu.create_record.call_args_list[1].args[3]

    # alias comes from the conftest-pinned legacy target (default alias "default"
    # per TargetRegistry legacy mode). Reconstruct the expected token.
    expected_key = hashlib.sha256(
        f"default:{VALID_PAYLOAD['original_text']}".encode()
    ).hexdigest()[:32]
    expected_token = f"{expected_key[:8]}-{expected_key[8:12]}-{expected_key[12:16]}-{expected_key[16:20]}-{expected_key[20:32]}"

    assert token_a == token_b
    assert token_a == expected_token
    UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    assert UUID_RE.match(token_a)
