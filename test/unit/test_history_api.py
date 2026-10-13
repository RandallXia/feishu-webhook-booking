# test_history_api.py — Behavior tests for AI request history wiring (app/main.py)
#
# Covers todo 3 of the admin-request-history plan:
#   - Webhook recording hooks: succeeded / failed / duplicate / registry-503
#     paths each persist exactly one HistoryEntry with the right fields
#   - Pending lifecycle (schema v2): the webhook inserts a pending row BEFORE
#     pipeline.run starts and finishes the SAME row on completion — while the
#     pipeline is suspended, GET /admin/ai/history already shows the in-flight
#     row; after it completes, the row reads back as terminal (succeeded,
#     pending=False) — so a completed request never shows a pending chip
#   - Contract regression: webhook success response NEVER gains top-level
#     "error"/"summary" keys (history-only data stays out of the response)
#   - GET /admin/ai/history: 401 / 404 AI_DISABLED / 404 HISTORY_DISABLED /
#     422 limit bounds / limit 1..1000 accepted / empty db → items: []
#
# Strategy (mirrors test/unit/test_webhook_ai.py:36-90):
#   - Mock helpers (_mock_feishu_client / _mock_pipeline / _mock_registry /
#     _enabled_settings) are copied from test_webhook_ai.py and extended with
#     ai_history_db_path + config_reload_token via dataclasses.replace.
#   - HistoryStore is attached MANUALLY on app.state — tests never rely on
#     lifespan's env reading (conftest pins AI_ENABLED=false before import),
#     which bypasses the get_settings cache complexity entirely.
#   - Assertions on recorded rows use a SEPARATE HistoryStore instance opened
#     on the same db file (WAL + committed inserts → rows are visible).

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
from httpx import ASGITransport

from app.config import get_settings
from app.history_store import HistoryStore
from app.main import app, lifespan
from app.pipeline import PipelineResult


WEBHOOK_TOKEN = "test-webhook-token"
ADMIN_TOKEN = "test-admin-token"
VALID_PAYLOAD = {"original_text": "麦当劳 ¥42 微信支付 2026-08-28"}
TEST_BASE_URL = "http://testserver"
ADMIN_HEADERS = {"X-Admin-Token": ADMIN_TOKEN}


def _mock_feishu_client(record_id: str = "rec-original-001") -> AsyncMock:
    """Copied from test_webhook_ai.py: update_original_text returns a record_id."""
    feishu = AsyncMock()
    feishu.update_original_text = AsyncMock(return_value=record_id)
    return feishu


def _mock_pipeline(result: PipelineResult) -> AsyncMock:
    """Copied from test_webhook_ai.py: run() returns the given PipelineResult."""
    pipeline = AsyncMock()
    pipeline.run = AsyncMock(return_value=result)
    return pipeline


def _mock_registry() -> AsyncMock:
    """Copied from test_webhook_ai.py: maybe_reload no-op + stub snapshot."""
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
    """Frozen Settings: AI on + history db + admin auth (extends test_webhook_ai._enabled_settings).

    config_reload_token is required because conftest does NOT pin it — without
    it _require_admin_auth raises 404 RELOAD_DISABLED before any handler logic.
    ai_model is set so the history "model" column assertion is non-trivial.
    """
    return replace(
        get_settings(),
        ai_enabled=True,
        ai_history_db_path=db_path,
        config_reload_token=ADMIN_TOKEN,
        ai_model="test-model",
    )


def _install_ai_stage(app, tmp_path: Path, result: PipelineResult) -> None:
    """Wire mocked AI components + a real HistoryStore on a tmp sqlite file."""
    db_path = tmp_path / "h.db"
    app.state.feishu_client = _mock_feishu_client()
    app.state.settings = _history_settings(db_path)
    app.state.history_store = HistoryStore(db_path)
    app.state.ai_pipeline = _mock_pipeline(result)
    app.state.ai_registry = _mock_registry()


