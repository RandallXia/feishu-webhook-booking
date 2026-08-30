"""Tests for app/toml_writer — deterministic TOML serialization.

Two pure functions: dump_targets (feishu-targets.toml) and dump_profile (ai-profile.toml).
Round-trip verified via tomllib.loads.
"""

from __future__ import annotations

import tomllib

from app.toml_writer import dump_profile, dump_targets

# =============================================================================
# dump_targets
# =============================================================================

# GIVEN a complete targets dict with two aliases (2025, 2026)
# WHEN dump_targets is called
# THEN tomllib.loads round-trips to the same structure, preserving types


def test_dump_targets_roundtrip_full():
    data = {
        "default_alias": "2026",
        "targets": {
            "2025": {
                "year": 2025,
                "app_token": "bascn_2025",
                "table_id": "tbl_2025",
                "record_id": "rec_2025",
                "original_field_name": "原始信息",
                "enabled": True,
            },
            "2026": {
                "year": 2026,
                "app_token": "bascn_2026",
                "table_id": "tbl_2026",
                "record_id": "rec_2026",
                "original_field_name": "原始信息",
                "enabled": True,
            },
        },
    }
    result = dump_targets(data)
    parsed = tomllib.loads(result)
    assert parsed["default_alias"] == "2026"
    assert parsed["targets"]["2025"]["year"] == 2025
    assert parsed["targets"]["2025"]["app_token"] == "bascn_2025"
    assert parsed["targets"]["2025"]["enabled"] is True
    assert parsed["targets"]["2026"]["year"] == 2026
    assert parsed["targets"]["2026"]["original_field_name"] == "原始信息"


# GIVEN a target with year=None
# WHEN dump_targets is called
# THEN the year key is omitted in the output


def test_dump_targets_year_none_omitted():
    data = {
        "default_alias": "future",
        "targets": {
            "future": {
                "year": None,
                "app_token": "bascn_future",
                "table_id": "tbl_future",
                "record_id": "rec_future",
                "original_field_name": "原始信息",
                "enabled": True,
            },
        },
    }
    result = dump_targets(data)
    parsed = tomllib.loads(result)
    assert "year" not in parsed["targets"]["future"]


# GIVEN a target without an enabled key
# WHEN dump_targets is called
# THEN enabled defaults to true in the output


def test_dump_targets_enabled_default_true():
    data = {
        "default_alias": "mytarget",
        "targets": {
            "mytarget": {
                "year": 2024,
                "app_token": "bascn_x",
                "table_id": "tbl_x",
                "record_id": "rec_x",
                "original_field_name": "原始信息",
            },
        },
    }
    result = dump_targets(data)
    parsed = tomllib.loads(result)
    assert parsed["targets"]["mytarget"]["enabled"] is True


# GIVEN an alias containing a hyphen
# WHEN dump_targets is called
# THEN the table header uses a quoted key and round-trips correctly


def test_dump_targets_special_char_alias():
    data = {
        "default_alias": "test-archive",
        "targets": {
            "test-archive": {
                "year": 2024,
                "app_token": "bascn_test",
                "table_id": "tbl_test",
                "record_id": "rec_test",
                "original_field_name": "field",
                "enabled": False,
            },
        },
    }
    result = dump_targets(data)
    parsed = tomllib.loads(result)
    assert parsed["targets"]["test-archive"]["app_token"] == "bascn_test"
    assert parsed["targets"]["test-archive"]["enabled"] is False


# GIVEN the same targets dict twice
# WHEN dump_targets is called both times
# THEN the output strings are byte-identical


def test_dump_targets_deterministic():
    data = {
        "default_alias": "2026",
        "targets": {
            "b": {
                "year": 2025,
                "app_token": "b",
                "table_id": "t",
                "record_id": "r",
                "original_field_name": "o",
                "enabled": True,
            },
            "a": {
                "year": 2024,
                "app_token": "a",
                "table_id": "t",
                "record_id": "r",
                "original_field_name": "o",
                "enabled": False,
            },
        },
    }
    assert dump_targets(data) == dump_targets(data)


# =============================================================================
# dump_profile
# =============================================================================

# GIVEN a complete profile dict with all 8 fields (matching ai-profile.toml.example)
# WHEN dump_profile is called
# THEN tomllib.loads round-trips to the same structure


