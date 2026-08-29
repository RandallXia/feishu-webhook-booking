# test_picker_endpoints.py — Behavior tests for admin Feishu picker routes.
#
# Four routes (all mirror reload_config auth: X-Admin-Token + secrets.compare_digest;
# CONFIG_RELOAD_TOKEN unset → 404 RELOAD_DISABLED; bad token → 401):
#   - GET  /admin/feishu/tables?app_token=           → list_tables
#   - GET  /admin/feishu/fields?app_token=&table_id= → list_fields + type mapping
#   - GET  /admin/feishu/records?app_token=&table_id=&page_token=
#                                                      → list_records + preview
#   - POST /admin/feishu/parse-url                    → pure regex, no Feishu IO
#
# Strategy mirrors test_admin_ai.py:
#   - conftest pins AI_ENABLED="false"; tests just stub app.state.feishu_client.
#   - httpx.AsyncClient(transport=ASGITransport(app), base_url="http://testserver")
#     with the lifespan async context manager run directly.

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest
from httpx import ASGITransport

from app.config import get_settings
from app.feishu_client import FeishuClientError
from app.main import app, lifespan


ADMIN_TOKEN = "test-admin-token"
TEST_BASE_URL = "http://testserver"


def _enabled_settings() -> "object":
    return replace(get_settings(), config_reload_token=ADMIN_TOKEN)


def _mock_feishu() -> AsyncMock:
    """AsyncMock FeishuClient — picker methods configured per test."""
    feishu = AsyncMock()
    return feishu


# ─── Auth: mirrors reload_config on every picker route ─────────────────────


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/admin/feishu/tables?app_token=appA"),
        ("GET", "/admin/feishu/fields?app_token=appA&table_id=tbl1"),
        ("GET", "/admin/feishu/records?app_token=appA&table_id=tbl1"),
        ("POST", "/admin/feishu/parse-url"),
    ],
)
async def test_picker_no_token_returns_401(method, path):
    """
    GIVEN CONFIG_RELOAD_TOKEN is set
    WHEN a picker route is called with NO X-Admin-Token header
    THEN the response status is 401
      AND the error code is UNAUTHORIZED
    """
    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.feishu_client = _mock_feishu()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            if method == "GET":
                response = await client.get(path)
            else:
                response = await client.post(path, json={"url": "https://x.feishu.cn/base/A?table=t"})

    assert response.status_code == 401
    assert response.json()["detail"]["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/admin/feishu/tables?app_token=appA"),
        ("GET", "/admin/feishu/fields?app_token=appA&table_id=tbl1"),
        ("GET", "/admin/feishu/records?app_token=appA&table_id=tbl1"),
        ("POST", "/admin/feishu/parse-url"),
    ],
)
async def test_picker_reload_disabled_returns_404(method, path, monkeypatch):
    """
    GIVEN CONFIG_RELOAD_TOKEN is unset
    WHEN a picker route is called with any X-Admin-Token
    THEN the response status is 404
      AND the error code is RELOAD_DISABLED
    """
    async with lifespan(app):
        settings = replace(get_settings(), config_reload_token=None)
        app.state.settings = settings

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            if method == "GET":
                response = await client.get(path, headers={"X-Admin-Token": "anything"})
            else:
                response = await client.post(
                    path,
                    json={"url": "https://x.feishu.cn/base/A?table=t"},
                    headers={"X-Admin-Token": "anything"},
                )

    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "RELOAD_DISABLED"


# ─── GET /admin/feishu/tables ──────────────────────────────────────────────


async def test_get_tables_happy():
    """
    GIVEN app.state.feishu_client.list_tables returns [{table_id, name}]
    WHEN GET /admin/feishu/tables?app_token=appA is called with valid X-Admin-Token
    THEN the response status is 200
      AND the body is {"tables": [...]} with the list passed through verbatim
      AND list_tables was called with app_token="appA"
    """
    feishu = _mock_feishu()
    feishu.list_tables = AsyncMock(
        return_value=[{"table_id": "tbl1", "name": "账单"}, {"table_id": "tbl2", "name": "原始"}]
    )

    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.feishu_client = feishu

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get(
                "/admin/feishu/tables?app_token=appA",
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "tables": [
            {"table_id": "tbl1", "name": "账单"},
            {"table_id": "tbl2", "name": "原始"},
        ]
    }
    feishu.list_tables.assert_awaited_once_with("appA")