def _read_history(db_path: Path) -> list[dict]:
    """Independent store instance on the same file — proves rows are persisted."""
    return HistoryStore(db_path).list_recent(10)


# ─── Recording path ①: pipeline succeeded ────────────────────────────────────


async def test_history_records_succeeded_pipeline_result(tmp_path):
    """
    GIVEN AI is on with a history db attached
       AND the mocked pipeline returns PipelineResult(ai_status="succeeded", ...)
    WHEN POST /v1/webhook/ocr is sent with a valid payload + token
    THEN the response status is 200
      AND the history db holds exactly 1 entry
      AND entry.ai_status == "succeeded"
      AND entry.model == settings.ai_model
      AND entry.original_text == the webhook payload text
      AND entry.elapsed_ms >= 0
      AND entry.summary / extracted / warnings round-trip through the db
      AND entry.pending is False (the row was finished — a completed request
          never shows a pending chip)
    """
    result = PipelineResult(
        ai_status="succeeded",
        bill_record_id="rec123",
        warnings=["flow fallback"],
        extracted={"amount": 42.0, "category": "餐饮"},
        dedup_hit=False,
        summary="麦当劳午餐",
    )

    async with lifespan(app):
        _install_ai_stage(app, tmp_path, result)

        async with httpx.AsyncClient(transport=ASGITransport(app), base_url=TEST_BASE_URL) as client:
            response = await client.post(
                "/v1/webhook/ocr",
                json=VALID_PAYLOAD,
                headers={"X-Webhook-Token": WEBHOOK_TOKEN},
            )

    assert response.status_code == 200
    entries = _read_history(tmp_path / "h.db")
    assert len(entries) == 1
    entry = entries[0]
    assert entry["ai_status"] == "succeeded"
    assert entry["model"] == "test-model"
    assert entry["original_text"] == VALID_PAYLOAD["original_text"]
    assert entry["elapsed_ms"] >= 0
    assert entry["bill_record_id"] == "rec123"
    assert entry["summary"] == "麦当劳午餐"
    assert entry["extracted"]["amount"] == 42.0
    assert entry["warnings"] == ["flow fallback"]
    assert entry["pending"] is False
    assert entry["request_id"]
    assert entry["ts_iso"]


# ─── Pending visibility: in-flight row readable BEFORE pipeline completes ────


async def test_inflight_request_visible_as_pending_then_finished(tmp_path):
    """
    GIVEN AI is on with a history db attached
       AND the mocked pipeline.run suspends on an asyncio.Event (never set
           until the test releases it)
    WHEN POST /v1/webhook/ocr is dispatched (created as a task, not awaited)
      AND the event loop yields (~50 ms) so the pending insert has committed
      AND GET /admin/ai/history is called while the pipeline is still running
    THEN the history response holds exactly 1 item with ai_status == "pending"
       AND item.pending is True (the in-flight request is visible server-side)
    WHEN the gate is released and the webhook task completes
      AND GET /admin/ai/history is called again
    THEN it still holds exactly 1 item (same row, finished — not re-inserted)
       AND item.ai_status == "succeeded" and item.pending is False
    """
    result = PipelineResult(
        ai_status="succeeded",
        bill_record_id="rec-gate",
        warnings=[],
        extracted={"amount": 42.0},
        dedup_hit=False,
        summary="麦当劳午餐",
    )
    gate = asyncio.Event()

    async def _gated_run(**kwargs):
        await gate.wait()
        return result

    async with lifespan(app):
        db_path = tmp_path / "h.db"
        app.state.feishu_client = _mock_feishu_client()
        app.state.settings = _history_settings(db_path)
        app.state.history_store = HistoryStore(db_path)
        app.state.ai_pipeline = AsyncMock()
        app.state.ai_pipeline.run = AsyncMock(side_effect=_gated_run)
        app.state.ai_registry = _mock_registry()

        async with httpx.AsyncClient(transport=ASGITransport(app), base_url=TEST_BASE_URL) as client:
            webhook_task = asyncio.create_task(
                client.post(
                    "/v1/webhook/ocr",
                    json=VALID_PAYLOAD,
                    headers={"X-Webhook-Token": WEBHOOK_TOKEN},
                )
            )
            await asyncio.sleep(0.05)

            inflight = await client.get("/admin/ai/history", headers=ADMIN_HEADERS)
            assert inflight.status_code == 200
            items = inflight.json()["items"]
            assert len(items) == 1
            assert items[0]["ai_status"] == "pending"
            assert items[0]["pending"] is True

            gate.set()
            webhook_response = await webhook_task

            assert webhook_response.status_code == 200
            finished = await client.get("/admin/ai/history", headers=ADMIN_HEADERS)
            assert finished.status_code == 200
            items = finished.json()["items"]
            assert len(items) == 1
            assert items[0]["ai_status"] == "succeeded"
            assert items[0]["pending"] is False
            assert items[0]["bill_record_id"] == "rec-gate"


