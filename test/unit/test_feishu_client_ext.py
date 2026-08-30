"""Real tests for FeishuClient extension methods (update_record_field, create_record, list_fields)."""

import json

import httpx
import pytest

from app.config import get_settings
from app.feishu_client import FeishuClient, FeishuClientError
from app.target_registry import FeishuTargetConfig


def _make_target():
    return FeishuTargetConfig(
        alias="default",
        year=None,
        app_token="test-app-token",
        table_id="test-table-id",
        record_id="test-record-id",
        original_field_name="\u539f\u59cb\u4fe1\u606f",
        enabled=True,
    )


def _patch_transport(monkeypatch, handler):
    """Inject httpx.MockTransport into httpx.AsyncClient via __init__ monkeypatch."""
    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)


def _token_response() -> httpx.Response:
    return httpx.Response(200, json={"tenant_access_token": "test-token", "expire": 7200})


def _is_token_request(request: httpx.Request) -> bool:
    return "tenant_access_token/internal" in str(request.url)


# ─── update_record_field ─────────────────────────────────────────────────


async def test_update_record_field_happy_path(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_token_request(request):
            return _token_response()
        # Assert PUT request shape
        assert request.method == "PUT"
        url = str(request.url)
        assert "test-app-token" in url
        assert "test-table-id" in url
        assert "test-record-id" in url
        body = json.loads(request.content)
        assert body["fields"] == {"\u6211\u7684\u5b57\u6bb5": "\u6211\u7684\u503c"}
        return httpx.Response(200, json={"code": 0, "data": {}})

    _patch_transport(monkeypatch, handler)
    client = FeishuClient(get_settings())
    target = _make_target()
    result = await client.update_record_field("\u6211\u7684\u5b57\u6bb5", "\u6211\u7684\u503c", target)
    assert result == "test-record-id"


async def test_update_record_field_non_200_raises_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_token_request(request):
            return _token_response()
        return httpx.Response(500, text="Internal Server Error")

    _patch_transport(monkeypatch, handler)
    client = FeishuClient(get_settings())
    target = _make_target()
    with pytest.raises(FeishuClientError) as exc:
        await client.update_record_field("f", "v", target)
    assert exc.value.stage == "update_record_field"
    assert exc.value.record_id == "test-record-id"


# ─── update_original_text ─────────────────────────────────────────────────


async def test_update_original_text_delegates_to_update_record_field(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_token_request(request):
            return _token_response()
        assert request.method == "PUT"
        url = str(request.url)
        assert "test-app-token" in url
        assert "test-table-id" in url
        assert "test-record-id" in url
        body = json.loads(request.content)
        # Delegates with original_field_name from target
        assert body["fields"] == {"\u539f\u59cb\u4fe1\u606f": "\u67d0\u4e9b\u539f\u59cb\u6587\u672c"}
        return httpx.Response(200, json={"code": 0, "data": {}})

    _patch_transport(monkeypatch, handler)
    client = FeishuClient(get_settings())
    target = _make_target()
    result = await client.update_original_text("\u67d0\u4e9b\u539f\u59cb\u6587\u672c", target)
    assert result == "test-record-id"


# ─── create_record ────────────────────────────────────────────────────────


async def test_create_record_happy_path(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_token_request(request):
            return _token_response()
        assert request.method == "POST"
        url = str(request.url)
        assert "test-app-token" in url
        assert "test-table-id" in url
        assert "client_token" not in url
        body = json.loads(request.content)
        assert body["fields"] == {"title": "hello"}
        return httpx.Response(
            200,
            json={"code": 0, "data": {"record": {"record_id": "new-rec-001"}}},
        )

    _patch_transport(monkeypatch, handler)
    client = FeishuClient(get_settings())
    result = await client.create_record(
        {"title": "hello"}, "test-app-token", "test-table-id"
    )
    assert result == "new-rec-001"


async def test_create_record_success_code_zero(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_token_request(request):
            return _token_response()
        assert request.method == "POST"
        return httpx.Response(
            200,
            json={"code": 0, "data": {"record": {"record_id": "existing-rec"}}},
        )

    _patch_transport(monkeypatch, handler)
    client = FeishuClient(get_settings())
    result = await client.create_record(
        {"title": "hello"}, "test-app-token", "test-table-id"
    )
    assert result == "existing-rec"


async def test_create_record_other_code_raises_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_token_request(request):
            return _token_response()
        return httpx.Response(200, json={"code": 999, "msg": "some error"})

    _patch_transport(monkeypatch, handler)
    client = FeishuClient(get_settings())
    with pytest.raises(FeishuClientError) as exc:
        await client.create_record(
            {"title": "hello"}, "test-app-token", "test-table-id"
        )
    assert exc.value.stage == "create_record"


async def test_create_record_http_500_raises_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_token_request(request):
            return _token_response()
        return httpx.Response(500, text="boom")

    _patch_transport(monkeypatch, handler)
    client = FeishuClient(get_settings())
    with pytest.raises(FeishuClientError) as exc:
        await client.create_record(
            {"title": "hello"}, "test-app-token", "test-table-id"
        )
    assert exc.value.stage == "create_record"


# ─── list_fields ──────────────────────────────────────────────────────────


async def test_list_fields_single_page(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_token_request(request):
            return _token_response()
        assert request.method == "GET"
        url = str(request.url)
        assert "test-app-token" in url
        assert "test-table-id" in url
        assert "page_size=100" in url
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "has_more": False,
                    "items": [
                        {"field_name": "name", "field_id": "f1", "type": 1},
                        {"field_name": "amount", "field_id": "f2", "type": 2},
                    ],
                },
            },
        )

    _patch_transport(monkeypatch, handler)
    client = FeishuClient(get_settings())
    result = await client.list_fields("test-app-token", "test-table-id")
    assert len(result) == 2
    assert result["name"]["field_id"] == "f1"
    assert result["amount"]["field_id"] == "f2"


async def test_list_fields_multi_page_merges_all(monkeypatch):
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        if _is_token_request(request):
            return _token_response()
        nonlocal call_count
        call_count += 1
        assert request.method == "GET"
        url = str(request.url)
        assert "test-app-token" in url
        assert "test-table-id" in url

        if call_count == 1:
            # First page: has_more=true, page_token set
            assert "page_token" not in url
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "has_more": True,
                        "page_token": "tok1",
                        "items": [
                            {"field_name": "name", "field_id": "f1", "type": 1},
                        ],
                    },
                },
            )
        # Second page: has_more=false, uses page_token from first
        assert "page_token=tok1" in url
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "has_more": False,
                    "items": [
                        {"field_name": "amount", "field_id": "f2", "type": 2},
                    ],
                },
            },
        )

    _patch_transport(monkeypatch, handler)
    client = FeishuClient(get_settings())
    result = await client.list_fields("test-app-token", "test-table-id")
    assert len(result) == 2
    assert result["name"]["field_id"] == "f1"
    assert result["amount"]["field_id"] == "f2"
    assert call_count == 2  # token + 2 pages = 3 calls, but token is cached after first call


async def test_list_fields_non_200_raises_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_token_request(request):
            return _token_response()
        return httpx.Response(500, text="boom")

    _patch_transport(monkeypatch, handler)
    client = FeishuClient(get_settings())
    with pytest.raises(FeishuClientError) as exc:
        await client.list_fields("test-app-token", "test-table-id")
    assert exc.value.stage == "list_fields"