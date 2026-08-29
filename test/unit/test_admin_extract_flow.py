# test_admin_extract_flow.py — Behavior tests for the extract-table config UI
# (admin.js §extract-section) backed by GET/PUT /admin/config/targets.
#
# The UI itself is vanilla JS exercised in the browser; these tests cover the
# Python-side contracts the UI depends on plus JS-grep gates that lock the
# function names the UI must expose. Three concern groups:
#
#   1. targets GET→PUT round-trip integration (ASGI): GET returns the editable
#      shape → the UI assembles a PUT body from it → PUT 200 writes the file.
#      Verifies the contract the UI's save flow relies on.
#   2. base_generation guard: two PUTs with the same base_generation → the
#      second is 409 STALE_WRITE (the UI's auto-reload-on-409 path depends on
#      this signal).
#   3. JS grep gates: admin.js contains the functions the UI requires
#      (renderAliasCard / renderRecordPicker / parseUrl + the error-path →
#      card-index mapper). admin.html contains the save/add button ids.
#
# Strategy mirrors test_config_targets_api.py:
#   - conftest pins legacy mode; dynamic-mode tests swap app.state.settings +
#     rebuild the registry mock.
#   - httpx.AsyncClient(transport=ASGITransport(app), base_url="http://testserver")
#     with the lifespan async context manager run directly.

from __future__ import annotations

import re
import subprocess
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
    TargetRegistrySnapshot,
)

ADMIN_TOKEN = "test-admin-token"
TEST_BASE_URL = "http://testserver"
_STATIC_DIR = Path(__file__).resolve().parents[2] / "app" / "static"


# --- Shared helpers (mirror test_config_targets_api.py) ---


def _targets_toml(alias_a: str = "2026", alias_b: str | None = "2025") -> str:
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


def _snapshot(targets, default_alias, generation=1):
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


def _mock_registry_healthy(snapshot, *, generation=1):
    state = {"generation": generation, "snapshot": snapshot}

    def _describe():
        return {
            "mode": "dynamic",
            "default_alias": state["snapshot"].default_alias,
            "target_count": len(state["snapshot"].targets_by_alias),
            "reload_generation": state["generation"],
            "config_valid": True,
            "last_reload_error": None,
            "source_path": state["snapshot"].source_path,
        }

    def _reload(*, force):
        state["generation"] += 1
        return _describe()

    registry = MagicMock(spec=TargetRegistry)
    registry.describe = MagicMock(side_effect=_describe)
    registry.get_snapshot = MagicMock(return_value=state["snapshot"])
    registry.reload = MagicMock(side_effect=_reload)
    registry.maybe_reload = MagicMock(return_value=None)
    return registry


# ─── 1. targets GET→PUT round-trip integration ─────────────────────────────


@pytest.fixture
def targets_file(tmp_path):
    path = tmp_path / "feishu-targets.toml"
    path.write_text(_targets_toml(), encoding="utf-8")
    return path


async def test_targets_get_then_put_round_trip_writes_file(targets_file):
    """
    GIVEN a dynamic-mode registry with 2 targets (2025 + 2026, default=2026)
       AND generation=1
    WHEN the UI flow runs: GET /admin/config/targets, then assembles a PUT body
       from the GET response (same targets, base_generation=GET.generation)
    THEN the PUT response status is 200
      AND response.success is True
      AND response.generation == 2 (reloaded)
      AND the file round-trips: re-parsed TOML contains both targets verbatim
    """
    t2026 = FeishuTargetConfig("2026", 2026, "bascn_2026", "tbl_2026", "rec_2026", "原始信息", True)
    t2025 = FeishuTargetConfig("2025", 2025, "bascn_2025", "tbl_2025", "rec_2025", "原始信息", True)
    snap = _snapshot([t2025, t2026], default_alias="2026", generation=1)
    registry = _mock_registry_healthy(snap, generation=1)

    async with lifespan(app):
        app.state.settings = _dynamic_settings(targets_file=targets_file)
        app.state.target_registry = registry

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            headers = {"X-Admin-Token": ADMIN_TOKEN}

            # Step 1: GET — the UI reads the editable shape.
            get_resp = await client.get("/admin/config/targets", headers=headers)
            assert get_resp.status_code == 200
            get_body = get_resp.json()
            assert get_body["generation"] == 1
            assert get_body["default_alias"] == "2026"
            targets = get_body["targets"]
            assert len(targets) == 2

            # Step 2: the UI assembles a PUT body from the GET response —
            # round-trip the targets verbatim, carrying base_generation from GET.
            put_body = {
                "default_alias": get_body["default_alias"],
                "targets": targets,
                "base_generation": get_body["generation"],
            }
            put_resp = await client.put(
                "/admin/config/targets", json=put_body, headers=headers
            )

    assert put_resp.status_code == 200, put_resp.text
    put_body_resp = put_resp.json()
    assert put_body_resp["success"] is True
    assert put_body_resp["generation"] == 2

    # File round-trips — both targets present, app_tokens preserved.
    written = targets_file.read_text(encoding="utf-8")
    parsed = tomllib.loads(written)
    assert parsed["default_alias"] == "2026"
    assert set(parsed["targets"].keys()) == {"2025", "2026"}
    assert parsed["targets"]["2026"]["app_token"] == "bascn_2026"
    assert parsed["targets"]["2025"]["app_token"] == "bascn_2025"


