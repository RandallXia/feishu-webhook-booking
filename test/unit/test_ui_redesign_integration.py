# test_ui_redesign_integration.py — Field-enable-toggle end-to-end ASGI tests
# (Todo 5 of the ui-redesign-field-toggles plan).
#
# Four scenarios that prove the per-field `enabled` flag flows end-to-end
# through the full ASGI stack (PUT /admin/config/profile → file → reload →
# snapshot → pipeline.run → feishu create_record / update_record_field). Mock
# strategy mirrors test_config_ui_integration.py: stub app.state.feishu_client
# / ai_registry / target_registry + settings replace (frozen → dataclasses.
# replace) + tmp files + a REAL AiPipeline wired with a mock extractor + spy
# feishu_client (the swapping-registry variant, so the webhook path sees the
# post-save profile).
#
#   1. Disabled bill field is NOT written end-to-end (others still are).
#   2. PUT enabled=false → GET echoes enabled=false (roundtrip).
#   3. Re-enable restores the write (disable → skip → re-enable → write again).
#   4. summary disabled: writeback skipped but bill record still created
#      (backend respects the UI's locked-open summary spec defensively).
#
# The 8-key fields fixture (incl. `enabled`) is shared with
# test_config_ui_integration.py — the API contract requires `enabled` as a
# pydantic-required key on every field dict.

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from httpx import ASGITransport

from app.ai_extractor import ExtractionResult
from app.ai_profile import (
    AiProfile,
    AiProfileSnapshot,
)
from app.config import get_settings
from app.field_codec import FieldSpec
from app.main import app, lifespan
from app.pipeline import AiPipeline
from app.target_registry import (
    FeishuTargetConfig,
    TargetRegistrySnapshot,
)


ADMIN_TOKEN = "test-admin-token"
WEBHOOK_TOKEN = "test-webhook-token"
TEST_BASE_URL = "http://testserver"


# ─── Shared fixtures + helpers (mirrors test_config_ui_integration.py) ──────


_PROFILE_FIELDS = (
    FieldSpec(
        ai_key="summary",
        feishu_field="精简原始数据",
        type="passthrough",
        target="extract",
        source="summary",
    ),
    FieldSpec(
        ai_key="description",
        feishu_field="描述",
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
        prompt="支出/收入",
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
        fallback="未知",
        prompt="支付方式",
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
        feishu_field="原始采集",
        type="passthrough",
        target="bill",
        source="summary",
    ),
)

_WHITELISTS: dict[str, set[str]] = {
    "收支类型": {"支出", "收入"},
    "分类": {"餐饮", "其他"},
    "支付方式": {"微信支付", "未知"},
}


def _profile(
    *,
    summary_field: str = "精简原始数据",
    disabled_keys: tuple[str, ...] = (),
) -> AiProfile:
    """Build an AiProfile, optionally disabling fields by ai_key.

    The summary passthrough FieldSpec's feishu_field must equal summary_field
    so encode_fields populates extract_fields[summary_field].
    """
    fields = tuple(
        replace(
            s,
            feishu_field=summary_field,
            enabled=(s.ai_key not in disabled_keys),
        )
        if s.ai_key == "summary"
        else replace(s, enabled=(s.ai_key not in disabled_keys))
        for s in _PROFILE_FIELDS
    )
    return AiProfile(
        prompt_header="你是一个账单提取助手",
        summary_field=summary_field,
        bill_app_token="bascn-bill",
        bill_table_id="tbl-bill",
        fields=fields,
    )


def _profile_dict(
    *,
    summary_field: str = "精简原始数据",
    disabled_keys: tuple[str, ...] = (),
) -> dict:
    """Editable profile shape (mirrors GET response + PUT body.profile)."""
    profile = _profile(summary_field=summary_field, disabled_keys=disabled_keys)
    return {
        "prompt_header": profile.prompt_header,
        "summary_field": profile.summary_field,
        "bill": {"app_token": "bascn-bill", "table_id": "tbl-bill"},
        "fields": [
            {
                "ai_key": s.ai_key, "feishu_field": s.feishu_field,
                "type": s.type, "target": s.target,
                "fallback": s.fallback, "prompt": s.prompt, "source": s.source,
                "enabled": s.enabled,
            }
            for s in profile.fields
        ],
    }


