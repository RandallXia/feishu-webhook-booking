# test_config_ui_integration.py — Cross-module ASGI integration tests for the
# config UI feature (Todo 12 of the frontend-config-ui plan).
#
# Five end-to-end scenarios that exercise the full ASGI stack (mock Feishu +
# mock AI) and prove the config UI's contracts hold across modules:
#
#   1. Full config roundtrip: GET → PUT targets → PUT profile → dry-run → webhook
#      proves the new summary_field flows end-to-end into update_record_field.
#   2. Read-only filesystem: both PUTs 409 RUNTIME_READONLY; GETs return OLD values.
#   3. XSS preview safety: a record preview containing <script> stays plain text.
#   4. Generation race: two concurrent-style PUTs on the same base_generation →
#      the second is rejected 409 STALE_WRITE (optimistic-concurrency guard).
#   5. Env save→get roundtrip: PUT env then GET env; secret never leaks.
#
# Mock strategy mirrors test_config_profile_api.py / test_config_targets_api.py /
# test_config_env_api.py: stub app.state.feishu_client / ai_registry /
# target_registry + settings replace (frozen → dataclasses.replace) + tmp files.
# Scenario 1 uses a REAL AiPipeline (mock extractor + spy feishu) so run() logic
# executes and update_record_field is called with profile.summary_field — the
# end-to-end proof that the saved config took effect.

from __future__ import annotations

import errno
import json
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


# ─── Shared fixtures + helpers ──────────────────────────────────────────────


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


def _profile(summary_field: str = "精简原始数据") -> AiProfile:
    # The summary passthrough FieldSpec's feishu_field must equal summary_field
    # so encode_fields populates extract_fields[summary_field] — the pipeline
    # then looks up extract_fields.get(profile.summary_field) for writeback.
    fields = tuple(
        replace(s, feishu_field=summary_field) if s.ai_key == "summary" else s
        for s in _PROFILE_FIELDS
    )
    return AiProfile(
        prompt_header="你是一个账单提取助手",
        summary_field=summary_field,
        bill_app_token="bascn-bill",
        bill_table_id="tbl-bill",
        fields=fields,
    )


