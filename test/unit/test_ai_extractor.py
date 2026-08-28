# test_ai_extractor.py — Behavior tests for dual-protocol AI extraction adapter
#
# Tests AiExtractor (app/ai_extractor.py):
#   - prompt-stateless: prompts passed per-call to extract(), not stored in constructor
#   - dual-protocol: dispatches to anthropic /v1/messages or openai /chat/completions
#   - structured tool-call response → ExtractionResult (7 fields)
#   - single HTTP call, no retry; errors carry stage (request/parse/validate)
#
# conftest.py pins AI_ENABLED=false at module level. Tests below build an
# AI-enabled Settings via dataclasses.replace(get_settings(), ai_*=...) to
# bypass get_settings() env validation, then inject httpx.MockTransport via
# monkeypatch on httpx.AsyncClient.__init__ (same pattern as test_feishu_client_ext.py).

import dataclasses
import json

import httpx
import pytest

from app.ai_extractor import AiExtractor, AiExtractorError, ExtractionResult
from app.config import get_settings


# ─── Test helpers ──────────────────────────────────────────────────────────


def _make_ai_settings(
    provider="anthropic",
    base_url=None,
    api_key="sk-test",
    model="test-model",
    timeout=20,
):
    """Build an AI-enabled Settings via dataclasses.replace (bypasses env validation)."""
    return dataclasses.replace(
        get_settings(),
        ai_enabled=True,
        ai_provider=provider,
        ai_base_url=base_url,
        ai_api_key=api_key,
        ai_model=model,
        ai_timeout_seconds=timeout,
        ai_profile_file=None,
        ai_profile_reload_interval_seconds=10,
        ai_dedup_ttl_seconds=300,
    )


def _patch_httpx(monkeypatch, handler):
    """Inject httpx.MockTransport into httpx.AsyncClient via __init__ monkeypatch."""
    transport = httpx.MockTransport(handler)
    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)


_VALID_INPUT = {
    "summary": "麦当劳 ¥42",
    "description": "麦当劳午餐",
    "flow_type": "支出",
    "amount": 42.0,
    "category": "餐饮",
    "payment_method": "微信",
    "bill_date": "2026-08-28",
}


def _anthropic_response(input_dict=_VALID_INPUT):
    return httpx.Response(
        200,
        json={
            "content": [
                {"type": "tool_use", "name": "submit_bill", "input": input_dict}
            ]
        },
    )


def _openai_response(input_dict=_VALID_INPUT):
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "submit_bill",
                                    "arguments": json.dumps(input_dict, ensure_ascii=False),
                                }
                            }
                        ]
                    }
                }
            ]
        },
    )


_FIELD_PROMPTS = {
    "summary": "一句话摘要",
    "description": "详细描述",
    "flow_type": "支出或收入",
    "amount": "金额",
    "category": "分类",
    "payment_method": "支付方式",
    "bill_date": "YYYY-MM-DD",
}


_PROMPT_HEADER = "你是一个账单提取助手。"


# ─── Tests ─────────────────────────────────────────────────────────────────


async def test_prompt_stateless_two_different_prompts(monkeypatch):
    # Given: an AiExtractor with no prompts stored in the constructor
    # And: a mock that records the request body for each call
    settings = _make_ai_settings(provider="anthropic")
    captured_bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_bodies.append(json.loads(request.content))
        return _anthropic_response()

    _patch_httpx(monkeypatch, handler)
    extractor = AiExtractor(settings)

    # When: extract() is called twice with different prompt_header + field_prompts
    prompts_a = {**_FIELD_PROMPTS, "summary": "摘要A"}
    prompts_b = {**_FIELD_PROMPTS, "summary": "摘要B"}
    await extractor.extract("text-a", "headerA", prompts_a)
    await extractor.extract("text-b", "headerB", prompts_b)

    # Then: each request body contains only its own prompts (system/content)
    system_a = captured_bodies[0]["system"]
    system_b = captured_bodies[1]["system"]
    assert "headerA" in system_a and "摘要A" in system_a
    assert "headerB" in system_b and "摘要B" in system_b
    # And: neither request leaks the other's prompt text
    assert "headerB" not in system_a and "摘要B" not in system_a
    assert "headerA" not in system_b and "摘要A" not in system_b


