from __future__ import annotations

import logging
import re
import secrets
import urllib.parse
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field

from .ai_extractor import AiExtractor, AiExtractorError, ExtractionResult
from .ai_profile import (
    AiProfile,
    AiProfileRegistry,
    AiProfileRegistryUnavailableError,
    ProfileConfigError,
    build_field_prompts,
)
from .config import Settings, get_settings
from .feishu_client import FeishuClient, FeishuClientError
from .field_codec import FieldSpec, encode_fields
from .pipeline import AiPipeline, PipelineResult
from .target_registry import TargetRegistry, TargetRegistryError, TargetRegistryUnavailableError, TargetSelectorError


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