def _stub_snapshot(profile: AiProfile, generation: int = 1) -> MagicMock:
    snapshot = MagicMock(spec=AiProfileSnapshot)
    snapshot.profile = profile
    snapshot.option_whitelists = _WHITELISTS
    snapshot.generation = generation
    return snapshot


def _mock_ai_registry_swapping(initial: AiProfile, reloaded: AiProfile, generation: int = 1) -> AsyncMock:
    """reload(force=True) swaps the served profile — mirrors the real registry
    re-reading the file on reload, so the webhook path sees the post-save profile.
    """
    state = {"generation": generation, "profile": initial}

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
        state["profile"] = reloaded
        return _get_status()

    registry = AsyncMock()
    registry.maybe_reload = AsyncMock(return_value=None)
    registry.get_snapshot = MagicMock(
        side_effect=lambda: _stub_snapshot(state["profile"], state["generation"])
    )
    registry.get_status = MagicMock(side_effect=_get_status)
    registry.reload = AsyncMock(side_effect=_reload)
    return registry


def _mock_ai_registry_static(profile: AiProfile, generation: int = 1) -> AsyncMock:
    """Non-swapping registry: reload just bumps generation, profile unchanged.

    Used by GET-roundtrip + summary-disabled tests (no mid-test profile swap).
    """
    state = {"generation": generation, "profile": profile}

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
    registry.get_snapshot = MagicMock(
        side_effect=lambda: _stub_snapshot(state["profile"], state["generation"])
    )
    registry.get_status = MagicMock(side_effect=_get_status)
    registry.reload = AsyncMock(side_effect=_reload)
    return registry


def _targets_snapshot(targets, default_alias, generation=1) -> TargetRegistrySnapshot:
    by_alias = {t.alias: t for t in targets}
    by_year = {t.year: t.alias for t in targets if t.year is not None}
    return TargetRegistrySnapshot(
        mode="dynamic", default_alias=default_alias,
        targets_by_alias=by_alias, aliases_by_year=by_year,
        loaded_at=0.0, source_path="/tmp/feishu-targets.toml",
        source_mtime=0.0, generation=generation,
    )


def _mock_target_registry(snapshot, generation=1) -> MagicMock:
    from app.target_registry import TargetRegistry

    state = {"generation": generation, "snapshot": snapshot}

    def _describe() -> dict:
        return {
            "mode": "dynamic",
            "default_alias": state["snapshot"].default_alias,
            "target_count": len(state["snapshot"].targets_by_alias),
            "reload_generation": state["generation"],
            "config_valid": True,
            "last_reload_error": None,
            "source_path": state["snapshot"].source_path,
        }

    def _reload(*, force: bool) -> dict:
        state["generation"] += 1
        return _describe()

    registry = MagicMock(spec=TargetRegistry)
    registry.describe = MagicMock(side_effect=_describe)
    registry.get_snapshot = MagicMock(return_value=state["snapshot"])
    registry.reload = MagicMock(side_effect=_reload)
    registry.maybe_reload = MagicMock(return_value=None)

    def _resolve(book_alias=None, year=None):
        snap = state["snapshot"]
        if book_alias and book_alias in snap.targets_by_alias:
            return snap.targets_by_alias[book_alias]
        if year is not None and year in snap.aliases_by_year:
            return snap.targets_by_alias[snap.aliases_by_year[year]]
        return snap.targets_by_alias[snap.default_alias]
    registry.resolve = MagicMock(side_effect=_resolve)
    return registry


def _dynamic_settings(targets_file, profile_file=None) -> object:
    kwargs = dict(
        config_reload_token=ADMIN_TOKEN,
        feishu_targets_file=targets_file,
    )
    if profile_file is not None:
        kwargs["ai_enabled"] = True
        kwargs["ai_profile_file"] = profile_file
    return replace(get_settings(), **kwargs)


def _targets_toml(aliases) -> str:
    lines = [f'default_alias = "{aliases[0][0]}"', ""]
    for alias, year in aliases:
        lines += [
            f'[targets."{alias}"]',
            f"year = {year}" if year is not None else "",
            f'app_token = "bascn_{alias}"',
            f'table_id = "tbl_{alias}"',
            f'record_id = "rec_{alias}"',
            'original_field_name = "原始信息"',
            "enabled = true",
            "",
        ]
    return "\n".join(ln for ln in lines if ln != "")