# ─── Recording path ②: pipeline failed (error preserved) ─────────────────────

async def test_history_records_failed_pipeline_result_with_error(tmp_path):
    """
    GIVEN AI is on with a history db attached
       AND the mocked pipeline returns PipelineResult(ai_status="failed", error="extract request: boom")
    WHEN POST /v1/webhook/ocr is sent with a valid payload + token
    THEN the response status is 200 (AI failure never breaks webhook semantics)
      AND the history db holds exactly 1 entry
      AND entry.ai_status == "failed"
      AND entry.error == "extract request: boom"
    """
    result = PipelineResult(
        ai_status="failed",
        bill_record_id=None,
        warnings=[],
        extracted={},
        dedup_hit=False,
        error="extract request: boom",
    )

    async with lifespan(app):
        _install_ai_stage(app, tmp_path, result)

        async with httpx.AsyncClient(transport=ASGITransport(app), base_url=TEST_BASE_URL) as client:
            response = await client.post(
                "/v1/webhook/ocr",
                json=VALID_PAYLOAD,
                headers={"X-Webhook-Token": WEBHOOK_TOKEN},
            )

    assert response.status_code == 200
    entries = _read_history(tmp_path / "h.db")
    assert len(entries) == 1
    entry = entries[0]
    assert entry["ai_status"] == "failed"
    assert entry["error"] == "extract request: boom"
    assert entry["bill_record_id"] is None


# ─── Recording path ③: pipeline duplicate ────────────────────────────────────


async def test_history_records_duplicate_pipeline_result(tmp_path):
    """
    GIVEN AI is on with a history db attached
       AND the mocked pipeline returns PipelineResult(ai_status="duplicate", dedup_hit=True)
    WHEN POST /v1/webhook/ocr is sent with a valid payload + token
    THEN the response status is 200
      AND the history db holds exactly 1 entry with ai_status == "duplicate"
      AND entry.error / entry.summary are None (duplicate fills neither)
    """
    result = PipelineResult(
        ai_status="duplicate",
        bill_record_id=None,
        warnings=[],
        extracted={},
        dedup_hit=True,
    )

    async with lifespan(app):
        _install_ai_stage(app, tmp_path, result)

        async with httpx.AsyncClient(transport=ASGITransport(app), base_url=TEST_BASE_URL) as client:
            response = await client.post(
                "/v1/webhook/ocr",
                json=VALID_PAYLOAD,
                headers={"X-Webhook-Token": WEBHOOK_TOKEN},
            )

    assert response.status_code == 200
    entries = _read_history(tmp_path / "h.db")
    assert len(entries) == 1
    entry = entries[0]
    assert entry["ai_status"] == "duplicate"
    assert entry["error"] is None
    assert entry["summary"] is None


# ─── Recording path ④: registry 503 (recorded BEFORE the raise) ──────────────


