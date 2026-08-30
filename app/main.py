from __future__ import annotations

import asyncio
import errno
import logging
import os
import re
import secrets
import urllib.parse
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field

from .ai_extractor import AiExtractor, AiExtractorError, ExtractionResult
from .ai_profile import (
    AiProfile,
    AiProfileRegistry,
    AiProfileRegistryUnavailableError,
    ProfileConfigError,
    build_field_prompts,
    validate_profile_candidate,
)
from .config import Settings, get_settings
from .feishu_client import FeishuClient, FeishuClientError
from .field_codec import FieldSpec, encode_fields
from .pipeline import AiPipeline, PipelineResult
from .target_registry import (
    FeishuTargetConfig,
    TargetRegistry,
    TargetRegistryConfigError,
    TargetRegistryError,
    TargetRegistryUnavailableError,
    TargetSelectorError,
    validate_targets,
)
from .toml_writer import dump_profile, dump_targets


class WebhookRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_text: str = Field(..., min_length=1, max_length=32768)
    source: str | None = Field(default=None, max_length=128)
    raw_ocr: str | None = Field(default=None, max_length=32768)
    book_alias: str | None = Field(default=None, min_length=1, max_length=64)
    year: int | None = Field(default=None, gt=0)


class ErrorBody(BaseModel):
    code: str
    message: str


class WebhookErrorResponse(BaseModel):
    success: bool = False
    request_id: str
    record_id: str | None = None
    stage: str | None = None
    error: ErrorBody


class WebhookSuccessResponse(BaseModel):
    success: bool = True
    request_id: str
    record_id: str
    book_alias: str
    message: str
    # AI stage fields — present only when AI_ENABLED=true and the pipeline ran.
    # response_model_exclude_none on the route drops these when None, so the
    # disabled-mode response is byte-identical to master.
    ai_status: str | None = None
    ai_record_id: str | None = None
    ai_warnings: list[str] | None = None
    ai_extracted: dict | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    target_registry = TargetRegistry(settings)
    target_registry.load_initial()
    app.state.settings = settings
    app.state.feishu_client = FeishuClient(settings)
    app.state.target_registry = target_registry

    # AI stage is constructed only when AI_ENABLED=true; registry load_initial
    # failures fail startup (aligns with target_registry.load_initial fail-fast).
    # The registry replaces the old static parse_profile — snapshots are
    # hot-reloaded per-request via maybe_reload + mtime check.
    if settings.ai_enabled:
        ai_registry = AiProfileRegistry(settings, app.state.feishu_client)
        await ai_registry.load_initial()
        app.state.ai_registry = ai_registry
        extractor = AiExtractor(settings)
        app.state.ai_extractor = extractor
        app.state.ai_pipeline = AiPipeline(settings, extractor, app.state.feishu_client)
    else:
        app.state.ai_extractor = None
        app.state.ai_registry = None
        app.state.ai_pipeline = None

    yield


app = FastAPI(title="Feishu Webhook Service", version="0.1.0", lifespan=lifespan)
logger = logging.getLogger("feishu_webhook_service")


@app.get("/admin/ai")
async def admin_ai_page() -> HTMLResponse:
    html_path = Path(__file__).parent / "static" / "admin.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


# Static assets for the admin page (admin.css / admin.js). Mounted at a
# distinct prefix from the page route so there is no path shadowing: the page
# is served at the exact path /admin/ai; assets live under /admin/ai/assets/.
app.mount(
    "/admin/ai/assets",
    StaticFiles(directory=Path(__file__).parent / "static"),
    name="admin-assets",
)


@app.get("/health")
async def health(request: Request) -> dict[str, object]:
    target_registry: TargetRegistry | None = getattr(request.app.state, "target_registry", None)
    if target_registry is None:
        return {"success": True, "status": "ok"}

    registry_status = target_registry.describe()
    return {
        "success": True,
        "status": "ok",
        "config_valid": registry_status["config_valid"],
        "target_mode": registry_status["mode"],
    }


@app.post(
    "/v1/webhook/ocr",
    response_model=WebhookSuccessResponse,
    # Drop ai_* fields when None so the disabled-mode response is byte-identical
    # to master (regression lock: test_regression_disabled_response_has_5_keys).
    response_model_exclude_none=True,
    responses={
        401: {"model": WebhookErrorResponse},
        422: {"model": WebhookErrorResponse},
        502: {"model": WebhookErrorResponse},
        503: {"model": WebhookErrorResponse},
        500: {"model": WebhookErrorResponse},
    },
)
async def ingest_ocr(
    payload: WebhookRequest,
    request: Request,
    x_webhook_token: str | None = Header(default=None, alias="X-Webhook-Token"),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
):
    settings: Settings = request.app.state.settings
    feishu_client: FeishuClient = request.app.state.feishu_client
    target_registry: TargetRegistry = request.app.state.target_registry
    request_id = x_request_id or f"req_{uuid.uuid4().hex[:12]}"

    if not x_webhook_token or not secrets.compare_digest(x_webhook_token, settings.webhook_shared_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "success": False,
                "request_id": request_id,
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "invalid webhook token",
                },
            },
        )

    original_text = payload.original_text.strip()
    if not original_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "success": False,
                "request_id": request_id,
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "original_text is required",
                },
            },
        )

    logger.info(
        "request accepted request_id=%s source=%s book_alias=%s year=%s",
        request_id,
        payload.source or "",
        payload.book_alias or "",
        payload.year if payload.year is not None else "",
    )

    try:
        target_registry.maybe_reload()
        target = target_registry.resolve(book_alias=payload.book_alias, year=payload.year)
        record_id = await feishu_client.update_original_text(original_text=original_text, target=target)
    except TargetSelectorError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "success": False,
                "request_id": request_id,
                "error": {
                    "code": "INVALID_TARGET_SELECTOR",
                    "message": str(exc),
                },
            },
        ) from exc
    except TargetRegistryUnavailableError as exc:
        logger.exception("target registry unavailable request_id=%s", request_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "success": False,
                "request_id": request_id,
                "error": {
                    "code": "TARGET_REGISTRY_UNAVAILABLE",
                    "message": str(exc),
                },
            },
        ) from exc
    except TargetRegistryError as exc:
        logger.exception("target registry reload failed request_id=%s", request_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "success": False,
                "request_id": request_id,
                "error": {
                    "code": "TARGET_REGISTRY_UNAVAILABLE",
                    "message": str(exc),
                },
            },
        ) from exc
    except FeishuClientError as exc:
        logger.exception(
            "feishu request failed request_id=%s stage=%s record_id=%s",
            request_id,
            exc.stage,
            exc.record_id,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "success": False,
                "request_id": request_id,
                "record_id": exc.record_id,
                "stage": exc.stage,
                "error": {
                    "code": "FEISHU_UPSTREAM_ERROR",
                    "message": str(exc),
                },
            },
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("unexpected failure request_id=%s", request_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "success": False,
                "request_id": request_id,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": str(exc),
                },
            },
        ) from exc

    logger.info(
        "request succeeded request_id=%s record_id=%s book_alias=%s",
        request_id,
        record_id,
        target.alias,
    )

    # AI stage runs only after the original-text write succeeded. The pipeline
    # never raises (AiPipeline.run catches Exception), so an AI failure still
    # returns 200. The ONE exception is AiProfileRegistryUnavailableError — the
    # registry is fail-closed (bad TOML / list_fields failure → config_valid=False),
    # and serving would risk polluting single_select options, so it 503s.
    ai_status: str | None = None
    ai_record_id: str | None = None
    ai_warnings: list[str] | None = None
    ai_extracted: dict | None = None
    ai_pipeline: AiPipeline | None = getattr(request.app.state, "ai_pipeline", None)
    ai_registry: AiProfileRegistry | None = getattr(request.app.state, "ai_registry", None)
    if settings.ai_enabled and ai_pipeline is not None and ai_registry is not None:
        try:
            await ai_registry.maybe_reload()
            snapshot = ai_registry.get_snapshot()
        except AiProfileRegistryUnavailableError as exc:
            logger.exception("ai profile registry unavailable request_id=%s", request_id)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "success": False,
                    "request_id": request_id,
                    "error": {
                        "code": "AI_PROFILE_UNAVAILABLE",
                        "message": str(exc),
                    },
                },
            ) from exc
        result: PipelineResult = await ai_pipeline.run(
            original_text=original_text,
            target=target,
            profile=snapshot.profile,
            option_whitelists=snapshot.option_whitelists,
        )
        ai_status = result.ai_status
        ai_record_id = result.bill_record_id
        ai_warnings = result.warnings if result.warnings else None
        ai_extracted = result.extracted if result.extracted else None
        logger.info(
            "ai stage done request_id=%s ai_status=%s ai_record_id=%s warnings=%s",
            request_id,
            ai_status,
            ai_record_id or "",
            len(result.warnings),
        )

    return WebhookSuccessResponse(
        request_id=request_id,
        record_id=record_id,
        book_alias=target.alias,
        message="configured record updated and 原始信息 updated",
        ai_status=ai_status,
        ai_record_id=ai_record_id,
        ai_warnings=ai_warnings,
        ai_extracted=ai_extracted,
    )