def _profile_toml(summary_field="精简原始数据") -> str:
    return (
        'prompt_header = "你是一个账单提取助手"\n\n'
        "[extract]\n"
        f'summary_field = "{summary_field}"\n\n'
        "[bill]\n"
        'app_token = "bascn-bill"\n'
        'table_id = "tbl-bill"\n'
    )


def _ok_extraction(**overrides) -> ExtractionResult:
    defaults = dict(
        summary="麦当劳 ¥42 微信支付 2026-08-28",
        description="麦当劳午餐",
        flow_type="支出",
        amount=42.0,
        category="餐饮",
        payment_method="微信支付",
        bill_date="2026-08-28",
    )
    defaults.update(overrides)
    return ExtractionResult(**defaults)


@pytest.fixture
def targets_file(tmp_path: Path) -> Path:
    path = tmp_path / "feishu-targets.toml"
    path.write_text(_targets_toml([("2026", 2026)]), encoding="utf-8")
    return path


@pytest.fixture
def profile_file(tmp_path: Path) -> Path:
    path = tmp_path / "ai-profile.toml"
    path.write_text(_profile_toml(), encoding="utf-8")
    return path


# ═════════════════════════════════════════════════════════════════════════════
# Scenario 1: disabled bill field is NOT written end-to-end
# ═════════════════════════════════════════════════════════════════════════════


async def test_disabled_field_not_written_end_to_end(monkeypatch, targets_file, profile_file):
    """
    GIVEN a dynamic-mode service (AI enabled) with a profile whose `category`
       field has enabled=false (all other 7 fields enabled=true)
       AND validate_profile_candidate returns that disabled-category profile
       AND a REAL AiPipeline wired with a mock extractor + a spy feishu_client
    WHEN the operator PUTs /admin/config/profile (saving category disabled)
       AND THEN POSTs /v1/webhook/ocr
    THEN the webhook's ai_status == "succeeded"
       AND feishu.create_record's `fields` arg does NOT contain the `分类` key
       (the disabled bill field is skipped end-to-end)
       AND feishu.create_record's `fields` arg DOES contain `描述` (an enabled
       bill field) — guards against the false positive where every field is
       skipped (which would also produce an absent `分类`).
    """
    disabled_profile = _profile(disabled_keys=("category",))

    monkeypatch.setattr(
        "app.main.validate_profile_candidate",
        AsyncMock(return_value=(disabled_profile, _WHITELISTS, [])),
    )

    t2026 = FeishuTargetConfig("2026", 2026, "bascn_2026", "tbl_2026", "rec_2026", "原始信息", True)
    targets_snap = _targets_snapshot([t2026], default_alias="2026", generation=1)
    target_registry = _mock_target_registry(targets_snap, generation=1)

    initial_profile = _profile()
    ai_registry = _mock_ai_registry_swapping(initial_profile, disabled_profile, generation=1)

    extractor = AsyncMock()
    extractor.extract = AsyncMock(return_value=_ok_extraction())

    feishu = AsyncMock()
    feishu.update_original_text = AsyncMock(return_value="rec_2026")
    feishu.update_record_field = AsyncMock(return_value="rec_2026")
    feishu.create_record = AsyncMock(return_value="rec-bill-new")

    settings = _dynamic_settings(targets_file, profile_file=profile_file)
    pipeline = AiPipeline(settings, extractor, feishu)

    async with lifespan(app):
        app.state.settings = settings
        app.state.target_registry = target_registry
        app.state.ai_registry = ai_registry
        app.state.ai_extractor = extractor
        app.state.ai_pipeline = pipeline
        app.state.feishu_client = feishu

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL,
        ) as client:
            # PUT profile — saves category disabled + reloads (swaps profile).
            r_put = await client.put(
                "/admin/config/profile",
                json={"profile": _profile_dict(disabled_keys=("category",)), "base_generation": 1},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )
            assert r_put.status_code == 200, r_put.text

            # POST webhook — the real path; AI pipeline runs against the
            # post-save (category-disabled) profile.
            r_hook = await client.post(
                "/v1/webhook/ocr",
                json={"original_text": "麦当劳 ¥42 微信支付", "book_alias": "2026"},
                headers={"X-Webhook-Token": WEBHOOK_TOKEN},
            )
            assert r_hook.status_code == 200, r_hook.text
            assert r_hook.json()["ai_status"] == "succeeded"

    # END-TO-END PROOF: the disabled `分类` field was skipped; an enabled
    # bill field (`描述`) was still written — so this is a real skip, not a
    # blanket skip that happens to also drop `分类`.
    feishu.create_record.assert_awaited_once()
    bill_fields = feishu.create_record.await_args.args[0]
    assert "分类" not in bill_fields, f"disabled field leaked into bill_fields: {bill_fields}"
    assert "描述" in bill_fields, f"enabled field missing (false-positive risk): {bill_fields}"


