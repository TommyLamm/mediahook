"""SQLite 任務狀態儲存。"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ACTIVE = ("queued", "running")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    url           TEXT NOT NULL,
    mode          TEXT NOT NULL,
    platform      TEXT NOT NULL,
    status        TEXT NOT NULL,
    result        TEXT,
    error_code    TEXT,
    error_message TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_status_updated ON jobs (status, updated_at);
"""

_UPDATABLE = frozenset({"status", "result", "error_code", "error_message"})


@dataclass
class Job:
    id: str
    url: str
    mode: str
    platform: str
    status: str  # queued | running | done | failed | expired
    result: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    created_at: float
    updated_at: float


class JobStore:
    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)

    def create(self, url: str, mode: str, platform: str) -> Job:
        job_id = secrets.token_urlsafe(9)
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs (id, url, mode, platform, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, 'queued', ?, ?)",
                (job_id, url, mode, platform, now, now),
            )
        return self.get(job_id)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["result"] = json.loads(data["result"]) if data["result"] else None
        return Job(**data)

    def update(self, job_id: str, **fields: Any) -> None:
        if unknown := set(fields) - _UPDATABLE:
            raise ValueError(f"不能更新欄位：{unknown}")
        if fields.get("result") is not None:
            fields["result"] = json.dumps(fields["result"], ensure_ascii=False)
        fields["updated_at"] = time.time()
        columns = ", ".join(f"{name} = ?" for name in fields)
        with self._lock:
            self._conn.execute(f"UPDATE jobs SET {columns} WHERE id = ?", (*fields.values(), job_id))

    def count_active(self) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'running')"
            ).fetchone()[0]

    def fail_active(self, error_code: str, error_message: str) -> int:
        """把還沒跑完的任務標成失敗（服務重啟時用）。"""
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE jobs SET status = 'failed', error_code = ?, error_message = ?, updated_at = ?"
                " WHERE status IN ('queued', 'running')",
                (error_code, error_message, time.time()),
            )
        return cursor.rowcount

    def finished_before(self, cutoff: float) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM jobs WHERE status IN ('done', 'failed') AND updated_at < ?",
                (cutoff,),
            ).fetchall()
        return [row[0] for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
