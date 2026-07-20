from __future__ import annotations

import logging
import secrets
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings, get_settings
from .feishu_client import FeishuClient, FeishuClientError
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
    yield


app = FastAPI(title="Feishu Webhook Service", version="0.1.0", lifespan=lifespan)
logger = logging.getLogger("feishu_webhook_service")


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
    return WebhookSuccessResponse(
        request_id=request_id,
        record_id=record_id,
        book_alias=target.alias,
        message="configured record updated and 原始信息 updated",
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

    return {
        "success": True,
        "request_id": request_id,
        **status_payload,
    }
