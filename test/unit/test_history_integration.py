# test_history_integration.py — End-to-end integration tests for AI request history
#
# Covers todo 6 of the admin-request-history plan (integration + docs todo):
#   - End-to-end: 3 webhook requests whose mocked pipeline yields
#     succeeded / failed / duplicate, then GET /admin/ai/history returns all
#     3 entries newest-first with complete meta fields
#   - Failure/summary passthrough: the failed entry's error and the succeeded
#     entry's summary round-trip from PipelineResult through the db to the API
#   - Restart semantics: a brand-new HistoryStore instance on the same file
#     replays the same 3 rows from disk (cross-connection persistence)
#   - Privacy: no secret values (AI_API_KEY / webhook token) leak into the
#     serialized history API response
#   - Contract regression: the webhook response NEVER carries error/summary keys
#
# Strategy mirrors test/unit/test_history_api.py (which itself mirrors
# test/unit/test_webhook_ai.py:36-90):
#   - conftest.py pins AI_ENABLED=false + AI_HISTORY_DB_PATH="" BEFORE app
#     import, so tests enable the AI stage via dataclasses.replace on
#     get_settings() and attach every AI-stage component manually to app.state.
#   - The mock pipeline uses side_effect=[r1, r2, r3] so 3 consecutive webhook
#     requests produce 3 distinct PipelineResults (test_history_api.py uses a
#     single return_value — the side_effect list is the integration delta).
#   - Auth probes ride the SAME replaced settings object as the functional
#     pipeline: webhook auth and the X-Admin-Token check both read
#     request.app.state.settings, so probe secrets are guaranteed to be the
#     only ones in play — any leak would show up in the response scan.

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
from httpx import ASGITransport

from app.config import get_settings
from app.history_store import HistoryStore
from app.main import app, lifespan
from app.pipeline import PipelineResult


TEST_BASE_URL = "http://testserver"

# Probe secrets planted into the replaced settings. The privacy scan asserts
# none of these values ever appear in the history API response.
PROBE_AI_API_KEY = "sk-test-secret-key"
PROBE_WEBHOOK_TOKEN = "whsec-test-token"
ADMIN_TOKEN = "test-admin-token"

ORIGINAL_TEXTS = {
    "succeeded": "瑞幸咖啡 ¥18.9 微信支付 2026-10-12",
    "failed": "麦当劳 ¥42 支付宝 2026-10-12",
    "duplicate": "山姆会员店 ¥298 微信支付 2026-10-12",
}

SUCCEEDED_SUMMARY = "瑞幸拿铁 18.9 元"
FAILED_ERROR = "extract request: boom"


def _mock_feishu_client(record_id: str = "rec-original-001") -> AsyncMock:
    """Copied from test_history_api.py: update_original_text returns a record_id."""
    feishu = AsyncMock()
    feishu.update_original_text = AsyncMock(return_value=record_id)
    return feishu


def _mock_pipeline_with_sequence(results: list[PipelineResult]) -> AsyncMock:
    """One mocked pipeline whose run() returns a distinct result per request."""
    pipeline = AsyncMock()
    pipeline.run = AsyncMock(side_effect=results)
    return pipeline


def _mock_registry() -> AsyncMock:
    """Copied from test_history_api.py: maybe_reload no-op + stub snapshot."""
    from app.ai_profile import AiProfile
    from app.field_codec import FieldSpec

    registry = AsyncMock()
    registry.maybe_reload = AsyncMock(return_value=None)
    stub_profile = AiProfile(
        prompt_header="stub",
        summary_field="精简原始数据",
        bill_app_token="bascn-stub",
        bill_table_id="tbl-stub",
        fields=(
            FieldSpec(
                ai_key="summary",
                feishu_field="精简原始数据",
                type="text",
                target="extract",
            ),
        ),
    )
    snapshot = MagicMock()
    snapshot.profile = stub_profile
    snapshot.option_whitelists = {}
    registry.get_snapshot = MagicMock(return_value=snapshot)
    return registry


def _history_settings(db_path: Path):
    """Frozen settings: AI on + history db + admin auth + probe secrets.

    config_reload_token is required for the X-Admin-Token gate (conftest does
    not pin it). ai_model gives the history "model" column a non-trivial value.
    """
    return replace(
        get_settings(),
        ai_enabled=True,
        ai_history_db_path=db_path,
        config_reload_token=ADMIN_TOKEN,
        ai_model="test-model",
        ai_api_key=PROBE_AI_API_KEY,
        webhook_shared_token=PROBE_WEBHOOK_TOKEN,
    )


