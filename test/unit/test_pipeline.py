# test_pipeline.py — Behavior tests for AiPipeline orchestration (app/pipeline.py)
#
# Tests AiPipeline.run() with mocked AiExtractor + FeishuClient:
#   - Happy path: full chain (extract → encode → writeback → create) succeeds
#   - AI failure: AiExtractorError → ai_status="failed", feishu.create_record NOT called
#   - Dedup hit: same text+alias second call → ai_status="duplicate", extractor NOT called
#   - Dedup TTL expiry: after TTL passes, second call runs the full chain again
#   - Summary writeback failure: update_record_field raises FeishuClientError → bill still created, warning captured
#   - encode_fields ValueError: ai_status="failed"
#   - Top-level catch: unexpected Exception in extractor → ai_status="failed", run does NOT raise
#   - client_token deterministic: same text+alias → same UUID-format client_token
#   - extracted dict has amount, category, flow_type, description for Shortcut notification
#
# All Feishu IO and AI extraction are mocked. No real network.

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import re

import pytest

from app.ai_extractor import AiExtractorError, ExtractionResult
from app.ai_profile import AiProfile
from app.field_codec import FieldSpec
from app.pipeline import AiPipeline, PipelineResult
from app.target_registry import FeishuTargetConfig


# ─── Test helpers ──────────────────────────────────────────────────────────


def _make_extraction(**kwargs) -> ExtractionResult:
    defaults = dict(
        summary="麦当劳 ¥42 微信支付",
        description="麦当劳午餐",
        flow_type="支出",
        amount=42.0,
        category="餐饮",
        payment_method="微信",
        bill_date="2026-08-28",
    )
    defaults.update(kwargs)
    return ExtractionResult(**defaults)


def _make_target() -> FeishuTargetConfig:
    return FeishuTargetConfig(
        alias="default",
        year=None,
        app_token="extract-app-token",
        table_id="extract-table-id",
        record_id="extract-record-id",
        original_field_name="原始信息",
        enabled=True,
    )


def _make_profile() -> AiProfile:
    return AiProfile(
        prompt_header="你是一个账单信息提取助手...",
        summary_field="精简原始数据",
        bill_app_token="bill-app-token",
        bill_table_id="bill-table-id",
        fields=(
            FieldSpec(
                ai_key="summary",
                feishu_field="精简原始数据",
                type="text",
                target="extract",
                prompt="提炼摘要",
            ),
            FieldSpec(
                ai_key="description",
                feishu_field="消费描述",
                type="text",
                target="bill",
                prompt="描述",
            ),
            FieldSpec(
                ai_key="flow_type",
                feishu_field="收支类型",
                type="single_select",
                target="bill",
                fallback="支出",
                prompt="收支",
            ),
            FieldSpec(
                ai_key="amount",
                feishu_field="金额",
                type="number",
                target="bill",
                prompt="金额",
            ),
            FieldSpec(
                ai_key="category",
                feishu_field="分类",
                type="single_select",
                target="bill",
                fallback="其他",
                prompt="分类",
            ),
            FieldSpec(
                ai_key="payment_method",
                feishu_field="支付方式",
                type="single_select",
                target="bill",
                fallback="其他",
                prompt="支付",
            ),
            FieldSpec(
                ai_key="bill_date",
                feishu_field="日期",
                type="date",
                target="bill",
                prompt="日期",
            ),
            FieldSpec(
                ai_key="raw_source",
                feishu_field="原始信息",
                type="passthrough",
                target="bill",
                source="summary",
            ),
        ),
    )


def _make_whitelists() -> dict[str, set[str]]:
    return {
        "收支类型": {"支出", "收入"},
        "分类": {"餐饮", "交通", "其他"},
        "支付方式": {"微信", "支付宝", "其他"},
    }


def _make_pipeline(
    *,
    extractor_return: ExtractionResult | None = None,
    extractor_raises: BaseException | None = None,
    feishu_create_return: str = "bill-rec-001",
    feishu_create_raises: BaseException | None = None,
    feishu_update_raises: BaseException | None = None,
) -> AiPipeline:
    settings = MagicMock()
    settings.ai_dedup_ttl_seconds = 300

    extractor = MagicMock()
    if extractor_raises is not None:
        extractor.extract = AsyncMock(side_effect=extractor_raises)
    else:
        extractor.extract = AsyncMock(return_value=extractor_return or _make_extraction())

    feishu = MagicMock()
    feishu.update_record_field = AsyncMock()
    if feishu_update_raises is not None:
        feishu.update_record_field = AsyncMock(side_effect=feishu_update_raises)
    feishu.create_record = AsyncMock(return_value=feishu_create_return)
    if feishu_create_raises is not None:
        feishu.create_record = AsyncMock(side_effect=feishu_create_raises)
    feishu.list_fields = AsyncMock(return_value={})

    return AiPipeline(settings, extractor, feishu)