async def test_history_records_registry_unavailable_before_503(tmp_path):
    """
    GIVEN AI is on with a history db attached
       AND the mocked registry's get_snapshot raises AiProfileRegistryUnavailableError
    WHEN POST /v1/webhook/ocr is sent with a valid payload + token
    THEN the response status is 503 with error.code == "AI_PROFILE_UNAVAILABLE"
      AND the history db holds exactly 1 entry
      AND entry.ai_status == "failed"
      AND entry.error == "profile registry unavailable"
    """
    from app.ai_profile import AiProfileRegistryUnavailableError

    async with lifespan(app):
        _install_ai_stage(app, tmp_path, PipelineResult(
            ai_status="succeeded",
            bill_record_id="should-not-happen",
            warnings=[],
            extracted={},
            dedup_hit=False,
        ))
        app.state.ai_registry.get_snapshot = MagicMock(
            side_effect=AiProfileRegistryUnavailableError("fail-closed")
        )

        async with httpx.AsyncClient(transport=ASGITransport(app), base_url=TEST_BASE_URL) as client:
            response = await client.post(
                "/v1/webhook/ocr",
                json=VALID_PAYLOAD,
                headers={"X-Webhook-Token": WEBHOOK_TOKEN},
            )

    assert response.status_code == 503
    assert response.json()["detail"]["error"]["code"] == "AI_PROFILE_UNAVAILABLE"
    entries = _read_history(tmp_path / "h.db")
    assert len(entries) == 1
    entry = entries[0]
    assert entry["ai_status"] == "failed"
    assert entry["error"] == "profile registry unavailable"
    assert entry["bill_record_id"] is None


# ─── Contract regression: response body never carries history-only data ─────


async def test_webhook_success_response_contract_has_no_error_or_summary_keys(tmp_path):
    """
    GIVEN AI is on and the mocked pipeline returns a succeeded result
    WHEN POST /v1/webhook/ocr is sent with a valid payload + token
    THEN the response JSON keys are exactly
           {success, request_id, record_id, book_alias, message, ai_status, ai_record_id, ai_extracted}
      AND NEITHER "error" NOR "summary" is a top-level key (history-only data)
    """
    result = PipelineResult(
        ai_status="succeeded",
        bill_record_id="rec123",
        warnings=[],
        extracted={"amount": 42.0, "category": "餐饮"},
        dedup_hit=False,
        summary="麦当劳午餐",
    )

    async with lifespan(app):
        _install_ai_stage(app, tmp_path, result)

        async with httpx.AsyncClient(transport=ASGITransport(app), base_url=TEST_BASE_URL) as client:
            response = await client.post(
                "/v1/webhook/ocr",
                json=VALID_PAYLOAD,
                headers={"X-Webhook-Token": WEBHOOK_TOKEN},
            )

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


# ─── GET /admin/ai/history: auth + gating ────────────────────────────────────


async def test_unauthorized_history_request_returns_401():
    """
    GIVEN settings carry a config_reload_token
       AND the request has NO X-Admin-Token header
    WHEN GET /admin/ai/history is sent
    THEN the response status is 401 with error.code == "UNAUTHORIZED"
    """
    async with lifespan(app):
        app.state.settings = _history_settings(Path("/tmp/unused-history.db"))

        async with httpx.AsyncClient(transport=ASGITransport(app), base_url=TEST_BASE_URL) as client:
            response = await client.get("/admin/ai/history")

    assert response.status_code == 401
    assert response.json()["detail"]["error"]["code"] == "UNAUTHORIZED"


async def test_ai_disabled_history_returns_404():
    """
    GIVEN AI_ENABLED is false (default conftest env) but admin auth passes
    WHEN GET /admin/ai/history is sent with a valid X-Admin-Token
    THEN the response status is 404 with error.code == "AI_DISABLED"
    """
    async with lifespan(app):
        app.state.settings = replace(get_settings(), config_reload_token=ADMIN_TOKEN)

        async with httpx.AsyncClient(transport=ASGITransport(app), base_url=TEST_BASE_URL) as client:
            response = await client.get("/admin/ai/history", headers=ADMIN_HEADERS)

    assert response.status_code == 404
    body = response.json()
    assert body["detail"]["error"]["code"] == "AI_DISABLED"
    assert body["detail"]["error"]["message"] == "AI stage is disabled"


