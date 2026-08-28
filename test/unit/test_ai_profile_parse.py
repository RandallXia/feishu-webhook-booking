# test_ai_profile_parse.py — Behavior tests for static TOML profile parsing (app/ai_profile.py)
#
# Tests parse_profile(path: Path) -> AiProfile:
#   - Valid TOML → AiProfile with all fields correct
#   - Missing bill.app_token → ProfileConfigError
#   - ai_key duplicate → ProfileConfigError
#   - single_select without fallback → ProfileConfigError
#   - summary_field mismatch with extract spec's feishu_field → ProfileConfigError
#   - Multiple target="extract" fields → ProfileConfigError
#   - Invalid type value → ProfileConfigError
#   - passthrough without source="summary" → ProfileConfigError
#
# TOML files are written to tmp_path per test. No IO, no monkeypatch beyond tmp_path.

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai_profile import AiProfile, ProfileConfigError, parse_profile


# ─── Test helpers ──────────────────────────────────────────────────────────


_VALID_PROFILE_TOML = """\
prompt_header = "你是一个账单信息提取助手..."

[extract]
summary_field = "精简原始数据"

[bill]
app_token = "bascnXXX"
table_id = "tblXXX"

[[fields]]
ai_key = "summary"
feishu_field = "精简原始数据"
type = "text"
target = "extract"
prompt = "提炼OCR文本的关键信息摘要"

[[fields]]
ai_key = "description"
feishu_field = "消费描述"
type = "text"
target = "bill"
prompt = "一句话描述这笔消费"

[[fields]]
ai_key = "flow_type"
feishu_field = "收支类型"
type = "single_select"
target = "bill"
fallback = "支出"
prompt = "支出或收入"

[[fields]]
ai_key = "amount"
feishu_field = "金额"
type = "number"
target = "bill"
prompt = "金额"

[[fields]]
ai_key = "category"
feishu_field = "分类"
type = "single_select"
target = "bill"
fallback = "其他"
prompt = "消费分类"

[[fields]]
ai_key = "payment_method"
feishu_field = "支付方式"
type = "single_select"
target = "bill"
fallback = "其他"
prompt = "支付方式"

[[fields]]
ai_key = "bill_date"
feishu_field = "日期"
type = "date"
target = "bill"
prompt = "日期 YYYY-MM-DD"

[[fields]]
ai_key = "raw_source"
feishu_field = "原始信息"
type = "passthrough"
target = "bill"
source = "summary"
"""