# ─── 2. base_generation guard (STALE_WRITE on second PUT) ──────────────────


async def test_targets_second_put_same_base_generation_returns_409_stale(targets_file):
    """
    GIVEN a dynamic-mode registry at generation=1
       AND the first PUT (base_generation=1) succeeds, bumping generation to 2
    WHEN a second PUT is sent with the SAME base_generation=1 (stale)
    THEN the second PUT response status is 409
      AND the error code is STALE_WRITE
      (the UI's auto-reload-on-409-STALE_WRITE path depends on this signal)
    """
    t2026 = FeishuTargetConfig("2026", 2026, "bascn_2026", "tbl_2026", "rec_2026", "原始信息", True)
    snap = _snapshot([t2026], default_alias="2026", generation=1)
    registry = _mock_registry_healthy(snap, generation=1)

    put_body = {
        "default_alias": "2026",
        "targets": [
            {
                "alias": "2026", "year": 2026, "app_token": "bascn_2026",
                "table_id": "tbl_2026", "record_id": "rec_2026",
                "original_field_name": "原始信息", "enabled": True,
            }
        ],
        "base_generation": 1,
    }

    async with lifespan(app):
        app.state.settings = _dynamic_settings(targets_file=targets_file)
        app.state.target_registry = registry

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            headers = {"X-Admin-Token": ADMIN_TOKEN}
            first = await client.put("/admin/config/targets", json=put_body, headers=headers)
            second = await client.put("/admin/config/targets", json=put_body, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"]["error"]["code"] == "STALE_WRITE"


# ─── 3. JS grep gates + admin.html id assertions ───────────────────────────


def _read_static(name: str) -> str:
    return (_STATIC_DIR / name).read_text(encoding="utf-8")


def test_admin_js_exposes_required_ui_functions():
    """
    GIVEN app/static/admin.js exists
    WHEN the source is scanned for the function declarations the extract-table
       UI requires
    THEN it contains function declarations for:
      - renderAliasCard  (renders one alias card)
      - renderRecordPicker (renders the record list + load-more)
      - parseUrl (calls POST /admin/feishu/parse-url)
      - the error-path → card-index mapper (parseErrorPath)
    """
    js = _read_static("admin.js")
    required = ["renderAliasCard", "renderRecordPicker", "parseUrl", "parseErrorPath"]
    for name in required:
        # Match `function <name>(` or `function <name> (` — declaration, not call.
        pattern = r"function\s+" + re.escape(name) + r"\s*\("
        assert re.search(pattern, js), f"admin.js missing function declaration: {name}"


def test_admin_js_node_check_passes():
    """
    GIVEN app/static/admin.js exists on disk
    WHEN `node --check app/static/admin.js` is executed
    THEN the exit code is 0 (JS syntax gate — todo 9 added new functions)
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


async def test_admin_html_extract_section_has_save_and_add_button_ids():
    """
    GIVEN the FastAPI app is running
    WHEN GET /admin/ai is requested
    THEN the extract-section contains id="save-targets-btn" and id="add-alias-btn"
      (the JS wires click handlers to these ids)
    """
    async with lifespan(app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get("/admin/ai")

    body = response.text
    assert 'id="save-targets-btn"' in body
    assert 'id="add-alias-btn"' in body
    assert 'id="extract-banner"' in body