def _profile_dict(summary_field: str = "精简原始数据") -> dict:
    """Editable profile shape (mirrors GET response + PUT body.profile)."""
    profile = _profile(summary_field)
    return {
        "prompt_header": profile.prompt_header,
        "summary_field": profile.summary_field,
        "bill": {"app_token": "bascn-bill", "table_id": "tbl-bill"},
        "fields": [
            {
                "ai_key": s.ai_key, "feishu_field": s.feishu_field,
                "type": s.type, "target": s.target,
                "fallback": s.fallback, "prompt": s.prompt, "source": s.source,
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


def _mock_ai_registry(profile: AiProfile, generation: int = 1) -> AsyncMock:
    """Healthy AI registry mock whose reload(force=True) bumps generation.

    Mirrors _mock_registry_healthy in test_config_profile_api.py so PUT profile
    can validate base_generation then reload → generation+1. The profile passed
    here is what get_snapshot() returns BOTH before and after reload — callers
    that need the snapshot to reflect a post-save profile swap must pass the
    new profile (the real registry re-reads the file on reload; the mock just
    serves whatever profile it was handed).
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
    registry.get_snapshot = MagicMock(return_value=_stub_snapshot(state["profile"], state["generation"]))
    registry.get_status = MagicMock(side_effect=_get_status)
    registry.reload = AsyncMock(side_effect=_reload)
    return registry


def _mock_ai_registry_swapping(initial: AiProfile, reloaded: AiProfile, generation: int = 1) -> AsyncMock:
    """Like _mock_ai_registry but reload(force=True) swaps the served profile.

    Use for roundtrip tests where the webhook path must see the NEW profile
    after a PUT profile + reload — mirrors the real registry re-reading the
    file on reload.
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
    """Healthy target registry mock whose reload(force=True) bumps generation."""
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
    # resolve() must return a real target for the webhook path.
    def _resolve(book_alias=None, year=None):
        snap = state["snapshot"]
        if book_alias and book_alias in snap.targets_by_alias:
            return snap.targets_by_alias[book_alias]
        if year is not None and year in snap.aliases_by_year:
            return snap.targets_by_alias[snap.aliases_by_year[year]]
        return snap.targets_by_alias[snap.default_alias]
    registry.resolve = MagicMock(side_effect=_resolve)
    return registry


def _dynamic_settings(targets_file, profile_file=None, env_file=None) -> object:
    kwargs = dict(
        config_reload_token=ADMIN_TOKEN,
        feishu_targets_file=targets_file,
    )
    if profile_file is not None:
        kwargs["ai_enabled"] = True
        kwargs["ai_profile_file"] = profile_file
    if env_file is not None:
        kwargs["env_file_path"] = env_file
    return replace(get_settings(), **kwargs)


def _target_dict(alias, *, year, app_token=None, table_id=None, record_id=None,
                 original_field_name="原始信息", enabled=True) -> dict:
    return {
        "alias": alias, "year": year,
        "app_token": app_token or f"bascn_{alias}",
        "table_id": table_id or f"tbl_{alias}",
        "record_id": record_id or f"rec_{alias}",
        "original_field_name": original_field_name, "enabled": enabled,
    }


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
    # Drop the empty year= line for yearless aliases (dump_targets omits None).
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
    path.write_text(_targets_toml([("2026", 2026), ("2025", 2025)]), encoding="utf-8")
    return path


@pytest.fixture
def profile_file(tmp_path: Path) -> Path:
    path = tmp_path / "ai-profile.toml"
    path.write_text(_profile_toml(), encoding="utf-8")
    return path


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    path = tmp_path / "feishu-webhook.env"
    path.write_text(
        "# Feishu webhook service env\n"
        "WEBHOOK_SHARED_TOKEN=hook-secret\n"
        "FEISHU_APP_ID=cli_test\n"
        "FEISHU_APP_SECRET=fs-secret\n"
        "AI_ENABLED=true\n"
        "AI_PROVIDER=anthropic\n"
        "AI_API_KEY=sk-old-key\n"
        "AI_MODEL=claude-3-5-sonnet\n",
        encoding="utf-8",
    )
    return path


# ═════════════════════════════════════════════════════════════════════════════
# Scenario 1: Full config roundtrip — saved config flows into the webhook path
# ═════════════════════════════════════════════════════════════════════════════


async def test_full_config_roundtrip_webhook_uses_new_config(
    monkeypatch, targets_file, profile_file,
):
    """
    GIVEN a dynamic-mode service (AI enabled) with a 2-alias targets file + a
       profile file whose summary_field="精简原始数据"
       AND validate_profile_candidate returns the NEW profile (summary_field
       changed to "AI摘要") so PUT persists + reloads
       AND a REAL AiPipeline wired with a mock extractor + a spy feishu_client
    WHEN the operator runs the full config roundtrip:
       1. GET /admin/config/targets         → 200, 2 targets
       2. PUT  /admin/config/targets         → 200 (add alias "2027")
       3. GET  /admin/config/profile         → 200, summary_field="精简原始数据"
       4. PUT  /admin/config/profile         → 200 (summary_field → "AI摘要")
       5. POST /admin/ai/test                → 200 ai_status=succeeded (dry-run)
       6. POST /v1/webhook/ocr               → 200, ai_status=succeeded
    THEN the webhook's AI pipeline calls feishu.update_record_field with the
       NEW summary_field ("AI摘要") — proving the saved config took effect
       end-to-end (PUT → file → reload → snapshot → pipeline.run).
    """
    from app import main as main_mod

    new_profile = _profile(summary_field="AI摘要")

    # validate_profile_candidate returns the new profile + no errors so PUT
    # proceeds to atomic save + reload.
    monkeypatch.setattr(
        main_mod, "validate_profile_candidate",
        AsyncMock(return_value=(new_profile, _WHITELISTS, [])),
    )

    # Initial registries reflect the PRE-save state (2 targets, old profile).
    t2026 = FeishuTargetConfig("2026", 2026, "bascn_2026", "tbl_2026", "rec_2026", "原始信息", True)
    t2025 = FeishuTargetConfig("2025", 2025, "bascn_2025", "tbl_2025", "rec_2025", "原始信息", True)
    targets_snap = _targets_snapshot([t2025, t2026], default_alias="2026", generation=1)
    target_registry = _mock_target_registry(targets_snap, generation=1)

    old_profile = _profile(summary_field="精简原始数据")
    # Swapping registry: reload(force=True) swaps the served profile to the new
    # one, mirroring the real registry re-reading the file — so the webhook
    # path (step 6) sees the new summary_field after the PUT (step 4) reloaded.
    ai_registry = _mock_ai_registry_swapping(old_profile, new_profile, generation=1)

    # Mock extractor returns a valid extraction; feishu is a SPY so we can
    # assert update_record_field was called with the new summary_field.
    extractor = AsyncMock()
    extractor.extract = AsyncMock(return_value=_ok_extraction())

    feishu = AsyncMock()
    feishu.update_original_text = AsyncMock(return_value="rec_2026")
    feishu.update_record_field = AsyncMock(return_value="rec_2026")
    feishu.create_record = AsyncMock(return_value="rec-bill-new")

    # Wire a REAL AiPipeline so run() logic executes (extract → encode →
    # writeback → create) and update_record_field is actually invoked.
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
            # 1. GET targets — baseline 2 aliases.
            r1 = await client.get(
                "/admin/config/targets", headers={"X-Admin-Token": ADMIN_TOKEN},
            )
            assert r1.status_code == 200, r1.text
            assert len(r1.json()["targets"]) == 2

            # 2. PUT targets — add alias "2027" (full-replace).
            new_targets = [
                _target_dict("2026", year=2026),
                _target_dict("2025", year=2025),
                _target_dict("2027", year=2027),
            ]
            r2 = await client.put(
                "/admin/config/targets",
                json={"default_alias": "2026", "targets": new_targets, "base_generation": 1},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )
            assert r2.status_code == 200, r2.text
            assert r2.json()["success"] is True

            # 3. GET profile — baseline summary_field (returned at top-level).
            r3 = await client.get(
                "/admin/config/profile", headers={"X-Admin-Token": ADMIN_TOKEN},
            )
            assert r3.status_code == 200, r3.text
            assert r3.json()["summary_field"] == "精简原始数据"

            # 4. PUT profile — change summary_field → "AI摘要".
            r4 = await client.put(
                "/admin/config/profile",
                json={"profile": _profile_dict(summary_field="AI摘要"), "base_generation": 1},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )
            assert r4.status_code == 200, r4.text
            assert r4.json()["success"] is True

            # 5. POST /admin/ai/test — dry-run with the (reloaded) new profile.
            r5 = await client.post(
                "/admin/ai/test",
                json={"text": "麦当劳 ¥42 微信支付"},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )
            assert r5.status_code == 200, r5.text
            assert r5.json()["ai_status"] == "succeeded"

            # 6. POST /v1/webhook/ocr — the real path; AI pipeline runs.
            r6 = await client.post(
                "/v1/webhook/ocr",
                json={"original_text": "麦当劳 ¥42 微信支付", "book_alias": "2026"},
                headers={"X-Webhook-Token": WEBHOOK_TOKEN},
            )
            assert r6.status_code == 200, r6.text
            assert r6.json()["ai_status"] == "succeeded"

    # END-TO-END PROOF: the AI pipeline's summary writeback used the NEW
    # summary_field saved in step 4 — the config UI's PUT actually took effect
    # for the live webhook path.
    feishu.update_record_field.assert_awaited()
    call_args = feishu.update_record_field.await_args
    # update_record_field(profile.summary_field, value, target)
    assert call_args.args[0] == "AI摘要"


# ═════════════════════════════════════════════════════════════════════════════
# Scenario 2: Read-only filesystem — both PUTs 409; GETs return OLD values
# ═════════════════════════════════════════════════════════════════════════════


async def test_readonly_full_path(monkeypatch, targets_file, profile_file):
    """
    GIVEN os.replace raises PermissionError (simulating a Docker :ro mount)
       AND a dynamic-mode service with a 2-alias targets file + a profile file
       AND validate_profile_candidate returns a valid new profile (so PUT
       reaches the os.replace step)
    WHEN the operator attempts:
       1. PUT /admin/config/targets  → 409 RUNTIME_READONLY
       2. PUT /admin/config/profile  → 409 RUNTIME_READONLY
       3. GET /admin/config/targets  → 200, targets UNCHANGED (old 2 aliases)
       4. GET /admin/config/profile  → 200, summary_field UNCHANGED
    THEN neither file was modified (the tmp+replace swap is atomic: a failed
       replace leaves the original file byte-identical).
    """
    from app import main as main_mod

    new_profile = _profile(summary_field="AI摘要")
    monkeypatch.setattr(
        main_mod, "validate_profile_candidate",
        AsyncMock(return_value=(new_profile, _WHITELISTS, [])),
    )

    def _replace_raises(src, dst):
        raise PermissionError(errno.EROFS, "Read-only file system", str(dst))

    monkeypatch.setattr(main_mod.os, "replace", _replace_raises)

    original_targets = targets_file.read_text(encoding="utf-8")
    original_profile = profile_file.read_text(encoding="utf-8")

    t2026 = FeishuTargetConfig("2026", 2026, "bascn_2026", "tbl_2026", "rec_2026", "原始信息", True)
    t2025 = FeishuTargetConfig("2025", 2025, "bascn_2025", "tbl_2025", "rec_2025", "原始信息", True)
    targets_snap = _targets_snapshot([t2025, t2026], default_alias="2026", generation=1)
    target_registry = _mock_target_registry(targets_snap, generation=1)
    ai_registry = _mock_ai_registry(_profile(), generation=1)

    settings = _dynamic_settings(targets_file, profile_file=profile_file)

    async with lifespan(app):
        app.state.settings = settings
        app.state.target_registry = target_registry
        app.state.ai_registry = ai_registry
        app.state.feishu_client = AsyncMock()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL,
        ) as client:
            # 1. PUT targets → 409 RUNTIME_READONLY
            r1 = await client.put(
                "/admin/config/targets",
                json={
                    "default_alias": "2026",
                    "targets": [
                        _target_dict("2026", year=2026),
                        _target_dict("2027", year=2027),
                    ],
                    "base_generation": 1,
                },
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )
            assert r1.status_code == 409
            assert r1.json()["detail"]["error"]["code"] == "RUNTIME_READONLY"

            # 2. PUT profile → 409 RUNTIME_READONLY
            r2 = await client.put(
                "/admin/config/profile",
                json={"profile": _profile_dict(summary_field="AI摘要"), "base_generation": 1},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )
            assert r2.status_code == 409
            assert r2.json()["detail"]["error"]["code"] == "RUNTIME_READONLY"

            # 3. GET targets → OLD values (2 aliases, not the attempted 2-with-2027).
            r3 = await client.get(
                "/admin/config/targets", headers={"X-Admin-Token": ADMIN_TOKEN},
            )
            assert r3.status_code == 200
            aliases = {t["alias"] for t in r3.json()["targets"]}
            assert aliases == {"2025", "2026"}

            # 4. GET profile → OLD summary_field (returned at top-level).
            r4 = await client.get(
                "/admin/config/profile", headers={"X-Admin-Token": ADMIN_TOKEN},
            )
            assert r4.status_code == 200
            assert r4.json()["summary_field"] == "精简原始数据"

    # Files byte-identical to the originals (atomic swap never happened).
    assert targets_file.read_text(encoding="utf-8") == original_targets
    assert profile_file.read_text(encoding="utf-8") == original_profile


# ═════════════════════════════════════════════════════════════════════════════
# Scenario 3: XSS preview — <script> in a record preview stays plain text
# ═════════════════════════════════════════════════════════════════════════════


async def test_xss_preview_is_plain_text():
    """
    GIVEN list_fields returns a fields_map where "账单名" is_primary=True
       AND list_records returns a record whose 账单名 field value is
       "<script>alert(1)</script>" (a script tag as a plain string value)
    WHEN GET /admin/feishu/records is called
    THEN the response is parseable JSON (no structural injection)
       AND body.items[0].preview equals the original "<script>alert(1)</script>"
       string verbatim (JSON serialization escapes it as needed; the value is
       a data string, not executed markup).
    """
    feishu = AsyncMock()
    feishu.list_fields = AsyncMock(return_value={
        "账单名": {"field_id": "fld1", "field_name": "账单名", "is_primary": True},
    })
    payload = "<script>alert(1)</script>"
    feishu.list_records = AsyncMock(return_value={
        "items": [{"record_id": "rec1", "fields": {"账单名": payload}}],
        "has_more": False,
        "page_token": None,
    })

    settings = replace(get_settings(), config_reload_token=ADMIN_TOKEN)

    async with lifespan(app):
        app.state.settings = settings
        app.state.feishu_client = feishu

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL,
        ) as client:
            response = await client.get(
                "/admin/feishu/records?app_token=appA&table_id=tbl1",
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    # Response is valid JSON (no structural injection broke parsing).
    body = json.loads(response.text)
    assert body["items"][0]["record_id"] == "rec1"
    # The preview field carries the script tag as a plain-text string value.
    assert body["items"][0]["preview"] == payload


# ═════════════════════════════════════════════════════════════════════════════
# Scenario 4: Generation race — two PUTs on the same base_generation
# ═════════════════════════════════════════════════════════════════════════════


async def test_generation_race_two_puts(monkeypatch, profile_file):
    """
    GIVEN ai_registry.get_status()["generation"] == 1
       AND validate_profile_candidate returns a valid new profile
    WHEN two PUT /admin/config/profile requests are issued with the SAME
       base_generation=1 (simulating a concurrent edit where the first PUT
       bumped the generation to 2 before the second arrived)
       AND the mock registry's reload bumps generation on the first PUT
    THEN the first PUT returns 200 success
       AND the second PUT returns 409 STALE_WRITE (the optimistic-concurrency
       guard rejects the stale base_generation)
    """
    from app import main as main_mod

    new_profile = _profile(summary_field="AI摘要")
    monkeypatch.setattr(
        main_mod, "validate_profile_candidate",
        AsyncMock(return_value=(new_profile, _WHITELISTS, [])),
    )

    settings = _dynamic_settings(targets_file=Path("/tmp/x.toml"), profile_file=profile_file)
    # generation=1; reload(force=True) bumps to 2 — so the second PUT (still
    # carrying base_generation=1) sees generation=2 → STALE_WRITE.
    ai_registry = _mock_ai_registry(_profile(), generation=1)

    async with lifespan(app):
        app.state.settings = settings
        app.state.ai_registry = ai_registry
        app.state.feishu_client = AsyncMock()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL,
        ) as client:
            # First PUT — succeeds, reload bumps generation 1 → 2.
            r1 = await client.put(
                "/admin/config/profile",
                json={"profile": _profile_dict(summary_field="AI摘要"), "base_generation": 1},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )
            assert r1.status_code == 200, r1.text
            assert r1.json()["success"] is True

            # Second PUT — same base_generation=1, but registry is now at 2.
            r2 = await client.put(
                "/admin/config/profile",
                json={"profile": _profile_dict(summary_field="AI摘要"), "base_generation": 1},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )
            assert r2.status_code == 409
            assert r2.json()["detail"]["error"]["code"] == "STALE_WRITE"


# ═════════════════════════════════════════════════════════════════════════════
# Scenario 5: Env save→get roundtrip — model changes, secret never leaks
# ═════════════════════════════════════════════════════════════════════════════


async def test_env_save_then_get_roundtrip(env_file):
    """
    GIVEN an env file with AI_MODEL=claude-3-5-sonnet + AI_API_KEY=sk-old-key
       AND ai_enabled=True + env_file_path → the tmp file
    WHEN the operator:
       1. PUT /admin/config/env with ai_model="gpt-4o" + api_key="sk-super-secret"
       2. GET /admin/config/env
    THEN the PUT returns 200 success=True (file written atomically)
       AND the env file now contains AI_MODEL=gpt-4o + AI_API_KEY=sk-super-secret
       AND the GET returns 200 with ai.ai_api_key_set=True
       AND the GET response text does NOT contain the secret value "sk-super-secret"

    Note: GET reads the in-memory frozen Settings (NOT the file), so the
    running ai_model stays "claude-3-5-sonnet" until a restart — this is by
    design (env is loaded once at startup). The roundtrip proof here is that
    the PUT persisted the new values to the file and the GET never leaks the
    secret, regardless of the in-memory/restart gap.
    """
    settings = replace(
        get_settings(),
        config_reload_token=ADMIN_TOKEN,
        ai_enabled=True,
        ai_provider="anthropic",
        ai_api_key="sk-old-key",
        ai_model="claude-3-5-sonnet",
        ai_base_url=None,
        ai_timeout_seconds=20,
        env_file_path=env_file,
    )

    async with lifespan(app):
        app.state.settings = settings

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL,
        ) as client:
            # 1. PUT env — change model + set a new api_key.
            r1 = await client.put(
                "/admin/config/env",
                json={
                    "ai": {
                        "ai_enabled": True,
                        "ai_provider": "anthropic",
                        "ai_base_url": None,
                        "ai_model": "gpt-4o",
                        "ai_timeout_seconds": 20,
                    },
                    "api_key": "sk-super-secret",
                },
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )
            assert r1.status_code == 200, r1.text
            assert r1.json()["success"] is True

            # 2. GET env — api_key_set stays True; secret never leaks.
            r2 = await client.get(
                "/admin/config/env", headers={"X-Admin-Token": ADMIN_TOKEN},
            )
            assert r2.status_code == 200
            body = r2.json()
            assert body["ai"]["ai_api_key_set"] is True
            # CRITICAL: the secret value never appears in the response body.
            assert "sk-super-secret" not in r2.text

    # The file was persisted with the new values (proves the PUT wrote correctly;
    # a restart would load these into Settings).
    written = env_file.read_text(encoding="utf-8")
    assert "AI_MODEL=gpt-4o" in written.splitlines()
    assert "AI_API_KEY=sk-super-secret" in written.splitlines()
