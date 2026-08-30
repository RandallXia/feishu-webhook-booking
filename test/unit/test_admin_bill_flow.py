# test_admin_bill_flow.py — Behavior tests for the bill-table config UI
# (admin.js §bill-section) backed by GET/PUT /admin/config/profile.
#
# The UI is vanilla JS exercised in the browser; these tests cover:
#   1. autoDerive heuristic parity — the JS autoDerive() is a pure function that
#      pre-fills the 8-row mapping after the user picks a bill table. The same
#      heuristic is reproduced in Python here and run against several field
#      lists; the JS implementation MUST agree (the parity is locked by
#      asserting the JS source carries the exact match keywords the Python
#      parity asserts on).
#   2. profile GET→PUT round-trip integration (ASGI): GET returns the editable
#      shape → the UI assembles a PUT body from it (8 fields, all 8 keys, None
#      preserved) → PUT 200 writes the file + reloads.
#   3. 422 path locator: PUT returns fields[i].<field> errors; the UI must map
#      them to the matching row + control. Asserted via the JS source carrying
#      the billParseErrorPath function and via the API 422 body shape.
#   4. JS grep gates: admin.js exposes renderBillRow / autoDerive / AI_KEYS +
#      node --check passes. admin.html contains save-profile-btn + prompt-header.
#
# Strategy mirrors test_admin_extract_flow.py + test_config_profile_api.py.

from __future__ import annotations

import re
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from httpx import ASGITransport

from app.ai_profile import AiProfile, AiProfileRegistryUnavailableError, AiProfileSnapshot
from app.config import get_settings
from app.field_codec import FieldSpec
from app.main import app, lifespan

ADMIN_TOKEN = "test-admin-token"
TEST_BASE_URL = "http://testserver"
_STATIC_DIR = Path(__file__).resolve().parents[2] / "app" / "static"


# --- Shared fixtures (mirror test_config_profile_api.py) ---

_PROFILE_FIELDS = (
    FieldSpec(ai_key="summary", feishu_field="精简原始数据", type="passthrough",
              target="extract", source="summary"),
    FieldSpec(ai_key="description", feishu_field="描述", type="text",
              target="bill", prompt="描述"),
    FieldSpec(ai_key="flow_type", feishu_field="收支类型", type="single_select",
              target="bill", fallback="支出", prompt="支出/收入"),
    FieldSpec(ai_key="amount", feishu_field="金额", type="number",
              target="bill", prompt="金额"),
    FieldSpec(ai_key="category", feishu_field="分类", type="single_select",
              target="bill", fallback="其他", prompt="分类"),
    FieldSpec(ai_key="payment_method", feishu_field="支付方式", type="single_select",
              target="bill", fallback="未知", prompt="支付方式"),
    FieldSpec(ai_key="bill_date", feishu_field="日期", type="date",
              target="bill", prompt="日期"),
    FieldSpec(ai_key="raw_source", feishu_field="原始采集", type="passthrough",
              target="bill", source="summary"),
)

_PROFILE = AiProfile(
    prompt_header="你是一个账单提取助手",
    summary_field="精简原始数据",
    bill_app_token="bascn-bill",
    bill_table_id="tbl-bill",
    fields=_PROFILE_FIELDS,
)

_WHITELISTS: dict[str, set[str]] = {
    "收支类型": {"支出", "收入"},
    "分类": {"餐饮", "其他"},
    "支付方式": {"微信支付", "未知"},
}


def _enabled_settings(profile_file: Path | None = None) -> object:
    kwargs = dict(ai_enabled=True, config_reload_token=ADMIN_TOKEN)
    if profile_file is not None:
        kwargs["ai_profile_file"] = profile_file
    return replace(get_settings(), **kwargs)


def _stub_snapshot() -> MagicMock:
    snapshot = MagicMock(spec=AiProfileSnapshot)
    snapshot.profile = _PROFILE
    snapshot.option_whitelists = _WHITELISTS
    snapshot.generation = 1
    return snapshot


