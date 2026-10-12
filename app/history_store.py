"""sqlite3-backed request history store for the AI pipeline.

Optional persistence layer: when the operator sets AI_HISTORY_DB_PATH, the
webhook route records each AI pipeline result so the admin UI can show recent
requests. Design constraints:

- **Never raises**: storage problems must not break the webhook 200 semantics.
  Construction failures (missing directory, unwritable file, foreign schema)
  flip the store into a degraded mode where insert/list_recent are no-ops.
- **Schema version guard**: the file's `user_version` stamps the layout.
  Version 0 → create table; 1 → reuse; anything else → degrade (a future or
  unknown schema must not be silently rewritten).
- **Lock discipline**: one connection opened with check_same_thread=False is
  shared across threads (callers hop threads via to_thread), so every
  operation runs under a threading.Lock.
- **Never logs original_text**: log only op names and exception messages.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


logger = logging.getLogger("feishu_webhook_service.history")

_SCHEMA_VERSION = 1

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    ts_iso TEXT NOT NULL,
    request_id TEXT NOT NULL,
    model TEXT NOT NULL,
    elapsed_ms INTEGER NOT NULL,
    ai_status TEXT NOT NULL,
    error TEXT,
    bill_record_id TEXT,
    original_text TEXT NOT NULL,
    summary TEXT,
    extracted TEXT NOT NULL,
    warnings TEXT NOT NULL
)
"""

_SELECT_COLUMNS = (
    "ts, ts_iso, request_id, model, elapsed_ms, ai_status, error, "
    "bill_record_id, original_text, summary, extracted, warnings"
)

# Error-log throttle: the first failure after a quiet period logs the full
# exception text; repeat failures within the window log a one-line summary
# only, so a broken disk cannot flood the log.
_ERROR_LOG_INTERVAL_SECONDS = 60.0
_last_error_log_ts: float = 0.0
_error_log_lock = threading.Lock()


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    ts: float
    ts_iso: str
    request_id: str
    model: str
    elapsed_ms: int
    ai_status: str
    error: str | None
    bill_record_id: str | None
    original_text: str
    summary: str | None
    extracted: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class HistoryStore:
    """Append-only sqlite request history. All failures degrade, never raise."""

    def __init__(self, db_path: Path) -> None:
        self._lock = threading.Lock()
        self._degraded = False
        self._conn: sqlite3.Connection | None = None
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=2000")
                version = int(conn.execute("PRAGMA user_version").fetchone()[0])
                if version == 0:
                    conn.execute(_CREATE_TABLE_SQL)
                    conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
                    conn.commit()
                elif version != _SCHEMA_VERSION:
                    raise RuntimeError(
                        f"incompatible schema user_version={version}, "
                        f"expected {_SCHEMA_VERSION}"
                    )
                self._conn = conn
            except Exception:
                conn.close()
                raise
        except Exception as exc:
            self._degraded = True
            self._conn = None
            logger.error(
                "History store degraded (history disabled): db_path=%s, reason=%s",
                db_path,
                exc,
            )

    def insert(self, entry: HistoryEntry) -> None:
        """Persist one entry; degraded mode and failures are silent no-ops."""
        if self._degraded or self._conn is None:
            return
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO history VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        entry.ts,
                        entry.ts_iso,
                        entry.request_id,
                        entry.model,
                        entry.elapsed_ms,
                        entry.ai_status,
                        entry.error,
                        entry.bill_record_id,
                        entry.original_text,
                        entry.summary,
                        json.dumps(entry.extracted, ensure_ascii=False),
                        json.dumps(entry.warnings, ensure_ascii=False),
                    ),
                )
                self._conn.commit()
        except Exception as exc:
            self._log_error_throttled("insert", exc)

    def list_recent(self, limit: int) -> list[dict]:
        """Newest-first rows as dicts; degraded mode and failures return []."""
        if self._degraded or self._conn is None:
            return []
        try:
            with self._lock:
                cursor = self._conn.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM history ORDER BY ts DESC, id DESC LIMIT ?",
                    (limit,),
                )
                rows = cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]
        except Exception as exc:
            self._log_error_throttled("list_recent", exc)
            return []

    @staticmethod
    def _row_to_dict(row: tuple) -> dict:
        (
            ts,
            ts_iso,
            request_id,
            model,
            elapsed_ms,
            ai_status,
            error,
            bill_record_id,
            original_text,
            summary,
            extracted_json,
            warnings_json,
        ) = row

        # Corrupt JSON columns must not break reads — fall back to empty
        # containers so the admin UI can still render the row's other fields.
        try:
            extracted = json.loads(extracted_json)
        except Exception:
            extracted = {}
        try:
            warnings = json.loads(warnings_json)
        except Exception:
            warnings = []

        return {
            "ts": ts,
            "ts_iso": ts_iso,
            "request_id": request_id,
            "model": model,
            "elapsed_ms": elapsed_ms,
            "ai_status": ai_status,
            "error": error,
            "bill_record_id": bill_record_id,
            "original_text": original_text,
            "summary": summary,
            "extracted": extracted,
            "warnings": warnings,
        }

    @staticmethod
    def _log_error_throttled(op: str, exc: Exception) -> None:
        global _last_error_log_ts
        now = time.monotonic()
        with _error_log_lock:
            first = now - _last_error_log_ts >= _ERROR_LOG_INTERVAL_SECONDS
            _last_error_log_ts = now
        if first:
            logger.error("History store %s failed: %s", op, exc)
        else:
            logger.error("History store %s failed (repeat suppressed)", op)
