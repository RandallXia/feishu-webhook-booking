# test_config_targets_api.py — Behavior tests for GET/PUT /admin/config/targets.
#
# Two routes (mirror reload_config auth + _require_admin_auth):
#   - GET /admin/config/targets  : read registry snapshot (degrades 200 on fail-closed)
#   - PUT /admin/config/targets  : validate → atomic save (tmp + os.replace) → reload
#
# PUT is full-replace semantics: the body's targets list wholly replaces the file
# (add + modify + delete in one round-trip). Legacy mode (FEISHU_TARGETS_FILE unset)
# → GET returns the single legacy target; PUT → 409 LEGACY_MODE.
#
# Strategy mirrors test_config_profile_api.py:
#   - conftest pins legacy mode (FEISHU_TARGETS_FILE="") so the default registry
#     is legacy; dynamic-mode tests swap app.state.settings + rebuild the registry.
#   - httpx.AsyncClient(transport=ASGITransport(app), base_url="http://testserver")
#     with the lifespan async context manager run directly.

from __future__ import annotations

import errno
import tomllib
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from httpx import ASGITransport

from app.config import get_settings
from app.main import app, lifespan
from app.target_registry import (
    FeishuTargetConfig,
    TargetRegistry,
    TargetRegistryConfigError,
    TargetRegistrySnapshot,
)


ADMIN_TOKEN = "test-admin-token"
TEST_BASE_URL = "http://testserver"


# --- Shared helpers ---


def _targets_toml(alias_a: str = "2026", alias_b: str | None = "2025") -> str:
    """A 2-alias targets TOML for seeding a tmp file."""
    lines = [f'default_alias = "{alias_a}"', ""]
    if alias_b is not None:
        lines += [
            f'[targets."{alias_b}"]',
            f"year = {alias_b}",
            f'app_token = "bascn_{alias_b}"',
            f'table_id = "tbl_{alias_b}"',
            f'record_id = "rec_{alias_b}"',
            'original_field_name = "原始信息"',
            "enabled = true",
            "",
        ]
    lines += [
        f'[targets."{alias_a}"]',
        f"year = {alias_a}",
        f'app_token = "bascn_{alias_a}"',
        f'table_id = "tbl_{alias_a}"',
        f'record_id = "rec_{alias_a}"',
        'original_field_name = "原始信息"',
        "enabled = true",
        "",
    ]
    return "\n".join(lines)


def _dynamic_settings(targets_file: Path | None) -> object:
    return replace(
        get_settings(),
        config_reload_token=ADMIN_TOKEN,
        feishu_targets_file=targets_file,
    )


def _legacy_settings() -> object:
    return replace(get_settings(), config_reload_token=ADMIN_TOKEN)


def _target_to_dict(t: FeishuTargetConfig) -> dict:
    return {
        "alias": t.alias,
        "year": t.year,
        "app_token": t.app_token,
        "table_id": t.table_id,
        "record_id": t.record_id,
        "original_field_name": t.original_field_name,
        "enabled": t.enabled,
    }


def _snapshot(targets: list[FeishuTargetConfig], default_alias: str, generation: int = 1) -> TargetRegistrySnapshot:
    by_alias = {t.alias: t for t in targets}
    by_year = {t.year: t.alias for t in targets if t.year is not None}
    return TargetRegistrySnapshot(
        mode="dynamic",
        default_alias=default_alias,
        targets_by_alias=by_alias,
        aliases_by_year=by_year,
        loaded_at=0.0,
        source_path="/tmp/feishu-targets.toml",
        source_mtime=0.0,
        generation=generation,
    )


def _mock_registry_healthy(
    snapshot: TargetRegistrySnapshot, *, generation: int = 1
) -> MagicMock:
    """Healthy registry mock with mutable generation (reload bumps it).

    describe() returns reload_generation (NOT generation — key-name asymmetry
    with the AI registry; mirrors the real TargetRegistry.describe()).
    """
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
    return registry


