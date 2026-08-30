from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from .config import Settings
from .target_registry import FeishuTargetConfig


logger = logging.getLogger("feishu_webhook_service.feishu_client")


class FeishuClientError(RuntimeError):
    def __init__(self, message: str, *, stage: str, record_id: str | None = None) -> None:
        super().__init__(message)
        self.stage = stage
        self.record_id = record_id


@dataclass(slots=True)
class TokenCache:
    access_token: str | None = None
    expire_at: float = 0.0


class FeishuClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._timeout = httpx.Timeout(settings.http_timeout_seconds)
        self._token_cache = TokenCache()

    async def _get_tenant_access_token(self) -> str:
        now = time.time()
        if self._token_cache.access_token and now < self._token_cache.expire_at:
            return self._token_cache.access_token

        url = f"{self._settings.feishu_base_url}/open-apis/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": self._settings.feishu_app_id,
            "app_secret": self._settings.feishu_app_secret,
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, json=payload)

        if response.status_code != 200:
            raise FeishuClientError(
                f"Failed to get tenant access token: HTTP {response.status_code}",
                stage="get_tenant_access_token",
            )

        data = response.json()
        code = data.get("code", 0)
        if code != 0:
            raise FeishuClientError(
                f"Failed to get tenant access token: code={code}, msg={data.get('msg', '')}",
                stage="get_tenant_access_token",
            )

        access_token = data.get("tenant_access_token")
        if not access_token:
            raise FeishuClientError(
                "Failed to get tenant access token: missing tenant_access_token",
                stage="get_tenant_access_token",
            )

        expire_seconds = int(data.get("expire", 7200))
        self._token_cache.access_token = access_token
        self._token_cache.expire_at = now + max(60, expire_seconds - self._settings.token_refresh_skew_seconds)
        return access_token

    async def update_original_text(self, original_text: str, target: FeishuTargetConfig) -> str:
        return await self.update_record_field(target.original_field_name, original_text, target)

    async def update_record_field(self, field_name: str, value: str, target: FeishuTargetConfig) -> str:
        token = await self._get_tenant_access_token()
        url = (
            f"{self._settings.feishu_base_url}/open-apis/bitable/v1/apps/"
            f"{target.app_token}/tables/{target.table_id}/records/{target.record_id}"
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        body = {"fields": {field_name: value}}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.put(url, headers=headers, json=body)

        if response.status_code != 200:
            raise FeishuClientError(
                f"Failed to update record field: HTTP {response.status_code} body={response.text}",
                stage="update_record_field",
                record_id=target.record_id,
            )

        data = response.json()
        code = data.get("code", 0)
        if code != 0:
            raise FeishuClientError(
                f"Failed to update record field: code={code}, msg={data.get('msg', '')}",
                stage="update_record_field",
                record_id=target.record_id,
            )

        return target.record_id

    async def create_record(self, fields: dict, app_token: str, table_id: str) -> str:
        token = await self._get_tenant_access_token()
        url = (
            f"{self._settings.feishu_base_url}/open-apis/bitable/v1/apps/"
            f"{app_token}/tables/{table_id}/records"
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, headers=headers, json={"fields": fields})

        if response.status_code != 200:
            raise FeishuClientError(
                f"Failed to create record: HTTP {response.status_code} body={response.text}",
                stage="create_record",
            )

        data = response.json()
        code = data.get("code", 0)
        msg = data.get("msg", "")

        if code != 0:
            raise FeishuClientError(
                f"Failed to create record: code={code}, msg={msg}",
                stage="create_record",
            )

        return data["data"]["record"]["record_id"]

    async def list_tables(self, app_token: str) -> list[dict]:
        """List all tables in a Bitable app, merging paginated responses.

        Returns a flat list of {table_id, name} dicts across all pages.
        """
        token = await self._get_tenant_access_token()
        base_url = (
            f"{self._settings.feishu_base_url}/open-apis/bitable/v1/apps/"
            f"{app_token}/tables"
        )
        headers = {"Authorization": f"Bearer {token}"}
        all_tables: list[dict] = []
        page_token: str | None = None

        while True:
            url = base_url + "?page_size=100"
            if page_token:
                url += f"&page_token={page_token}"

            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, headers=headers)

            if response.status_code != 200:
                raise FeishuClientError(
                    f"Failed to list tables: HTTP {response.status_code} body={response.text}",
                    stage="list_tables",
                )

            data = response.json()
            code = data.get("code", 0)
            if code != 0:
                raise FeishuClientError(
                    f"Failed to list tables: code={code}, msg={data.get('msg', '')}",
                    stage="list_tables",
                )

            for item in data.get("data", {}).get("items", []):
                all_tables.append({"table_id": item["table_id"], "name": item["name"]})

            if not data.get("data", {}).get("has_more"):
                break
            page_token = data["data"]["page_token"]

        return all_tables

    async def list_records(
        self, app_token: str, table_id: str, page_token: str | None = None
    ) -> dict:
        """Search records in a Bitable table (single page).

        Uses the official POST /records/search endpoint (GET list is deprecated).
        Pagination is single-page: callers drive subsequent pages by passing the
        returned page_token back in. The request body is the minimum legal shape
        ({}) since all body fields (view_id/field_names/sort/filter/automatic_fields)
        are optional; page_token + page_size are query parameters, not body fields.
        """
        token = await self._get_tenant_access_token()
        url = (
            f"{self._settings.feishu_base_url}/open-apis/bitable/v1/apps/"
            f"{app_token}/tables/{table_id}/records/search?page_size=50"
        )
        if page_token:
            url += f"&page_token={page_token}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, headers=headers, json={})

        if response.status_code != 200:
            raise FeishuClientError(
                f"Failed to list records: HTTP {response.status_code} body={response.text}",
                stage="list_records",
            )

        data = response.json()
        code = data.get("code", 0)
        if code != 0:
            raise FeishuClientError(
                f"Failed to list records: code={code}, msg={data.get('msg', '')}",
                stage="list_records",
            )

        page_data = data.get("data", {})
        items = [
            {"record_id": item["record_id"], "fields": item.get("fields", {})}
            for item in page_data.get("items", [])
        ]
        return {
            "items": items,
            "has_more": page_data.get("has_more", False),
            "page_token": page_data.get("page_token") if page_data.get("has_more") else None,
        }

    async def list_fields(self, app_token: str, table_id: str) -> dict[str, dict]:
        token = await self._get_tenant_access_token()
        base_url = (
            f"{self._settings.feishu_base_url}/open-apis/bitable/v1/apps/"
            f"{app_token}/tables/{table_id}/fields"
        )
        headers = {
            "Authorization": f"Bearer {token}",
        }
        all_fields: dict[str, dict] = {}
        page_token: str | None = None

        while True:
            url = base_url + "?page_size=100"
            if page_token:
                url += f"&page_token={page_token}"

            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, headers=headers)

            if response.status_code != 200:
                raise FeishuClientError(
                    f"Failed to list fields: HTTP {response.status_code} body={response.text}",
                    stage="list_fields",
                )

            data = response.json()
            code = data.get("code", 0)
            if code != 0:
                raise FeishuClientError(
                    f"Failed to list fields: code={code}, msg={data.get('msg', '')}",
                    stage="list_fields",
                )

            for item in data.get("data", {}).get("items", []):
                all_fields[item["field_name"]] = item

            if not data.get("data", {}).get("has_more"):
                break
            page_token = data["data"]["page_token"]

        return all_fields