# ═════════════════════════════════════════════════════════════════════════════
# Scenario 2: PUT enabled=false → GET echoes enabled=false (roundtrip)
# ═════════════════════════════════════════════════════════════════════════════


async def test_disabled_field_get_roundtrip(monkeypatch, targets_file, profile_file):
    """
    GIVEN a dynamic-mode service (AI enabled) with a baseline all-enabled profile
       AND validate_profile_candidate returns a profile whose `category` field
       has enabled=false
    WHEN the operator PUTs /admin/config/profile (saving category disabled)
       AND THEN GETs /admin/config/profile
    THEN the GET response's `category` field row has enabled == false
       AND every other field row has enabled == true (only the one toggled)
    """
    disabled_profile = _profile(disabled_keys=("category",))

    monkeypatch.setattr(
        "app.main.validate_profile_candidate",
        AsyncMock(return_value=(disabled_profile, _WHITELISTS, [])),
    )

    t2026 = FeishuTargetConfig("2026", 2026, "bascn_2026", "tbl_2026", "rec_2026", "原始信息", True)
    targets_snap = _targets_snapshot([t2026], default_alias="2026", generation=1)
    target_registry = _mock_target_registry(targets_snap, generation=1)

    initial_profile = _profile()
    ai_registry = _mock_ai_registry_swapping(initial_profile, disabled_profile, generation=1)

    settings = _dynamic_settings(targets_file, profile_file=profile_file)

    async with lifespan(app):
        app.state.settings = settings
        app.state.target_registry = target_registry
        app.state.ai_registry = ai_registry
        app.state.feishu_client = AsyncMock()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL,
        ) as client:
            r_put = await client.put(
                "/admin/config/profile",
                json={"profile": _profile_dict(disabled_keys=("category",)), "base_generation": 1},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )
            assert r_put.status_code == 200, r_put.text

            r_get = await client.get(
                "/admin/config/profile", headers={"X-Admin-Token": ADMIN_TOKEN},
            )
            assert r_get.status_code == 200, r_get.text

    body = r_get.json()
    fields = {f["ai_key"]: f for f in body["fields"]}
    assert fields["category"]["enabled"] is False
    # Every other field stays enabled (only the one toggled).
    for key in ("summary", "description", "flow_type", "amount",
                "payment_method", "bill_date", "raw_source"):
        assert fields[key]["enabled"] is True, f"{key} should still be enabled"


# ═════════════════════════════════════════════════════════════════════════════
# Scenario 3: re-enable restores the write (disable → skip → enable → write)
# ═════════════════════════════════════════════════════════════════════════════