# ─── Happy path ─────────────────────────────────────────────────────────────


async def test_happy_full_chain():
    """
    GIVEN a valid AiPipeline with mocked extractor (returns ExtractionResult)
       AND mocked FeishuClient (update_record_field + create_record succeed)
    WHEN run() is called with original_text, target, profile, and option_whitelists
    THEN ai_status == "succeeded"
      AND update_record_field was called BEFORE create_record (call order)
      AND create_record received a client_token in UUID format
      AND dedup_hit == False
      AND bill_record_id matches the mock's return
    """
    pipeline = _make_pipeline()
    target = _make_target()
    profile = _make_profile()
    whitelists = _make_whitelists()

    result = await pipeline.run("麦当劳 ¥42", target, profile, whitelists)

    assert result.ai_status == "succeeded"
    assert result.bill_record_id == "bill-rec-001"
    assert result.dedup_hit is False

    assert pipeline._feishu.update_record_field.call_count == 1
    assert pipeline._feishu.create_record.call_count == 1

    call_names = [
        call[0] for call in pipeline._feishu.method_calls
    ]
    update_idx = None
    create_idx = None
    for i, (name, _args, _kwargs) in enumerate(pipeline._feishu.mock_calls):
        if name == "update_record_field":
            update_idx = i
        elif name == "create_record":
            create_idx = i
    assert update_idx is not None and create_idx is not None
    assert update_idx < create_idx

    UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    assert UUID_RE.match(pipeline._feishu.create_record.call_args.args[3])


# ─── AI failure ─────────────────────────────────────────────────────────────


async def test_ai_failure_returns_failed():
    """
    GIVEN a pipeline where the extractor raises AiExtractorError
    WHEN run() is called
    THEN ai_status == "failed"
      AND feishu.create_record was NOT called
      AND dedup_hit == False
    """
    pipeline = _make_pipeline(extractor_raises=AiExtractorError("boom", stage="request"))
    target = _make_target()
    profile = _make_profile()
    whitelists = _make_whitelists()

    result = await pipeline.run("麦当劳 ¥42", target, profile, whitelists)

    assert result.ai_status == "failed"
    assert result.dedup_hit is False
    assert pipeline._feishu.create_record.call_count == 0


# ─── Dedup hit ──────────────────────────────────────────────────────────────


async def test_dedup_hit_returns_duplicate():
    """
    GIVEN a pipeline that has successfully processed a text+alias once
    WHEN run() is called again with the SAME text+alias (within TTL)
    THEN ai_status == "duplicate"
      AND extractor.extract was NOT called on the second invocation
      AND feishu.create_record was NOT called on the second invocation
      AND dedup_hit == True
    """
    pipeline = _make_pipeline()
    target = _make_target()
    profile = _make_profile()
    whitelists = _make_whitelists()

    first = await pipeline.run("麦当劳 ¥42", target, profile, whitelists)
    assert first.ai_status == "succeeded"

    initial_extract_count = pipeline._extractor.extract.call_count
    initial_create_count = pipeline._feishu.create_record.call_count

    second = await pipeline.run("麦当劳 ¥42", target, profile, whitelists)

    assert second.ai_status == "duplicate"
    assert second.dedup_hit is True
    assert pipeline._extractor.extract.call_count == initial_extract_count
    assert pipeline._feishu.create_record.call_count == initial_create_count


# ─── Dedup TTL expiry ───────────────────────────────────────────────────────


async def test_dedup_ttl_expiry_reruns():
    """
    GIVEN a pipeline that has successfully processed a text+alias once
       AND time.time() is monkeypatched so the second call is AFTER TTL expiry
    WHEN run() is called again with the same text+alias
    THEN ai_status == "succeeded" (NOT "duplicate")
      AND extractor.extract WAS called on the second invocation
    """
    pipeline = _make_pipeline()
    target = _make_target()
    profile = _make_profile()
    whitelists = _make_whitelists()

    base_time = [1000.0]

    def fake_time():
        return base_time[0]

    import app.pipeline as pipeline_mod

    monkeypatch_target = pipeline_mod.time
    pipeline_mod.time.time = fake_time

    first = await pipeline.run("麦当劳 ¥42", target, profile, whitelists)
    assert first.ai_status == "succeeded"

    initial_extract_count = pipeline._extractor.extract.call_count

    base_time[0] = 1000.0 + 301.0

    second = await pipeline.run("麦当劳 ¥42", target, profile, whitelists)

    assert second.ai_status == "succeeded"
    assert pipeline._extractor.extract.call_count == initial_extract_count + 1

    pipeline_mod.time.time = monkeypatch_target.time


