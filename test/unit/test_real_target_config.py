from dataclasses import replace
from pathlib import Path
import tomllib

import httpx
import pytest

from app.config import get_settings
from app.main import app, lifespan
from app.target_registry import TargetRegistry, TargetRegistryConfigError


async def test_real_registry_loads_and_saves_targets(tmp_path: Path) -> None:
    path = tmp_path / "targets.toml"
    path.write_text(
        'default_alias = "2026"\n[targets."2026"]\n'
        'year = 2026\napp_token = "app1"\ntable_id = "table1"\nrecord_id = "record1"\n',
        encoding="utf-8",
    )
    settings = replace(get_settings(), feishu_targets_file=path, config_reload_token="admin")
    registry = TargetRegistry(settings)
    registry.load_initial()
    headers = {"X-Admin-Token": "admin"}

    async with lifespan(app):
        app.state.settings = settings
        app.state.target_registry = registry
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/admin/config/targets", headers=headers)
            assert response.status_code == 200
            body = response.json()
            assert body["generation"] == 1
            assert [target["alias"] for target in body["targets"]] == ["2026"]
            body["targets"].append({"alias": "2027", "year": 2027, "app_token": "app2", "table_id": "table2", "record_id": "record2"})
            saved = await client.put("/admin/config/targets", headers=headers, json={
                "default_alias": body["default_alias"], "targets": body["targets"], "base_generation": body["generation"],
            })
            assert saved.status_code == 200, saved.text
            assert saved.json()["generation"] == 2
            loaded = await client.get("/admin/config/targets", headers=headers)
            assert {target["alias"] for target in loaded.json()["targets"]} == {"2026", "2027"}

    assert set(tomllib.loads(path.read_text(encoding="utf-8"))["targets"]) == {"2026", "2027"}


async def test_real_registry_reports_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "targets.toml"
    path.write_text('default_alias = "a"\n[targets.a]\napp_token = "a"\ntable_id = "b"\nrecord_id = "c"\n', encoding="utf-8")
    settings = replace(get_settings(), feishu_targets_file=path, config_reload_token="admin")
    registry = TargetRegistry(settings)
    registry.load_initial()
    path.write_text('default_alias = "missing"\n', encoding="utf-8")
    with pytest.raises(TargetRegistryConfigError):
        registry.reload(force=True)

    async with lifespan(app):
        app.state.settings = settings
        app.state.target_registry = registry
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/admin/config/targets", headers={"X-Admin-Token": "admin"})
    assert response.status_code == 200
    assert response.json()["config_valid"] is False
    assert response.json()["targets"] is None


async def test_real_legacy_registry_is_readable_but_not_writable() -> None:
    settings = replace(get_settings(), feishu_targets_file=None, config_reload_token="admin")
    registry = TargetRegistry(settings)
    registry.load_initial()
    headers = {"X-Admin-Token": "admin"}
    async with lifespan(app):
        app.state.settings = settings
        app.state.target_registry = registry
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/admin/config/targets", headers=headers)
            body = response.json()
            assert body["mode"] == "legacy"
            assert body["targets"][0]["alias"] == "default"
            saved = await client.put("/admin/config/targets", headers=headers, json={
                "default_alias": body["default_alias"], "targets": body["targets"], "base_generation": body["generation"],
            })
    assert saved.status_code == 409
    assert saved.json()["detail"]["error"]["code"] == "LEGACY_MODE"