def _mock_registry_fail_closed() -> MagicMock:
    registry = MagicMock(spec=TargetRegistry)
    registry.describe = MagicMock(
        return_value={
            "mode": "dynamic",
            "default_alias": None,
            "target_count": 0,
            "reload_generation": 0,
            "config_valid": False,
            "last_reload_error": "Missing targets registry file: /tmp/feishu-targets.toml",
            "source_path": "/tmp/feishu-targets.toml",
        }
    )
    registry.get_snapshot = MagicMock(side_effect=Exception("unavailable"))
    registry.reload = MagicMock(side_effect=TargetRegistryConfigError("still bad"))
    registry.maybe_reload = MagicMock(return_value=None)
    return registry


# ─── GET /admin/config/targets ─────────────────────────────────────────────


async def test_get_targets_dynamic_happy(tmp_path):
    """
    GIVEN a dynamic-mode registry with 2 targets (alias 2025 + 2026, default=2026)
       AND generation=1
    WHEN GET /admin/config/targets is called with valid X-Admin-Token
    THEN the response status is 200
      AND body.mode == "dynamic"
      AND body.default_alias == "2026"
      AND body.targets has 2 entries each with all 7 keys
      AND body.generation == 1
      AND body.config_valid is True
    """
    t2026 = FeishuTargetConfig("2026", 2026, "bascn_2026", "tbl_2026", "rec_2026", "原始信息", True)
    t2025 = FeishuTargetConfig("2025", 2025, "bascn_2025", "tbl_2025", "rec_2025", "原始信息", True)
    snap = _snapshot([t2025, t2026], default_alias="2026", generation=1)

    async with lifespan(app):
        app.state.settings = _dynamic_settings(targets_file=tmp_path / "feishu-targets.toml")
        app.state.target_registry = _mock_registry_healthy(snap, generation=1)

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get(
                "/admin/config/targets",
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "dynamic"
    assert body["default_alias"] == "2026"
    assert body["generation"] == 1
    assert body["config_valid"] is True
    targets = body["targets"]
    assert len(targets) == 2
    for t in targets:
        assert set(t.keys()) == {
            "alias", "year", "app_token", "table_id", "record_id",
            "original_field_name", "enabled",
        }
    aliases = {t["alias"] for t in targets}
    assert aliases == {"2025", "2026"}


async def test_get_targets_legacy_mode_returns_200():
    """
    GIVEN legacy mode (FEISHU_TARGETS_FILE unset, conftest default)
       AND a single legacy target (alias="default", year=None)
    WHEN GET /admin/config/targets is called
    THEN the response status is 200
      AND body.mode == "legacy"
      AND body.targets has 1 entry with alias="default" + year=None
    """
    legacy_target = FeishuTargetConfig(
        "default", None, "test-app-token", "test-table-id", "test-record-id", "原始信息", True
    )
    snap = TargetRegistrySnapshot(
        mode="legacy",
        default_alias="default",
        targets_by_alias={"default": legacy_target},
        aliases_by_year={},
        loaded_at=0.0,
        source_path=None,
        source_mtime=None,
        generation=1,
    )
    registry = MagicMock(spec=TargetRegistry)
    registry.describe = MagicMock(
        return_value={
            "mode": "legacy",
            "default_alias": "default",
            "target_count": 1,
            "reload_generation": 1,
            "config_valid": True,
            "last_reload_error": None,
            "source_path": None,
        }
    )
    registry.get_snapshot = MagicMock(return_value=snap)

    async with lifespan(app):
        app.state.settings = _legacy_settings()
        app.state.target_registry = registry

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get(
                "/admin/config/targets",
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "legacy"
    assert body["default_alias"] == "default"
    assert body["targets"] is not None
    assert len(body["targets"]) == 1
    assert body["targets"][0]["alias"] == "default"
    assert body["targets"][0]["year"] is None


async def test_get_targets_fail_closed_degrades_200():
    """
    GIVEN a fail-closed registry (config_valid=false, get_snapshot raises)
       AND describe() returns last_reload_error diagnostics
    WHEN GET /admin/config/targets is called
    THEN the response status is 200 (NOT 503)
      AND body.targets is null
      AND body.config_valid is False
      AND body.last_reload_error is present
    """
    async with lifespan(app):
        app.state.settings = _dynamic_settings(targets_file=Path("/tmp/missing.toml"))
        app.state.target_registry = _mock_registry_fail_closed()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get(
                "/admin/config/targets",
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["targets"] is None
    assert body["config_valid"] is False
    assert "last_reload_error" in body
    assert body["last_reload_error"]


async def test_get_targets_no_token_returns_401():
    """
    GIVEN CONFIG_RELOAD_TOKEN is set
    WHEN GET /admin/config/targets is called with NO X-Admin-Token
    THEN the response status is 401 UNAUTHORIZED
    """
    async with lifespan(app):
        app.state.settings = _legacy_settings()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get("/admin/config/targets")

    assert response.status_code == 401
    assert response.json()["detail"]["error"]["code"] == "UNAUTHORIZED"


async def test_get_targets_reload_disabled_returns_404():
    """
    GIVEN CONFIG_RELOAD_TOKEN is unset
    WHEN GET /admin/config/targets is called
    THEN the response status is 404 RELOAD_DISABLED
    """
    async with lifespan(app):
        app.state.settings = replace(get_settings(), config_reload_token=None)

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get(
                "/admin/config/targets",
                headers={"X-Admin-Token": "anything"},
            )

    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "RELOAD_DISABLED"


# ─── PUT /admin/config/targets ─────────────────────────────────────────────


@pytest.fixture
def targets_file(tmp_path: Path) -> Path:
    """A tmp feishu-targets.toml seeded with 2 aliases (2025 + 2026)."""
    path = tmp_path / "feishu-targets.toml"
    path.write_text(_targets_toml(), encoding="utf-8")
    return path


def _put_body(targets: list[dict], default_alias: str, base_generation: int = 1) -> dict:
    return {
        "default_alias": default_alias,
        "targets": targets,
        "base_generation": base_generation,
    }


def _target_dict(
    alias: str, *, year: int | None, app_token: str | None = None,
    table_id: str | None = None, record_id: str | None = None,
    original_field_name: str = "原始信息", enabled: bool = True,
) -> dict:
    return {
        "alias": alias,
        "year": year,
        "app_token": app_token or f"bascn_{alias}",
        "table_id": table_id or f"tbl_{alias}",
        "record_id": record_id or f"rec_{alias}",
        "original_field_name": original_field_name,
        "enabled": enabled,
    }


async def test_put_targets_happy_full_replace_add_modify_delete(targets_file):
    """
    GIVEN a dynamic-mode registry (generation=1) + a 2-alias file (2025, 2026)
       AND a PUT body with 3 aliases: keep 2026 (modified app_token), drop 2025,
          add 2027 — full replacement
    WHEN PUT /admin/config/targets is called with base_generation=1
    THEN the response status is 200
      AND response.success is True + response.generation == 2 (reloaded)
      AND a .bak file was created with the ORIGINAL content
      AND the file now contains exactly the 3 new targets (dump_targets output)
      AND target_registry.reload(force=True) was called
    """
    from app.toml_writer import dump_targets

    original_content = targets_file.read_text(encoding="utf-8")

    # Snapshot reflects the OLD file (2 aliases) so GET would show 2; the PUT
    # replaces the file then reload bumps generation to 2. The mock's reload
    # does NOT re-read the file (it just bumps generation), so we keep the
    # snapshot as the pre-PUT state.
    t2026_old = FeishuTargetConfig("2026", 2026, "bascn_2026", "tbl_2026", "rec_2026", "原始信息", True)
    t2025_old = FeishuTargetConfig("2025", 2025, "bascn_2025", "tbl_2025", "rec_2025", "原始信息", True)
    snap = _snapshot([t2025_old, t2026_old], default_alias="2026", generation=1)
    registry = _mock_registry_healthy(snap, generation=1)

    new_targets = [
        _target_dict("2026", year=2026, app_token="bascn_MODIFIED"),
        _target_dict("2027", year=2027),
        _target_dict("archive", year=None, enabled=False),
    ]

    async with lifespan(app):
        app.state.settings = _dynamic_settings(targets_file=targets_file)
        app.state.target_registry = registry

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/targets",
                json=_put_body(new_targets, default_alias="2026", base_generation=1),
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["generation"] == 2

    # .bak created with the ORIGINAL content
    bak_path = targets_file.with_suffix(".toml.bak")
    assert bak_path.is_file()
    assert bak_path.read_text(encoding="utf-8") == original_content

    # File now contains exactly the 3 new targets — round-trip via tomllib
    written = targets_file.read_text(encoding="utf-8")
    parsed = tomllib.loads(written)
    assert parsed["default_alias"] == "2026"
    assert set(parsed["targets"].keys()) == {"2026", "2027", "archive"}
    assert parsed["targets"]["2026"]["app_token"] == "bascn_MODIFIED"
    assert parsed["targets"]["archive"]["enabled"] is False
    assert "year" not in parsed["targets"]["archive"]  # year=None omitted

    # And the written text matches dump_targets output for the same input
    expected_dump_input = {
        "default_alias": "2026",
        "targets": {
            "2026": {
                "year": 2026, "app_token": "bascn_MODIFIED",
                "table_id": "tbl_2026", "record_id": "rec_2026",
                "original_field_name": "原始信息", "enabled": True,
            },
            "2027": {
                "year": 2027, "app_token": "bascn_2027",
                "table_id": "tbl_2027", "record_id": "rec_2027",
                "original_field_name": "原始信息", "enabled": True,
            },
            "archive": {
                "year": None, "app_token": "bascn_archive",
                "table_id": "tbl_archive", "record_id": "rec_archive",
                "original_field_name": "原始信息", "enabled": False,
            },
        },
    }
    assert written == dump_targets(expected_dump_input)

    registry.reload.assert_called_once_with(force=True)


async def test_put_targets_validation_failure_returns_422_no_write(targets_file):
    """
    GIVEN a PUT body with 3 distinct validation errors (bad alias regex,
       duplicate year, default_alias not in targets)
    WHEN PUT /admin/config/targets is called
    THEN the response status is 422
      AND response.errors is a list with one entry per error, each with a path
      AND the targets file is UNCHANGED (byte-for-byte)
      AND NO .bak file was created
      AND registry.reload was NOT called
    """
    original_content = targets_file.read_text(encoding="utf-8")
    t2026 = FeishuTargetConfig("2026", 2026, "bascn_2026", "tbl_2026", "rec_2026", "原始信息", True)
    t2025 = FeishuTargetConfig("2025", 2025, "bascn_2025", "tbl_2025", "rec_2025", "原始信息", True)
    snap = _snapshot([t2025, t2026], default_alias="2026", generation=1)
    registry = _mock_registry_healthy(snap, generation=1)

    # 3 errors: "bad alias!" (regex), two targets with year=2028 (duplicate),
    # default_alias="missing" (not in targets).
    bad_targets = [
        _target_dict("bad alias!", year=2028),
        _target_dict("also2028", year=2028),
    ]

    async with lifespan(app):
        app.state.settings = _dynamic_settings(targets_file=targets_file)
        app.state.target_registry = registry

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/targets",
                json=_put_body(bad_targets, default_alias="missing", base_generation=1),
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 422
    errors = response.json()["detail"]["errors"]
    assert isinstance(errors, list)
    assert len(errors) >= 3  # at least 3 distinct errors collected
    paths = {e["path"] for e in errors}
    # Each error path is targets[i].<field> or default_alias
    for p in paths:
        assert p.startswith("targets[") or p == "default_alias", f"unexpected path: {p}"
    # The bad-alias error must reference targets[0].alias
    assert "targets[0].alias" in paths

    # File untouched, no .bak
    assert targets_file.read_text(encoding="utf-8") == original_content
    assert not targets_file.with_suffix(".toml.bak").is_file()
    registry.reload.assert_not_called()


async def test_put_targets_stale_generation_returns_409(targets_file):
    """
    GIVEN registry.describe()["reload_generation"] == 5
    WHEN PUT /admin/config/targets is called with base_generation=1 (stale)
    THEN the response status is 409 STALE_WRITE
      AND the targets file is UNCHANGED
      AND registry.reload was NOT called
    """
    t2026 = FeishuTargetConfig("2026", 2026, "bascn_2026", "tbl_2026", "rec_2026", "原始信息", True)
    snap = _snapshot([t2026], default_alias="2026", generation=5)
    registry = _mock_registry_healthy(snap, generation=5)

    original_content = targets_file.read_text(encoding="utf-8")

    async with lifespan(app):
        app.state.settings = _dynamic_settings(targets_file=targets_file)
        app.state.target_registry = registry

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/targets",
                json=_put_body(
                    [_target_dict("2026", year=2026)],
                    default_alias="2026", base_generation=1,
                ),
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "STALE_WRITE"
    assert targets_file.read_text(encoding="utf-8") == original_content
    registry.reload.assert_not_called()


async def test_put_targets_legacy_mode_returns_409():
    """
    GIVEN legacy mode (FEISHU_TARGETS_FILE unset, conftest default)
    WHEN PUT /admin/config/targets is called
    THEN the response status is 409 LEGACY_MODE
      AND the error message mentions FEISHU_TARGETS_FILE
    """
    async with lifespan(app):
        app.state.settings = _legacy_settings()

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/targets",
                json=_put_body(
                    [_target_dict("default", year=None)],
                    default_alias="default", base_generation=1,
                ),
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 409
    body = response.json()["detail"]
    assert body["error"]["code"] == "LEGACY_MODE"
    assert "FEISHU_TARGETS_FILE" in body["error"]["message"]


async def test_put_targets_readonly_filesystem_returns_409(monkeypatch, targets_file):
    """
    GIVEN validate passes BUT os.replace raises OSError(EROFS) (simulating :ro mount)
    WHEN PUT /admin/config/targets is called
    THEN the response status is 409 RUNTIME_READONLY
      AND response.suggested_action == "host-edit"
      AND the targets file is UNCHANGED
    """
    from app import main as main_mod

    t2026 = FeishuTargetConfig("2026", 2026, "bascn_2026", "tbl_2026", "rec_2026", "原始信息", True)
    snap = _snapshot([t2026], default_alias="2026", generation=1)
    registry = _mock_registry_healthy(snap, generation=1)

    def _replace_raises(src, dst):
        raise OSError(errno.EROFS, "Read-only file system", str(dst))

    monkeypatch.setattr(main_mod.os, "replace", _replace_raises)

    original_content = targets_file.read_text(encoding="utf-8")

    async with lifespan(app):
        app.state.settings = _dynamic_settings(targets_file=targets_file)
        app.state.target_registry = registry

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/targets",
                json=_put_body(
                    [_target_dict("2026", year=2026)],
                    default_alias="2026", base_generation=1,
                ),
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )

    assert response.status_code == 409
    body = response.json()["detail"]
    assert body["error"]["code"] == "RUNTIME_READONLY"
    assert body["error"]["suggested_action"] == "host-edit"
    assert targets_file.read_text(encoding="utf-8") == original_content


async def test_put_targets_no_token_returns_401(targets_file):
    """
    GIVEN CONFIG_RELOAD_TOKEN is set + dynamic mode
    WHEN PUT /admin/config/targets is called with NO X-Admin-Token
    THEN the response status is 401 UNAUTHORIZED
    """
    async with lifespan(app):
        app.state.settings = _dynamic_settings(targets_file=targets_file)

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/targets",
                json=_put_body(
                    [_target_dict("2026", year=2026)],
                    default_alias="2026", base_generation=1,
                ),
            )

    assert response.status_code == 401
    assert response.json()["detail"]["error"]["code"] == "UNAUTHORIZED"


async def test_put_targets_reload_disabled_returns_404(targets_file):
    """
    GIVEN CONFIG_RELOAD_TOKEN is unset
    WHEN PUT /admin/config/targets is called
    THEN the response status is 404 RELOAD_DISABLED
    """
    async with lifespan(app):
        app.state.settings = replace(
            get_settings(),
            config_reload_token=None,
            feishu_targets_file=targets_file,
        )

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.put(
                "/admin/config/targets",
                json=_put_body(
                    [_target_dict("2026", year=2026)],
                    default_alias="2026", base_generation=1,
                ),
                headers={"X-Admin-Token": "anything"},
            )

    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "RELOAD_DISABLED"
