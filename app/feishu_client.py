from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from .config import Settings
from .target_registry import FeishuTargetConfig


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
        token = await self._get_tenant_access_token()
        url = (
            f"{self._settings.feishu_base_url}/open-apis/bitable/v1/apps/"
            f"{target.app_token}/tables/{target.table_id}/records/{target.record_id}"
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        body = {
            "fields": {
                target.original_field_name: original_text,
            }
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.put(url, headers=headers, json=body)

        if response.status_code != 200:
            raise FeishuClientError(
                f"Failed to update 原始信息: HTTP {response.status_code} body={response.text}",
                stage="update_original_text",
                record_id=target.record_id,
            )

        data = response.json()
        code = data.get("code", 0)
        if code != 0:
            raise FeishuClientError(
                f"Failed to update 原始信息: code={code}, msg={data.get('msg', '')}",
                stage="update_original_text",
                record_id=target.record_id,
            )

        return target.record_id