def _mock_registry_healthy(generation: int = 1) -> AsyncMock:
    state = {"generation": generation}

    def _get_status() -> dict:
        return {
            "generation": state["generation"],
            "config_valid": True,
            "last_reload_error": None,
            "field_count": len(_PROFILE_FIELDS),
            "source_path": "/tmp/profile.toml",
        }

    def _reload(*, force: bool) -> dict:
        state["generation"] += 1
        return _get_status()

    registry = AsyncMock()
    registry.maybe_reload = AsyncMock(return_value=None)
    registry.get_snapshot = MagicMock(return_value=_stub_snapshot())
    registry.get_status = MagicMock(side_effect=_get_status)
    registry.reload = AsyncMock(side_effect=_reload)
    return registry


def _mock_registry_fail_closed() -> AsyncMock:
    registry = AsyncMock()
    registry.maybe_reload = AsyncMock(return_value=None)
    registry.get_snapshot = MagicMock(
        side_effect=AiProfileRegistryUnavailableError("profile bad")
    )
    registry.get_status = MagicMock(
        return_value={
            "generation": 0, "config_valid": False,
            "last_reload_error": "TOML parse error: missing [bill]",
            "field_count": 0, "source_path": "/tmp/profile.toml",
        }
    )
    return registry


def _mock_feishu() -> AsyncMock:
    return AsyncMock()


def _profile_dict() -> dict:
    """Editable profile shape (mirrors GET response top-level fields)."""
    return {
        "prompt_header": _PROFILE.prompt_header,
        "summary_field": _PROFILE.summary_field,
        "bill": {
            "app_token": _PROFILE.bill_app_token,
            "table_id": _PROFILE.bill_table_id,
        },
        "fields": [
            {
                "ai_key": s.ai_key, "feishu_field": s.feishu_field,
                "type": s.type, "target": s.target,
                "fallback": s.fallback, "prompt": s.prompt, "source": s.source,
                "enabled": s.enabled,
            }
            for s in _PROFILE_FIELDS
        ],
    }


# ─── 1. autoDerive heuristic parity (Python re-implementation) ─────────────
#
# autoDerive() in admin.js inspects each Feishu field's name (substring match)
# and type, and pre-fills the 8-row mapping. Only EMPTY rows are filled
# (existing selections preserved). The Python parity below mirrors the exact
# rules the JS must implement; the JS source is then grepped for the same
# keywords to lock the parity.

# The 8 ai_keys in fixed row order. raw_source is the 8th row.
AI_KEYS = (
    "summary", "description", "flow_type", "amount",
    "category", "payment_method", "bill_date", "raw_source",
)


def _auto_derive_py(fields):
    """Python parity of admin.js autoDerive(). fields is a list of
    {name, type, options, is_primary}. Returns a dict ai_key -> {feishu_field,
    type, fallback} for matched rows only (unmatched rows omitted → UI leaves
    them as "未映射" / empty)."""
    out = {}
    # First pass: collect candidate fields per rule, honoring priority.
    bill_date_candidate = None
    for f in fields:
        name = f["name"] or ""
        ftype = f["type"]
        # amount: name contains 金额
        if "金额" in name and "amount" not in out:
            out["amount"] = {"feishu_field": name, "type": "number", "fallback": None}
            continue
        # bill_date: name contains 日期 — 账单日期 wins, else first match.
        if "日期" in name:
            if "bill_date" not in out:
                out["bill_date"] = {"feishu_field": name, "type": "date", "fallback": None}
                bill_date_candidate = name
            elif "账单" in name and "账单" not in (bill_date_candidate or ""):
                # 账单日期 takes priority over a previously-matched plain 日期.
                out["bill_date"]["feishu_field"] = name
                bill_date_candidate = name
            continue
        # category: name contains 分类
        if "分类" in name and "category" not in out:
            spec_type = "single_select" if ftype == "single_select" else "text"
            fb = None
            if spec_type == "single_select" and f.get("options"):
                fb = f["options"][0]
            out["category"] = {"feishu_field": name, "type": spec_type, "fallback": fb}
            continue
        # flow_type: name contains 收支 AND 类型
        if "收支" in name and "类型" in name and "flow_type" not in out:
            spec_type = "single_select" if ftype == "single_select" else "text"
            fb = None
            if spec_type == "single_select" and f.get("options"):
                fb = f["options"][0]
            out["flow_type"] = {"feishu_field": name, "type": spec_type, "fallback": fb}
            continue
        # payment_method: name contains 支付 OR 途径
        if ("支付" in name or "途径" in name) and "payment_method" not in out:
            spec_type = "single_select" if ftype == "single_select" else "text"
            fb = None
            if spec_type == "single_select" and f.get("options"):
                fb = f["options"][0]
            out["payment_method"] = {"feishu_field": name, "type": spec_type, "fallback": fb}
            continue
        # description: name contains 描述
        if "描述" in name and "description" not in out:
            out["description"] = {"feishu_field": name, "type": "text", "fallback": None}
            continue
        # summary: name contains 精简 OR 摘要
        if ("精简" in name or "摘要" in name) and "summary" not in out:
            out["summary"] = {"feishu_field": name, "type": "passthrough", "fallback": None}
            continue
        # raw_source: name contains 原始采集
        if "原始采集" in name and "raw_source" not in out:
            out["raw_source"] = {"feishu_field": name, "type": "passthrough", "fallback": None}
            continue
    return out