@app.post(
    "/admin/config/reload",
    responses={401: {"model": WebhookErrorResponse}, 404: {"model": WebhookErrorResponse}, 503: {"model": WebhookErrorResponse}},
)
async def reload_config(
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    request_id = x_request_id or f"req_{uuid.uuid4().hex[:12]}"

    if not settings.config_reload_token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "success": False,
                "request_id": request_id,
                "error": {
                    "code": "RELOAD_DISABLED",
                    "message": "config reload endpoint is disabled",
                },
            },
        )

    if not x_admin_token or not secrets.compare_digest(x_admin_token, settings.config_reload_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "success": False,
                "request_id": request_id,
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "invalid admin token",
                },
            },
        )

    target_registry: TargetRegistry = request.app.state.target_registry
    try:
        status_payload = target_registry.reload(force=True)
    except TargetRegistryError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "success": False,
                "request_id": request_id,
                "error": {
                    "code": "TARGET_REGISTRY_UNAVAILABLE",
                    "message": str(exc),
                },
            },
        ) from exc

    response: dict[str, object] = {
        "success": True,
        "request_id": request_id,
        **status_payload,
    }

    # AI registry reload — best-effort, mirrors the target registry pattern.
    # _load_snapshot re-raises ProfileConfigError/FeishuClientError AFTER
    # setting config_valid=False, so get_status() returns fail-closed
    # diagnostics without raising. AI_ENABLED=false → ai_registry is None
    # and we just emit ai_profile: null (backward-compatible key presence).
    ai_registry: AiProfileRegistry | None = getattr(request.app.state, "ai_registry", None)
    if ai_registry is not None:
        try:
            await ai_registry.reload(force=True)
        except (AiProfileRegistryUnavailableError, ProfileConfigError, FeishuClientError):
            pass  # config_valid already False — diagnostics via get_status()
        response["ai_profile"] = ai_registry.get_status()
    else:
        response["ai_profile"] = None

    return response


class AiTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1, max_length=32768)


def _admin_auth_error(code: str, message: str, request_id: str) -> HTTPException:
    return HTTPException(
        status_code=(
            status.HTTP_404_NOT_FOUND
            if code == "RELOAD_DISABLED"
            else status.HTTP_401_UNAUTHORIZED
        ),
        detail={
            "success": False,
            "request_id": request_id,
            "error": {"code": code, "message": message},
        },
    )


@app.post(
    "/admin/ai/test",
    responses={
        401: {"model": WebhookErrorResponse},
        404: {"model": WebhookErrorResponse},
        503: {"model": WebhookErrorResponse},
    },
)
async def ai_test_dry_run(
    payload: AiTestRequest,
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    request_id = x_request_id or f"req_{uuid.uuid4().hex[:12]}"

    if not settings.config_reload_token:
        raise _admin_auth_error("RELOAD_DISABLED", "config reload endpoint is disabled", request_id)
    if not x_admin_token or not secrets.compare_digest(x_admin_token, settings.config_reload_token):
        raise _admin_auth_error("UNAUTHORIZED", "invalid admin token", request_id)

    ai_registry: AiProfileRegistry | None = getattr(request.app.state, "ai_registry", None)
    if not settings.ai_enabled or ai_registry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "success": False,
                "request_id": request_id,
                "error": {"code": "AI_DISABLED", "message": "AI stage is disabled"},
            },
        )

    # Dry run: registry reload (mirrors ingest_ocr). Fail-closed registry → 503
    # (a bad profile cannot guarantee safe single_select encoding).
    try:
        await ai_registry.maybe_reload()
        snapshot = ai_registry.get_snapshot()
    except AiProfileRegistryUnavailableError as exc:
        logger.exception("ai profile registry unavailable request_id=%s", request_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "success": False,
                "request_id": request_id,
                "error": {"code": "AI_PROFILE_UNAVAILABLE", "message": str(exc)},
            },
        ) from exc

    extractor: AiExtractor | None = getattr(request.app.state, "ai_extractor", None)
    field_prompts = build_field_prompts(snapshot.profile, snapshot.option_whitelists)

    try:
        extraction: ExtractionResult = await extractor.extract(
            payload.text, snapshot.profile.prompt_header, field_prompts
        )
    except AiExtractorError as exc:
        # Dry-run never 5xx for AI failures — the point is to surface the failure
        # body for prompt iteration without risking webhook semantics.
        logger.warning(
            "ai dry-run extract failed request_id=%s stage=%s", request_id, exc.stage
        )
        return {"ai_status": "failed", "error": str(exc)}

    extract_fields, bill_fields, warnings = encode_fields(
        extraction, list(snapshot.profile.fields), snapshot.option_whitelists
    )

    return {
        "ai_status": "succeeded",
        "extracted": {
            "summary": extraction.summary,
            "description": extraction.description,
            "flow_type": extraction.flow_type,
            "amount": extraction.amount,
            "category": extraction.category,
            "payment_method": extraction.payment_method,
            "bill_date": extraction.bill_date,
        },
        "bill_fields": bill_fields,
        "summary_writeback": {
            "field": snapshot.profile.summary_field,
            "value": extract_fields.get(snapshot.profile.summary_field),
        },
        "warnings": warnings,
    }


