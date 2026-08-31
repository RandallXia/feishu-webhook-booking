# test_field_codec.py — Behavior tests for Feishu field encoding (option whitelist + timezone date)
#
# Tests encode_fields (app/field_codec.py):
#   - text: strip, skip empty → warning
#   - number: passthrough float
#   - single_select: whitelist hit/miss/fallback/raise (pollution prevention)
#   - date: parse YYYY-MM-DD → Shanghai midnight ms timestamp; bad format → today fallback
#   - passthrough: source="summary" → extraction.summary
#   - routing: target="extract" vs "bill" → separate dicts
#
# Pure function tests — no IO, no monkeypatch, no fixtures needed.

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.ai_extractor import ExtractionResult
from app.field_codec import FieldSpec, encode_fields

_SHANGHAI = ZoneInfo("Asia/Shanghai")


# ─── Test helpers ──────────────────────────────────────────────────────────


def _make_extraction(**kwargs):
    """Build an ExtractionResult with defaults overridable by kwargs."""
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


def _shanghai_midnight_ms(year: int, month: int, day: int) -> int:
    """Return ms timestamp for midnight Shanghai time on the given date."""
    dt = datetime(year, month, day, 0, 0, tzinfo=_SHANGHAI)
    return int(dt.timestamp() * 1000)


# ─── Text ──────────────────────────────────────────────────────────────────


def test_text_normal():
    """
    GIVEN a text FieldSpec with a non-empty extraction field
    WHEN encode_fields is called
    THEN the stripped value appears in the target dict with no warnings
    """
    extraction = _make_extraction(summary="  麦当劳 ¥42  ")
    specs = [
        FieldSpec(ai_key="summary", feishu_field="原始信息", type="text", target="extract"),
    ]
    extract_fields, bill_fields, warnings = encode_fields(extraction, specs, {})
    assert extract_fields["原始信息"] == "麦当劳 ¥42"
    assert bill_fields == {}
    assert warnings == []


def test_text_empty_skipped():
    """
    GIVEN a text FieldSpec with an empty extraction field
    WHEN encode_fields is called
    THEN the field is NOT in the output dict and a warning contains "empty text skipped"
    """
    extraction = _make_extraction(summary="")
    specs = [
        FieldSpec(ai_key="summary", feishu_field="原始信息", type="text", target="extract"),
    ]
    extract_fields, bill_fields, warnings = encode_fields(extraction, specs, {})
    assert "原始信息" not in extract_fields
    assert bill_fields == {}
    assert any("empty text skipped" in w for w in warnings)


# ─── Number ────────────────────────────────────────────────────────────────


def test_number_passthrough():
    """
    GIVEN a number FieldSpec
    WHEN encode_fields is called
    THEN the float value appears in the dict
    """
    extraction = _make_extraction(amount=99.5)
    specs = [
        FieldSpec(ai_key="amount", feishu_field="金额", type="number", target="bill"),
    ]
    extract_fields, bill_fields, warnings = encode_fields(extraction, specs, {})
    assert bill_fields["金额"] == "99.5"
    assert extract_fields == {}
    assert warnings == []


# ─── Single select ─────────────────────────────────────────────────────────


def test_single_select_hit():
    """
    GIVEN a single_select FieldSpec with a value in the whitelist
    WHEN encode_fields is called
    THEN the original value is used with no warning
    """
    extraction = _make_extraction(category="餐饮")
    specs = [
        FieldSpec(ai_key="category", feishu_field="分类", type="single_select", target="extract", fallback="其他"),
    ]
    whitelists = {"分类": {"餐饮", "交通", "其他"}}
    extract_fields, bill_fields, warnings = encode_fields(extraction, specs, whitelists)
    assert extract_fields["分类"] == "餐饮"
    assert warnings == []


def test_single_select_miss_fallback():
    """
    GIVEN a single_select FieldSpec with a value NOT in the whitelist
    AND a fallback that IS in the whitelist
    WHEN encode_fields is called
    THEN the fallback is used and a warning contains "option fallback"
    """
    extraction = _make_extraction(category="娱乐")
    specs = [
        FieldSpec(ai_key="category", feishu_field="分类", type="single_select", target="extract", fallback="其他"),
    ]
    whitelists = {"分类": {"餐饮", "交通", "其他"}}
    extract_fields, bill_fields, warnings = encode_fields(extraction, specs, whitelists)
    assert extract_fields["分类"] == "其他"
    assert any("option fallback" in w for w in warnings)
    assert "娱乐" in warnings[0]


def test_single_select_trailing_space_miss():
    """
    GIVEN a single_select FieldSpec with a value containing trailing whitespace
    AND the whitelist contains the clean value but not the dirty one
    WHEN encode_fields is called
    THEN trailing space is NOT stripped (pollution prevention) → fallback + warning
    """
    extraction = _make_extraction(category="餐饮 ")  # trailing space — NOT "餐饮"
    specs = [
        FieldSpec(ai_key="category", feishu_field="分类", type="single_select", target="extract", fallback="其他"),
    ]
    whitelists = {"分类": {"餐饮", "交通", "其他"}}
    extract_fields, bill_fields, warnings = encode_fields(extraction, specs, whitelists)
    assert extract_fields["分类"] == "其他"
    assert any("option fallback" in w for w in warnings)