def test_auto_derive_standard_named_fields_full_set():
    """
    GIVEN a bill table whose field names cover every heuristic keyword:
       精简原始数据, 描述, 收支类型(single_select), 金额, 分类(single_select),
       支付方式(single_select), 账单日期, 原始采集
    WHEN autoDerive runs over them
    THEN every one of the 8 ai_keys is matched with the expected feishu_field
      AND single_select fields get type=single_select + fallback=first option
      AND number/date/passthrough fields get no fallback
    """
    fields = [
        {"name": "精简原始数据", "type": "text", "options": None, "is_primary": True},
        {"name": "描述", "type": "text", "options": None, "is_primary": False},
        {"name": "收支类型", "type": "single_select", "options": ["支出", "收入"], "is_primary": False},
        {"name": "金额", "type": "number", "options": None, "is_primary": False},
        {"name": "分类", "type": "single_select", "options": ["餐饮", "其他"], "is_primary": False},
        {"name": "支付方式", "type": "single_select", "options": ["微信支付", "未知"], "is_primary": False},
        {"name": "账单日期", "type": "date", "options": None, "is_primary": False},
        {"name": "原始采集", "type": "text", "options": None, "is_primary": False},
    ]
    derived = _auto_derive_py(fields)
    assert set(derived.keys()) == set(AI_KEYS)
    assert derived["summary"]["feishu_field"] == "精简原始数据"
    assert derived["summary"]["type"] == "passthrough"
    assert derived["description"]["feishu_field"] == "描述"
    assert derived["description"]["type"] == "text"
    assert derived["flow_type"]["feishu_field"] == "收支类型"
    assert derived["flow_type"]["type"] == "single_select"
    assert derived["flow_type"]["fallback"] == "支出"
    assert derived["amount"]["feishu_field"] == "金额"
    assert derived["amount"]["type"] == "number"
    assert derived["amount"]["fallback"] is None
    assert derived["category"]["feishu_field"] == "分类"
    assert derived["category"]["type"] == "single_select"
    assert derived["category"]["fallback"] == "餐饮"
    assert derived["payment_method"]["feishu_field"] == "支付方式"
    assert derived["payment_method"]["fallback"] == "微信支付"
    assert derived["bill_date"]["feishu_field"] == "账单日期"
    assert derived["bill_date"]["type"] == "date"
    assert derived["raw_source"]["feishu_field"] == "原始采集"
    assert derived["raw_source"]["type"] == "passthrough"