@app.get(
    "/admin/ai/profile",
    responses={401: {"model": WebhookErrorResponse}, 404: {"model": WebhookErrorResponse}},
)
async def ai_profile_inspect(
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    request_id = x_request_id or f"req_{uuid.uuid4().hex[:12]}"

    if not settings.config_reload_token:
        raise _admin_auth_error("RELOAD_DISABLED", "config reload endpoint is disabled", request_id)
    if not x_admin_token or not secrets.compare_digest(x_admin_token, settings.config_reload_token):
        raise _admin_auth_error("UNAUTHORIZED", "invalid admin token", request_id)

    ai_registry: AiProfileRegistry | None = getattr(request.app.state, "ai_registry", None)
    if not settings.ai_enabled or ai_registry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "success": False,
                "request_id": request_id,
                "error": {"code": "AI_DISABLED", "message": "AI stage is disabled"},
            },
        )

    base: dict[str, object] = {
        "ai_enabled": True,
        "provider": settings.ai_provider,
        "model": settings.ai_model,
        "timeout_seconds": settings.ai_timeout_seconds,
        "dedup_ttl_seconds": settings.ai_dedup_ttl_seconds,
        "registry": ai_registry.get_status(),
    }

    # Fail-closed branch: get_snapshot raises. The route stays 200 (NEITHER 500
    # NOR 503) so admin tooling can render degraded diagnostics. The base body
    # already carries registry.config_valid=false + last_reload_error.
    try:
        snapshot = ai_registry.get_snapshot()
    except AiProfileRegistryUnavailableError:
        base["profile"] = None
        base["whitelists"] = None
        return base

    base["profile"] = {
        "summary_field": snapshot.profile.summary_field,
        "bill": {
            "app_token": snapshot.profile.bill_app_token,
            "table_id": snapshot.profile.bill_table_id,
        },
        "fields": [_field_spec_to_dict(spec) for spec in snapshot.profile.fields],
    }
    base["whitelists"] = {
        field: sorted(options)
        for field, options in snapshot.option_whitelists.items()
    }
    return base


def _field_spec_to_dict(spec: FieldSpec) -> dict[str, object]:
    return {
        "ai_key": spec.ai_key,
        "feishu_field": spec.feishu_field,
        "type": spec.type,
        "target": spec.target,
        "fallback": spec.fallback,
        "prompt": spec.prompt,
        "source": spec.source,
        "enabled": spec.enabled,
    }


# ---------------------------------------------------------------------------
# Admin Feishu picker endpoints (frontend config UI support)
#
# Four routes that mirror reload_config auth (X-Admin-Token + compare_digest;
# CONFIG_RELOAD_TOKEN unset → 404 RELOAD_DISABLED). They proxy read-only Feishu
# list calls + a pure URL parser so the admin page can browse bitable tables
# without leaking credentials to the browser. The webhook contract is unchanged.
# ---------------------------------------------------------------------------

# Feishu bitable field type code → generic label. The ui_type string is
# preferred when present (more stable across API revisions); this int map is
# the fallback. Unrecognized codes → "unknown".
_FEISHU_FIELD_TYPE_BY_CODE: dict[int, str] = {
    1: "text",
    2: "number",
    3: "single_select",
    4: "multi_select",
    5: "date",
    7: "checkbox",
    11: "person",
    13: "phone",
    15: "url",
    17: "attachment",
    18: "single_link",
    20: "formula",
    21: "duplex_link",
    22: "location",
    1001: "date",  # created_time
    1002: "date",  # modified_time
    1005: "number",  # auto_number (numeric)
}

# ui_type string (case-insensitive suffix match) → generic label.
_FEISHU_UI_TYPE_TOKENS: dict[str, str] = {
    "text": "text",
    "number": "number",
    "singleselect": "single_select",
    "multiselect": "multi_select",
    "datetime": "date",
    "date": "date",
    "checkbox": "checkbox",
}

# Hostname suffix whitelist for parse-url. Subdomains of feishu.cn and
# larksuite.com are accepted (e.g. xxx.feishu.cn, my.larksuite.com).
_PARSE_URL_HOST_SUFFIXES = ("feishu.cn", "larksuite.com")


def _require_admin_auth(
    settings: Settings, x_admin_token: str | None, request_id: str
) -> None:
    """Mirror reload_config auth gate. Raises HTTPException on failure."""
    if not settings.config_reload_token:
        raise _admin_auth_error("RELOAD_DISABLED", "config reload endpoint is disabled", request_id)
    if not x_admin_token or not secrets.compare_digest(x_admin_token, settings.config_reload_token):
        raise _admin_auth_error("UNAUTHORIZED", "invalid admin token", request_id)


def _feishu_upstream_error(exc: FeishuClientError, request_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={
            "success": False,
            "request_id": request_id,
            "stage": exc.stage,
            "error": {"code": "FEISHU_UPSTREAM_ERROR", "message": str(exc)},
        },
    )


def _map_field_type(field_def: dict) -> str:
    """Map a Feishu field_def to a generic type label.

    Prefers ui_type (string, stable across API revisions), falling back to the
    integer type code. SingleSelect is the only type the UI needs to know
    precisely (it drives the options picker); everything else is best-effort.
    """
    ui_type = str(field_def.get("ui_type") or "").strip().lower()
    if ui_type:
        # Suffix match so "BitableSingleSelect" / "SingleSelect" both map.
        for token, label in _FEISHU_UI_TYPE_TOKENS.items():
            if ui_type.endswith(token):
                return label
    code = field_def.get("type")
    if isinstance(code, int):
        return _FEISHU_FIELD_TYPE_BY_CODE.get(code, "unknown")
    return "unknown"


def _build_field_view(field_def: dict) -> dict[str, object]:
    """Translate one Feishu field_def into the picker response shape."""
    field_type = _map_field_type(field_def)
    options: list[str] | None = None
    if field_type == "single_select":
        prop = field_def.get("property")
        if isinstance(prop, dict):
            raw_options = prop.get("options")
            if isinstance(raw_options, list):
                options = [
                    opt.get("name")
                    for opt in raw_options
                    if isinstance(opt, dict) and isinstance(opt.get("name"), str)
                ]
    return {
        "name": field_def.get("field_name", ""),
        "type": field_type,
        "options": options,
        "is_primary": bool(field_def.get("is_primary", False)),
    }