async def test_reenable_restores_write(monkeypatch, targets_file, profile_file):
    """
    GIVEN a dynamic-mode service (AI enabled)
       AND validate_profile_candidate returns a category-disabled profile on
       the first PUT, then a category-enabled profile on the second PUT
       AND a REAL AiPipeline wired with a mock extractor + spy feishu_client
    WHEN the operator:
       1. PUTs profile with category enabled=false → reloads
       2. POSTs /v1/webhook/ocr → create_record.fields has NO `分类`
       3. PUTs profile with category enabled=true → reloads
       4. POSTs /v1/webhook/ocr again → create_record.fields HAS `分类`
    THEN the second webhook's bill_fields contains `分类` — re-enabling the
       field restores the write path (toggle is reversible, no stuck state).
    """
    disabled_profile = _profile(disabled_keys=("category",))
    enabled_profile = _profile()  # all enabled

    # Two-step swap: first PUT → disabled profile, second PUT → enabled profile.
    call_count = {"n": 0}

    async def _validate(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return (disabled_profile, _WHITELISTS, [])
        return (enabled_profile, _WHITELISTS, [])

    monkeypatch.setattr("app.main.validate_profile_candidate", AsyncMock(side_effect=_validate))

    t2026 = FeishuTargetConfig("2026", 2026, "bascn_2026", "tbl_2026", "rec_2026", "原始信息", True)
    targets_snap = _targets_snapshot([t2026], default_alias="2026", generation=1)
    target_registry = _mock_target_registry(targets_snap, generation=1)

    # Three-phase swapping: initial → disabled (after PUT 1) → enabled (after PUT 2).
    # The webhook calls run between PUTs, so get_snapshot must reflect the
    # latest reload's swapped profile.
    phase = {"profile": enabled_profile, "generation": 1}

    def _get_status() -> dict:
        return {
            "generation": phase["generation"],
            "config_valid": True,
            "last_reload_error": None,
            "field_count": len(_PROFILE_FIELDS),
            "source_path": "/tmp/profile.toml",
        }

    def _reload(*, force: bool) -> dict:
        phase["generation"] += 1
        # The profile swapped to is whatever validate_profile_candidate
        # returned in the corresponding PUT — but the mock registry can't see
        # that. Instead, we drive the swap via the call_count: PUT 1 → disabled,
        # PUT 2 → enabled. This mirrors the real registry re-reading the file
        # that the PUT just wrote.
        if phase["generation"] == 2:  # after PUT 1
            phase["profile"] = disabled_profile
        elif phase["generation"] == 3:  # after PUT 2
            phase["profile"] = enabled_profile
        return _get_status()

    ai_registry = AsyncMock()
    ai_registry.maybe_reload = AsyncMock(return_value=None)
    ai_registry.get_snapshot = MagicMock(
        side_effect=lambda: _stub_snapshot(phase["profile"], phase["generation"])
    )
    ai_registry.get_status = MagicMock(side_effect=_get_status)
    ai_registry.reload = AsyncMock(side_effect=_reload)

    extractor = AsyncMock()
    extractor.extract = AsyncMock(return_value=_ok_extraction())

    feishu = AsyncMock()
    feishu.update_original_text = AsyncMock(return_value="rec_2026")
    feishu.update_record_field = AsyncMock(return_value="rec_2026")
    feishu.create_record = AsyncMock(return_value="rec-bill-new")

    settings = _dynamic_settings(targets_file, profile_file=profile_file)
    pipeline = AiPipeline(settings, extractor, feishu)

    async with lifespan(app):
        app.state.settings = settings
        app.state.target_registry = target_registry
        app.state.ai_registry = ai_registry
        app.state.ai_extractor = extractor
        app.state.ai_pipeline = pipeline
        app.state.feishu_client = feishu

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL,
        ) as client:
            # 1. PUT profile — disable category.
            r1 = await client.put(
                "/admin/config/profile",
                json={"profile": _profile_dict(disabled_keys=("category",)), "base_generation": 1},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )
            assert r1.status_code == 200, r1.text

            # 2. POST webhook — category disabled → no `分类` in bill_fields.
            r2 = await client.post(
                "/v1/webhook/ocr",
                json={"original_text": "麦当劳 ¥42 微信支付", "book_alias": "2026"},
                headers={"X-Webhook-Token": WEBHOOK_TOKEN},
            )
            assert r2.status_code == 200, r2.text
            assert r2.json()["ai_status"] == "succeeded"

            # 3. PUT profile — re-enable category (base_generation=2 after PUT 1).
            r3 = await client.put(
                "/admin/config/profile",
                json={"profile": _profile_dict(), "base_generation": 2},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )
            assert r3.status_code == 200, r3.text

            # 4. POST webhook — category re-enabled → `分类` back in bill_fields.
            # Different original_text than step 2 to avoid the in-memory AI
            # dedup (sha256(alias:text), TTL 300s) — otherwise the second
            # webhook short-circuits as `duplicate` before reaching encode.
            r4 = await client.post(
                "/v1/webhook/ocr",
                json={"original_text": "星巴克 ¥35 支付宝", "book_alias": "2026"},
                headers={"X-Webhook-Token": WEBHOOK_TOKEN},
            )
            assert r4.status_code == 200, r4.text
            assert r4.json()["ai_status"] == "succeeded"

    # create_record called twice (once per webhook). The second call's
    # bill_fields must contain `分类` — the re-enable restored the write.
    assert feishu.create_record.await_count == 2
    second_bill_fields = feishu.create_record.await_args_list[1].args[0]
    assert "分类" in second_bill_fields, (
        f"re-enable did not restore write: {second_bill_fields}"
    )
    # And the first call (disabled phase) had no `分类` — confirms the skip
    # happened, so the restore is a real change not a constant.
    first_bill_fields = feishu.create_record.await_args_list[0].args[0]
    assert "分类" not in first_bill_fields