async def test_history_disabled_returns_404_when_store_missing(tmp_path):
    """
    GIVEN AI is on and ai_history_db_path is set via replace
       AND app.state.history_store is None (operator gate / no store attached)
    WHEN GET /admin/ai/history is sent with a valid X-Admin-Token
    THEN the response status is 404 with error.code == "HISTORY_DISABLED"
      AND error.message == "request history is not configured"
    """
    async with lifespan(app):
        app.state.settings = _history_settings(tmp_path / "h.db")
        app.state.ai_registry = _mock_registry()
        # history_store stays None (lifespan's AI-disabled else-branch set it)

        async with httpx.AsyncClient(transport=ASGITransport(app), base_url=TEST_BASE_URL) as client:
            response = await client.get("/admin/ai/history", headers=ADMIN_HEADERS)

    assert response.status_code == 404
    body = response.json()
    assert body["detail"]["error"]["code"] == "HISTORY_DISABLED"
    assert body["detail"]["error"]["message"] == "request history is not configured"


# ─── GET /admin/ai/history: limit validation ─────────────────────────────────


async def test_limit_below_minimum_rejected_422():
    """
    GIVEN the history route validates limit with Query(ge=1)
    WHEN GET /admin/ai/history?limit=0 is sent
    THEN the response status is 422 (pydantic validation, handler never runs)
    """
    async with lifespan(app):
        app.state.settings = _history_settings(Path("/tmp/unused-history.db"))

        async with httpx.AsyncClient(transport=ASGITransport(app), base_url=TEST_BASE_URL) as client:
            response = await client.get("/admin/ai/history", params={"limit": 0}, headers=ADMIN_HEADERS)

    assert response.status_code == 422


async def test_limit_above_maximum_rejected_422():
    """
    GIVEN the history route validates limit with Query(le=1000)
    WHEN GET /admin/ai/history?limit=1001 is sent
    THEN the response status is 422
    """
    async with lifespan(app):
        app.state.settings = _history_settings(Path("/tmp/unused-history.db"))

        async with httpx.AsyncClient(transport=ASGITransport(app), base_url=TEST_BASE_URL) as client:
            response = await client.get("/admin/ai/history", params={"limit": 1001}, headers=ADMIN_HEADERS)

    assert response.status_code == 422


async def test_limit_boundary_values_accepted(tmp_path):
    """
    GIVEN AI is on with an empty history db attached
    WHEN GET /admin/ai/history is sent with limit=1, then limit=1000
    THEN both responses are 200
      AND each echoes {"success": true, "items": [], "limit": <N>}
    """
    async with lifespan(app):
        app.state.settings = _history_settings(tmp_path / "h.db")
        app.state.ai_registry = _mock_registry()
        app.state.history_store = HistoryStore(tmp_path / "h.db")

        async with httpx.AsyncClient(transport=ASGITransport(app), base_url=TEST_BASE_URL) as client:
            for limit in (1, 1000):
                response = await client.get(
                    "/admin/ai/history", params={"limit": limit}, headers=ADMIN_HEADERS
                )
                assert response.status_code == 200
                assert response.json() == {"success": True, "items": [], "limit": limit}


async def test_empty_history_returns_empty_items(tmp_path):
    """
    GIVEN AI is on with a configured but never-written history db
    WHEN GET /admin/ai/history is sent with the default limit
    THEN the response status is 200
      AND the body is {"success": true, "items": [], "limit": 200}
    """
    async with lifespan(app):
        app.state.settings = _history_settings(tmp_path / "h.db")
        app.state.ai_registry = _mock_registry()
        app.state.history_store = HistoryStore(tmp_path / "h.db")

        async with httpx.AsyncClient(transport=ASGITransport(app), base_url=TEST_BASE_URL) as client:
            response = await client.get("/admin/ai/history", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"success": True, "items": [], "limit": 200}