def _write_profile(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "profile.toml"
    path.write_text(content, encoding="utf-8")
    return path


# ─── Valid profile ─────────────────────────────────────────────────────────


def test_valid_profile_parses_all_fields(tmp_path):
    """
    GIVEN a well-formed profile TOML with extract/bill/prompt_header and 8 fields
    WHEN parse_profile is called with the file path
    THEN an AiProfile is returned with prompt_header, summary_field, bill_app_token,
         bill_table_id, and a tuple of 8 FieldSpecs preserving order and values
    """
    path = _write_profile(tmp_path, _VALID_PROFILE_TOML)
    profile = parse_profile(path)

    assert profile.prompt_header == "你是一个账单信息提取助手..."
    assert profile.summary_field == "精简原始数据"
    assert profile.bill_app_token == "bascnXXX"
    assert profile.bill_table_id == "tblXXX"
    assert len(profile.fields) == 8

    f0 = profile.fields[0]
    assert f0.ai_key == "summary"
    assert f0.feishu_field == "精简原始数据"
    assert f0.type == "text"
    assert f0.target == "extract"

    flow = next(f for f in profile.fields if f.ai_key == "flow_type")
    assert flow.type == "single_select"
    assert flow.fallback == "支出"

    raw = next(f for f in profile.fields if f.ai_key == "raw_source")
    assert raw.type == "passthrough"
    assert raw.source == "summary"


# ─── Missing bill.app_token ────────────────────────────────────────────────


def test_missing_bill_app_token_raises(tmp_path):
    """
    GIVEN a profile TOML where bill.app_token is missing or empty
    WHEN parse_profile is called
    THEN ProfileConfigError is raised with "app_token" in the message
    """
    bad = _VALID_PROFILE_TOML.replace('app_token = "bascnXXX"\n', "")
    path = _write_profile(tmp_path, bad)
    with pytest.raises(ProfileConfigError, match="app_token"):
        parse_profile(path)


# ─── Duplicate ai_key ──────────────────────────────────────────────────────


def test_duplicate_ai_key_raises(tmp_path):
    """
    GIVEN a profile TOML where two [[fields]] share the same ai_key
    WHEN parse_profile is called
    THEN ProfileConfigError is raised mentioning the duplicate ai_key name
    """
    bad = _VALID_PROFILE_TOML.replace(
        'ai_key = "raw_source"',
        'ai_key = "description"',
    )
    path = _write_profile(tmp_path, bad)
    with pytest.raises(ProfileConfigError, match="description"):
        parse_profile(path)


# ─── single_select without fallback ───────────────────────────────────────


def test_single_select_without_fallback_raises(tmp_path):
    """
    GIVEN a profile TOML with a single_select field that has no fallback
    WHEN parse_profile is called
    THEN ProfileConfigError is raised mentioning the field needs a fallback
    """
    bad = _VALID_PROFILE_TOML.replace(
        'fallback = "支出"\n',
        "",
        1,
    )
    path = _write_profile(tmp_path, bad)
    with pytest.raises(ProfileConfigError, match="flow_type"):
        parse_profile(path)


# ─── summary_field mismatch ────────────────────────────────────────────────


def test_summary_field_mismatch_raises(tmp_path):
    """
    GIVEN a profile TOML where [extract].summary_field does NOT equal the
         feishu_field of the single target="extract" field spec
    WHEN parse_profile is called
    THEN ProfileConfigError is raised mentioning summary_field mismatch
    """
    bad = _VALID_PROFILE_TOML.replace(
        'summary_field = "精简原始数据"\n\n[bill]',
        'summary_field = "不匹配字段"\n\n[bill]',
    )
    path = _write_profile(tmp_path, bad)
    with pytest.raises(ProfileConfigError, match="summary_field"):
        parse_profile(path)


# ─── Multiple target="extract" fields ──────────────────────────────────────


def test_multiple_extract_targets_raises(tmp_path):
    """
    GIVEN a profile TOML with two [[fields]] having target="extract"
    WHEN parse_profile is called
    THEN ProfileConfigError is raised mentioning exactly one extract field is required
    """
    bad = _VALID_PROFILE_TOML.replace(
        'ai_key = "description"\nfeishu_field = "消费描述"\ntype = "text"\ntarget = "bill"',
        'ai_key = "description"\nfeishu_field = "消费描述"\ntype = "text"\ntarget = "extract"',
    )
    path = _write_profile(tmp_path, bad)
    with pytest.raises(ProfileConfigError, match="extract"):
        parse_profile(path)


# ─── Invalid type value ────────────────────────────────────────────────────


def test_invalid_type_raises(tmp_path):
    """
    GIVEN a profile TOML where a field has type="unknown_type"
    WHEN parse_profile is called
    THEN ProfileConfigError is raised mentioning the invalid type value
    """
    bad = _VALID_PROFILE_TOML.replace(
        'ai_key = "amount"\nfeishu_field = "金额"\ntype = "number"',
        'ai_key = "amount"\nfeishu_field = "金额"\ntype = "unknown_type"',
    )
    path = _write_profile(tmp_path, bad)
    with pytest.raises(ProfileConfigError, match="unknown_type"):
        parse_profile(path)


# ─── passthrough without source="summary" ─────────────────────────────────


def test_passthrough_without_summary_source_raises(tmp_path):
    """
    GIVEN a profile TOML with a passthrough field whose source is not "summary"
         (either missing or some other value)
    WHEN parse_profile is called
    THEN ProfileConfigError is raised mentioning the passthrough source constraint
    """
    bad = _VALID_PROFILE_TOML.replace('source = "summary"', "")
    path = _write_profile(tmp_path, bad)
    with pytest.raises(ProfileConfigError, match="raw_source"):
        parse_profile(path)