def _preview_from_record(
    record_fields: dict, fields_meta: dict[str, dict]
) -> str:
    """Build a ≤80 char preview for a record.

    Prefers the is_primary field's value (str-ified); falls back to the first
    text-coercible value in fields_meta insertion order; "" when none.
    """
    primary_name: str | None = None
    for name, field_def in fields_meta.items():
        if isinstance(field_def, dict) and field_def.get("is_primary"):
            primary_name = name
            break

    chosen: object = None
    if primary_name is not None and primary_name in record_fields:
        chosen = record_fields[primary_name]
    else:
        # First non-None value in fields_meta insertion order. A field whose
        # value is None or absent is skipped; the first present value wins
        # (str-ified below) so the preview is never empty-by-mistake.
        for name in fields_meta:
            if name in record_fields and record_fields[name] is not None:
                chosen = record_fields[name]
                break

    if chosen is None:
        return ""
    preview = str(chosen)
    return preview[:80] if len(preview) > 80 else preview


@app.get(
    "/admin/feishu/tables",
    responses={401: {"model": WebhookErrorResponse}, 404: {"model": WebhookErrorResponse}, 502: {"model": WebhookErrorResponse}},
)
async def feishu_picker_tables(
    request: Request,
    app_token: str = Query(..., min_length=1),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    request_id = x_request_id or f"req_{uuid.uuid4().hex[:12]}"
    _require_admin_auth(settings, x_admin_token, request_id)

    feishu_client: FeishuClient = request.app.state.feishu_client
    try:
        tables = await feishu_client.list_tables(app_token)
    except FeishuClientError as exc:
        logger.exception("feishu list_tables failed request_id=%s", request_id)
        raise _feishu_upstream_error(exc, request_id) from exc
    return {"tables": tables}


@app.get(
    "/admin/feishu/fields",
    responses={401: {"model": WebhookErrorResponse}, 404: {"model": WebhookErrorResponse}, 502: {"model": WebhookErrorResponse}},
)
async def feishu_picker_fields(
    request: Request,
    app_token: str = Query(..., min_length=1),
    table_id: str = Query(..., min_length=1),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    request_id = x_request_id or f"req_{uuid.uuid4().hex[:12]}"
    _require_admin_auth(settings, x_admin_token, request_id)

    feishu_client: FeishuClient = request.app.state.feishu_client
    try:
        fields_map = await feishu_client.list_fields(app_token, table_id)
    except FeishuClientError as exc:
        logger.exception("feishu list_fields failed request_id=%s", request_id)
        raise _feishu_upstream_error(exc, request_id) from exc
    return {"fields": [_build_field_view(fd) for fd in fields_map.values()]}


@app.get(
    "/admin/feishu/records",
    responses={401: {"model": WebhookErrorResponse}, 404: {"model": WebhookErrorResponse}, 502: {"model": WebhookErrorResponse}},
)
async def feishu_picker_records(
    request: Request,
    app_token: str = Query(..., min_length=1),
    table_id: str = Query(..., min_length=1),
    page_token: str | None = Query(default=None),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    request_id = x_request_id or f"req_{uuid.uuid4().hex[:12]}"
    _require_admin_auth(settings, x_admin_token, request_id)

    feishu_client: FeishuClient = request.app.state.feishu_client
    # Preview generation needs field metadata (is_primary flag) — fetch once,
    # then map each record's fields to a preview string.
    try:
        fields_meta = await feishu_client.list_fields(app_token, table_id)
        result = await feishu_client.list_records(app_token, table_id, page_token)
    except FeishuClientError as exc:
        logger.exception("feishu list_records failed request_id=%s", request_id)
        raise _feishu_upstream_error(exc, request_id) from exc

    items = [
        {
            "record_id": item["record_id"],
            "preview": _preview_from_record(item.get("fields", {}), fields_meta),
        }
        for item in result.get("items", [])
    ]
    return {
        "items": items,
        "has_more": result.get("has_more", False),
        "next_page_token": result.get("page_token"),
    }


class ParseUrlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(..., min_length=1, max_length=2048)


@app.post(
    "/admin/feishu/parse-url",
    responses={401: {"model": WebhookErrorResponse}, 404: {"model": WebhookErrorResponse}, 422: {"model": WebhookErrorResponse}},
)
async def feishu_picker_parse_url(
    payload: ParseUrlRequest,
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    request_id = x_request_id or f"req_{uuid.uuid4().hex[:12]}"
    _require_admin_auth(settings, x_admin_token, request_id)

    parsed = urllib.parse.urlparse(payload.url)
    hostname = (parsed.hostname or "").lower()

    def _unsupported() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "success": False,
                "request_id": request_id,
                "error": {
                    "code": "UNSUPPORTED_URL",
                    "message": "请从浏览器地址栏复制 /base/ 链接（暂不支持 wiki 链接）",
                },
            },
        )

    # Domain whitelist: *.feishu.cn / *.larksuite.com (suffix match covers
    # subdomains AND the apex; an attacker-controlled lookalike domain like
    # feishu.cn.evil.com does NOT suffix-match "feishu.cn").
    if not any(
        hostname == suffix or hostname.endswith("." + suffix)
        for suffix in _PARSE_URL_HOST_SUFFIXES
    ):
        raise _unsupported()

    # Path must contain /base/{app_token} — app_token non-empty.
    # Split on "/" and locate the "base" segment; the next segment is the token.
    path_segments = [seg for seg in parsed.path.split("/") if seg]
    try:
        base_idx = path_segments.index("base")
    except ValueError:
        raise _unsupported() from None
    if base_idx + 1 >= len(path_segments):
        raise _unsupported()
    app_token = path_segments[base_idx + 1]
    if not app_token:
        raise _unsupported()

    # Query must contain table={table_id} (non-empty).
    query_params = urllib.parse.parse_qs(parsed.query)
    table_values = query_params.get("table", [])
    if not table_values or not table_values[0]:
        raise _unsupported()
    table_id = table_values[0]

    return {"app_token": app_token, "table_id": table_id}


# ---------------------------------------------------------------------------
# Admin config profile read/write (frontend config UI support)
#
# GET /admin/config/profile  : editable profile shape (degrades 200 on fail-closed)
# PUT /admin/config/profile  : validate → atomic save (tmp + os.replace) → reload
#
# Auth mirrors reload_config (X-Admin-Token + compare_digest; CONFIG_RELOAD_TOKEN
# unset → 404 RELOAD_DISABLED). Both routes 404 AI_DISABLED when AI_ENABLED=false
# or ai_registry is None. PUT serializes writes via a module-level asyncio.Lock
# + generation guard (STALE_WRITE 409) so concurrent edits can't clobber each
# other. The webhook contract is unchanged.
# ---------------------------------------------------------------------------

# Module-level write mutex — one in-flight profile save at a time. Persists
# across requests; tests reset app.state per-case but the lock itself is just
# a mutex (never holds profile data).
_profile_write_lock = asyncio.Lock()


def _profile_to_editable(profile: AiProfile) -> dict[str, object]:
    """AiProfile → editable dict shape (all 7 field keys, None preserved).

    Distinct from _field_spec_to_dict callers that drop None: the config UI
    needs every key present so the form can render empty inputs uniformly.
    """
    return {
        "prompt_header": profile.prompt_header,
        "summary_field": profile.summary_field,
        "bill": {
            "app_token": profile.bill_app_token,
            "table_id": profile.bill_table_id,
        },
        "fields": [_field_spec_to_dict(spec) for spec in profile.fields],
    }


@app.get(
    "/admin/config/profile",
    responses={401: {"model": WebhookErrorResponse}, 404: {"model": WebhookErrorResponse}},
)
async def config_profile_get(
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    request_id = x_request_id or f"req_{uuid.uuid4().hex[:12]}"
    _require_admin_auth(settings, x_admin_token, request_id)

    ai_registry: AiProfileRegistry | None = getattr(request.app.state, "ai_registry", None)
    if not settings.ai_enabled or ai_registry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "success": False,
                "request_id": request_id,
                "error": {"code": "AI_DISABLED", "message": "AI stage is disabled"},
            },
        )

    registry_status = ai_registry.get_status()

    # Fail-closed branch: get_snapshot raises. The route stays 200 (mirrors
    # GET /admin/ai/profile) so admin tooling can render degraded diagnostics.
    try:
        snapshot = ai_registry.get_snapshot()
    except AiProfileRegistryUnavailableError:
        return {
            "profile": None,
            "generation": registry_status.get("generation", 0),
            "config_valid": registry_status.get("config_valid", False),
            "last_reload_error": registry_status.get("last_reload_error"),
        }

    editable = _profile_to_editable(snapshot.profile)
    return {
        "prompt_header": editable["prompt_header"],
        "summary_field": editable["summary_field"],
        "bill": editable["bill"],
        "fields": editable["fields"],
        "generation": registry_status.get("generation", 0),
        "config_valid": registry_status.get("config_valid", True),
    }


class ProfileFieldInput(BaseModel):
    """One [[fields]] entry in a PUT /admin/config/profile body.

    `enabled` is required-without-default so a UI collection omission fails
    loud (422) rather than silently defaulting the field to "on" — a stale
    toggle state in the UI must never reach disk unnoticed.
    """

    model_config = ConfigDict(extra="forbid")

    ai_key: str = Field(..., min_length=1, max_length=128)
    feishu_field: str | None = Field(default=None, max_length=128)
    type: str = Field(..., min_length=1, max_length=64)
    target: str = Field(..., min_length=1, max_length=64)
    fallback: str | None = Field(default=None, max_length=128)
    prompt: str | None = Field(default=None, max_length=4096)
    source: str | None = Field(default=None, max_length=64)
    enabled: bool


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_header: str = Field(..., min_length=1, max_length=8192)
    summary_field: str = Field(..., min_length=1, max_length=128)
    bill: dict[str, str]
    fields: list[ProfileFieldInput]


class ConfigProfilePutBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: ProfileInput
    base_generation: int = Field(..., ge=0)


@app.put(
    "/admin/config/profile",
    responses={
        401: {"model": WebhookErrorResponse},
        404: {"model": WebhookErrorResponse},
        409: {"model": WebhookErrorResponse},
        422: {"model": WebhookErrorResponse},
        502: {"model": WebhookErrorResponse},
    },
)
async def config_profile_put(
    payload: ConfigProfilePutBody,
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    request_id = x_request_id or f"req_{uuid.uuid4().hex[:12]}"
    _require_admin_auth(settings, x_admin_token, request_id)

    ai_registry: AiProfileRegistry | None = getattr(request.app.state, "ai_registry", None)
    if not settings.ai_enabled or ai_registry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "success": False,
                "request_id": request_id,
                "error": {"code": "AI_DISABLED", "message": "AI stage is disabled"},
            },
        )

    profile_file: Path | None = settings.ai_profile_file
    if profile_file is None:
        # AI is enabled but no profile path configured — cannot write.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "success": False,
                "request_id": request_id,
                "error": {
                    "code": "RUNTIME_READONLY",
                    "message": "AI_PROFILE_FILE is not configured",
                    "suggested_action": "host-edit",
                },
            },
        )

    async with _profile_write_lock:
        registry_status = ai_registry.get_status()
        current_generation = registry_status.get("generation", 0)
        if current_generation != payload.base_generation:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "success": False,
                    "request_id": request_id,
                    "error": {
                        "code": "STALE_WRITE",
                        "message": (
                            f"registry generation {current_generation} != "
                            f"base_generation {payload.base_generation}"
                        ),
                    },
                },
            )

        # Resolve the extract-table三元组 via the target registry (legacy mode
        # returns the env-configured default target; dynamic mode uses
        # default_alias). TargetSelectorError → 422 (mirrors ingest_ocr).
        target_registry: TargetRegistry = request.app.state.target_registry
        try:
            target = target_registry.resolve(book_alias=None, year=None)
        except TargetSelectorError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "success": False,
                    "request_id": request_id,
                    "error": {
                        "code": "INVALID_TARGET_SELECTOR",
                        "message": str(exc),
                    },
                },
            ) from exc
        except TargetRegistryUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "success": False,
                    "request_id": request_id,
                    "error": {
                        "code": "TARGET_REGISTRY_UNAVAILABLE",
                        "message": str(exc),
                    },
                },
            ) from exc

        # Convert the editable shape (top-level summary_field) to the
        # dump_profile input shape (extract.summary_field).
        profile_in = payload.profile.model_dump()
        dump_input = {
            "prompt_header": profile_in.get("prompt_header"),
            "extract": {"summary_field": profile_in.get("summary_field")},
            "bill": profile_in.get("bill", {}),
            "fields": profile_in.get("fields", []),
        }
        candidate_text = dump_profile(dump_input)

        feishu_client: FeishuClient = request.app.state.feishu_client
        _validated_profile, _whitelists, errors = await validate_profile_candidate(
            candidate_text,
            feishu_client,
            target.app_token,
            target.table_id,
        )
        if errors:
            logger.warning(
                "config profile PUT validation failed request_id=%s errors=%s",
                request_id,
                errors,
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "success": False,
                    "request_id": request_id,
                    "errors": errors,
                },
            )

        # Atomic save: .bak the current file → write tmp → os.replace(tmp, real).
        # os.replace is atomic on POSIX (rename(2)); a crash between tmp write
        # and replace leaves the old file intact.
        try:
            if profile_file.is_file():
                bak_path = profile_file.with_suffix(".toml.bak")
                bak_path.write_bytes(profile_file.read_bytes())
            tmp_path = profile_file.with_suffix(".toml.tmp")
            tmp_path.write_text(candidate_text, encoding="utf-8")
            os.replace(tmp_path, profile_file)
        except OSError as exc:
            # :ro mount (Docker) or permission issue — surface a host-edit hint.
            if exc.errno in (errno.EROFS, errno.EACCES, errno.EPERM):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "success": False,
                        "request_id": request_id,
                        "error": {
                            "code": "RUNTIME_READONLY",
                            "message": (
                                "runtime 目录只读（Docker :ro 挂载），"
                                "请在宿主机编辑文件后调用 /admin/config/reload"
                            ),
                            "suggested_action": "host-edit",
                        },
                    },
                ) from exc
            raise

        # Reload the registry so the new generation reflects the saved file.
        # reload(force=True) returns get_status(); we surface the new generation.
        await ai_registry.reload(force=True)
        new_status = ai_registry.get_status()

    return {
        "success": True,
        "generation": new_status.get("generation", 0),
        "warnings": [],
    }


