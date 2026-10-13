# test_history_store.py — Behavior tests for HistoryStore (app/history_store.py)
#
# Tests the sqlite3-backed request history store:
#   - Round trip: insert (Chinese/newline/emoji text, extracted dict, warnings)
#     → list_recent returns deep-equal rows; error/bill_record_id/summary None
#     and non-None both preserved
#   - Ordering: latest ts first, limit truncates
#   - Empty DB: list_recent returns []
#   - Incompatible schema (user_version=99): constructor degrades without
#     raising, insert/list_recent are no-ops, the file is never touched
#   - Unwritable directory: constructor degrades without raising
#   - Corrupt JSON columns: list_recent falls back to {} / [] without raising
#   - v1 → v2 migration: pre-existing v1 db (no pending column) migrates in
#     place, old rows read back with pending=False, user_version becomes 2
#   - Pending lifecycle: insert_pending → finish_entry flips the SAME row to
#     its terminal state; finish only touches rows still pending
#   - Settings gate: AI_HISTORY_DB_PATH unset → None; set → Path
#
# All storage is real sqlite3 against tmp_path files. No mocks, no network.

from __future__ import annotations

import os
import sqlite3

import pytest

from app.history_store import HistoryEntry, HistoryStore


# ─── Test helpers ──────────────────────────────────────────────────────────


def _make_entry(**kwargs) -> HistoryEntry:
    defaults = dict(
        ts=1700000000.0,
        ts_iso="2026-08-28T12:00:00+08:00",
        request_id="req_test001",
        model="claude-3-5-sonnet",
        elapsed_ms=123,
        ai_status="succeeded",
        error=None,
        bill_record_id=None,
        original_text="麦当劳 ¥42 午餐",
        summary=None,
        extracted={"amount": 42.0, "category": "餐饮", "flow_type": "支出"},
        warnings=["分类回退 → 其他"],
    )
    defaults.update(kwargs)
    return HistoryEntry(**defaults)


# ─── Round trip ─────────────────────────────────────────────────────────────


def test_round_trip_preserves_entry(tmp_path):
    """
    GIVEN a HistoryStore backed by a fresh sqlite file
    WHEN two entries are inserted — one with Chinese/newline/emoji text and
         None error/bill_record_id/summary, one with those fields populated
    THEN list_recent returns both rows, latest ts first
      AND each row deep-equals the inserted entry (extracted dict and
          warnings list intact, None fields preserved as None)
    """
    store = HistoryStore(tmp_path / "history.db")
    entry_none = _make_entry(
        ts=1000.0,
        request_id="req_none_fields",
        original_text="星巴克 拿铁 ¥38\n第二行备注 ☕ emoji ok",
        extracted={"amount": 38.0, "category": "餐饮", "note": "中文值"},
        warnings=["未知分类 → 回退 其他"],
    )
    entry_full = _make_entry(
        ts=2000.0,
        request_id="req_full_fields",
        error="extractor timeout",
        bill_record_id="recBkg123",
        summary="星巴克 拿铁 ¥38",
    )
    store.insert(entry_none)
    store.insert(entry_full)

    rows = store.list_recent(10)

    assert rows == [
        {
            "ts": 2000.0,
            "ts_iso": "2026-08-28T12:00:00+08:00",
            "request_id": "req_full_fields",
            "model": "claude-3-5-sonnet",
            "elapsed_ms": 123,
            "ai_status": "succeeded",
            "error": "extractor timeout",
            "bill_record_id": "recBkg123",
            "original_text": "麦当劳 ¥42 午餐",
            "summary": "星巴克 拿铁 ¥38",
            "extracted": {"amount": 42.0, "category": "餐饮", "flow_type": "支出"},
            "warnings": ["分类回退 → 其他"],
            "pending": False,
        },
        {
            "ts": 1000.0,
            "ts_iso": "2026-08-28T12:00:00+08:00",
            "request_id": "req_none_fields",
            "model": "claude-3-5-sonnet",
            "elapsed_ms": 123,
            "ai_status": "succeeded",
            "error": None,
            "bill_record_id": None,
            "original_text": "星巴克 拿铁 ¥38\n第二行备注 ☕ emoji ok",
            "summary": None,
            "extracted": {"amount": 38.0, "category": "餐饮", "note": "中文值"},
            "warnings": ["未知分类 → 回退 其他"],
            "pending": False,
        },
    ]