def _install_ai_stage(app, db_path: Path, results: list[PipelineResult]) -> None:
    """Wire mocked AI components + a real HistoryStore on a tmp sqlite file."""
    app.state.feishu_client = _mock_feishu_client()
    app.state.settings = _history_settings(db_path)
    app.state.history_store = HistoryStore(db_path)
    app.state.ai_pipeline = _mock_pipeline_with_sequence(results)
    app.state.ai_registry = _mock_registry()


def _succeeded_result() -> PipelineResult:
    return PipelineResult(
        ai_status="succeeded",
        bill_record_id="recBill1",
        warnings=["flow fallback"],
        extracted={"amount": 18.9, "category": "餐饮"},
        dedup_hit=False,
        summary=SUCCEEDED_SUMMARY,
    )


def _failed_result() -> PipelineResult:
    return PipelineResult(
        ai_status="failed",
        bill_record_id=None,
        warnings=[],
        extracted={},
        dedup_hit=False,
        error=FAILED_ERROR,
    )


def _duplicate_result() -> PipelineResult:
    return PipelineResult(
        ai_status="duplicate",
        bill_record_id=None,
        warnings=[],
        extracted={},
        dedup_hit=True,
    )


async def _post_webhook(client: httpx.AsyncClient, text: str) -> httpx.Response:
    return await client.post(
        "/v1/webhook/ocr",
        json={"original_text": text},
        headers={"X-Webhook-Token": PROBE_WEBHOOK_TOKEN},
    )


# ─── ① End-to-end: 3 requests → 3 recorded entries, newest first ────────────


