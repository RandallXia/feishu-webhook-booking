"""Real tests for FeishuClient picker methods (list_tables, list_records).

list_records request shape is verified against the official Feishu search endpoint
(POST /open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search):
page_token + page_size are QUERY params; the body is a JSON object whose fields
(view_id / field_names / sort / filter / automatic_fields) are all OPTIONAL, so an
empty body {} is the minimum legal shape.
"""

import json

import httpx
import pytest

from app.config import get_settings
from app.feishu_client import FeishuClient, FeishuClientError


def _patch_transport(monkeypatch, handler):
    """Inject httpx.MockTransport into httpx.AsyncClient via __init__ monkeypatch.

    Mirrors the pattern in test_feishu_client_ext.py.
    """
    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)


def _token_response() -> httpx.Response:
    return httpx.Response(200, json={"tenant_access_token": "test-token", "expire": 7200})


def _is_token_request(request: httpx.Request) -> bool:
    return "tenant_access_token/internal" in str(request.url)


# ─── list_tables ──────────────────────────────────────────────────────────


async def test_list_tables_single_page(monkeypatch):
    # Given: a FeishuClient with a mock returning one page (has_more=False)
    # When: list_tables(app_token) is called
    # Then: a single GET is made to /tables?page_size=100 with Bearer token
    # And: returns [{table_id, name}] for every item on the page
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_token_request(request):
            return _token_response()
        assert request.method == "GET"
        url = str(request.url)
        assert "/open-apis/bitable/v1/apps/app-A/tables" in url
        assert "page_size=100" in url
        assert "page_token" not in url
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "has_more": False,
                    "items": [
                        {"table_id": "tbl1", "name": "账单明细"},
                        {"table_id": "tbl2", "name": "原始信息"},
                    ],
                },
            },
        )

    _patch_transport(monkeypatch, handler)
    client = FeishuClient(get_settings())
    result = await client.list_tables("app-A")
    assert result == [
        {"table_id": "tbl1", "name": "账单明细"},
        {"table_id": "tbl2", "name": "原始信息"},
    ]


async def test_list_tables_multi_page_merges_all(monkeypatch):
    # Given: a mock where page 1 returns has_more=True + page_token=tok1
    # And: page 2 (with page_token=tok1) returns has_more=False
    # When: list_tables(app_token) is called
    # Then: both pages are fetched and their items are merged in order
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        if _is_token_request(request):
            return _token_response()
        nonlocal call_count
        call_count += 1
        url = str(request.url)
        assert "/open-apis/bitable/v1/apps/app-A/tables" in url
        assert "page_size=100" in url

        if call_count == 1:
            assert "page_token" not in url
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "has_more": True,
                        "page_token": "tok1",
                        "items": [{"table_id": "tbl1", "name": "page1"}],
                    },
                },
            )
        assert "page_token=tok1" in url
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "has_more": False,
                    "items": [
                        {"table_id": "tbl2", "name": "page2-a"},
                        {"table_id": "tbl3", "name": "page2-b"},
                    ],
                },
            },
        )

    _patch_transport(monkeypatch, handler)
    client = FeishuClient(get_settings())
    result = await client.list_tables("app-A")
    assert result == [
        {"table_id": "tbl1", "name": "page1"},
        {"table_id": "tbl2", "name": "page2-a"},
        {"table_id": "tbl3", "name": "page2-b"},
    ]
    assert call_count == 2


async def test_list_tables_code_non_zero_raises_error(monkeypatch):
    # Given: a mock returning HTTP 200 but code!=0
    # When: list_tables(app_token) is called
    # Then: FeishuClientError(stage="list_tables") is raised
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_token_request(request):
            return _token_response()
        return httpx.Response(200, json={"code": 1254040, "msg": "BaseTokenNotFound"})

    _patch_transport(monkeypatch, handler)
    client = FeishuClient(get_settings())
    with pytest.raises(FeishuClientError) as exc:
        await client.list_tables("app-A")
    assert exc.value.stage == "list_tables"