def test_single_select_double_miss_raises():
    """
    GIVEN a single_select FieldSpec with a value NOT in the whitelist
    AND a fallback ALSO not in the whitelist
    WHEN encode_fields is called
    THEN ValueError is raised (config error)
    """
    extraction = _make_extraction(category="娱乐")
    specs = [
        FieldSpec(ai_key="category", feishu_field="分类", type="single_select", target="extract", fallback="未知"),
    ]
    whitelists = {"分类": {"餐饮", "交通", "其他"}}
    import pytest
    with pytest.raises(ValueError, match="single_select"):
        encode_fields(extraction, specs, whitelists)


# ─── Date ──────────────────────────────────────────────────────────────────


def test_date_parse():
    """
    GIVEN a date FieldSpec with a valid YYYY-MM-DD value
    WHEN encode_fields is called
    THEN the ms timestamp for Shanghai midnight is returned with no warnings
    """
    extraction = _make_extraction(bill_date="2026-01-15")
    specs = [
        FieldSpec(ai_key="bill_date", feishu_field="日期", type="date", target="extract"),
    ]
    extract_fields, bill_fields, warnings = encode_fields(extraction, specs, {})
    expected = _shanghai_midnight_ms(2026, 1, 15)
    assert extract_fields["日期"] == expected
    assert warnings == []


def test_date_with_hhmm_parse():
    """
    GIVEN a date FieldSpec with a valid YYYY-MM-DD HH:mm value
    WHEN encode_fields is called
    THEN the ms timestamp for Shanghai timezone is returned (HH:mm preserved)
      AND no warnings
    """
    extraction = _make_extraction(bill_date="2026-08-28 09:57")
    specs = [
        FieldSpec(ai_key="bill_date", feishu_field="日期", type="date", target="extract"),
    ]
    extract_fields, bill_fields, warnings = encode_fields(extraction, specs, {})
    # 2026-08-28 09:57 in Shanghai = 2026-08-28 01:57 UTC
    from datetime import timezone
    expected_dt = datetime(2026, 8, 28, 9, 57, tzinfo=_SHANGHAI)
    expected_ms = int(expected_dt.timestamp() * 1000)
    assert extract_fields["日期"] == expected_ms
    assert warnings == []


def test_date_with_hhmmss_parse():
    """
    GIVEN a date FieldSpec with a valid YYYY-MM-DD HH:mm:ss value
    WHEN encode_fields is called
    THEN the ms timestamp for Shanghai timezone is returned (HH:mm:ss preserved)
      AND no warnings
    """
    extraction = _make_extraction(bill_date="2026-08-28 11:48:02")
    specs = [
        FieldSpec(ai_key="bill_date", feishu_field="日期", type="date", target="extract"),
    ]
    extract_fields, bill_fields, warnings = encode_fields(extraction, specs, {})
    # 2026-08-28 11:48:02 in Shanghai = 2026-08-28 03:48:02 UTC
    expected_dt = datetime(2026, 8, 28, 11, 48, 2, tzinfo=_SHANGHAI)
    expected_ms = int(expected_dt.timestamp() * 1000)
    assert extract_fields["日期"] == expected_ms
    assert warnings == []


def test_date_bad_format_fallback_today():
    """
    GIVEN a date FieldSpec with a non-YYYY-MM-DD value
    WHEN encode_fields is called
    THEN today's date in Shanghai is used and a warning contains "date fallback to today"
    """
    extraction = _make_extraction(bill_date="2026/01/15")
    specs = [
        FieldSpec(ai_key="bill_date", feishu_field="日期", type="date", target="extract"),
    ]
    extract_fields, bill_fields, warnings = encode_fields(extraction, specs, {})
    today = datetime.now(_SHANGHAI)
    expected = _shanghai_midnight_ms(today.year, today.month, today.day)
    assert extract_fields["日期"] == expected
    assert any("date fallback to today" in w for w in warnings)


def test_date_timezone_semantics():
    """
    GIVEN a date FieldSpec with a valid YYYY-MM-DD value
    WHEN encode_fields is called
    THEN the ms timestamp corresponds to Shanghai midnight, NOT UTC midnight
    (UTC 2026-01-01 00:00:00 = Shanghai 2026-01-01 08:00:00, which is the wrong day)
    """
    extraction = _make_extraction(bill_date="2026-01-01")
    specs = [
        FieldSpec(ai_key="bill_date", feishu_field="日期", type="date", target="extract"),
    ]
    extract_fields, _, _ = encode_fields(extraction, specs, {})

    shanghai_midnight_ts = _shanghai_midnight_ms(2026, 1, 1)

    # UTC midnight would be 8 hours EARLIER (2025-12-31 16:00 UTC)
    utc_midnight_dt = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    utc_midnight_ms = int(utc_midnight_dt.timestamp() * 1000)

    assert extract_fields["日期"] == shanghai_midnight_ts
    assert shanghai_midnight_ts != utc_midnight_ms, (
        "Shanghai midnight must differ from UTC midnight for the same date string"
    )
    # Shanghai midnight = 8 hours ahead of UTC midnight in ms
    assert shanghai_midnight_ts == utc_midnight_ms - 8 * 3600 * 1000, (
        "Shanghai midnight is 8 hours earlier than UTC midnight in epoch ms"
    )


