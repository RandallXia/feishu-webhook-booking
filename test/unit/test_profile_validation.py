# test_profile_validation.py — Behavior tests for profile candidate validation (app/ai_profile.py)
#
# Tests validate_profile_candidate(profile_text, feishu, extract_app_token, extract_table_id):
#   1. Valid candidate (bill fields present + fallbacks in options + summary_field in extract table) → zero errors
#   2. summary_field NOT in extract table fields → errors contain path="extract.summary_field"
#   3. single_select fallback NOT in options + field name NOT in bill table → two errors collected (different paths)
#   4. Non-single_select bill field name NOT in bill table → errors (new expansion)
#   5. parse error (bad TOML) → errors contain path="profile"
#   6. list_fields failure on bill table → fail-closed errors (path="bill")
#   7. list_fields failure on extract table → errors contain path="extract"
#   8. parse_profile_text refactor: golden regression — all ProfileConfigError messages byte-identical
#
# All FeishuClient IO is mocked (AsyncMock). list_fields dispatches by (app_token, table_id).
# Extract-table三元组 is passed explicitly; this function does NOT read target_registry.

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.ai_profile import (
    AiProfile,
    ProfileConfigError,
    parse_profile,
    parse_profile_text,
    validate_profile_candidate,
)
from app.feishu_client import FeishuClient, FeishuClientError


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
fallback = "支出"
target = "bill"
prompt = "支出或收入"