async def test_list_tables_non_200_raises_error(monkeypatch):
    # Given: a mock returning HTTP 500 for the tables endpoint
    # When: list_tables(app_token) is called
    # Then: FeishuClientError(stage="list_tables") is raised
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_token_request(request):
            return _token_response()
        return httpx.Response(500, text="boom")

    _patch_transport(monkeypatch, handler)
    client = FeishuClient(get_settings())
    with pytest.raises(FeishuClientError) as exc:
        await client.list_tables("app-A")
    assert exc.value.stage == "list_tables"


# ─── list_records ─────────────────────────────────────────────────────────


async def test_list_records_happy_path(monkeypatch):
    # Given: a FeishuClient with a mock returning one page of records
    # When: list_records(app_token, table_id) is called
    # Then: a POST is made to /records/search?page_size=50 with Bearer token
    # And: the request body is {} (minimum legal shape — all body fields optional)
    # And: returns {items, has_more, page_token} parsed from data
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if _is_token_request(request):
            return _token_response()
        assert request.method == "POST"
        url = str(request.url)
        assert "/open-apis/bitable/v1/apps/app-A/tables/tbl1/records/search" in url
        assert "page_size=50" in url
        assert request.headers["Authorization"] == "Bearer test-token"
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "has_more": False,
                    "items": [
                        {"record_id": "rec1", "fields": {"金额": 10.5, "类型": "餐饮"}},
                        {"record_id": "rec2", "fields": {"金额": 20.0, "类型": "交通"}},
                    ],
                    "total": 2,
                },
            },
        )

    _patch_transport(monkeypatch, handler)
    client = FeishuClient(get_settings())
    result = await client.list_records("app-A", "tbl1")
    assert captured["body"] == {}
    assert result["has_more"] is False
    assert result["page_token"] is None
    assert [item["record_id"] for item in result["items"]] == ["rec1", "rec2"]
    assert result["items"][0]["fields"] == {"金额": 10.5, "类型": "餐饮"}


async def test_list_records_page_token_passthrough(monkeypatch):
    # Given: a mock that echoes the incoming page_token query param in the response
    # When: list_records(app_token, table_id, page_token="tokX") is called
    # Then: page_token=tokX is sent as a query param on the POST
    # And: the returned page_token reflects the next page marker
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if _is_token_request(request):
            return _token_response()
        assert request.method == "POST"
        url = str(request.url)
        assert "page_token=tokX" in url
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "has_more": True,
                    "page_token": "tokY",
                    "items": [{"record_id": "rec3", "fields": {}}],
                },
            },
        )

    _patch_transport(monkeypatch, handler)
    client = FeishuClient(get_settings())
    result = await client.list_records("app-A", "tbl1", page_token="tokX")
    assert captured["body"] == {}
    assert result["has_more"] is True
    assert result["page_token"] == "tokY"
    assert result["items"] == [{"record_id": "rec3", "fields": {}}]


async def test_list_records_code_non_zero_raises_error(monkeypatch):
    # Given: a mock returning HTTP 200 but code!=0
    # When: list_records(app_token, table_id) is called
    # Then: FeishuClientError(stage="list_records") is raised
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_token_request(request):
            return _token_response()
        return httpx.Response(200, json={"code": 1254004, "msg": "WrongTableId"})

    _patch_transport(monkeypatch, handler)
    client = FeishuClient(get_settings())
    with pytest.raises(FeishuClientError) as exc:
        await client.list_records("app-A", "tbl1")
    assert exc.value.stage == "list_records"


async def test_list_records_non_200_raises_error(monkeypatch):
    # Given: a mock returning HTTP 500 for the search endpoint
    # When: list_records(app_token, table_id) is called
    # Then: FeishuClientError(stage="list_records") is raised
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_token_request(request):
            return _token_response()
        return httpx.Response(500, text="boom")

    _patch_transport(monkeypatch, handler)
    client = FeishuClient(get_settings())
    with pytest.raises(FeishuClientError) as exc:
        await client.list_records("app-A", "tbl1")
    assert exc.value.stage == "list_records"
