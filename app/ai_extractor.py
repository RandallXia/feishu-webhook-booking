"""Dual-protocol structured AI extraction adapter.

Calls anthropic /v1/messages or openai /chat/completions with a forced
tool_call (submit_bill) and parses the structured response into ExtractionResult.

Design:
- prompt-stateless: prompts are passed per-call to extract(), enabling the
  caller to hot-reload profile changes without re-constructing this object.
- single HTTP call, no retry.
- per-call httpx.AsyncClient (aligns with feishu_client.py pattern).
- errors carry `stage` (request/parse/validate) for upstream error mapping.
- never logs original_text or ai_api_key.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

import httpx

from .config import Settings


logger = logging.getLogger("feishu_webhook_service.ai_extractor")


# Field name → JSON schema type for the submit_bill tool input.
# order matters for the required list (deterministic, matches ExtractionResult).
_FIELD_TYPES: dict[str, str] = {
    "summary": "string",
    "description": "string",
    "flow_type": "string",
    "amount": "number",
    "category": "string",
    "payment_method": "string",
    "bill_date": "string",
}

_REQUIRED_KEYS = list(_FIELD_TYPES.keys())
_BILL_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class AiExtractorError(RuntimeError):
    """Carries a `stage` (request/parse/validate) for upstream error mapping."""

    def __init__(self, message: str, *, stage: str) -> None:
        super().__init__(message)
        self.stage = stage


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    summary: str
    description: str
    flow_type: str
    amount: float
    category: str
    payment_method: str
    bill_date: str  # YYYY-MM-DD


def _build_system_text(prompt_header: str, field_prompts: dict[str, str]) -> str:
    lines = [prompt_header]
    lines.extend(f"- {k}: {v}" for k, v in field_prompts.items())
    return "\n".join(lines)


def _build_tool_properties(field_prompts: dict[str, str]) -> dict[str, dict[str, str]]:
    # Use field_prompts descriptions when available, fall back to the key name.
    return {
        key: {"type": _FIELD_TYPES[key], "description": field_prompts.get(key, key)}
        for key in _REQUIRED_KEYS
    }


def _validate_input(raw: dict) -> ExtractionResult:
    missing = [k for k in _REQUIRED_KEYS if k not in raw]
    if missing:
        raise AiExtractorError(
            f"Missing required fields: {missing}", stage="validate"
        )

    summary = raw["summary"]
    description = raw["description"]
    flow_type = raw["flow_type"]
    category = raw["category"]
    payment_method = raw["payment_method"]
    bill_date = raw["bill_date"]

    for name, value in (
        ("summary", summary),
        ("description", description),
        ("flow_type", flow_type),
        ("category", category),
        ("payment_method", payment_method),
    ):
        if not isinstance(value, str) or not value.strip():
            raise AiExtractorError(
                f"Field {name!r} must be a non-empty string", stage="validate"
            )

    try:
        amount = float(raw["amount"])
    except (TypeError, ValueError) as exc:
        raise AiExtractorError(
            f"Field 'amount' is not a number: {raw['amount']!r}", stage="validate"
        ) from exc
    if amount <= 0:
        raise AiExtractorError(
            f"Field 'amount' must be positive, got {amount}", stage="validate"
        )

    if not isinstance(bill_date, str) or not _BILL_DATE_RE.match(bill_date):
        raise AiExtractorError(
            f"Field 'bill_date' must match YYYY-MM-DD, got {bill_date!r}",
            stage="validate",
        )

    return ExtractionResult(
        summary=summary,
        description=description,
        flow_type=flow_type,
        amount=amount,
        category=category,
        payment_method=payment_method,
        bill_date=bill_date,
    )


class AiExtractor:
    """Prompt-stateless AI extractor supporting anthropic and openai protocols."""

    def __init__(self, settings: Settings) -> None:
        # NO prompts stored here — prompts are passed per-call to extract().
        self._settings = settings
        self._timeout = httpx.Timeout(settings.ai_timeout_seconds)

    async def extract(
        self,
        original_text: str,
        prompt_header: str,
        field_prompts: dict[str, str],
    ) -> ExtractionResult:
        provider = self._settings.ai_provider
        if provider == "anthropic":
            raw = await self._call_anthropic(original_text, prompt_header, field_prompts)
        elif provider == "openai":
            raw = await self._call_openai(original_text, prompt_header, field_prompts)
        else:
            raise AiExtractorError(
                f"Unsupported ai_provider: {provider!r}", stage="request"
            )
        return _validate_input(raw)

    async def _call_anthropic(
        self,
        original_text: str,
        prompt_header: str,
        field_prompts: dict[str, str],
    ) -> dict:
        settings = self._settings
        base = settings.ai_base_url or "https://api.anthropic.com"
        url = f"{base}/v1/messages"
        headers = {
            "x-api-key": settings.ai_api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        body = {
            "model": settings.ai_model,
            "max_tokens": 1024,
            "system": _build_system_text(prompt_header, field_prompts),
            "messages": [{"role": "user", "content": original_text}],
            "tools": [
                {
                    "name": "submit_bill",
                    "description": "Submit extracted bill fields",
                    "input_schema": {
                        "type": "object",
                        "properties": _build_tool_properties(field_prompts),
                        "required": _REQUIRED_KEYS,
                    },
                }
            ],
            "tool_choice": {"type": "tool", "name": "submit_bill"},
        }

        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise AiExtractorError(
                f"Anthropic request timed out: {exc}", stage="request"
            ) from exc
        except httpx.HTTPError as exc:
            raise AiExtractorError(
                f"Anthropic request failed: {exc}", stage="request"
            ) from exc

        if response.status_code != 200:
            raise AiExtractorError(
                f"Anthropic returned HTTP {response.status_code}", stage="request"
            )

        try:
            data = response.json()
        except Exception as exc:
            raise AiExtractorError(
                f"Failed to parse Anthropic response JSON: {exc}", stage="parse"
            ) from exc

        try:
            content = data["content"]
            tool_input = None
            for item in content:
                if item.get("type") == "tool_use":
                    tool_input = item.get("input")
                    break
            if not isinstance(tool_input, dict):
                raise AiExtractorError(
                    "Anthropic response missing tool_use item with input",
                    stage="parse",
                )
        except (KeyError, TypeError) as exc:
            raise AiExtractorError(
                f"Unexpected Anthropic response shape: {exc}", stage="parse"
            ) from exc

        elapsed_ms = int((time.time() - start) * 1000)
        logger.info(
            "AI extract provider=%s model=%s stage=success elapsed_ms=%s",
            settings.ai_provider,
            settings.ai_model,
            elapsed_ms,
        )
        return tool_input

    async def _call_openai(
        self,
        original_text: str,
        prompt_header: str,
        field_prompts: dict[str, str],
    ) -> dict:
        settings = self._settings
        base = settings.ai_base_url or "https://api.openai.com/v1"
        url = f"{base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.ai_api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": settings.ai_model,
            "messages": [
                {"role": "system", "content": _build_system_text(prompt_header, field_prompts)},
                {"role": "user", "content": original_text},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "submit_bill",
                        "description": "Submit extracted bill fields",
                        "parameters": {
                            "type": "object",
                            "properties": _build_tool_properties(field_prompts),
                            "required": _REQUIRED_KEYS,
                        },
                    },
                }
            ],
            "tool_choice": {
                "type": "function",
                "function": {"name": "submit_bill"},
            },
        }

        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise AiExtractorError(
                f"OpenAI request timed out: {exc}", stage="request"
            ) from exc
        except httpx.HTTPError as exc:
            raise AiExtractorError(
                f"OpenAI request failed: {exc}", stage="request"
            ) from exc

        if response.status_code != 200:
            raise AiExtractorError(
                f"OpenAI returned HTTP {response.status_code}", stage="request"
            )

        try:
            data = response.json()
            tool_call = data["choices"][0]["message"]["tool_calls"][0]
            arguments = tool_call["function"]["arguments"]
        except (KeyError, TypeError, IndexError) as exc:
            raise AiExtractorError(
                f"Unexpected OpenAI response shape: {exc}", stage="parse"
            ) from exc

        if not isinstance(arguments, str):
            raise AiExtractorError(
                f"OpenAI tool arguments is not a string: {type(arguments).__name__}",
                stage="parse",
            )

        try:
            tool_input = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise AiExtractorError(
                f"Failed to parse OpenAI tool arguments JSON: {exc}", stage="parse"
            ) from exc

        if not isinstance(tool_input, dict):
            raise AiExtractorError(
                f"OpenAI tool arguments did not decode to an object: {type(tool_input).__name__}",
                stage="parse",
            )

        elapsed_ms = int((time.time() - start) * 1000)
        logger.info(
            "AI extract provider=%s model=%s stage=success elapsed_ms=%s",
            settings.ai_provider,
            settings.ai_model,
            elapsed_ms,
        )
        return tool_input