def test_auto_derive_date_double_candidate_picks_bill_date():
    """
    GIVEN two date-like fields: 记录日期 (first) and 账单日期 (second)
    WHEN autoDerive runs
    THEN bill_date maps to 账单日期 (the 账单 prefix wins over the first match)
    """
    fields = [
        {"name": "记录日期", "type": "date", "options": None, "is_primary": False},
        {"name": "账单日期", "type": "date", "options": None, "is_primary": False},
    ]
    derived = _auto_derive_py(fields)
    assert derived["bill_date"]["feishu_field"] == "账单日期"


def test_auto_derive_no_matching_fields_leaves_all_empty():
    """
    GIVEN a bill table whose field names match NONE of the heuristic keywords
    WHEN autoDerive runs
    THEN the derived dict is empty (UI leaves every row as 未映射 — the user
      must fill them; the server-side 422 is the backstop)
    """
    fields = [
        {"name": "备注", "type": "text", "options": None, "is_primary": False},
        {"name": "创建时间", "type": "date", "options": None, "is_primary": False},
    ]
    assert _auto_derive_py(fields) == {}


def test_auto_derive_single_select_field_pre_selects_type_and_fallback():
    """
    GIVEN a 分类 field of Feishu type single_select with options [餐饮, 其他]
    WHEN autoDerive runs
    THEN the category row gets type=single_select AND fallback=餐饮 (first)
    """
    fields = [
        {"name": "分类", "type": "single_select", "options": ["餐饮", "其他"], "is_primary": False},
    ]
    derived = _auto_derive_py(fields)
    assert derived["category"]["type"] == "single_select"
    assert derived["category"]["fallback"] == "餐饮"


def test_auto_derive_amount_field_is_single_select_still_number_by_name():
    """
    GIVEN a field named 金额 whose Feishu type is single_select (an odd edge
      case where the Feishu column was misconfigured as a dropdown for a number)
    WHEN autoDerive runs
    THEN amount maps to that field with type=number (name match wins over
      Feishu type — the heuristic is name-driven; the user can correct it)
    """
    fields = [
        {"name": "金额", "type": "single_select", "options": ["1", "2"], "is_primary": False},
    ]
    derived = _auto_derive_py(fields)
    assert derived["amount"]["feishu_field"] == "金额"
    assert derived["amount"]["type"] == "number"
    assert derived["amount"]["fallback"] is None


# ─── 2. profile GET→PUT round-trip integration ─────────────────────────────


@pytest.fixture
def profile_file(tmp_path: Path) -> Path:
    path = tmp_path / "ai-profile.toml"
    path.write_text(
        'prompt_header = "initial"\n\n'
        "[extract]\nsummary_field = \"精简原始数据\"\n\n"
        "[bill]\napp_token = \"bascn-bill\"\ntable_id = \"tbl-bill\"\n",
        encoding="utf-8",
    )
    return path


async def test_profile_get_then_put_round_trip_writes_file(monkeypatch, profile_file):
    """
    GIVEN a healthy ai_registry at generation=1 + a tmp profile file
    WHEN the UI flow runs: GET /admin/config/profile, assembles a PUT body
       from the GET response (8 fields, all 8 keys, base_generation=GET.gen)
       AND validate_profile_candidate returns no errors
    THEN the PUT response status is 200 + success=True + generation=2
      AND a .bak file was created with the ORIGINAL content
      AND ai_registry.reload(force=True) was awaited
    """
    from app import main as main_mod
    from app.toml_writer import dump_profile

    monkeypatch.setattr(
        main_mod, "validate_profile_candidate",
        AsyncMock(return_value=(_PROFILE, _WHITELISTS, [])),
    )

    original_content = profile_file.read_text(encoding="utf-8")
    registry = _mock_registry_healthy(generation=1)

    async with lifespan(app):
        app.state.settings = _enabled_settings(profile_file=profile_file)
        app.state.ai_registry = registry
        app.state.feishu_client = _mock_feishu()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            headers = {"X-Admin-Token": ADMIN_TOKEN}

            # Step 1: GET — the UI reads the editable shape.
            get_resp = await client.get("/admin/config/profile", headers=headers)
            assert get_resp.status_code == 200
            get_body = get_resp.json()
            assert get_body["generation"] == 1
            assert get_body["config_valid"] is True
            fields = get_body["fields"]
            assert len(fields) == 8
            # Every field carries all 8 keys (None preserved for the UI).
            for f in fields:
                assert set(f.keys()) == {
                    "ai_key", "feishu_field", "type", "target",
                    "fallback", "prompt", "source", "enabled",
                }

            # Step 2: UI assembles the PUT body — round-trip the profile verbatim,
            # carrying base_generation from GET.
            put_body = {
                "profile": {
                    "prompt_header": get_body["prompt_header"],
                    "summary_field": get_body["summary_field"],
                    "bill": get_body["bill"],
                    "fields": fields,
                },
                "base_generation": get_body["generation"],
            }
            put_resp = await client.put(
                "/admin/config/profile", json=put_body, headers=headers
            )

    assert put_resp.status_code == 200, put_resp.text
    put_body_resp = put_resp.json()
    assert put_body_resp["success"] is True
    assert put_body_resp["generation"] == 2
    assert put_body_resp["warnings"] == []

    # .bak holds the original content.
    bak_path = profile_file.with_suffix(".toml.bak")
    assert bak_path.is_file()
    assert bak_path.read_text(encoding="utf-8") == original_content

    # reload was force-called.
    registry.reload.assert_awaited_once()
    assert registry.reload.await_args.kwargs.get("force") is True