async def test_anthropic_happy_path(monkeypatch):
    # Given: settings.ai_provider="anthropic" with default base_url (None)
    # And: a mock returning a valid tool_use response
    settings = _make_ai_settings(provider="anthropic")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _anthropic_response()

    _patch_httpx(monkeypatch, handler)
    extractor = AiExtractor(settings)

    # When: extract() is called
    result = await extractor.extract("some ocr text", _PROMPT_HEADER, _FIELD_PROMPTS)

    # Then: request URL path is /v1/messages on api.anthropic.com
    req = captured[0]
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    # And: header x-api-key is present
    assert req.headers["x-api-key"] == "sk-test"
    assert req.headers["anthropic-version"] == "2023-06-01"
    # And: body contains tool_choice with name=submit_bill
    body = json.loads(req.content)
    assert body["tool_choice"] == {"type": "tool", "name": "submit_bill"}
    assert body["tools"][0]["name"] == "submit_bill"
    assert set(body["tools"][0]["input_schema"]["properties"].keys()) == set(_VALID_INPUT.keys())
    # And: returns ExtractionResult with all 7 fields populated
    assert isinstance(result, ExtractionResult)
    assert result.summary == "麦当劳 ¥42"
    assert result.amount == 42.0
    assert result.bill_date == "2026-08-28"


async def test_openai_happy_path(monkeypatch):
    # Given: settings.ai_provider="openai" with default base_url (None)
    # And: a mock returning a valid tool_calls response
    settings = _make_ai_settings(provider="openai")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _openai_response()

    _patch_httpx(monkeypatch, handler)
    extractor = AiExtractor(settings)

    # When: extract() is called
    result = await extractor.extract("some ocr text", _PROMPT_HEADER, _FIELD_PROMPTS)

    # Then: request URL path is /chat/completions on api.openai.com/v1
    req = captured[0]
    assert str(req.url) == "https://api.openai.com/v1/chat/completions"
    # And: header Authorization: Bearer <key> is present
    assert req.headers["authorization"] == "Bearer sk-test"
    # And: body contains tools[0].function.name=submit_bill
    body = json.loads(req.content)
    assert body["tools"][0]["function"]["name"] == "submit_bill"
    assert body["tool_choice"]["function"]["name"] == "submit_bill"
    # And: returns ExtractionResult with all 7 fields populated
    assert isinstance(result, ExtractionResult)
    assert result.summary == "麦当劳 ¥42"
    assert result.flow_type == "支出"
    assert result.payment_method == "微信"


async def test_anthropic_timeout_raises(monkeypatch):
    # Given: a mock handler that raises httpx.TimeoutException
    settings = _make_ai_settings(provider="anthropic")
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        raise httpx.TimeoutException("simulated timeout")

    _patch_httpx(monkeypatch, handler)
    extractor = AiExtractor(settings)

    # When: extract() is called
    with pytest.raises(AiExtractorError) as exc:
        await extractor.extract("text", _PROMPT_HEADER, _FIELD_PROMPTS)

    # Then: AiExtractorError is raised with stage="request"
    assert exc.value.stage == "request"
    # And: the mock handler was invoked exactly once (no retry)
    assert call_count == 1


async def test_missing_required_key_raises(monkeypatch):
    # Given: a mock returning a tool_use input missing "amount"
    settings = _make_ai_settings(provider="anthropic")
    bad_input = {k: v for k, v in _VALID_INPUT.items() if k != "amount"}

    def handler(request: httpx.Request) -> httpx.Response:
        return _anthropic_response(bad_input)

    _patch_httpx(monkeypatch, handler)
    extractor = AiExtractor(settings)

    # When: extract() is called
    with pytest.raises(AiExtractorError) as exc:
        await extractor.extract("text", _PROMPT_HEADER, _FIELD_PROMPTS)

    # Then: AiExtractorError is raised with stage="validate"
    assert exc.value.stage == "validate"


async def test_http_500_raises(monkeypatch):
    # Given: a mock returning HTTP 500
    settings = _make_ai_settings(provider="anthropic")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    _patch_httpx(monkeypatch, handler)
    extractor = AiExtractor(settings)

    # When: extract() is called
    with pytest.raises(AiExtractorError) as exc:
        await extractor.extract("text", _PROMPT_HEADER, _FIELD_PROMPTS)

    # Then: AiExtractorError is raised with stage="request"
    assert exc.value.stage == "request"


async def test_openai_invalid_json_arguments_raises(monkeypatch):
    # Given: a mock where tool_calls[0].function.arguments is invalid JSON
    settings = _make_ai_settings(provider="openai")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "submit_bill",
                                        "arguments": "{not valid json}",
                                    }
                                }
                            ]
                        }
                    }
                ]
            },
        )

    _patch_httpx(monkeypatch, handler)
    extractor = AiExtractor(settings)

    # When: extract() is called
    with pytest.raises(AiExtractorError) as exc:
        await extractor.extract("text", _PROMPT_HEADER, _FIELD_PROMPTS)

    # Then: AiExtractorError is raised with stage="parse"
    assert exc.value.stage == "parse"


