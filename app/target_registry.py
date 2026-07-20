from __future__ import annotations

import logging
import re
import threading
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .config import Settings


logger = logging.getLogger("feishu_webhook_service.target_registry")
_ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class TargetRegistryError(RuntimeError):
    pass


class TargetRegistryConfigError(TargetRegistryError):
    pass


class TargetSelectorError(TargetRegistryError):
    pass


class TargetRegistryUnavailableError(TargetRegistryError):
    pass


@dataclass(frozen=True, slots=True)
class FeishuTargetConfig:
    alias: str
    year: int | None
    app_token: str
    table_id: str
    record_id: str
    original_field_name: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class TargetRegistrySnapshot:
    mode: str
    default_alias: str
    targets_by_alias: dict[str, FeishuTargetConfig]
    aliases_by_year: dict[int, str]
    loaded_at: float
    source_path: str | None
    source_mtime: float | None
    generation: int


class TargetRegistry:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._snapshot: TargetRegistrySnapshot | None = None
        self._generation = 0
        self._last_reload_check_at = 0.0
        self._last_reload_error: str | None = None
        self._config_valid = True

    def load_initial(self) -> None:
        snapshot = self._load_snapshot()
        with self._lock:
            self._snapshot = snapshot
            self._config_valid = True
            self._last_reload_error = None

    def maybe_reload(self) -> None:
        if self._settings.feishu_targets_file is None:
            return

        now = time.time()
        if now - self._last_reload_check_at < self._settings.target_reload_interval_seconds:
            return
        self._last_reload_check_at = now

        with self._lock:
            snapshot = self._snapshot

        current_mtime = self._stat_targets_file()
        if snapshot and snapshot.source_mtime == current_mtime and self._config_valid:
            return

        self.reload(force=True)

    def reload(self, *, force: bool) -> dict[str, object]:
        if not force:
            return self.describe()

        snapshot = self._load_snapshot()
        with self._lock:
            self._snapshot = snapshot
            self._config_valid = True
            self._last_reload_error = None

        logger.info(
            "target registry reloaded mode=%s default_alias=%s target_count=%s generation=%s",
            snapshot.mode,
            snapshot.default_alias,
            len(snapshot.targets_by_alias),
            snapshot.generation,
        )
        return self.describe()

    def resolve(self, book_alias: str | None, year: int | None) -> FeishuTargetConfig:
        with self._lock:
            snapshot = self._snapshot
            config_valid = self._config_valid

        if snapshot is None:
            raise TargetRegistryUnavailableError("target registry is not loaded")
        if not config_valid:
            raise TargetRegistryUnavailableError("target registry is temporarily unavailable")

        if book_alias and year is not None:
            alias_target = snapshot.targets_by_alias.get(book_alias)
            year_alias = snapshot.aliases_by_year.get(year)
            if alias_target is None or year_alias is None or alias_target.alias != year_alias:
                raise TargetSelectorError("book_alias and year resolve to different targets")
            if not alias_target.enabled:
                raise TargetSelectorError("requested target is disabled")
            return alias_target

        if book_alias:
            target = snapshot.targets_by_alias.get(book_alias)
            if target is None:
                raise TargetSelectorError("unknown book alias")
            if not target.enabled:
                raise TargetSelectorError("requested target is disabled")
            return target

        if year is not None:
            alias = snapshot.aliases_by_year.get(year)
            if alias is None:
                raise TargetSelectorError("unknown book year")
            target = snapshot.targets_by_alias[alias]
            if not target.enabled:
                raise TargetSelectorError("requested target is disabled")
            return target

        return snapshot.targets_by_alias[snapshot.default_alias]

    def describe(self) -> dict[str, object]:
        with self._lock:
            snapshot = self._snapshot
            return {
                "mode": snapshot.mode if snapshot else "uninitialized",
                "default_alias": snapshot.default_alias if snapshot else None,
                "target_count": len(snapshot.targets_by_alias) if snapshot else 0,
                "reload_generation": snapshot.generation if snapshot else 0,
                "config_valid": self._config_valid,
                "last_reload_error": self._last_reload_error,
                "source_path": snapshot.source_path if snapshot else None,
            }

    def _load_snapshot(self) -> TargetRegistrySnapshot:
        try:
            if self._settings.feishu_targets_file is None:
                return self._build_legacy_snapshot()
            return self._build_dynamic_snapshot(self._settings.feishu_targets_file)
        except TargetRegistryError as exc:
            with self._lock:
                self._config_valid = False
                self._last_reload_error = str(exc)
            logger.error("target registry reload failed error=%s", exc)
            raise

    def _build_legacy_snapshot(self) -> TargetRegistrySnapshot:
        app_token = self._settings.feishu_app_token
        table_id = self._settings.feishu_table_id
        record_id = self._settings.feishu_record_id
        if not app_token or not table_id or not record_id:
            raise TargetRegistryConfigError("legacy single-target configuration is incomplete")

        target = FeishuTargetConfig(
            alias="default",
            year=None,
            app_token=app_token,
            table_id=table_id,
            record_id=record_id,
            original_field_name=self._settings.feishu_original_field_name,
            enabled=True,
        )
        self._generation += 1
        return TargetRegistrySnapshot(
            mode="legacy",
            default_alias=target.alias,
            targets_by_alias={target.alias: target},
            aliases_by_year={},
            loaded_at=time.time(),
            source_path=None,
            source_mtime=None,
            generation=self._generation,
        )

    def _build_dynamic_snapshot(self, targets_file: Path) -> TargetRegistrySnapshot:
        if not targets_file.is_file():
            raise TargetRegistryConfigError(f"Missing targets registry file: {targets_file}")

        data = tomllib.loads(targets_file.read_text(encoding="utf-8"))
        default_alias = str(data.get("default_alias", "")).strip()
        if not default_alias:
            raise TargetRegistryConfigError("targets registry must define default_alias")

        raw_targets = data.get("targets")
        if not isinstance(raw_targets, dict) or not raw_targets:
            raise TargetRegistryConfigError("targets registry must define at least one target")

        targets_by_alias: dict[str, FeishuTargetConfig] = {}
        aliases_by_year: dict[int, str] = {}
        for alias, raw_target in raw_targets.items():
            target = self._parse_target(alias, raw_target)
            targets_by_alias[target.alias] = target
            if target.year is not None:
                if target.year in aliases_by_year:
                    raise TargetRegistryConfigError(f"duplicate target year: {target.year}")
                aliases_by_year[target.year] = target.alias

        default_target = targets_by_alias.get(default_alias)
        if default_target is None:
            raise TargetRegistryConfigError("default_alias does not exist in targets registry")
        if not default_target.enabled:
            raise TargetRegistryConfigError("default_alias target must be enabled")

        self._generation += 1
        return TargetRegistrySnapshot(
            mode="dynamic",
            default_alias=default_alias,
            targets_by_alias=targets_by_alias,
            aliases_by_year=aliases_by_year,
            loaded_at=time.time(),
            source_path=str(targets_file),
            source_mtime=targets_file.stat().st_mtime,
            generation=self._generation,
        )

    def _parse_target(self, alias: object, raw_target: object) -> FeishuTargetConfig:
        if not isinstance(alias, str) or not _ALIAS_PATTERN.match(alias):
            raise TargetRegistryConfigError(f"invalid target alias: {alias!r}")
        if not isinstance(raw_target, dict):
            raise TargetRegistryConfigError(f"target {alias} must be an object")

        app_token = self._required_target_value(alias, raw_target, "app_token")
        table_id = self._required_target_value(alias, raw_target, "table_id")
        record_id = self._required_target_value(alias, raw_target, "record_id")
        original_field_name = str(raw_target.get("original_field_name", self._settings.feishu_original_field_name)).strip()
        enabled = bool(raw_target.get("enabled", True))

        year_raw = raw_target.get("year")
        if year_raw is None:
            year = None
        elif isinstance(year_raw, int) and year_raw > 0:
            year = year_raw
        else:
            raise TargetRegistryConfigError(f"target {alias} has invalid year")

        return FeishuTargetConfig(
            alias=alias,
            year=year,
            app_token=app_token,
            table_id=table_id,
            record_id=record_id,
            original_field_name=original_field_name or self._settings.feishu_original_field_name,
            enabled=enabled,
        )

    def _required_target_value(self, alias: str, raw_target: dict[str, object], field_name: str) -> str:
        value = raw_target.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise TargetRegistryConfigError(f"target {alias} is missing {field_name}")
        return value.strip()

    def _stat_targets_file(self) -> float:
        targets_file = self._settings.feishu_targets_file
        if targets_file is None or not targets_file.is_file():
            raise TargetRegistryConfigError(f"Missing targets registry file: {targets_file}")
        return targets_file.stat().st_mtime