# ─── Ordering + limit ───────────────────────────────────────────────────────


def test_list_recent_orders_desc_and_limit_truncates(tmp_path):
    """
    GIVEN a HistoryStore with 5 entries inserted at distinct ts values
    WHEN list_recent(limit=3) is called
    THEN exactly 3 rows come back
      AND they are the 3 newest entries, ts strictly decreasing
    """
    store = HistoryStore(tmp_path / "history.db")
    for i in range(1, 6):
        store.insert(_make_entry(ts=float(i), request_id=f"req_{i}"))

    rows = store.list_recent(3)

    assert len(rows) == 3
    assert [row["ts"] for row in rows] == [5.0, 4.0, 3.0]
    assert [row["request_id"] for row in rows] == ["req_5", "req_4", "req_3"]


# ─── Empty DB ───────────────────────────────────────────────────────────────


def test_list_recent_on_empty_db_returns_empty_list(tmp_path):
    """
    GIVEN a HistoryStore backed by a fresh sqlite file with no inserts
    WHEN list_recent is called
    THEN an empty list is returned
    """
    store = HistoryStore(tmp_path / "history.db")

    assert store.list_recent(10) == []


# ─── Schema version guard ───────────────────────────────────────────────────


def test_future_user_version_degrades_without_raising(tmp_path):
    """
    GIVEN a sqlite file pre-created with user_version=99 (unknown future schema)
    WHEN a HistoryStore is constructed on it
      AND insert / list_recent are called
    THEN construction does not raise and the store is degraded
      AND insert is a no-op and list_recent returns []
      AND the file is never touched (user_version still 99, no history table)
    """
    db_path = tmp_path / "history.db"
    pre = sqlite3.connect(str(db_path))
    pre.execute("PRAGMA user_version=99")
    pre.commit()
    pre.close()

    store = HistoryStore(db_path)

    assert store._degraded is True
    store.insert(_make_entry())  # must not raise
    assert store.list_recent(10) == []

    after = sqlite3.connect(str(db_path))
    try:
        version = after.execute("PRAGMA user_version").fetchone()[0]
        tables = after.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='history'"
        ).fetchall()
    finally:
        after.close()
    assert version == 99
    assert tables == []


# ─── Unwritable directory ───────────────────────────────────────────────────


@pytest.mark.skipif(os.name == "nt", reason="POSIX chmod-based read-only simulation")
def test_readonly_directory_degrades_without_raising(tmp_path):
    """
    GIVEN a chmod 0o555 (read-only) directory
    WHEN a HistoryStore is constructed with its db file inside that directory
      AND insert / list_recent are called
    THEN construction does not raise and the store is degraded
      AND insert is a no-op and list_recent returns []
    """
    ro_dir = tmp_path / "ro"
    ro_dir.mkdir()
    ro_dir.chmod(0o555)
    try:
        store = HistoryStore(ro_dir / "history.db")

        assert store._degraded is True
        store.insert(_make_entry())  # must not raise
        assert store.list_recent(10) == []
    finally:
        ro_dir.chmod(0o755)  # restore so pytest can clean up tmp_path


# ─── Corrupt JSON fallback ──────────────────────────────────────────────────


def test_corrupt_json_columns_fall_back_without_raising(tmp_path):
    """
    GIVEN a working HistoryStore with one good entry
      AND a manually inserted row whose extracted/warnings columns are invalid JSON
    WHEN list_recent is called
    THEN it does not raise and returns both rows
      AND the corrupt row's extracted falls back to {} and warnings to []
      AND the good row is returned intact
    """
    db_path = tmp_path / "history.db"
    store = HistoryStore(db_path)
    store.insert(_make_entry(ts=2000.0, request_id="req_good"))

    manual = sqlite3.connect(str(db_path))
    try:
        manual.execute(
            "INSERT INTO history VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                1000.0,
                "1999-01-01T00:00:00+00:00",
                "req_corrupt",
                "claude-3-5-sonnet",
                1,
                "succeeded",
                None,
                None,
                "text",
                None,
                "not-json{{",
                "also-bad[",
                0,
            ),
        )
        manual.commit()
    finally:
        manual.close()

    rows = store.list_recent(10)

    assert [row["request_id"] for row in rows] == ["req_good", "req_corrupt"]
    corrupt = rows[1]
    assert corrupt["extracted"] == {}
    assert corrupt["warnings"] == []
    good = rows[0]
    assert good["extracted"] == {"amount": 42.0, "category": "餐饮", "flow_type": "支出"}
    assert good["warnings"] == ["分类回退 → 其他"]


