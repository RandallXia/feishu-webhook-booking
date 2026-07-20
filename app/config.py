from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_env_file(env_file: Path, *, required: bool = False) -> None:
    if required and not env_file.is_file():
        raise RuntimeError(f"Missing runtime env file: {env_file}")

    if not env_file.is_file():
        return

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[7:].strip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]

        os.environ.setdefault(key, value)


def _load_runtime_env_files() -> None:
    # Production/runtime config comes from an external env file when configured.
    # Local development still falls back to the project-root .env.
    external_env_file = os.getenv("FEISHU_ENV_FILE", "").strip()
    if external_env_file:
        env_file = Path(external_env_file).expanduser()
        if not env_file.is_absolute():
            env_file = (PROJECT_ROOT / env_file).resolve()
        _load_env_file(env_file, required=True)

    _load_env_file(PROJECT_ROOT / ".env")


_load_runtime_env_files()


@dataclass(frozen=True, slots=True)
class Settings:
    host: str
    port: int
    log_level: str
    webhook_shared_token: str
    feishu_base_url: str
    feishu_app_id: str
    feishu_app_secret: str
    feishu_app_token: str | None
    feishu_table_id: str | None
    feishu_record_id: str | None
    feishu_original_field_name: str
    http_timeout_seconds: int
    token_refresh_skew_seconds: int
    feishu_targets_file: Path | None
    target_reload_interval_seconds: int
    config_reload_token: str | None


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _optional_env(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be an integer") from exc

    if value < 0:
        raise RuntimeError(f"Environment variable {name} must be zero or positive")
    return value


def _path_env(name: str) -> Path | None:
    raw_value = _optional_env(name)
    if not raw_value:
        return None

    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    feishu_targets_file = _path_env("FEISHU_TARGETS_FILE")
    if feishu_targets_file is None:
        feishu_app_token = _require_env("FEISHU_APP_TOKEN")
        feishu_table_id = _require_env("FEISHU_TABLE_ID")
        feishu_record_id = _require_env("FEISHU_RECORD_ID")
    else:
        feishu_app_token = _optional_env("FEISHU_APP_TOKEN")
        feishu_table_id = _optional_env("FEISHU_TABLE_ID")
        feishu_record_id = _optional_env("FEISHU_RECORD_ID")

    return Settings(
        host=os.getenv("HOST", "0.0.0.0").strip() or "0.0.0.0",
        port=_int_env("PORT", 2398),
        log_level=(os.getenv("LOG_LEVEL", "INFO").strip() or "INFO").upper(),
        webhook_shared_token=_require_env("WEBHOOK_SHARED_TOKEN"),
        feishu_base_url=(os.getenv("FEISHU_BASE_URL", "https://open.feishu.cn").strip() or "https://open.feishu.cn").rstrip("/"),
        feishu_app_id=_require_env("FEISHU_APP_ID"),
        feishu_app_secret=_require_env("FEISHU_APP_SECRET"),
        feishu_app_token=feishu_app_token,
        feishu_table_id=feishu_table_id,
        feishu_record_id=feishu_record_id,
        feishu_original_field_name=os.getenv("FEISHU_ORIGINAL_FIELD_NAME", "原始信息").strip() or "原始信息",
        http_timeout_seconds=_int_env("HTTP_TIMEOUT_SECONDS", 10),
        token_refresh_skew_seconds=_int_env("TOKEN_REFRESH_SKEW_SECONDS", 300),
        feishu_targets_file=feishu_targets_file,
        target_reload_interval_seconds=_int_env("FEISHU_TARGET_RELOAD_INTERVAL_SECONDS", 10),
        config_reload_token=_optional_env("CONFIG_RELOAD_TOKEN"),
    )