# ---------------------------------------------------------------------------
# Admin config targets read/write (frontend config UI support)
#
# GET /admin/config/targets  : registry snapshot (degrades 200 on fail-closed;
#                              legacy mode returns the single legacy target)
# PUT /admin/config/targets  : validate → atomic save (tmp + os.replace) → reload
#
# PUT is full-replace: the body's targets list wholly replaces the file. Legacy
# mode (FEISHU_TARGETS_FILE unset) → 409 LEGACY_MODE (UI editing not supported).
# Auth mirrors reload_config (X-Admin-Token + compare_digest; CONFIG_RELOAD_TOKEN
# unset → 404 RELOAD_DISABLED). PUT serializes via a module-level asyncio.Lock
# (distinct from _profile_write_lock — two files, independent) + generation guard
# (STALE_WRITE 409). The webhook contract is unchanged.
# ---------------------------------------------------------------------------

# Module-level write mutex — one in-flight targets save at a time. Distinct from
# _profile_write_lock (the two files are independent; no reason for one edit to
# block the other).
_targets_write_lock = asyncio.Lock()

# Alias regex mirrored from target_registry._ALIAS_PATTERN — duplicated here so
# the PUT endpoint can attribute errors to targets[i].alias without importing a
# private. Kept in sync by test_config_targets_api.py (regex parity locked).
_TARGETS_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# Required string fields per target — used for collect-all path attribution.
_TARGETS_REQUIRED_STR_FIELDS = ("app_token", "table_id", "record_id")