# ─── v1 → v2 migration ──────────────────────────────────────────────────────


def test_v1_database_migrates_in_place_and_old_rows_stay_readable(tmp_path):
    """
    GIVEN a hand-built v1 database (user_version=1, no pending column, one row
         of legacy data inserted with the 13-column v1 layout)
    WHEN a HistoryStore is constructed on it
      AND a new pending entry is inserted and listed
    THEN construction does not raise
      AND the legacy row is still readable with pending == False (bool-ified)
      AND the new row is inserted successfully with pending == True
      AND PRAGMA user_version == 2
    """
    db_path = tmp_path / "history.db"
    v1 = sqlite3.connect(str(db_path))
    try:
        v1.execute("PRAGMA user_version=1")
        v1.execute(
            "CREATE TABLE history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "ts REAL NOT NULL,"
            "ts_iso TEXT NOT NULL,"
            "request_id TEXT NOT NULL,"
            "model TEXT NOT NULL,"
            "elapsed_ms INTEGER NOT NULL,"
            "ai_status TEXT NOT NULL,"
            "error TEXT,"
            "bill_record_id TEXT,"
            "original_text TEXT NOT NULL,"
            "summary TEXT,"
            "extracted TEXT NOT NULL,"
            "warnings TEXT NOT NULL)"
        )
        v1.execute(
            "INSERT INTO history VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                1000.0,
                "2026-01-01T00:00:00+08:00",
                "req_legacy_v1",
                "claude-3-5-sonnet",
                55,
                "succeeded",
                None,
                None,
                "旧版历史行",
                None,
                "{}",
                "[]",
            ),
        )
        v1.commit()
    finally:
        v1.close()

    store = HistoryStore(db_path)

    assert store._degraded is False
    store.insert_pending(_make_entry(ts=2000.0, request_id="req_new_pending"))

    rows = store.list_recent(10)
    assert [row["request_id"] for row in rows] == ["req_new_pending", "req_legacy_v1"]
    legacy = rows[1]
    assert legacy["pending"] is False
    assert legacy["original_text"] == "旧版历史行"
    new_row = rows[0]
    assert new_row["ai_status"] == "pending"
    assert new_row["pending"] is True

    check = sqlite3.connect(str(db_path))
    try:
        version = check.execute("PRAGMA user_version").fetchone()[0]
    finally:
        check.close()
    assert version == 2


# ─── Pending lifecycle: insert_pending / finish_entry ───────────────────────


def test_insert_pending_then_finish_entry_updates_same_row_to_terminal(tmp_path):
    """
    GIVEN a working HistoryStore
    WHEN insert_pending stores an entry and returns a rowid
      AND the row is listed before finishing
      AND finish_entry transitions that rowid to ai_status="succeeded"
    THEN the pre-finish row shows ai_status == "pending" and pending == True
      AND the post-finish row has the SAME id, now ai_status == "succeeded",
          pending == False, elapsed_ms / ts refreshed to the finish values,
          and extracted / warnings / summary / bill_record_id round-tripped
    """
    store = HistoryStore(tmp_path / "history.db")

    row_id = store.insert_pending(
        _make_entry(ts=1000.0, request_id="req_flow", ai_status="pending")
    )

    assert isinstance(row_id, int)

    inflight = store.list_recent(10)
    assert len(inflight) == 1
    assert inflight[0]["ai_status"] == "pending"
    assert inflight[0]["pending"] is True
    assert inflight[0]["request_id"] == "req_flow"

    store.finish_entry(
        row_id,
        ai_status="succeeded",
        error=None,
        bill_record_id="rec999",
        summary="精简数据",
        extracted={"amount": 42.0, "category": "餐饮"},
        warnings=["分类回退 → 其他"],
        elapsed_ms=777,
        ts_iso="2026-08-28T13:00:00+08:00",
    )

    rows = store.list_recent(10)
    assert len(rows) == 1
    finished = rows[0]
    assert finished["request_id"] == "req_flow"  # same row, not a second one
    assert finished["ai_status"] == "succeeded"
    assert finished["pending"] is False
    assert finished["bill_record_id"] == "rec999"
    assert finished["summary"] == "精简数据"
    assert finished["extracted"] == {"amount": 42.0, "category": "餐饮"}
    assert finished["warnings"] == ["分类回退 → 其他"]
    assert finished["elapsed_ms"] == 777
    assert finished["ts_iso"] == "2026-08-28T13:00:00+08:00"
    assert finished["ts"] >= 1000.0  # ts refreshed to the completion moment