async def test_three_webhook_requests_recorded_and_listed_newest_first(tmp_path):
    """
    GIVEN AI is on with a history db attached
       AND the mocked pipeline returns succeeded / failed / duplicate in order
    WHEN 3 webhook POSTs are sent with valid payloads + tokens
    THEN every webhook response is 200
      AND GET /admin/ai/history returns 200 with exactly 3 items
      AND items are newest-first (last request's status first)
      AND the status multiset is {"succeeded", "failed", "duplicate"}
      AND every item carries non-empty meta: model / elapsed_ms / ts_iso / request_id / original_text
    """
    db_path = tmp_path / "h.db"
    results = [_succeeded_result(), _failed_result(), _duplicate_result()]

    async with lifespan(app):
        _install_ai_stage(app, db_path, results)

        async with httpx.AsyncClient(transport=ASGITransport(app), base_url=TEST_BASE_URL) as client:
            webhook_responses = [
                await _post_webhook(client, ORIGINAL_TEXTS[key]) for key in ("succeeded", "failed", "duplicate")
            ]
            history_response = await client.get(
                "/admin/ai/history",
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    for response in webhook_responses:
        assert response.status_code == 200

    assert history_response.status_code == 200
    body = history_response.json()
    assert body["success"] is True
    assert body["limit"] == 200
    items = body["items"]
    assert len(items) == 3

    # Newest first: the duplicate (3rd request) must be items[0].
    assert [item["ai_status"] for item in items] == ["duplicate", "failed", "succeeded"]
    assert {item["ai_status"] for item in items} == {"succeeded", "failed", "duplicate"}

    for item in items:
        assert item["model"] == "test-model"
        assert item["elapsed_ms"] >= 0
        assert item["ts_iso"]
        assert item["request_id"]
        assert item["original_text"] in ORIGINAL_TEXTS.values()

    # Per-status original_text round-trip proves entries map to the right request.
    status_to_text = {item["ai_status"]: item["original_text"] for item in items}
    for key in ("succeeded", "failed", "duplicate"):
        assert status_to_text[key] == ORIGINAL_TEXTS[key]


# ─── ② Failure reason / summary passthrough from the API response ───────────


async def test_failed_error_and_succeeded_summary_round_trip_through_api(tmp_path):
    """
    GIVEN the same 3-request setup as the end-to-end flow
    WHEN GET /admin/ai/history is read
    THEN the failed item's error equals the PipelineResult error verbatim
      AND the succeeded item's summary equals the extraction summary verbatim
    """
    db_path = tmp_path / "h.db"
    results = [_succeeded_result(), _failed_result(), _duplicate_result()]

    async with lifespan(app):
        _install_ai_stage(app, db_path, results)

        async with httpx.AsyncClient(transport=ASGITransport(app), base_url=TEST_BASE_URL) as client:
            for key in ("succeeded", "failed", "duplicate"):
                await _post_webhook(client, ORIGINAL_TEXTS[key])
            history_response = await client.get(
                "/admin/ai/history",
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert history_response.status_code == 200
    items = history_response.json()["items"]
    by_status = {item["ai_status"]: item for item in items}
    assert by_status["failed"]["error"] == FAILED_ERROR
    assert by_status["succeeded"]["summary"] == SUCCEEDED_SUMMARY
    # duplicate fills neither field (pipeline keeps it that way)
    assert by_status["duplicate"]["error"] is None
    assert by_status["duplicate"]["summary"] is None


# ─── ③ Restart semantics: independent store replays the same rows ───────────


async def test_independent_store_instance_replays_same_rows_from_disk(tmp_path):
    """
    GIVEN 3 webhook requests were recorded through the running app
    WHEN a brand-new HistoryStore is opened on the same db file (simulating a
         process restart — no shared in-memory state with the app's store)
    THEN list_recent(10) returns the same 3 entries newest-first
      AND their statuses / original texts match the live session's rows
    """
    db_path = tmp_path / "h.db"
    results = [_succeeded_result(), _failed_result(), _duplicate_result()]

    async with lifespan(app):
        _install_ai_stage(app, db_path, results)

        async with httpx.AsyncClient(transport=ASGITransport(app), base_url=TEST_BASE_URL) as client:
            for key in ("succeeded", "failed", "duplicate"):
                await _post_webhook(client, ORIGINAL_TEXTS[key])

    # Opened AFTER lifespan shutdown — a fresh connection replaying from disk.
    restarted_entries = HistoryStore(db_path).list_recent(10)
    assert len(restarted_entries) == 3
    assert [entry["ai_status"] for entry in restarted_entries] == [
        "duplicate",
        "failed",
        "succeeded",
    ]
    assert {entry["original_text"] for entry in restarted_entries} == set(ORIGINAL_TEXTS.values())


# ─── ④ Privacy: no secret values in the history API response ────────────────


async def test_history_response_contains_no_secret_values(tmp_path):
    """
    GIVEN settings carry probe secrets (ai_api_key="sk-test-secret-key",
          webhook_shared_token="whsec-test-token") and the 3 requests ran
    WHEN GET /admin/ai/history is sent with a valid X-Admin-Token
    THEN the response is 200
      AND json.dumps(items) contains NEITHER probe secret value
      AND the response contains no "api_key" / "token" keys at all
    """
    db_path = tmp_path / "h.db"
    results = [_succeeded_result(), _failed_result(), _duplicate_result()]

    async with lifespan(app):
        _install_ai_stage(app, db_path, results)

        async with httpx.AsyncClient(transport=ASGITransport(app), base_url=TEST_BASE_URL) as client:
            for key in ("succeeded", "failed", "duplicate"):
                await _post_webhook(client, ORIGINAL_TEXTS[key])
            history_response = await client.get(
                "/admin/ai/history",
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert history_response.status_code == 200
    serialized = json.dumps(history_response.json(), ensure_ascii=False)
    assert PROBE_AI_API_KEY not in serialized
    assert PROBE_WEBHOOK_TOKEN not in serialized

    item_keys = {key for item in history_response.json()["items"] for key in item}
    assert "api_key" not in item_keys
    assert "token" not in item_keys


# ─── ⑤ Contract regression: webhook response never gains error/summary ──────


async def test_webhook_response_contract_has_no_error_or_summary_keys(tmp_path):
    """
    GIVEN AI is on and the mocked pipeline returns a succeeded result with
          summary + error fields set to non-None values
    WHEN POST /v1/webhook/ocr is sent with a valid payload + token
    THEN the response JSON keys are exactly
           {success, request_id, record_id, book_alias, message,
            ai_status, ai_record_id, ai_extracted}
      AND NEITHER "error" NOR "summary" is a top-level key (history-only data)
    """
    db_path = tmp_path / "h.db"
    result = PipelineResult(
        ai_status="succeeded",
        bill_record_id="recBill1",
        warnings=[],
        extracted={"amount": 18.9, "category": "餐饮"},
        dedup_hit=False,
        summary=SUCCEEDED_SUMMARY,
        error=FAILED_ERROR,  # set even on success: proves the route never echoes it
    )

    async with lifespan(app):
        _install_ai_stage(app, db_path, [result])

        async with httpx.AsyncClient(transport=ASGITransport(app), base_url=TEST_BASE_URL) as client:
            response = await _post_webhook(client, ORIGINAL_TEXTS["succeeded"])

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "success",
        "request_id",
        "record_id",
        "book_alias",
        "message",
        "ai_status",
        "ai_record_id",
        "ai_extracted",
    }
    assert "error" not in body
    assert "summary" not in body