# ─── 3. 422 path locator — fields[i].<field> shape ──────────────────────────


async def test_profile_put_422_fields_path_error_shape(monkeypatch, profile_file):
    """
    GIVEN validate_profile_candidate returns a single error with path
       "fields[2].fallback" (the 3rd row's fallback control)
    WHEN PUT /admin/config/profile is called
    THEN the response status is 422
      AND the body is {detail: {errors: [{path, message}]}}
      (the UI's billParseErrorPath consumes errors[i].path to locate the row)
    """
    from app import main as main_mod

    errors = [{"path": "fields[2].fallback", "message": "fallback not in options"}]
    monkeypatch.setattr(
        main_mod, "validate_profile_candidate",
        AsyncMock(return_value=(_PROFILE, _WHITELISTS, errors)),
    )

    registry = _mock_registry_healthy(generation=1)
    original_content = profile_file.read_text(encoding="utf-8")

    async with lifespan(app):
        app.state.settings = _enabled_settings(profile_file=profile_file)
        app.state.ai_registry = registry
        app.state.feishu_client = _mock_feishu()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/profile",
                json={"profile": _profile_dict(), "base_generation": 1},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 422
    body = response.json()
    assert body["detail"]["errors"] == errors
    # File untouched.
    assert profile_file.read_text(encoding="utf-8") == original_content
    registry.reload.assert_not_awaited()


async def test_profile_put_422_extract_summary_field_path(monkeypatch, profile_file):
    """
    GIVEN validate returns an error with path "extract.summary_field"
    WHEN PUT /admin/config/profile is called
    THEN the response status is 422
      AND the body carries the path verbatim (UI maps it to the summary row)
    """
    from app import main as main_mod

    errors = [{"path": "extract.summary_field", "message": "summary field not in extract table"}]
    monkeypatch.setattr(
        main_mod, "validate_profile_candidate",
        AsyncMock(return_value=(_PROFILE, _WHITELISTS, errors)),
    )

    registry = _mock_registry_healthy(generation=1)

    async with lifespan(app):
        app.state.settings = _enabled_settings(profile_file=profile_file)
        app.state.ai_registry = registry
        app.state.feishu_client = _mock_feishu()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/profile",
                json={"profile": _profile_dict(), "base_generation": 1},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 422
    assert response.json()["detail"]["errors"] == errors