# ─── Passthrough ───────────────────────────────────────────────────────────


def test_passthrough_summary_to_bill():
    """
    GIVEN a passthrough FieldSpec with source="summary" and target="bill"
    WHEN encode_fields is called
    THEN extraction.summary appears in the bill dict
    """
    extraction = _make_extraction(summary="麦当劳 ¥42 微信支付")
    specs = [
        FieldSpec(ai_key="", feishu_field="原始信息", type="passthrough", target="bill", source="summary"),
    ]
    extract_fields, bill_fields, warnings = encode_fields(extraction, specs, {})
    assert bill_fields["原始信息"] == "麦当劳 ¥42 微信支付"
    assert extract_fields == {}
    assert warnings == []


# ─── Routing ───────────────────────────────────────────────────────────────


def test_extract_bill_routing():
    """
    GIVEN multiple FieldSpecs with different targets
    WHEN encode_fields is called
    THEN extract_fields and bill_fields each get the right fields
    """
    extraction = _make_extraction()
    specs = [
        FieldSpec(ai_key="summary", feishu_field="原始信息", type="text", target="extract"),
        FieldSpec(ai_key="amount", feishu_field="金额", type="number", target="bill"),
        FieldSpec(ai_key="category", feishu_field="分类", type="single_select", target="extract",
                  fallback="其他"),
        FieldSpec(ai_key="bill_date", feishu_field="日期", type="date", target="bill"),
    ]
    whitelists = {"分类": {"餐饮", "交通", "其他"}}
    extract_fields, bill_fields, warnings = encode_fields(extraction, specs, whitelists)

    # extract dict
    assert extract_fields["原始信息"] == "麦当劳 ¥42 微信支付"
    assert extract_fields["分类"] == "餐饮"

    # bill dict
    assert bill_fields["金额"] == "42.0"
    # date filed should be a timestamp (int)
    assert isinstance(bill_fields["日期"], int)
    expected_date = _shanghai_midnight_ms(2026, 8, 28)
    assert bill_fields["日期"] == expected_date

    # no overlap
    assert set(extract_fields) & set(bill_fields) == set()
    assert warnings == []


# ─── enabled flag ──────────────────────────────────────────────────────────


def test_disabled_spec_skipped_in_extract_and_bill():
    """
    GIVEN a FieldSpec with enabled=False alongside enabled=True specs
    WHEN encode_fields is called
    THEN the disabled spec's field is absent from both extract_fields AND bill_fields
      AND no warning is emitted for the disabled spec
    """
    extraction = _make_extraction()
    specs = [
        FieldSpec(ai_key="summary", feishu_field="原始信息", type="text", target="extract"),
        FieldSpec(ai_key="amount", feishu_field="金额", type="number", target="bill"),
        FieldSpec(
            ai_key="category", feishu_field="分类", type="single_select", target="extract",
            fallback="其他", enabled=False,
        ),
    ]
    extract_fields, bill_fields, warnings = encode_fields(extraction, specs, {})
    assert "分类" not in extract_fields
    assert "原始信息" in extract_fields
    assert "金额" in bill_fields
    assert not any("分类" in w for w in warnings)


def test_disabled_single_select_skips_whitelist_check():
    """
    GIVEN a disabled single_select spec whose value is NOT in the whitelist
       AND no valid fallback (would normally raise ValueError)
    WHEN encode_fields is called
    THEN NO ValueError is raised (the disabled `continue` precedes the whitelist check)
      AND the field is absent from the output
    """
    extraction = _make_extraction(category="娱乐")
    specs = [
        FieldSpec(
            ai_key="category", feishu_field="分类", type="single_select", target="extract",
            fallback="未知", enabled=False,
        ),
    ]
    whitelists = {"分类": {"餐饮", "交通", "其他"}}
    extract_fields, bill_fields, warnings = encode_fields(extraction, specs, whitelists)
    assert extract_fields == {}
    assert bill_fields == {}
    assert warnings == []


def test_enabled_defaults_true():
    """
    GIVEN a FieldSpec constructed WITHOUT passing enabled
    WHEN the spec is inspected
    THEN spec.enabled is True (backward-compatible default)
    """
    spec = FieldSpec(
        ai_key="summary", feishu_field="原始信息", type="text", target="extract",
    )
    assert spec.enabled is True