from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StorageConfig:
    db_path: str


class StorageManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id TEXT,
                    created_at TEXT,
                    status TEXT
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id TEXT,
                    execution_id TEXT,
                    action_type TEXT,
                    source_path TEXT,
                    target_path TEXT,
                    status TEXT
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS executions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    execution_id TEXT,
                    plan_id TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    status TEXT
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    execution_id TEXT,
                    timestamp TEXT,
                    error_message TEXT
                );
                """
            )

    def insert_plan(self, *, plan_id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO plans (plan_id, created_at, status) VALUES (?, ?, ?)",
                (plan_id, _utc_now_iso(), status),
            )

    def insert_execution(
        self,
        *,
        execution_id: str,
        plan_id: str,
        status: str,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO executions (execution_id, plan_id, started_at, finished_at, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    execution_id,
                    plan_id,
                    started_at or _utc_now_iso(),
                    finished_at,
                    status,
                ),
            )

    def insert_action(
        self,
        *,
        plan_id: str,
        execution_id: str,
        action_type: str,
        source_path: str | None,
        target_path: str | None,
        status: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO actions (plan_id, execution_id, action_type, source_path, target_path, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (plan_id, execution_id, action_type, source_path, target_path, status),
            )

    def insert_error(
        self,
        *,
        execution_id: str,
        error_message: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO errors (execution_id, timestamp, error_message)
                VALUES (?, ?, ?)
                """,
                (execution_id, _utc_now_iso(), error_message),
            )

    def mark_execution_finished(
        self, *, execution_id: str, status: str, finished_at: str | None = None
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE executions
                SET status = ?, finished_at = COALESCE(?, finished_at)
                WHERE execution_id = ?
                """,
                (status, finished_at or _utc_now_iso(), execution_id),
            )