def _collect_target_errors(
    body_targets: list, default_alias: object, *, original_field_name_default: str
) -> list[dict[str, str]]:
    """Collect-all validation for the PUT /admin/config/targets body.

    Mirrors app.target_registry.validate_targets rules but returns a list of
    ``{"path": str, "message": str}`` entries instead of raising. Errors are
    attributed to ``targets[i].<field>`` (per-target) or ``default_alias``
    (top-level) so the UI can highlight the offending input.

    Does NOT short-circuit: a target with a bad alias AND a missing app_token
    surfaces both errors. Duplicate years are attributed to the second
    occurrence (targets[j].year).
    """
    errors: list[dict[str, str]] = []

    # default_alias: must be a non-empty string present in the targets list.
    if not isinstance(default_alias, str) or not default_alias.strip():
        errors.append({"path": "default_alias", "message": "default_alias must be a non-empty string"})
        default_alias_clean = ""
    else:
        default_alias_clean = default_alias.strip()

    aliases_seen: dict[str, int] = {}
    years_seen: dict[int, int] = {}
    valid_targets: dict[str, dict] = {}

    for i, raw in enumerate(body_targets):
        if not isinstance(raw, dict):
            errors.append({"path": f"targets[{i}]", "message": "target must be an object"})
            continue

        # alias
        alias = raw.get("alias")
        if not isinstance(alias, str) or not _TARGETS_ALIAS_RE.match(alias):
            errors.append({"path": f"targets[{i}].alias", "message": f"invalid target alias: {alias!r}"})
            alias = None
        elif alias in aliases_seen:
            errors.append({
                "path": f"targets[{i}].alias",
                "message": f"duplicate target alias: {alias}",
            })
            alias = None
        else:
            aliases_seen[alias] = i

        # Required string fields
        for field_name in _TARGETS_REQUIRED_STR_FIELDS:
            val = raw.get(field_name)
            if not isinstance(val, str) or not val.strip():
                errors.append({
                    "path": f"targets[{i}].{field_name}",
                    "message": f"target {alias or i} is missing {field_name}",
                })

        # year: None OK; int>0 OK; else error
        year_raw = raw.get("year")
        year: int | None = None
        if year_raw is None:
            year = None
        elif isinstance(year_raw, int) and year_raw > 0:
            year = year_raw
            if year in years_seen:
                errors.append({
                    "path": f"targets[{i}].year",
                    "message": f"duplicate target year: {year}",
                })
            else:
                years_seen[year] = i
        else:
            errors.append({
                "path": f"targets[{i}].year",
                "message": f"target {alias or i} has invalid year",
            })

        # original_field_name: optional, falls back to default; must be a string if present
        ofn = raw.get("original_field_name", original_field_name_default)
        if not isinstance(ofn, str):
            errors.append({
                "path": f"targets[{i}].original_field_name",
                "message": f"target {alias or i} has invalid original_field_name",
            })

        # enabled: optional, defaults True; must be bool if present
        enabled_raw = raw.get("enabled", True)
        if not isinstance(enabled_raw, bool):
            errors.append({
                "path": f"targets[{i}].enabled",
                "message": f"target {alias or i} has invalid enabled (must be bool)",
            })

        if alias is not None:
            valid_targets[alias] = raw

    # default_alias must exist in targets AND be enabled (only check if we got
    # past the string check — avoids a redundant error when default_alias itself
    # was malformed).
    if default_alias_clean:
        default_target = valid_targets.get(default_alias_clean)
        if default_target is None:
            errors.append({
                "path": "default_alias",
                "message": "default_alias does not exist in targets registry",
            })
        elif not isinstance(default_target.get("enabled", True), bool) or default_target.get("enabled", True) is False:
            errors.append({
                "path": "default_alias",
                "message": "default_alias target must be enabled",
            })

    return errors