async def test_custom_base_url_concatenation(monkeypatch):
    # Given: settings.ai_base_url="https://my-relay.example.com" (anthropic)
    settings = _make_ai_settings(
        provider="anthropic", base_url="https://my-relay.example.com"
    )
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _anthropic_response()

    _patch_httpx(monkeypatch, handler)
    extractor = AiExtractor(settings)

    # When: extract() is called
    await extractor.extract("text", _PROMPT_HEADER, _FIELD_PROMPTS)

    # Then: request URL is https://my-relay.example.com/v1/messages
    assert str(captured[0].url) == "https://my-relay.example.com/v1/messages"
    # And: request does NOT go to api.anthropic.com
    assert "api.anthropic.com" not in str(captured[0].url)


async def test_amount_must_be_positive(monkeypatch):
    # Given: a mock returning amount <= 0
    settings = _make_ai_settings(provider="anthropic")

    def handler_negative(request: httpx.Request) -> httpx.Response:
        bad = {**_VALID_INPUT, "amount": -5.0}
        return _anthropic_response(bad)

    _patch_httpx(monkeypatch, handler_negative)
    extractor = AiExtractor(settings)

    # When: extract() is called with negative amount
    with pytest.raises(AiExtractorError) as exc:
        await extractor.extract("text", _PROMPT_HEADER, _FIELD_PROMPTS)
    # Then: AiExtractorError is raised with stage="validate"
    assert exc.value.stage == "validate"


async def test_amount_zero_raises(monkeypatch):
    # Given: a mock returning amount == 0
    settings = _make_ai_settings(provider="anthropic")

    def handler_zero(request: httpx.Request) -> httpx.Response:
        bad = {**_VALID_INPUT, "amount": 0}
        return _anthropic_response(bad)

    _patch_httpx(monkeypatch, handler_zero)
    extractor = AiExtractor(settings)

    # When: extract() is called with zero amount
    with pytest.raises(AiExtractorError) as exc:
        await extractor.extract("text", _PROMPT_HEADER, _FIELD_PROMPTS)
    # Then: AiExtractorError is raised with stage="validate"
    assert exc.value.stage == "validate"


async def test_bill_date_format_validation(monkeypatch):
    # Given: a mock returning bill_date="2026/08/28" (wrong separator)
    settings = _make_ai_settings(provider="anthropic")

    def handler(request: httpx.Request) -> httpx.Response:
        bad = {**_VALID_INPUT, "bill_date": "2026/08/28"}
        return _anthropic_response(bad)

    _patch_httpx(monkeypatch, handler)
    extractor = AiExtractor(settings)

    # When: extract() is called
    with pytest.raises(AiExtractorError) as exc:
        await extractor.extract("text", _PROMPT_HEADER, _FIELD_PROMPTS)

    # Then: AiExtractorError is raised with stage="validate"
    assert exc.value.stage == "validate"


async def test_empty_string_field_raises(monkeypatch):
    # Given: a mock returning summary="" (empty string)
    settings = _make_ai_settings(provider="anthropic")

    def handler(request: httpx.Request) -> httpx.Response:
        bad = {**_VALID_INPUT, "summary": ""}
        return _anthropic_response(bad)

    _patch_httpx(monkeypatch, handler)
    extractor = AiExtractor(settings)

    # When: extract() is called
    with pytest.raises(AiExtractorError) as exc:
        await extractor.extract("text", _PROMPT_HEADER, _FIELD_PROMPTS)

    # Then: AiExtractorError is raised with stage="validate"
    assert exc.value.stage == "validate"


async def test_anthropic_response_without_tool_use_raises(monkeypatch):
    # Given: a mock returning content with no tool_use item (unexpected shape)
    settings = _make_ai_settings(provider="anthropic")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "I cannot help with that."}]},
        )

    _patch_httpx(monkeypatch, handler)
    extractor = AiExtractor(settings)

    # When: extract() is called
    with pytest.raises(AiExtractorError) as exc:
        await extractor.extract("text", _PROMPT_HEADER, _FIELD_PROMPTS)

    # Then: AiExtractorError is raised with stage="parse"
    assert exc.value.stage == "parse"


async def test_openai_response_without_tool_calls_raises(monkeypatch):
    # Given: a mock returning choices[0].message with no tool_calls
    settings = _make_ai_settings(provider="openai")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "no tools here"}}]},
        )

    _patch_httpx(monkeypatch, handler)
    extractor = AiExtractor(settings)

    # When: extract() is called
    with pytest.raises(AiExtractorError) as exc:
        await extractor.extract("text", _PROMPT_HEADER, _FIELD_PROMPTS)

    # Then: AiExtractorError is raised with stage="parse"
    assert exc.value.stage == "parse"