async def test_profile_put_422_bill_path_returns_banner_shape(monkeypatch, profile_file):
    """
    GIVEN validate returns an error with path "bill" (section-level — e.g. the
       bill app_token/table_id block is missing or malformed)
    WHEN PUT /admin/config/profile is called
    THEN the response status is 422
      AND the body is {detail: {errors: [{path: "bill", message: ...}]}}
      (the UI's billParseErrorPath maps path "bill" → {banner: true} and the
       save catch shows the message in both the bill-banner AND bill-error-row;
       this fixes the prior silent-drop where path="bill" had no locator branch)
    """
    from app import main as main_mod

    errors = [{"path": "bill", "message": "bill table app_token and table_id are required"}]
    monkeypatch.setattr(
        main_mod, "validate_profile_candidate",
        AsyncMock(return_value=(_PROFILE, _WHITELISTS, errors)),
    )

    registry = _mock_registry_healthy(generation=1)

    async with lifespan(app):
        app.state.settings = _enabled_settings(profile_file=profile_file)
        app.state.ai_registry = registry
        app.state.feishu_client = _mock_feishu()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/profile",
                json={"profile": _profile_dict(), "base_generation": 1},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 422
    assert response.json()["detail"]["errors"] == errors


async def test_profile_put_stale_generation_returns_409_stale(profile_file):
    """
    GIVEN ai_registry.get_status generation=5
    WHEN PUT /admin/config/profile is called with base_generation=1 (stale)
    THEN the response is 409 STALE_WRITE
      (the UI's auto-reload-on-409 path depends on this signal)
    """
    registry = _mock_registry_healthy(generation=5)

    async with lifespan(app):
        app.state.settings = _enabled_settings(profile_file=profile_file)
        app.state.ai_registry = registry
        app.state.feishu_client = _mock_feishu()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/profile",
                json={"profile": _profile_dict(), "base_generation": 1},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "STALE_WRITE"


# ─── 4. JS grep gates + admin.html id assertions ───────────────────────────


def _read_static(name: str) -> str:
    return (_STATIC_DIR / name).read_text(encoding="utf-8")


def test_admin_js_exposes_bill_ui_functions():
    """
    GIVEN app/static/admin.js exists
    WHEN the source is scanned for the function declarations the bill-table
       UI requires
    THEN it contains function declarations for:
      - renderBillRow (renders one row of the 8-row mapping)
      - autoDerive (the pure prefill function)
      - billParseErrorPath (the fields[i].<field> → row+control mapper)
    AND it declares an AI_KEYS constant
    """
    js = _read_static("admin.js")
    required = ["renderBillRow", "autoDerive", "billParseErrorPath"]
    for name in required:
        pattern = r"function\s+" + re.escape(name) + r"\s*\("
        assert re.search(pattern, js), f"admin.js missing function declaration: {name}"
    assert re.search(r"\bAI_KEYS\b", js), "admin.js missing AI_KEYS constant"


def test_admin_js_ai_keys_constant_has_eight_elements():
    """
    GIVEN admin.js declares AI_KEYS
    WHEN the array literal is extracted
    THEN it contains exactly 8 string elements in the fixed row order:
      summary, description, flow_type, amount, category, payment_method,
      bill_date, raw_source
    """
    js = _read_static("admin.js")
    m = re.search(r"AI_KEYS\s*=\s*\[([^\]]*)\]", js, re.S)
    assert m, "AI_KEYS array literal not found"
    elements = re.findall(r"""["']([^"']+)["']""", m.group(1))
    assert elements == [
        "summary", "description", "flow_type", "amount",
        "category", "payment_method", "bill_date", "raw_source",
    ]


def test_admin_js_auto_derive_uses_heuristic_keywords():
    """
    GIVEN admin.js autoDerive function
    WHEN the function body is scanned
    THEN it references every keyword the Python parity asserts on:
      金额, 日期, 账单, 分类, 收支, 类型, 支付, 途径, 描述, 精简, 摘要, 原始采集
      (locks JS↔Python parity)
    """
    js = _read_static("admin.js")
    # Slice from `function autoDerive` to the next top-level `function `.
    m = re.search(r"function\s+autoDerive\s*\([^)]*\)\s*\{(.+?)\n\s*function\s", js, re.S)
    assert m, "autoDerive function body not isolatable"
    body = m.group(1)
    for kw in ["金额", "日期", "账单", "分类", "收支", "类型",
               "支付", "途径", "描述", "精简", "摘要", "原始采集"]:
        assert kw in body, f"autoDerive missing keyword: {kw}"