# ─── Summary writeback failure ────────────────────────────────────────────


async def test_summary_writeback_failure_continues():
    """
    GIVEN a pipeline where feishu.update_record_field raises FeishuClientError
    WHEN run() is called
    THEN ai_status == "succeeded" (bill creation is primary, writeback is best-effort)
      AND feishu.create_record WAS called
      AND result.warnings contains a string mentioning "summary writeback failed"
    """
    from app.feishu_client import FeishuClientError

    pipeline = _make_pipeline(
        feishu_update_raises=FeishuClientError("writeback boom", stage="update_record_field"),
    )
    target = _make_target()
    profile = _make_profile()
    whitelists = _make_whitelists()

    result = await pipeline.run("麦当劳 ¥42", target, profile, whitelists)

    assert result.ai_status == "succeeded"
    assert result.bill_record_id == "bill-rec-001"
    assert any("summary writeback failed" in w for w in result.warnings)
    assert pipeline._feishu.create_record.call_count == 1


# ─── encode_fields ValueError ──────────────────────────────────────────────


async def test_encode_valueerror_returns_failed():
    """
    GIVEN a pipeline where encode_fields raises ValueError (double-miss config error)
    WHEN run() is called
    THEN ai_status == "failed"
      AND feishu.create_record was NOT called
    """
    pipeline = _make_pipeline()
    target = _make_target()
    profile = _make_profile()
    whitelists = _make_whitelists()

    import app.pipeline as pipeline_mod

    def raise_valueerror(*args, **kwargs):
        raise ValueError("single_select fallback also not in whitelist")

    monkeypatch_target = pipeline_mod.encode_fields
    pipeline_mod.encode_fields = raise_valueerror

    try:
        result = await pipeline.run("麦当劳 ¥42", target, profile, whitelists)
    finally:
        pipeline_mod.encode_fields = monkeypatch_target

    assert result.ai_status == "failed"
    assert pipeline._feishu.create_record.call_count == 0


# ─── Top-level exception catch ─────────────────────────────────────────────


async def test_run_catches_all_exceptions():
    """
    GIVEN a pipeline where the extractor raises an unexpected Exception (NOT AiExtractorError)
    WHEN run() is called
    THEN ai_status == "failed" (run NEVER raises)
      AND run() returns normally without propagating the exception
    """
    pipeline = _make_pipeline(extractor_raises=RuntimeError("unexpected boom"))
    target = _make_target()
    profile = _make_profile()
    whitelists = _make_whitelists()

    result = await pipeline.run("麦当劳 ¥42", target, profile, whitelists)

    assert result.ai_status == "failed"
    assert result.dedup_hit is False


# ─── client_token deterministic ───────────────────────────────────────────


async def test_client_token_deterministic():
    """
    GIVEN a pipeline processing the same text+alias twice (with dedup cleared between calls)
    WHEN both runs produce a client_token for feishu.create_record
    THEN both client_tokens are identical
      AND both are valid UUIDs
    """
    pipeline_a = _make_pipeline()
    target = _make_target()
    profile = _make_profile()
    whitelists = _make_whitelists()

    await pipeline_a.run("麦当劳 ¥42", target, profile, whitelists)
    token_a = pipeline_a._feishu.create_record.call_args.args[3]

    pipeline_b = _make_pipeline()
    await pipeline_b.run("麦当劳 ¥42", target, profile, whitelists)
    token_b = pipeline_b._feishu.create_record.call_args.args[3]

    UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    assert token_a == token_b
    assert UUID_RE.match(token_a)


# ─── extracted dict has notification fields ─────────────────────────────────