def test_dump_profile_roundtrip_full():
    data = {
        "prompt_header": "你是一个账单信息提取助手。",
        "extract": {"summary_field": "精简原始数据"},
        "bill": {"app_token": "bascnXXX", "table_id": "tblXXX"},
        "fields": [
            {
                "ai_key": "summary",
                "feishu_field": "精简原始数据",
                "type": "text",
                "target": "extract",
                "prompt": "提炼摘要",
            },
            {
                "ai_key": "description",
                "feishu_field": "消费描述",
                "type": "text",
                "target": "bill",
                "prompt": "一句话描述",
            },
            {
                "ai_key": "flow_type",
                "feishu_field": "收支类型",
                "type": "single_select",
                "target": "bill",
                "fallback": "支出",
                "prompt": "支出还是收入",
            },
            {
                "ai_key": "amount",
                "feishu_field": "金额",
                "type": "number",
                "target": "bill",
                "prompt": "纯数字",
            },
            {
                "ai_key": "category",
                "feishu_field": "收支分类",
                "type": "single_select",
                "target": "bill",
                "fallback": "其他",
                "prompt": "账单分类",
            },
            {
                "ai_key": "payment_method",
                "feishu_field": "支付途径",
                "type": "single_select",
                "target": "bill",
                "fallback": "未知",
                "prompt": "支付方式",
            },
            {
                "ai_key": "bill_date",
                "feishu_field": "账单日期",
                "type": "date",
                "target": "bill",
                "prompt": "格式 YYYY-MM-DD",
            },
            {
                "ai_key": "raw_source",
                "feishu_field": "原始采集账单数据",
                "type": "passthrough",
                "target": "bill",
                "source": "summary",
                "prompt": "",
            },
        ],
    }
    result = dump_profile(data)
    parsed = tomllib.loads(result)
    assert parsed["prompt_header"] == "你是一个账单信息提取助手。"
    assert parsed["extract"]["summary_field"] == "精简原始数据"
    assert parsed["bill"]["app_token"] == "bascnXXX"
    assert parsed["bill"]["table_id"] == "tblXXX"
    assert len(parsed["fields"]) == 8
    assert parsed["fields"][0]["ai_key"] == "summary"
    assert parsed["fields"][7]["ai_key"] == "raw_source"
    assert parsed["fields"][7]["source"] == "summary"
    assert parsed["fields"][7]["prompt"] == ""


# GIVEN a profile with prompt containing newlines, double quotes, and backslashes
# WHEN dump_profile is called
# THEN the output contains proper escape sequences and round-trips correctly


def test_dump_profile_escape():
    data = {
        "prompt_header": "Line1\nLine2",
        "extract": {"summary_field": "摘要"},
        "bill": {"app_token": "bascnX", "table_id": "tblX"},
        "fields": [
            {
                "ai_key": "summary",
                "feishu_field": 'field"quote',
                "type": "text",
                "target": "extract",
                "prompt": "path\\to\\nowhere",
            },
        ],
    }
    result = dump_profile(data)
    # Verify escape sequences in raw TOML output
    assert "\\n" in result
    assert '\\"' in result
    assert "\\\\" in result
    # Round-trip must restore original values
    parsed = tomllib.loads(result)
    assert parsed["prompt_header"] == "Line1\nLine2"
    assert parsed["fields"][0]["feishu_field"] == 'field"quote'
    assert parsed["fields"][0]["prompt"] == "path\\to\\nowhere"


# GIVEN a profile with Chinese values
# WHEN dump_profile is called
# THEN Chinese characters are preserved as-is (UTF-8) and round-trip


def test_dump_profile_chinese():
    data = {
        "prompt_header": "测试",
        "extract": {"summary_field": "摘要"},
        "bill": {"app_token": "bascnX", "table_id": "tblX"},
        "fields": [
            {
                "ai_key": "summary",
                "feishu_field": "精简原始数据",
                "type": "text",
                "target": "extract",
                "prompt": "测试提示",
            },
        ],
    }
    result = dump_profile(data)
    assert "测试" in result
    assert "精简原始数据" in result
    parsed = tomllib.loads(result)
    assert parsed["prompt_header"] == "测试"
    assert parsed["fields"][0]["feishu_field"] == "精简原始数据"


# GIVEN a profile with fallback and source omitted (None in the dict)
# WHEN dump_profile is called
# THEN those keys are not present in the output