def test_admin_js_node_check_passes():
    """
    GIVEN app/static/admin.js exists on disk
    WHEN `node --check app/static/admin.js` is executed
    THEN the exit code is 0 (JS syntax gate — todo 10 added new functions)
    """
    js_path = _STATIC_DIR / "admin.js"
    assert js_path.is_file()
    result = subprocess.run(
        ["node", "--check", str(js_path)], capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"node --check failed (exit {result.returncode}):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_admin_js_has_enable_toggle_class():
    """
    GIVEN app/static/admin.js (todo 3 — per-field toggle UI)
    WHEN the source is scanned
    THEN it contains the `enable-toggle` class (the CSS toggle checkbox
       wired into renderBillRow) at least once
    """
    js = _read_static("admin.js")
    assert js.count("enable-toggle") >= 1, "admin.js missing enable-toggle class"


def test_admin_js_has_bill_row_disabled_class():
    """
    GIVEN app/static/admin.js (todo 3 — disabled-row visual state)
    WHEN the source is scanned
    THEN it contains the `bill-row-disabled` class (toggled on a row when
       its enable toggle is off) at least once
    """
    js = _read_static("admin.js")
    assert js.count("bill-row-disabled") >= 1, "admin.js missing bill-row-disabled class"


def test_admin_html_bill_section_has_table_skeleton():
    """
    GIVEN app/static/admin.html (todo 3 — table-based mapping UI)
    WHEN the source is scanned
    THEN it contains `<table class="bill-table"` (the 8-row mapping table)
       AND `<tbody id="bill-fields-list"` (the rows container)
    """
    html = _read_static("admin.html")
    assert '<table class="bill-table"' in html, "admin.html missing bill-table table"
    assert '<tbody id="bill-fields-list"' in html, "admin.html missing bill-fields-list tbody"


def test_admin_bill_derive_btn_not_button_selector():
    """
    GIVEN app/static/admin.js + admin.css (todo 3 — derive link is now <a>)
    WHEN the source is scanned for `bill-derive-btn`
    THEN no line matches a `button#bill-derive-btn` or `.btn#bill-derive-btn`
       selector prefix (the derive control is an <a>, not a button)
    """
    for name in ("admin.js", "admin.css"):
        src = _read_static(name)
        for line in src.splitlines():
            if "bill-derive-btn" in line:
                assert "button#" not in line, f"{name} has button#bill-derive-btn selector"
                assert ".btn#" not in line, f"{name} has .btn#bill-derive-btn selector"


def test_admin_js_bill_parse_error_path_handles_bill_and_extract_banner():
    """
    GIVEN admin.js billParseErrorPath function (todo 3 — banner routing fix)
    WHEN the function body is scanned
    THEN it returns {banner: true} for path "bill" or "extract"
       (the section-level error → showBanner path; fixes the prior silent-drop
       where path="bill"/"extract" had no locator branch)
    """
    js = _read_static("admin.js")
    m = re.search(r"function\s+billParseErrorPath\s*\([^)]*\)\s*\{(.+?)\n\s*function\s", js, re.S)
    assert m, "billParseErrorPath function body not isolatable"
    body = m.group(1)
    assert "banner" in body, "billParseErrorPath missing banner branch for bill/extract paths"
    assert "'bill'" in body or '"bill"' in body, "billParseErrorPath missing 'bill' path literal"
    assert "'extract'" in body or '"extract"' in body, "billParseErrorPath missing 'extract' path literal"


async def test_admin_html_bill_section_has_required_ids():
    """
    GIVEN the FastAPI app is running
    WHEN GET /admin/ai is requested
    THEN the bill-section contains id="save-profile-btn" and id="prompt-header"
       (the JS wires click/input handlers to these ids)
    """
    async with lifespan(app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get("/admin/ai")

    body = response.text
    assert 'id="save-profile-btn"' in body
    assert 'id="prompt-header"' in body
    assert 'id="bill-section"' in body
    assert 'id="bill-banner"' in body