async def test_get_tables_upstream_error_returns_502():
    """
    GIVEN app.state.feishu_client.list_tables raises FeishuClientError
    WHEN GET /admin/feishu/tables?app_token=appA is called with valid X-Admin-Token
    THEN the response status is 502
      AND the error code is FEISHU_UPSTREAM_ERROR
    """
    feishu = _mock_feishu()
    feishu.list_tables = AsyncMock(
        side_effect=FeishuClientError("boom", stage="list_tables")
    )

    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.feishu_client = feishu

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get(
                "/admin/feishu/tables?app_token=appA",
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 502
    assert response.json()["detail"]["error"]["code"] == "FEISHU_UPSTREAM_ERROR"


# ─── GET /admin/feishu/fields ──────────────────────────────────────────────


def _fields_map() -> dict[str, dict]:
    """Mirror the real list_fields return shape: {field_name: field_def}."""
    return {
        "账单名": {
            "field_name": "账单名",
            "type": 1,
            "ui_type": "Text",
            "is_primary": True,
            "property": {},
        },
        "收支类型": {
            "field_name": "收支类型",
            "type": 3,
            "ui_type": "SingleSelect",
            "is_primary": False,
            "property": {
                "options": [
                    {"name": "支出"},
                    {"name": "收入"},
                ]
            },
        },
        "金额": {
            "field_name": "金额",
            "type": 2,
            "ui_type": "Number",
            "is_primary": False,
            "property": {},
        },
        "日期": {
            "field_name": "日期",
            "type": 5,
            "ui_type": "DateTime",
            "is_primary": False,
            "property": {},
        },
        "未知字段": {
            "field_name": "未知字段",
            "type": 999,
            "is_primary": False,
            "property": {},
        },
    }


async def test_get_fields_happy():
    """
    GIVEN app.state.feishu_client.list_fields returns a fields_map with text/number/
           single_select/date/unknown types and one is_primary field
    WHEN GET /admin/feishu/fields?app_token=appA&table_id=tbl1 is called
    THEN the response status is 200
      AND the body is {"fields": [...]} with type mapping:
        - SingleSelect ui_type → "single_select" + options=[...]
        - Text → "text", Number → "number", DateTime → "date", unknown → "unknown"
      AND every field carries is_primary + options (null when not single_select)
      AND list_fields was called with (appA, tbl1)
    """
    feishu = _mock_feishu()
    feishu.list_fields = AsyncMock(return_value=_fields_map())

    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.feishu_client = feishu

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get(
                "/admin/feishu/fields?app_token=appA&table_id=tbl1",
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    fields = response.json()["fields"]
    by_name = {f["name"]: f for f in fields}

    # is_primary text field
    assert by_name["账单名"] == {
        "name": "账单名",
        "type": "text",
        "options": None,
        "is_primary": True,
    }
    # single_select with options list
    assert by_name["收支类型"]["type"] == "single_select"
    assert by_name["收支类型"]["options"] == ["支出", "收入"]
    assert by_name["收支类型"]["is_primary"] is False
    # number / date / unknown
    assert by_name["金额"]["type"] == "number"
    assert by_name["金额"]["options"] is None
    assert by_name["日期"]["type"] == "date"
    assert by_name["未知字段"]["type"] == "unknown"

    feishu.list_fields.assert_awaited_once_with("appA", "tbl1")


async def test_get_fields_upstream_error_returns_502():
    """
    GIVEN app.state.feishu_client.list_fields raises FeishuClientError
    WHEN GET /admin/feishu/fields?app_token=appA&table_id=tbl1 is called
    THEN the response status is 502
      AND the error code is FEISHU_UPSTREAM_ERROR
    """
    feishu = _mock_feishu()
    feishu.list_fields = AsyncMock(
        side_effect=FeishuClientError("boom", stage="list_fields")
    )

    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.feishu_client = feishu

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get(
                "/admin/feishu/fields?app_token=appA&table_id=tbl1",
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 502
    assert response.json()["detail"]["error"]["code"] == "FEISHU_UPSTREAM_ERROR"


# ─── GET /admin/feishu/records ─────────────────────────────────────────────


async def test_get_records_happy_preview_is_primary():
    """
    GIVEN list_fields returns a fields_map where "账单名" is_primary=True
       AND list_records returns 2 records, the first with 账单名="麦当劳"
    WHEN GET /admin/feishu/records?app_token=appA&table_id=tbl1 is called
    THEN the response status is 200
      AND body.items[0].preview == "麦当劳" (the is_primary field value)
      AND body.has_more + body.next_page_token are passed through
      AND list_records was called with (appA, tbl1, None)
    """
    feishu = _mock_feishu()
    feishu.list_fields = AsyncMock(return_value=_fields_map())
    feishu.list_records = AsyncMock(
        return_value={
            "items": [
                {"record_id": "rec1", "fields": {"账单名": "麦当劳", "金额": 42}},
                {"record_id": "rec2", "fields": {"账单名": "星巴克", "金额": 30}},
            ],
            "has_more": True,
            "page_token": "tokNext",
        }
    )

    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.feishu_client = feishu

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get(
                "/admin/feishu/records?app_token=appA&table_id=tbl1",
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0] == {"record_id": "rec1", "preview": "麦当劳"}
    assert body["items"][1] == {"record_id": "rec2", "preview": "星巴克"}
    assert body["has_more"] is True
    assert body["next_page_token"] == "tokNext"
    feishu.list_records.assert_awaited_once_with("appA", "tbl1", None)


async def test_get_records_page_token_passthrough():
    """
    GIVEN list_fields returns a fields_map + list_records returns a page
    WHEN GET /admin/feishu/records?app_token=appA&table_id=tbl1&page_token=tokX
    THEN list_records is called with page_token="tokX"
      AND the response next_page_token reflects the upstream value
    """
    feishu = _mock_feishu()
    feishu.list_fields = AsyncMock(return_value=_fields_map())
    feishu.list_records = AsyncMock(
        return_value={
            "items": [{"record_id": "rec3", "fields": {"账单名": "test"}}],
            "has_more": False,
            "page_token": None,
        }
    )

    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.feishu_client = feishu

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get(
                "/admin/feishu/records?app_token=appA&table_id=tbl1&page_token=tokX",
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["preview"] == "test"
    assert body["has_more"] is False
    assert body["next_page_token"] is None
    feishu.list_records.assert_awaited_once_with("appA", "tbl1", "tokX")


async def test_get_records_no_is_primary_falls_back_to_first_text():
    """
    GIVEN list_fields returns fields where NO field is is_primary
       AND list_records returns a record whose first text-like field is "金额"
    WHEN GET /admin/feishu/records is called
    THEN preview is the first text-coercible field value (str-ified)
    """
    feishu = _mock_feishu()
    fields_map = {
        "金额": {"field_name": "金额", "type": 2, "is_primary": False, "property": {}},
        "备注": {"field_name": "备注", "type": 1, "is_primary": False, "property": {}},
    }
    feishu.list_fields = AsyncMock(return_value=fields_map)
    feishu.list_records = AsyncMock(
        return_value={
            "items": [
                {"record_id": "rec1", "fields": {"金额": 42.5, "备注": "午餐"}},
            ],
            "has_more": False,
            "page_token": None,
        }
    )

    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.feishu_client = feishu

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get(
                "/admin/feishu/records?app_token=appA&table_id=tbl1",
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    # first text-coercible field in fields_map insertion order is "金额" → "42.5"
    assert response.json()["items"][0]["preview"] == "42.5"


async def test_get_records_preview_truncated_at_80_chars():
    """
    GIVEN list_records returns a record whose is_primary value is 100 chars
    WHEN GET /admin/feishu/records is called
    THEN preview is truncated to exactly 80 characters
    """
    feishu = _mock_feishu()
    feishu.list_fields = AsyncMock(return_value=_fields_map())
    long_value = "x" * 100
    feishu.list_records = AsyncMock(
        return_value={
            "items": [{"record_id": "rec1", "fields": {"账单名": long_value}}],
            "has_more": False,
            "page_token": None,
        }
    )

    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.feishu_client = feishu

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get(
                "/admin/feishu/records?app_token=appA&table_id=tbl1",
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    assert response.json()["items"][0]["preview"] == "x" * 80


async def test_get_records_empty_fields_preview_empty_string():
    """
    GIVEN list_records returns a record with empty fields {}
    WHEN GET /admin/feishu/records is called
    THEN preview is "" (no is_primary, no text values)
    """
    feishu = _mock_feishu()
    feishu.list_fields = AsyncMock(return_value=_fields_map())
    feishu.list_records = AsyncMock(
        return_value={
            "items": [{"record_id": "rec1", "fields": {}}],
            "has_more": False,
            "page_token": None,
        }
    )

    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.feishu_client = feishu

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get(
                "/admin/feishu/records?app_token=appA&table_id=tbl1",
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    assert response.json()["items"][0]["preview"] == ""


async def test_get_records_upstream_error_returns_502():
    """
    GIVEN list_fields succeeds but list_records raises FeishuClientError
    WHEN GET /admin/feishu/records is called
    THEN the response status is 502 FEISHU_UPSTREAM_ERROR
    """
    feishu = _mock_feishu()
    feishu.list_fields = AsyncMock(return_value=_fields_map())
    feishu.list_records = AsyncMock(
        side_effect=FeishuClientError("boom", stage="list_records")
    )

    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.feishu_client = feishu

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get(
                "/admin/feishu/records?app_token=appA&table_id=tbl1",
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 502
    assert response.json()["detail"]["error"]["code"] == "FEISHU_UPSTREAM_ERROR"


# ─── POST /admin/feishu/parse-url ──────────────────────────────────────────


async def test_parse_url_happy_table_query_param():
    """
    GIVEN a valid feishu.cn /base/ URL with ?table=tbl1 as the FIRST query param
    WHEN POST /admin/feishu/parse-url is called with {"url": "..."}
    THEN the response status is 200
      AND the body is {"app_token": "...", "table_id": "tbl1"}
    """
    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.feishu_client = _mock_feishu()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.post(
                "/admin/feishu/parse-url",
                json={"url": "https://x.feishu.cn/base/appA?table=tbl1&view=v1"},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    assert response.json() == {"app_token": "appA", "table_id": "tbl1"}


async def test_parse_url_happy_table_amp_param():
    """
    GIVEN a valid feishu.cn /base/ URL with &table=tbl1 as a non-first param
    WHEN POST /admin/feishu/parse-url is called
    THEN the response status is 200
      AND the body is {"app_token": "appA", "table_id": "tbl1"}
    """
    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.feishu_client = _mock_feishu()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.post(
                "/admin/feishu/parse-url",
                json={"url": "https://x.feishu.cn/base/appA?view=v1&table=tbl1"},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    assert response.json() == {"app_token": "appA", "table_id": "tbl1"}


async def test_parse_url_larksuite_domain_happy():
    """
    GIVEN a valid larksuite.com /base/ URL
    WHEN POST /admin/feishu/parse-url is called
    THEN the response status is 200 (larksuite.com is whitelisted)
    """
    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.feishu_client = _mock_feishu()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.post(
                "/admin/feishu/parse-url",
                json={"url": "https://xxx.larksuite.com/base/appB?table=tbl9"},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    assert response.json() == {"app_token": "appB", "table_id": "tbl9"}


async def test_parse_url_wiki_link_returns_422():
    """
    GIVEN a feishu.cn wiki link (no /base/ segment)
    WHEN POST /admin/feishu/parse-url is called
    THEN the response status is 422
      AND the error code is UNSUPPORTED_URL
    """
    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.feishu_client = _mock_feishu()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.post(
                "/admin/feishu/parse-url",
                json={"url": "https://x.feishu.cn/wiki/AnArticleId"},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 422
    body = response.json()
    assert body["detail"]["error"]["code"] == "UNSUPPORTED_URL"
    assert "/base/" in body["detail"]["error"]["message"]


async def test_parse_url_non_whitelisted_domain_returns_422():
    """
    GIVEN a /base/ URL on a non-whitelisted domain (example.com)
    WHEN POST /admin/feishu/parse-url is called
    THEN the response status is 422 UNSUPPORTED_URL
    """
    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.feishu_client = _mock_feishu()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.post(
                "/admin/feishu/parse-url",
                json={"url": "https://example.com/base/appA?table=tbl1"},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "UNSUPPORTED_URL"


async def test_parse_url_missing_table_param_returns_422():
    """
    GIVEN a feishu.cn /base/ URL with NO table query param
    WHEN POST /admin/feishu/parse-url is called
    THEN the response status is 422 UNSUPPORTED_URL
    """
    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.feishu_client = _mock_feishu()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.post(
                "/admin/feishu/parse-url",
                json={"url": "https://x.feishu.cn/base/appA?view=v1"},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "UNSUPPORTED_URL"


async def test_parse_url_missing_base_segment_returns_422():
    """
    GIVEN a feishu.cn URL with NO /base/ path segment
    WHEN POST /admin/feishu/parse-url is called
    THEN the response status is 422 UNSUPPORTED_URL
    """
    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.feishu_client = _mock_feishu()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.post(
                "/admin/feishu/parse-url",
                json={"url": "https://x.feishu.cn/docs/someDoc"},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "UNSUPPORTED_URL"


async def test_parse_url_missing_app_token_returns_422():
    """
    GIVEN a feishu.cn /base/ URL with an EMPTY app_token (/base/?table=...)
    WHEN POST /admin/feishu/parse-url is called
    THEN the response status is 422 UNSUPPORTED_URL
    """
    async with lifespan(app):
        app.state.settings = _enabled_settings()
        app.state.feishu_client = _mock_feishu()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.post(
                "/admin/feishu/parse-url",
                json={"url": "https://x.feishu.cn/base/?table=tbl1"},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "UNSUPPORTED_URL"
