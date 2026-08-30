import os
import sys

# Python 3.10 compat: tomllib is stdlib in 3.11+, provide backport
if sys.version_info < (3, 11):
    import tomli as tomllib
    sys.modules["tomllib"] = tomllib

# Pin mode + AI vars BEFORE any app import (config.py:55 runs _load_runtime_env_files at import)
os.environ["FEISHU_TARGETS_FILE"] = ""
os.environ["FEISHU_ENV_FILE"] = ""
os.environ["AI_ENABLED"] = "false"
os.environ["AI_PROFILE_FILE"] = ""

# Test env defaults (setdefault — won't override if already set)
os.environ.setdefault("WEBHOOK_SHARED_TOKEN", "test-webhook-token")
os.environ.setdefault("FEISHU_APP_ID", "test-app-id")
os.environ.setdefault("FEISHU_APP_SECRET", "test-app-secret")
os.environ.setdefault("FEISHU_APP_TOKEN", "test-app-token")
os.environ.setdefault("FEISHU_TABLE_ID", "test-table-id")
os.environ.setdefault("FEISHU_RECORD_ID", "test-record-id")

import pytest


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()