async def test_extracted_dict_has_notification_fields():
    """
    GIVEN a successful pipeline run
    WHEN result.extracted is inspected
    THEN it contains keys: amount, category, flow_type, description
      AND their values match the ExtractionResult fields
    """
    extraction = _make_extraction()
    pipeline = _make_pipeline(extractor_return=extraction)
    target = _make_target()
    profile = _make_profile()
    whitelists = _make_whitelists()

    result = await pipeline.run("麦当劳 ¥42", target, profile, whitelists)

    assert result.ai_status == "succeeded"
    assert result.extracted["amount"] == extraction.amount
    assert result.extracted["category"] == extraction.category
    assert result.extracted["flow_type"] == extraction.flow_type
    assert result.extracted["description"] == extraction.description


# ─── enabled flag ──────────────────────────────────────────────────────────


def _make_profile_with_disabled(disabled_key: str) -> AiProfile:
    """Profile clone of _make_profile() with one FieldSpec.enabled=False.

    Rebuilds the fields tuple so the spec for `disabled_key` is replaced with
    an enabled=False variant — dataclass(frozen=True) prevents in-place mutation.
    """
    base = _make_profile()
    new_fields = []
    for spec in base.fields:
        if spec.ai_key == disabled_key:
            from dataclasses import replace as dc_replace
            new_fields.append(dc_replace(spec, enabled=False))
        else:
            new_fields.append(spec)
    return AiProfile(
        prompt_header=base.prompt_header,
        summary_field=base.summary_field,
        bill_app_token=base.bill_app_token,
        bill_table_id=base.bill_table_id,
        fields=tuple(new_fields),
    )


async def test_disabled_category_omitted_from_create_record():
    """
    GIVEN a profile where the category spec has enabled=False
    WHEN the pipeline runs
    THEN the bill_fields passed to create_record do NOT contain the category key
      AND ai_status == "succeeded"
    """
    pipeline = _make_pipeline()
    target = _make_target()
    profile = _make_profile_with_disabled("category")
    whitelists = _make_whitelists()

    result = await pipeline.run("麦当劳 ¥42", target, profile, whitelists)

    assert result.ai_status == "succeeded"
    bill_fields_arg = pipeline._feishu.create_record.call_args.args[0]
    assert "分类" not in bill_fields_arg


async def test_disabled_summary_skips_update_record_field():
    """
    GIVEN a profile where the summary spec has enabled=False
    WHEN the pipeline runs
    THEN feishu.update_record_field is NEVER called (zero invocations)
      AND ai_status == "succeeded"
    """
    pipeline = _make_pipeline()
    target = _make_target()
    profile = _make_profile_with_disabled("summary")
    whitelists = _make_whitelists()

    result = await pipeline.run("麦当劳 ¥42", target, profile, whitelists)

    assert result.ai_status == "succeeded"
    assert pipeline._feishu.update_record_field.call_count == 0


async def test_all_bill_fields_disabled_skips_create_record():
    """
    GIVEN a profile where EVERY target="bill" spec is disabled
    WHEN the pipeline runs
    THEN feishu.create_record is NEVER called
      AND ai_status == "succeeded"
      AND result.warnings contains "all bill fields disabled"
    """
    pipeline = _make_pipeline()
    target = _make_target()
    base = _make_profile()
    new_fields = []
    from dataclasses import replace as dc_replace
    for spec in base.fields:
        if spec.target == "bill":
            new_fields.append(dc_replace(spec, enabled=False))
        else:
            new_fields.append(spec)
    profile = AiProfile(
        prompt_header=base.prompt_header,
        summary_field=base.summary_field,
        bill_app_token=base.bill_app_token,
        bill_table_id=base.bill_table_id,
        fields=tuple(new_fields),
    )
    whitelists = _make_whitelists()

    result = await pipeline.run("麦当劳 ¥42", target, profile, whitelists)

    assert result.ai_status == "succeeded"
    assert pipeline._feishu.create_record.call_count == 0
    assert any("all bill fields disabled" in w for w in result.warnings)


async def test_disabled_category_omitted_from_extracted_dict():
    """
    GIVEN a profile where the category spec has enabled=False
    WHEN the pipeline runs and result.extracted is inspected
    THEN the `category` key is ABSENT from extracted
      AND the other three keys (amount/flow_type/description) are present
    """
    pipeline = _make_pipeline()
    target = _make_target()
    profile = _make_profile_with_disabled("category")
    whitelists = _make_whitelists()

    result = await pipeline.run("麦当劳 ¥42", target, profile, whitelists)

    assert result.ai_status == "succeeded"
    assert "category" not in result.extracted
    assert "amount" in result.extracted
    assert "flow_type" in result.extracted
    assert "description" in result.extracted
