"""AI extraction pipeline orchestrator — extract → encode → writeback → create.

Owns the only path that touches both the AI extractor and Feishu in one
transactional flow. Design constraints:

- **Never raises**: run() catches everything; AI stage failures must not break
  webhook 200 semantics. Caller always gets a PipelineResult.
- **Dedup is in-memory + success-only**: a failed AI extraction must NOT be
  recorded — a legal retry must be allowed to run.
- **Lock discipline**: threading.Lock guards only dict/reference swaps. All
  network IO (extract, list_fields, update_record_field, create_record) happens
  OUTSIDE the lock.
- **Never logs original_text**: log only ids/alias/timings/warning counts.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field

from .ai_extractor import AiExtractor, AiExtractorError, ExtractionResult
from .ai_profile import AiProfile, build_field_prompts
from .feishu_client import FeishuClient, FeishuClientError
from .field_codec import encode_fields
from .target_registry import FeishuTargetConfig


logger = logging.getLogger("feishu_webhook_service.pipeline")


@dataclass(frozen=True, slots=True)
class PipelineResult:
    ai_status: str  # "succeeded" | "failed" | "duplicate"
    bill_record_id: str | None
    warnings: list[str]
    extracted: dict[str, object]
    dedup_hit: bool


class _OptionWhitelistCache:
    """Lazy cache for bill-table single_select option whitelists.

    Wave 1 bridge — deleted in todo 8 when AiProfileRegistry takes over and
    exposes whitelists from a pre-loaded snapshot. Fetches outside the lock,
    swaps inside the lock.
    """

    def __init__(self, feishu: FeishuClient) -> None:
        self._feishu = feishu
        self._cache: dict[str, set[str]] | None = None
        self._lock = threading.Lock()

    async def get(self, profile: AiProfile) -> dict[str, set[str]]:
        with self._lock:
            cached = self._cache
        if cached is not None:
            return cached

        fields_map = await self._feishu.list_fields(
            profile.bill_app_token, profile.bill_table_id
        )
        whitelists = self._extract_whitelists(fields_map)
        with self._lock:
            if self._cache is None:
                self._cache = whitelists
            return self._cache

    @staticmethod
    def _extract_whitelists(
        fields_map: dict[str, dict],
    ) -> dict[str, set[str]]:
        whitelists: dict[str, set[str]] = {}
        for field_name, field_def in fields_map.items():
            if not isinstance(field_def, dict):
                continue
            prop = field_def.get("property")
            if not isinstance(prop, dict):
                continue
            options = prop.get("options")
            if not isinstance(options, list):
                continue
            names = {
                opt.get("name")
                for opt in options
                if isinstance(opt, dict) and isinstance(opt.get("name"), str)
            }
            if names:
                whitelists[field_name] = names
        return whitelists


class AiPipeline:
    def __init__(
        self,
        settings,
        extractor: AiExtractor,
        feishu: FeishuClient,
    ) -> None:
        self._settings = settings
        self._extractor = extractor
        self._feishu = feishu
        self._dedup: dict[str, float] = {}
        self._dedup_lock = threading.Lock()
        self._whitelist_cache = _OptionWhitelistCache(feishu)

    async def run(
        self,
        original_text: str,
        target: FeishuTargetConfig,
        profile: AiProfile,
        option_whitelists: dict[str, set[str]] | None = None,
    ) -> PipelineResult:
        try:
            return await self._run_impl(
                original_text, target, profile, option_whitelists
            )
        except Exception:
            logger.exception(
                "pipeline run failed alias=%s", target.alias
            )
            return PipelineResult(
                ai_status="failed",
                bill_record_id=None,
                warnings=[],
                extracted={},
                dedup_hit=False,
            )

    async def _run_impl(
        self,
        original_text: str,
        target: FeishuTargetConfig,
        profile: AiProfile,
        option_whitelists: dict[str, set[str]] | None,
    ) -> PipelineResult:
        dedup_key = hashlib.sha256(
            f"{target.alias}:{original_text}".encode()
        ).hexdigest()

        ttl = self._settings.ai_dedup_ttl_seconds
        now = time.time()
        with self._dedup_lock:
            stored_ts = self._dedup.get(dedup_key)
        if stored_ts is not None and now - stored_ts <= ttl:
            logger.info(
                "pipeline dedup hit alias=%s", target.alias
            )
            return PipelineResult(
                ai_status="duplicate",
                bill_record_id=None,
                warnings=[],
                extracted={},
                dedup_hit=True,
            )

        # Resolve whitelists BEFORE building field_prompts (single_select option
        # injection needs the whitelist). Production path always provides
        # option_whitelists from the registry snapshot; the cache fallback is
        # a Wave-1 bridge for tests that don't pass them.
        if option_whitelists is None:
            whitelists = await self._whitelist_cache.get(profile)
        else:
            whitelists = option_whitelists

        field_prompts = build_field_prompts(profile, whitelists)

        start = time.time()
        try:
            extraction = await self._extractor.extract(
                original_text, profile.prompt_header, field_prompts
            )
        except AiExtractorError as exc:
            logger.warning(
                "pipeline ai extract failed alias=%s stage=%s",
                target.alias,
                exc.stage,
            )
            return PipelineResult(
                ai_status="failed",
                bill_record_id=None,
                warnings=[],
                extracted={},
                dedup_hit=False,
            )

        elapsed_ms = int((time.time() - start) * 1000)
        logger.info(
            "pipeline ai extract ok alias=%s elapsed_ms=%s",
            target.alias,
            elapsed_ms,
        )

        try:
            extract_fields, bill_fields, warnings = encode_fields(
                extraction, list(profile.fields), whitelists
            )
        except ValueError as exc:
            logger.warning(
                "pipeline encode failed alias=%s error=%s",
                target.alias,
                exc,
            )
            return PipelineResult(
                ai_status="failed",
                bill_record_id=None,
                warnings=[],
                extracted={},
                dedup_hit=False,
            )

        if extract_fields:
            summary_value = extract_fields.get(profile.summary_field)
            if summary_value is not None:
                try:
                    await self._feishu.update_record_field(
                        profile.summary_field, summary_value, target
                    )
                except FeishuClientError as exc:
                    warnings.append(f"summary writeback failed: {exc}")

        client_token = "ai-bill-" + dedup_key[:40]
        try:
            bill_record_id = await self._feishu.create_record(
                bill_fields,
                profile.bill_app_token,
                profile.bill_table_id,
                client_token,
            )
        except FeishuClientError as exc:
            logger.warning(
                "pipeline bill create failed alias=%s stage=%s",
                target.alias,
                exc.stage,
            )
            return PipelineResult(
                ai_status="failed",
                bill_record_id=None,
                warnings=warnings,
                extracted={},
                dedup_hit=False,
            )

        with self._dedup_lock:
            self._dedup[dedup_key] = time.time()

        extracted = {
            "amount": extraction.amount,
            "category": extraction.category,
            "flow_type": extraction.flow_type,
            "description": extraction.description,
        }
        logger.info(
            "pipeline ok alias=%s bill_record_id=%s warnings=%s",
            target.alias,
            bill_record_id,
            len(warnings),
        )
        return PipelineResult(
            ai_status="succeeded",
            bill_record_id=bill_record_id,
            warnings=warnings,
            extracted=extracted,
            dedup_hit=False,
        )
