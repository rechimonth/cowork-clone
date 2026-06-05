from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp with a stable Z suffix."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class MetricRecord:
    name: str
    value: float
    unit: str
    timestamp: str
    tags: dict[str, Any]
    error: str | None = None
    fallback: bool = False


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_metrics_db(db_path: str) -> None:
    """Create the metrics table if it does not exist."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                name TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                error TEXT,
                fallback INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(name);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics(timestamp);")


def record_metric(
    db_path: str,
    name: str,
    value: float = 1.0,
    *,
    unit: str = "count",
    tags: dict[str, Any] | None = None,
    error: str | None = None,
    fallback: bool = False,
    timestamp: str | None = None,
) -> MetricRecord:
    """Persist a metric row and return the normalized record."""
    if not name.strip():
        raise ValueError("Metric name cannot be empty")

    init_metrics_db(db_path)
    record = MetricRecord(
        name=name.strip(),
        value=float(value),
        unit=unit,
        timestamp=timestamp or utc_now_iso(),
        tags=tags or {},
        error=error,
        fallback=bool(fallback),
    )

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO metrics (timestamp, name, value, unit, tags_json, error, fallback)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.timestamp,
                record.name,
                record.value,
                record.unit,
                json.dumps(record.tags, ensure_ascii=False, sort_keys=True),
                record.error,
                int(record.fallback),
            ),
        )

    return record


def record_execution_time(
    db_path: str,
    execution_id: str,
    duration_s: float,
    *,
    tags: dict[str, Any] | None = None,
) -> MetricRecord:
    metric_tags = {"execution_id": execution_id}
    if tags:
        metric_tags.update(tags)
    return record_metric(
        db_path,
        "execution_time",
        duration_s,
        unit="seconds",
        tags=metric_tags,
    )


def record_fallback(
    db_path: str,
    component: str,
    reason: str,
    *,
    tags: dict[str, Any] | None = None,
) -> MetricRecord:
    metric_tags = {"component": component, "reason": reason}
    if tags:
        metric_tags.update(tags)
    return record_metric(
        db_path,
        "fallback",
        1.0,
        tags=metric_tags,
        fallback=True,
    )


def record_error(
    db_path: str,
    component: str,
    error: str,
    *,
    tags: dict[str, Any] | None = None,
) -> MetricRecord:
    metric_tags = {"component": component}
    if tags:
        metric_tags.update(tags)
    return record_metric(
        db_path,
        "error",
        1.0,
        tags=metric_tags,
        error=error,
    )


def get_metrics_summary(db_path: str) -> dict[str, Any]:
    """Return aggregate metrics for dashboards and validations."""
    init_metrics_db(db_path)
    summary: dict[str, Any] = {
        "total": 0,
        "by_name": {},
        "fallbacks": 0,
        "errors": 0,
        "latest_timestamp": None,
        "execution_time": {"count": 0, "avg_seconds": None, "max_seconds": None},
    }

    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        summary["total"] = conn.execute("SELECT COUNT(*) AS c FROM metrics").fetchone()["c"]
        rows = conn.execute(
            """
            SELECT name, COUNT(*) AS count, SUM(value) AS total_value
            FROM metrics
            GROUP BY name
            ORDER BY name
            """
        ).fetchall()
        summary["by_name"] = {
            row["name"]: {"count": row["count"], "total_value": row["total_value"]}
            for row in rows
        }
        summary["fallbacks"] = conn.execute(
            "SELECT COUNT(*) AS c FROM metrics WHERE fallback = 1"
        ).fetchone()["c"]
        summary["errors"] = conn.execute(
            "SELECT COUNT(*) AS c FROM metrics WHERE error IS NOT NULL"
        ).fetchone()["c"]
        summary["latest_timestamp"] = conn.execute(
            "SELECT MAX(timestamp) AS ts FROM metrics"
        ).fetchone()["ts"]
        exec_row = conn.execute(
            """
            SELECT COUNT(*) AS count, AVG(value) AS avg_seconds, MAX(value) AS max_seconds
            FROM metrics
            WHERE name = 'execution_time'
            """
        ).fetchone()
        summary["execution_time"] = {
            "count": exec_row["count"],
            "avg_seconds": exec_row["avg_seconds"],
            "max_seconds": exec_row["max_seconds"],
        }

    return summary