def test_dump_profile_none_omitted():
    data = {
        "prompt_header": "h",
        "extract": {"summary_field": "s"},
        "bill": {"app_token": "bascnX", "table_id": "tblX"},
        "fields": [
            {
                "ai_key": "summary",
                "feishu_field": "f",
                "type": "text",
                "target": "extract",
                "prompt": "p",
            },
        ],
    }
    result = dump_profile(data)
    parsed = tomllib.loads(result)
    field = parsed["fields"][0]
    assert field["ai_key"] == "summary"
    assert "fallback" not in field
    assert "source" not in field


# GIVEN the same profile dict twice
# WHEN dump_profile is called both times
# THEN the output strings are byte-identical


def test_dump_profile_deterministic():
    data = {
        "prompt_header": "header",
        "extract": {"summary_field": "s"},
        "bill": {"app_token": "bascnX", "table_id": "tblX"},
        "fields": [
            {
                "ai_key": "b",
                "feishu_field": "f",
                "type": "text",
                "target": "extract",
                "prompt": "p",
            },
            {
                "ai_key": "a",
                "feishu_field": "g",
                "type": "text",
                "target": "bill",
                "prompt": "q",
            },
        ],
    }
    assert dump_profile(data) == dump_profile(data)


# GIVEN a profile dict
# WHEN dump_profile is called
# THEN the prompt_header value appears in the output before the first [section] header


def test_dump_profile_prompt_header_first():
    data = {
        "prompt_header": "I am first",
        "extract": {"summary_field": "s"},
        "bill": {"app_token": "bascnX", "table_id": "tblX"},
        "fields": [
            {
                "ai_key": "summary",
                "feishu_field": "f",
                "type": "text",
                "target": "extract",
                "prompt": "p",
            },
        ],
    }
    result = dump_profile(data)
    prompt_idx = result.index("I am first")
    first_bracket = result.index("[")
    assert prompt_idx < first_bracket, (
        f"prompt_header value at index {prompt_idx} should appear before "
        f"first [section] at index {first_bracket}"
    )


# ─── enabled flag serialization ────────────────────────────────────────────


def test_dump_profile_enabled_both_states_serialized():
    """
    GIVEN a profile dict whose fields include both enabled=True AND enabled=False
    WHEN dump_profile is called
    THEN both `enabled = true` and `enabled = false` appear verbatim in the output
      (False must NOT be omitted — the `val is None` guard passes for False)
      AND tomllib round-trips both values back to their bool originals
    """
    data = {
        "prompt_header": "h",
        "extract": {"summary_field": "s"},
        "bill": {"app_token": "bascnX", "table_id": "tblX"},
        "fields": [
            {
                "ai_key": "a", "feishu_field": "f", "type": "text",
                "target": "extract", "prompt": "p", "enabled": True,
            },
            {
                "ai_key": "b", "feishu_field": "g", "type": "text",
                "target": "bill", "prompt": "q", "enabled": False,
            },
        ],
    }
    result = dump_profile(data)
    assert "enabled = true" in result
    assert "enabled = false" in result
    parsed = tomllib.loads(result)
    assert parsed["fields"][0]["enabled"] is True
    assert parsed["fields"][1]["enabled"] is False


def test_dump_profile_enabled_key_order_after_target():
    """
    GIVEN a profile dict with a field carrying enabled
    WHEN dump_profile is called
    THEN the `enabled` key appears in the output immediately AFTER `target`
       AND before `fallback`/`source`/`prompt`
    """
    data = {
        "prompt_header": "h",
        "extract": {"summary_field": "s"},
        "bill": {"app_token": "bascnX", "table_id": "tblX"},
        "fields": [
            {
                "ai_key": "a", "feishu_field": "f", "type": "text",
                "target": "extract", "fallback": "fb", "source": "summary",
                "prompt": "p", "enabled": True,
            },
        ],
    }
    result = dump_profile(data)
    # Isolate the [[fields]] block (first [[fields]] to end of its key list).
    block_start = result.index("[[fields]]")
    block = result[block_start:]
    target_idx = block.index("target =")
    enabled_idx = block.index("enabled =")
    fallback_idx = block.index("fallback =")
    source_idx = block.index("source =")
    prompt_idx = block.index("prompt =")
    assert target_idx < enabled_idx < fallback_idx
    assert enabled_idx < source_idx
    assert enabled_idx < prompt_idx