def _body_to_dump_input(body: dict) -> dict:
    """Convert PUT body shape to dump_targets input shape.

    Body: ``{"default_alias": str, "targets": [{alias, year, app_token, ...}]}``
    dump_targets: ``{"default_alias": str, "targets": {alias: {year, app_token, ...}}}``

    year=None is preserved (dump_targets omits it); enabled defaults to True.
    """
    targets_dict: dict[str, dict] = {}
    for t in body.get("targets", []):
        alias = t["alias"]
        entry: dict[str, object] = {
            "app_token": t.get("app_token"),
            "table_id": t.get("table_id"),
            "record_id": t.get("record_id"),
            "original_field_name": t.get("original_field_name"),
            "year": t.get("year"),
            "enabled": t.get("enabled", True),
        }
        targets_dict[alias] = entry
    return {"default_alias": body["default_alias"], "targets": targets_dict}


@app.get(
    "/admin/config/targets",
    responses={401: {"model": WebhookErrorResponse}, 404: {"model": WebhookErrorResponse}},
)
async def config_targets_get(
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    request_id = x_request_id or f"req_{uuid.uuid4().hex[:12]}"
    _require_admin_auth(settings, x_admin_token, request_id)

    target_registry: TargetRegistry = request.app.state.target_registry
    describe = target_registry.describe()
    generation = describe.get("reload_generation", 0)
    config_valid = describe.get("config_valid", False)

    # Fail-closed branch: get_snapshot raises / returns None. The route stays
    # 200 (mirrors GET /admin/config/profile) so admin tooling can render the
    # degraded diagnostics instead of a 503.
    try:
        snapshot = target_registry.get_snapshot()
    except Exception:  # noqa: BLE001 — registry raises typed errors; degrade to 200
        return {
            "mode": describe.get("mode", "uninitialized"),
            "default_alias": None,
            "targets": None,
            "generation": generation,
            "config_valid": config_valid,
            "last_reload_error": describe.get("last_reload_error"),
            "source_path": describe.get("source_path"),
        }

    targets = [
        {
            "alias": t.alias,
            "year": t.year,
            "app_token": t.app_token,
            "table_id": t.table_id,
            "record_id": t.record_id,
            "original_field_name": t.original_field_name,
            "enabled": t.enabled,
        }
        for t in snapshot.targets_by_alias.values()
    ]
    return {
        "mode": describe.get("mode", snapshot.mode),
        "default_alias": describe.get("default_alias", snapshot.default_alias),
        "targets": targets,
        "generation": generation,
        "config_valid": config_valid,
    }


class ConfigTargetsPutBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_alias: str
    targets: list[dict[str, object]]
    base_generation: int = Field(..., ge=0)


@app.put(
    "/admin/config/targets",
    responses={
        401: {"model": WebhookErrorResponse},
        404: {"model": WebhookErrorResponse},
        409: {"model": WebhookErrorResponse},
        422: {"model": WebhookErrorResponse},
    },
)
async def config_targets_put(
    payload: ConfigTargetsPutBody,
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    request_id = x_request_id or f"req_{uuid.uuid4().hex[:12]}"
    _require_admin_auth(settings, x_admin_token, request_id)

    # Legacy mode → 409 LEGACY_MODE. UI editing is dynamic-mode only; the legacy
    # single-target env vars (FEISHU_APP_TOKEN/TABLE_ID/RECORD_ID) are managed
    # via the host env file, not this endpoint.
    targets_file: Path | None = settings.feishu_targets_file
    if targets_file is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "success": False,
                "request_id": request_id,
                "error": {
                    "code": "LEGACY_MODE",
                    "message": (
                        "旧版单目标模式不支持 UI 编辑，请配置 FEISHU_TARGETS_FILE 后使用动态模式"
                    ),
                },
            },
        )

    target_registry: TargetRegistry = request.app.state.target_registry

    async with _targets_write_lock:
        # Generation guard — describe()["reload_generation"] is the canonical
        # key (asymmetric with the AI registry's "generation" key; mirrors the
        # real TargetRegistry.describe()).
        describe = target_registry.describe()
        current_generation = describe.get("reload_generation", 0)
        if current_generation != payload.base_generation:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "success": False,
                    "request_id": request_id,
                    "error": {
                        "code": "STALE_WRITE",
                        "message": (
                            f"registry generation {current_generation} != "
                            f"base_generation {payload.base_generation}"
                        ),
                    },
                },
            )

        # Collect-all validation — produces path-tagged errors for the UI.
        errors = _collect_target_errors(
            payload.targets,
            payload.default_alias,
            original_field_name_default=settings.feishu_original_field_name,
        )
        if errors:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "success": False,
                    "request_id": request_id,
                    "errors": errors,
                },
            )

        # Body → dump_targets input shape (list → dict keyed by alias).
        dump_input = _body_to_dump_input(payload.model_dump())
        candidate_text = dump_targets(dump_input)

        # Atomic save: .bak the current file → write tmp → os.replace(tmp, real).
        # os.replace is atomic on POSIX (rename(2)); a crash between tmp write
        # and replace leaves the old file intact.
        try:
            if targets_file.is_file():
                bak_path = targets_file.with_suffix(".toml.bak")
                bak_path.write_bytes(targets_file.read_bytes())
            tmp_path = targets_file.with_suffix(".toml.tmp")
            tmp_path.write_text(candidate_text, encoding="utf-8")
            os.replace(tmp_path, targets_file)
        except OSError as exc:
            if exc.errno in (errno.EROFS, errno.EACCES, errno.EPERM):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "success": False,
                        "request_id": request_id,
                        "error": {
                            "code": "RUNTIME_READONLY",
                            "message": (
                                "runtime 目录只读（Docker :ro 挂载），"
                                "请在宿主机编辑文件后调用 /admin/config/reload"
                            ),
                            "suggested_action": "host-edit",
                        },
                    },
                ) from exc
            raise

        # Reload the registry so the new generation reflects the saved file.
        target_registry.reload(force=True)
        new_describe = target_registry.describe()

    return {
        "success": True,
        "generation": new_describe.get("reload_generation", 0),
    }


# ---------------------------------------------------------------------------
# Admin config env read/write (frontend config UI support)
#
# GET /admin/config/env  : AI connection settings (NEVER returns secret values;
#                          *_set booleans only)
# PUT /admin/config/env  : line-based env file edit (atomic save)
#
# PUT reads the existing env file, replaces/adds/deletes AI_* lines, preserves
# all other lines (comments + FEISHU_* + WEBHOOK_*) byte-for-byte. env_file_path
# None (env vars set directly, no file) → 409 ENV_FILE_NOT_FOUND. Auth mirrors
# reload_config. A separate module-level asyncio.Lock serializes env writes.
# The webhook contract is unchanged; a restart is required for changes to take
# effect (env vars are read once at import via _load_runtime_env_files).
# ---------------------------------------------------------------------------

_env_write_lock = asyncio.Lock()

# Characters that force double-quote wrapping per config.py:36 strip semantics.
_ENV_QUOTE_CHARS = frozenset('#= "\'\t\\')