def test_finish_entry_only_touches_rows_still_pending(tmp_path):
    """
    GIVEN a pending row that has already been finished once
    WHEN finish_entry is called a second time on the same rowid
       with different terminal data
    THEN no error is raised
      AND the row still holds the FIRST finish's terminal state
      AND list_recent still returns exactly 1 row
    """
    store = HistoryStore(tmp_path / "history.db")
    row_id = store.insert_pending(
        _make_entry(ts=1000.0, request_id="req_guard", ai_status="pending")
    )

    store.finish_entry(
        row_id,
        ai_status="succeeded",
        error=None,
        bill_record_id="rec-first",
        summary="first",
        extracted={"amount": 1.0},
        warnings=[],
        elapsed_ms=100,
        ts_iso="2026-08-28T12:00:00+08:00",
    )
    store.finish_entry(
        row_id,
        ai_status="failed",
        error="second finish must not win",
        bill_record_id="rec-second",
        summary="second",
        extracted={},
        warnings=["late"],
        elapsed_ms=999,
        ts_iso="2026-08-28T14:00:00+08:00",
    )

    rows = store.list_recent(10)
    assert len(rows) == 1
    row = rows[0]
    assert row["ai_status"] == "succeeded"
    assert row["bill_record_id"] == "rec-first"
    assert row["summary"] == "first"
    assert row["elapsed_ms"] == 100
    assert row["error"] is None


def test_insert_pending_on_degraded_store_returns_none_without_raising(tmp_path):
    """
    GIVEN a HistoryStore degraded by a read-only directory
    WHEN insert_pending is called
    THEN it returns None without raising
      AND finish_entry on a dummy rowid is a silent no-op
    """
    ro_dir = tmp_path / "ro"
    ro_dir.mkdir()
    ro_dir.chmod(0o555)
    try:
        store = HistoryStore(ro_dir / "history.db")

        assert store._degraded is True
        row_id = store.insert_pending(_make_entry(ai_status="pending"))
        assert row_id is None
        store.finish_entry(1, ai_status="failed", error=None, bill_record_id=None,
                           summary=None, extracted={}, warnings=[], elapsed_ms=0,
                           ts_iso="2026-08-28T12:00:00+08:00")  # must not raise
        assert store.list_recent(10) == []
    finally:
        ro_dir.chmod(0o755)


# ─── Settings gate ──────────────────────────────────────────────────────────


def test_settings_ai_history_db_path_defaults_to_none(monkeypatch):
    """
    GIVEN AI_HISTORY_DB_PATH is not set in the environment
    WHEN get_settings() is called (cache cleared first)
    THEN ai_history_db_path is None (history feature off)
    """
    monkeypatch.delenv("AI_HISTORY_DB_PATH", raising=False)

    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    assert settings.ai_history_db_path is None


def test_settings_ai_history_db_path_read_from_env(monkeypatch, tmp_path):
    """
    GIVEN AI_HISTORY_DB_PATH points at a path under tmp_path
    WHEN get_settings() is called (cache cleared first)
    THEN ai_history_db_path is a Path equal to that value
    """
    db_path = tmp_path / "x.db"
    monkeypatch.setenv("AI_HISTORY_DB_PATH", str(db_path))

    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    assert settings.ai_history_db_path == db_path