[[fields]]
ai_key = "amount"
feishu_field = "金额"
type = "number"
target = "bill"
prompt = "金额"
"""


_EXTRACT_APP_TOKEN = "extractAppTok"
_EXTRACT_TABLE_ID = "extractTbl"


def _single_select_field(name: str, options: list[str]) -> dict:
    return {
        "field_name": name,
        "property": {"options": [{"name": opt} for opt in options]},
    }


def _plain_field(name: str) -> dict:
    """A non-single_select field entry in list_fields response."""
    return {"field_name": name, "property": {"type": "text"}}


def _make_feishu(
    bill_fields: dict[str, dict] | None = None,
    extract_fields: dict[str, dict] | None = None,
    bill_side_effect: Exception | None = None,
    extract_side_effect: Exception | None = None,
) -> AsyncMock:
    """Mock FeishuClient whose list_fields dispatches by (app_token, table_id)."""
    feishu = AsyncMock(spec=FeishuClient)

    async def _list_fields(app_token: str, table_id: str) -> dict[str, dict]:
        if app_token == "bascnXXX" and table_id == "tblXXX":
            if bill_side_effect is not None:
                raise bill_side_effect
            return bill_fields or {}
        if app_token == _EXTRACT_APP_TOKEN and table_id == _EXTRACT_TABLE_ID:
            if extract_side_effect is not None:
                raise extract_side_effect
            return extract_fields or {}
        raise FeishuClientError(
            f"unknown target app_token={app_token} table_id={table_id}",
            stage="list_fields",
        )

    feishu.list_fields = AsyncMock(side_effect=_list_fields)
    return feishu


def _default_bill_fields() -> dict[str, dict]:
    return {
        "消费描述": _plain_field("消费描述"),
        "收支类型": _single_select_field("收支类型", ["支出", "收入"]),
        "金额": _plain_field("金额"),
    }


def _default_extract_fields() -> dict[str, dict]:
    return {
        "精简原始数据": _plain_field("精简原始数据"),
    }


def _error_paths(errors: list[dict]) -> list[str]:
    return [e["path"] for e in errors]


# ─── 1. Valid candidate → zero errors ──────────────────────────────────────


async def test_valid_candidate_zero_errors():
    """
    GIVEN a valid profile TOML text
       AND a FeishuClient whose bill list_fields returns matching fields + options
       AND an extract table containing summary_field
    WHEN validate_profile_candidate is awaited with the text + extract三元组
    THEN returned errors list is empty
      AND returned profile is an AiProfile with summary_field="精简原始数据"
      AND returned whitelists contains the single_select field's options
    """
    feishu = _make_feishu(
        bill_fields=_default_bill_fields(),
        extract_fields=_default_extract_fields(),
    )

    profile, whitelists, errors = await validate_profile_candidate(
        _VALID_PROFILE_TOML, feishu, _EXTRACT_APP_TOKEN, _EXTRACT_TABLE_ID
    )

    assert errors == []
    assert isinstance(profile, AiProfile)
    assert profile.summary_field == "精简原始数据"
    assert "收支类型" in whitelists
    assert whitelists["收支类型"] == {"支出", "收入"}
    assert "消费描述" not in whitelists
    assert "金额" not in whitelists


# ─── 2. summary_field NOT in extract table → errors path="extract.summary_field" ──


async def test_summary_field_not_in_extract_table_errors():
    """
    GIVEN a valid profile TOML text whose [extract].summary_field="精简原始数据"
       AND an extract table list_fields response that does NOT contain "精简原始数据"
    WHEN validate_profile_candidate is awaited
    THEN returned errors list contains exactly one entry with path="extract.summary_field"
      AND the message mentions the missing summary_field name
    """
    feishu = _make_feishu(
        bill_fields=_default_bill_fields(),
        extract_fields={"some_other_field": _plain_field("some_other_field")},
    )

    profile, whitelists, errors = await validate_profile_candidate(
        _VALID_PROFILE_TOML, feishu, _EXTRACT_APP_TOKEN, _EXTRACT_TABLE_ID
    )

    assert _error_paths(errors) == ["extract.summary_field"]
    assert "精简原始数据" in errors[0]["message"]


# ─── 3. fallback not in options + field name missing → two errors collected ──


async def test_fallback_missing_and_field_name_missing_collects_two_errors():
    """
    GIVEN a profile with a single_select field whose fallback="支出"
       AND a bill table list_fields response where the field name is MISSING entirely
          (so neither options nor any field entry exists)
    WHEN validate_profile_candidate is awaited
    THEN returned errors list contains TWO entries with distinct paths
      AND one path is "fields[i].fallback" (fallback not in options)
      AND another path is "fields[i].feishu_field" (field name not in bill table)
    """
    bill_fields = {
        "消费描述": _plain_field("消费描述"),
        "金额": _plain_field("金额"),
    }
    feishu = _make_feishu(
        bill_fields=bill_fields,
        extract_fields=_default_extract_fields(),
    )

    profile, whitelists, errors = await validate_profile_candidate(
        _VALID_PROFILE_TOML, feishu, _EXTRACT_APP_TOKEN, _EXTRACT_TABLE_ID
    )

    paths = _error_paths(errors)
    assert "fields[2].feishu_field" in paths
    assert "fields[2].fallback" in paths
    assert len(errors) == 2
    assert isinstance(profile, AiProfile)


# ─── 4. Non-single_select bill field name NOT in bill table → errors ────────


async def test_non_single_select_bill_field_missing_errors():
    """
    GIVEN a profile with a target="bill" text field "消费描述"
       AND a bill table list_fields response that does NOT contain "消费描述"
    WHEN validate_profile_candidate is awaited
    THEN returned errors list contains an entry with path="fields[i].feishu_field"
      AND the message mentions "消费描述"
    """
    bill_fields = {
        "收支类型": _single_select_field("收支类型", ["支出", "收入"]),
        "金额": _plain_field("金额"),
    }
    feishu = _make_feishu(
        bill_fields=bill_fields,
        extract_fields=_default_extract_fields(),
    )

    profile, whitelists, errors = await validate_profile_candidate(
        _VALID_PROFILE_TOML, feishu, _EXTRACT_APP_TOKEN, _EXTRACT_TABLE_ID
    )

    paths = _error_paths(errors)
    assert "fields[1].feishu_field" in paths
    msg = next(e["message"] for e in errors if e["path"] == "fields[1].feishu_field")
    assert "消费描述" in msg
    assert not any(p.endswith(".fallback") for p in paths)


# ─── 5. parse error → errors path="profile" ─────────────────────────────────


async def test_parse_error_returns_profile_path():
    """
    GIVEN a profile text that fails parse_profile_text (e.g. missing [extract] section)
    WHEN validate_profile_candidate is awaited
    THEN returned errors list contains exactly one entry with path="profile"
      AND returned profile is None
      AND returned whitelists is an empty dict
    """
    bad_text = 'prompt_header = "x"\n[bill]\napp_token="a"\ntable_id="b"\n'
    feishu = _make_feishu(
        bill_fields=_default_bill_fields(),
        extract_fields=_default_extract_fields(),
    )

    profile, whitelists, errors = await validate_profile_candidate(
        bad_text, feishu, _EXTRACT_APP_TOKEN, _EXTRACT_TABLE_ID
    )

    assert _error_paths(errors) == ["profile"]
    assert profile is None
    assert whitelists == {}
    feishu.list_fields.assert_not_awaited()


# ─── 6. list_fields failure on bill table → fail-closed errors ─────────────


async def test_bill_list_fields_failure_fail_closed_errors():
    """
    GIVEN a valid profile TOML text
       AND a FeishuClient whose bill list_fields raises FeishuClientError
    WHEN validate_profile_candidate is awaited
    THEN returned errors list contains an entry with path="bill"
      AND the message starts with "list_fields failed"
      AND returned profile is None (fail-closed: cannot validate → not approved)
    """
    feishu = _make_feishu(
        bill_side_effect=FeishuClientError("network down", stage="list_fields"),
        extract_fields=_default_extract_fields(),
    )

    profile, whitelists, errors = await validate_profile_candidate(
        _VALID_PROFILE_TOML, feishu, _EXTRACT_APP_TOKEN, _EXTRACT_TABLE_ID
    )

    assert _error_paths(errors) == ["bill"]
    assert errors[0]["message"].startswith("list_fields failed")
    assert "network down" in errors[0]["message"]
    assert profile is None
    assert whitelists == {}
    assert feishu.list_fields.await_count == 1


# ─── 7. list_fields failure on extract table → errors path="extract" ───────


async def test_extract_list_fields_failure_errors_extract_path():
    """
    GIVEN a valid profile TOML text
       AND a FeishuClient whose bill list_fields succeeds
       AND whose extract list_fields raises FeishuClientError
    WHEN validate_profile_candidate is awaited
    THEN returned errors list contains an entry with path="extract"
      AND the message starts with "list_fields failed"
    """
    feishu = _make_feishu(
        bill_fields=_default_bill_fields(),
        extract_side_effect=FeishuClientError("extract down", stage="list_fields"),
    )

    profile, whitelists, errors = await validate_profile_candidate(
        _VALID_PROFILE_TOML, feishu, _EXTRACT_APP_TOKEN, _EXTRACT_TABLE_ID
    )

    paths = _error_paths(errors)
    assert "extract" in paths
    extract_err = next(e for e in errors if e["path"] == "extract")
    assert extract_err["message"].startswith("list_fields failed")
    assert "extract down" in extract_err["message"]
    assert isinstance(profile, AiProfile)
    assert "extract.summary_field" not in paths


# ─── 8. parse_profile_text refactor: golden regression ──────────────────────


def test_parse_profile_text_delegates_to_path_wrapper(tmp_path):
    """
    GIVEN a valid profile TOML written to a file
    WHEN parse_profile(path) and parse_profile_text(text) are both called
    THEN both return AiProfile instances with identical field values
      AND the file-level error "Missing profile file: {path}" stays in parse_profile
    """
    path = tmp_path / "profile.toml"
    path.write_text(_VALID_PROFILE_TOML, encoding="utf-8")

    via_path = parse_profile(path)
    via_text = parse_profile_text(_VALID_PROFILE_TOML)

    assert via_path == via_text
    assert via_path.summary_field == via_text.summary_field
    assert via_path.fields == via_text.fields


def test_parse_profile_missing_file_error_message_unchanged(tmp_path):
    """
    GIVEN a nonexistent profile file path
    WHEN parse_profile is called with that path
    THEN ProfileConfigError is raised with message exactly f"Missing profile file: {path}"
      (the file-level error stays in parse_profile, NOT parse_profile_text)
    """
    missing = tmp_path / "does_not_exist.toml"
    with pytest.raises(ProfileConfigError) as exc_info:
        parse_profile(missing)
    assert str(exc_info.value) == f"Missing profile file: {missing}"


def test_parse_profile_text_golden_missing_extract_section():
    """
    GIVEN a profile TOML text without an [extract] section
    WHEN parse_profile_text is called
    THEN ProfileConfigError message contains "profile must define [extract] section"
    """
    bad = 'prompt_header = "x"\n[bill]\napp_token="a"\ntable_id="b"\n[[fields]]\nai_key="k"\nfeishu_field="f"\ntype="text"\ntarget="extract"\n'
    with pytest.raises(ProfileConfigError, match=r"\[extract\] section"):
        parse_profile_text(bad)


def test_parse_profile_text_golden_missing_bill_section():
    """
    GIVEN a profile TOML text without a [bill] section
    WHEN parse_profile_text is called
    THEN ProfileConfigError message contains "profile must define [bill] section"
    """
    bad = 'prompt_header="x"\n[extract]\nsummary_field="f"\n[[fields]]\nai_key="k"\nfeishu_field="f"\ntype="text"\ntarget="extract"\n'
    with pytest.raises(ProfileConfigError, match=r"\[bill\] section"):
        parse_profile_text(bad)


def test_parse_profile_text_golden_empty_summary_field():
    """
    GIVEN a profile TOML text with empty [extract].summary_field
    WHEN parse_profile_text is called
    THEN ProfileConfigError message contains "[extract].summary_field must be non-empty"
    """
    bad = 'prompt_header="x"\n[extract]\nsummary_field=""\n[bill]\napp_token="a"\ntable_id="b"\n[[fields]]\nai_key="k"\nfeishu_field="f"\ntype="text"\ntarget="extract"\n'
    with pytest.raises(ProfileConfigError, match=r"summary_field must be non-empty"):
        parse_profile_text(bad)


def test_parse_profile_text_golden_empty_bill_app_token():
    """
    GIVEN a profile TOML text with empty [bill].app_token
    WHEN parse_profile_text is called
    THEN ProfileConfigError message contains "[bill].app_token must be non-empty"
    """
    bad = 'prompt_header="x"\n[extract]\nsummary_field="f"\n[bill]\napp_token=""\ntable_id="b"\n[[fields]]\nai_key="k"\nfeishu_field="f"\ntype="text"\ntarget="extract"\n'
    with pytest.raises(ProfileConfigError, match=r"app_token must be non-empty"):
        parse_profile_text(bad)


def test_parse_profile_text_golden_empty_bill_table_id():
    """
    GIVEN a profile TOML text with empty [bill].table_id
    WHEN parse_profile_text is called
    THEN ProfileConfigError message contains "[bill].table_id must be non-empty"
    """
    bad = 'prompt_header="x"\n[extract]\nsummary_field="f"\n[bill]\napp_token="a"\ntable_id=""\n[[fields]]\nai_key="k"\nfeishu_field="f"\ntype="text"\ntarget="extract"\n'
    with pytest.raises(ProfileConfigError, match=r"table_id must be non-empty"):
        parse_profile_text(bad)


def test_parse_profile_text_golden_empty_prompt_header():
    """
    GIVEN a profile TOML text with empty prompt_header
    WHEN parse_profile_text is called
    THEN ProfileConfigError message contains "prompt_header must be non-empty"
    """
    bad = 'prompt_header=""\n[extract]\nsummary_field="f"\n[bill]\napp_token="a"\ntable_id="b"\n[[fields]]\nai_key="k"\nfeishu_field="f"\ntype="text"\ntarget="extract"\n'
    with pytest.raises(ProfileConfigError, match=r"prompt_header must be non-empty"):
        parse_profile_text(bad)