def _quote_env_value(value: str) -> str:
    """Wrap *value* for an env file line, quoting when necessary.

    Mirrors the inverse of config.py:36 strip-one-layer semantics: if the value
    contains any char that would break line parsing (# = space " ' \\ tab), wrap
    in double quotes and escape internal " and \\ so the loader recovers the
    original. Bare values (no special chars) pass through unquoted.
    """
    if value == "":
        return '""'
    if any(c in _ENV_QUOTE_CHARS for c in value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _format_env_line(key: str, value: object) -> str:
    if isinstance(value, bool):
        return f"{key}={'true' if value else 'false'}"
    if isinstance(value, int):
        return f"{key}={value}"
    return f"{key}={_quote_env_value(str(value))}"


def _rewrite_env_lines(
    original_text: str, updates: dict[str, object | None]
) -> str:
    """Apply *updates* to an env file's text, preserving all other lines.

    For each AI_* key in *updates*:
      - value is None → delete any matching existing line (delete semantics)
      - value is a str/int/bool → replace the first matching line; if absent,
        append at the end of the file
    Lines not matching any update key pass through verbatim. The matching
    pattern ``^(export )?AI_(\\w+)=`` mirrors config.py:24 loader's export-strip.
    """
    env_line_re = re.compile(r"^(export\s+)?(AI_\w+)\s*=")
    consumed: set[str] = set()
    out_lines: list[str] = []

    for raw_line in original_text.splitlines():
        m = env_line_re.match(raw_line)
        if m:
            key = m.group(2)
            if key in updates:
                new_val = updates[key]
                if new_val is None:
                    consumed.add(key)
                    continue
                out_lines.append(_format_env_line(key, new_val))
                consumed.add(key)
                continue
        out_lines.append(raw_line)

    # Append any update keys that weren't present in the original file.
    # Order: iterate updates in insertion order (Python dict preserves it) so
    # the body field order is reflected in the appended block.
    appended: list[str] = []
    for key, new_val in updates.items():
        if key in consumed:
            continue
        if new_val is None:
            continue
        appended.append(_format_env_line(key, new_val))

    if appended:
        # Ensure a newline separates existing content from appended block.
        if out_lines and out_lines[-1].strip() != "":
            out_lines.append("")
        out_lines.extend(appended)

    # Preserve trailing newline if the original had one.
    had_trailing_newline = original_text.endswith("\n")
    result = "\n".join(out_lines)
    if had_trailing_newline:
        result += "\n"
    return result


@app.get(
    "/admin/config/env",
    responses={401: {"model": WebhookErrorResponse}, 404: {"model": WebhookErrorResponse}},
)
async def config_env_get(
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    request_id = x_request_id or f"req_{uuid.uuid4().hex[:12]}"
    _require_admin_auth(settings, x_admin_token, request_id)

    env_file = settings.env_file_path
    return {
        "env_file": str(env_file) if env_file is not None else None,
        "ai": {
            "ai_enabled": settings.ai_enabled,
            "ai_provider": settings.ai_provider,
            "ai_base_url": settings.ai_base_url,
            "ai_model": settings.ai_model,
            "ai_timeout_seconds": settings.ai_timeout_seconds,
            "ai_api_key_set": bool(settings.ai_api_key),
        },
        "other": {
            "feishu_app_id": settings.feishu_app_id,
            "feishu_app_secret_set": bool(settings.feishu_app_secret),
            "webhook_shared_token_set": bool(settings.webhook_shared_token),
            "config_reload_token_set": bool(settings.config_reload_token),
        },
        "restart_required": True,
    }


class ConfigEnvPutBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ai: dict[str, object]
    api_key: str | None = None


@app.put(
    "/admin/config/env",
    responses={
        401: {"model": WebhookErrorResponse},
        404: {"model": WebhookErrorResponse},
        409: {"model": WebhookErrorResponse},
        422: {"model": WebhookErrorResponse},
    },
)
async def config_env_put(
    payload: ConfigEnvPutBody,
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    request_id = x_request_id or f"req_{uuid.uuid4().hex[:12]}"
    _require_admin_auth(settings, x_admin_token, request_id)

    env_file: Path | None = settings.env_file_path
    if env_file is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "success": False,
                "request_id": request_id,
                "error": {
                    "code": "ENV_FILE_NOT_FOUND",
                    "message": (
                        "未找到 env 文件（环境变量直设模式），"
                        "请通过 FEISHU_ENV_FILE 或项目根 .env 文件管理"
                    ),
                },
            },
        )

    # Build the updates dict from the body. AI_API_KEY is special: non-empty
    # string → update; None/"" → skip (leave the existing line untouched). The
    # other AI_* keys: None → delete the line; str/int/bool → replace/append.
    ai = payload.ai
    updates: dict[str, object | None] = {}

    if "ai_enabled" in ai:
        ai_enabled = ai["ai_enabled"]
        if isinstance(ai_enabled, bool):
            updates["AI_ENABLED"] = ai_enabled

    # Body field name → env key name. None → delete line; str/int → replace/append.
    _STR_FIELD_MAP = {
        "ai_provider": "AI_PROVIDER",
        "ai_base_url": "AI_BASE_URL",
        "ai_model": "AI_MODEL",
    }
    _INT_FIELD_MAP = {"ai_timeout_seconds": "AI_TIMEOUT_SECONDS"}

    for body_field, env_key in _STR_FIELD_MAP.items():
        if body_field in ai:
            val = ai[body_field]
            if val is None or isinstance(val, str):
                updates[env_key] = val

    for body_field, env_key in _INT_FIELD_MAP.items():
        if body_field in ai:
            val = ai[body_field]
            if val is None or isinstance(val, int):
                updates[env_key] = val

    api_key = payload.api_key
    if api_key:
        updates["AI_API_KEY"] = api_key

    async with _env_write_lock:
        try:
            original_text = env_file.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            # File disappeared between GET and PUT — treat as not-editable.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "success": False,
                    "request_id": request_id,
                    "error": {
                        "code": "ENV_FILE_NOT_FOUND",
                        "message": f"env file vanished: {env_file}",
                    },
                },
            ) from exc

        new_text = _rewrite_env_lines(original_text, updates)

        try:
            bak_path = env_file.with_suffix(".env.bak")
            bak_path.write_bytes(original_text.encode("utf-8"))
            tmp_path = env_file.with_suffix(".env.tmp")
            tmp_path.write_text(new_text, encoding="utf-8")
            os.replace(tmp_path, env_file)
        except OSError as exc:
            if exc.errno in (errno.EROFS, errno.EACCES, errno.EPERM):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "success": False,
                        "request_id": request_id,
                        "error": {
                            "code": "RUNTIME_READONLY",
                            "message": (
                                "runtime 目录只读（Docker :ro 挂载），"
                                "请在宿主机编辑文件后重启服务"
                            ),
                            "suggested_action": "host-edit",
                        },
                    },
                ) from exc
            raise

    return {
        "success": True,
        "restart_required": True,
    }