# ═════════════════════════════════════════════════════════════════════════════
# Scenario 4: summary disabled — writeback skipped, bill record still created
# ═════════════════════════════════════════════════════════════════════════════


async def test_summary_disabled_still_writes_bill(monkeypatch, targets_file, profile_file):
    """
    GIVEN a dynamic-mode service (AI enabled) with a profile whose `summary`
       field (target=extract) has enabled=false — the UI hard-locks this spec
       open, but the backend must respect the flag defensively
       AND a REAL AiPipeline wired with a mock extractor + spy feishu_client
    WHEN the operator PUTs /admin/config/profile (saving summary disabled)
       AND THEN POSTs /v1/webhook/ocr
    THEN feishu.update_record_field is NOT called (the summary writeback was
       skipped because summary_spec.enabled is False)
       AND feishu.create_record IS still called (bill record creation is
       independent of the summary writeback — raw_source passthrough + the
       other bill fields still flow through)
       AND the webhook response's ai_status == "succeeded" (the pipeline did
       not fail just because the summary writeback was disabled)
    """
    disabled_summary_profile = _profile(disabled_keys=("summary",))

    monkeypatch.setattr(
        "app.main.validate_profile_candidate",
        AsyncMock(return_value=(disabled_summary_profile, _WHITELISTS, [])),
    )

    t2026 = FeishuTargetConfig("2026", 2026, "bascn_2026", "tbl_2026", "rec_2026", "原始信息", True)
    targets_snap = _targets_snapshot([t2026], default_alias="2026", generation=1)
    target_registry = _mock_target_registry(targets_snap, generation=1)

    initial_profile = _profile()
    ai_registry = _mock_ai_registry_swapping(initial_profile, disabled_summary_profile, generation=1)

    extractor = AsyncMock()
    extractor.extract = AsyncMock(return_value=_ok_extraction())

    feishu = AsyncMock()
    feishu.update_original_text = AsyncMock(return_value="rec_2026")
    feishu.update_record_field = AsyncMock(return_value="rec_2026")
    feishu.create_record = AsyncMock(return_value="rec-bill-new")

    settings = _dynamic_settings(targets_file, profile_file=profile_file)
    pipeline = AiPipeline(settings, extractor, feishu)

    async with lifespan(app):
        app.state.settings = settings
        app.state.target_registry = target_registry
        app.state.ai_registry = ai_registry
        app.state.ai_extractor = extractor
        app.state.ai_pipeline = pipeline
        app.state.feishu_client = feishu

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL,
        ) as client:
            r_put = await client.put(
                "/admin/config/profile",
                json={"profile": _profile_dict(disabled_keys=("summary",)), "base_generation": 1},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )
            assert r_put.status_code == 200, r_put.text

            r_hook = await client.post(
                "/v1/webhook/ocr",
                json={"original_text": "麦当劳 ¥42 微信支付", "book_alias": "2026"},
                headers={"X-Webhook-Token": WEBHOOK_TOKEN},
            )
            assert r_hook.status_code == 200, r_hook.text
            assert r_hook.json()["ai_status"] == "succeeded"

    # update_record_field was NOT called for the summary writeback — the
    # disabled summary spec short-circuits the writeback branch in _run_impl.
    # (update_original_text IS called once, but that's the webhook's own
    # 原始信息 write, not the AI summary writeback.)
    feishu.update_record_field.assert_not_awaited()
    # create_record WAS called — bill record creation is independent of the
    # summary writeback; raw_source passthrough + the other bill fields still
    # produce a non-empty bill_fields dict.
    feishu.create_record.assert_awaited_once()
