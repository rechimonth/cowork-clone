from __future__ import annotations

import json
import os
import sqlite3
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class MemoryItem:
    key: str
    value: str
    kind: str
    tags: dict[str, Any]
    created_at: str
    last_accessed_at: str


class MemoryProvider(Protocol):
    """Read/write contextual memory provider.

    Memory only supplies context. It never executes filesystem or browser actions.
    """

    def remember(
        self,
        key: str,
        value: str,
        *,
        kind: str = "generic",
        tags: dict[str, Any] | None = None,
    ) -> MemoryItem:
        ...

    def recall(self, key: str) -> MemoryItem | None:
        ...

    def search(self, query: str, *, limit: int = 10) -> list[MemoryItem]:
        ...

    def forget(self, key: str) -> bool:
        ...

    def clear(self) -> None:
        ...


class ShortTermMemory:
    def __init__(self, max_items: int = 100):
        self.max_items = max(1, int(max_items))
        self._items: OrderedDict[str, MemoryItem] = OrderedDict()

    def remember(
        self,
        key: str,
        value: str,
        *,
        kind: str = "generic",
        tags: dict[str, Any] | None = None,
    ) -> MemoryItem:
        if not key:
            raise ValueError("Memory key cannot be empty")
        now = utc_now_iso()
        existing = self._items.get(key)
        item = MemoryItem(
            key=key,
            value=value,
            kind=kind,
            tags=tags or {},
            created_at=existing.created_at if existing else now,
            last_accessed_at=now,
        )
        self._items.pop(key, None)
        self._items[key] = item
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)
        return item

    def recall(self, key: str) -> MemoryItem | None:
        item = self._items.get(key)
        if item is None:
            return None
        refreshed = MemoryItem(
            key=item.key,
            value=item.value,
            kind=item.kind,
            tags=item.tags,
            created_at=item.created_at,
            last_accessed_at=utc_now_iso(),
        )
        self._items.pop(key, None)
        self._items[key] = refreshed
        return refreshed

    def search(self, query: str, *, limit: int = 10) -> list[MemoryItem]:
        q = query.lower()
        matches = [
            item
            for item in reversed(self._items.values())
            if q in item.key.lower()
            or q in item.value.lower()
            or q in item.kind.lower()
            or q in json.dumps(item.tags, ensure_ascii=False).lower()
        ]
        return matches[: max(0, limit)]

    def forget(self, key: str) -> bool:
        existed = key in self._items
        self._items.pop(key, None)
        return existed

    def clear(self) -> None:
        self._items.clear()


class LongTermMemoryAdapter:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL UNIQUE,
                    value TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_accessed_at TEXT NOT NULL
                );
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_kind ON memory_items(kind);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_key ON memory_items(key);")

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> MemoryItem:
        try:
            tags = json.loads(row["tags_json"] or "{}")
        except json.JSONDecodeError:
            tags = {}
        return MemoryItem(
            key=row["key"],
            value=row["value"],
            kind=row["kind"],
            tags=tags,
            created_at=row["created_at"],
            last_accessed_at=row["last_accessed_at"],
        )

    def remember(
        self,
        key: str,
        value: str,
        *,
        kind: str = "generic",
        tags: dict[str, Any] | None = None,
    ) -> MemoryItem:
        if not key:
            raise ValueError("Memory key cannot be empty")
        now = utc_now_iso()
        tags_json = json.dumps(tags or {}, ensure_ascii=False, sort_keys=True)

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM memory_items WHERE key = ?",
                (key,),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO memory_items (key, value, kind, tags_json, created_at, last_accessed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    kind = excluded.kind,
                    tags_json = excluded.tags_json,
                    last_accessed_at = excluded.last_accessed_at
                """,
                (key, value, kind, tags_json, created_at, now),
            )

        return MemoryItem(key, value, kind, tags or {}, created_at, now)

    def recall(self, key: str) -> MemoryItem | None:
        now = utc_now_iso()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM memory_items WHERE key = ?", (key,)).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE memory_items SET last_accessed_at = ? WHERE key = ?",
                (now, key),
            )
        item = self._row_to_item(row)
        return MemoryItem(item.key, item.value, item.kind, item.tags, item.created_at, now)

    def search(self, query: str, *, limit: int = 10) -> list[MemoryItem]:
        like = f"%{query}%"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM memory_items
                WHERE key LIKE ? OR value LIKE ? OR kind LIKE ? OR tags_json LIKE ?
                ORDER BY last_accessed_at DESC, id DESC
                LIMIT ?
                """,
                (like, like, like, like, max(0, limit)),
            ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def forget(self, key: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM memory_items WHERE key = ?", (key,))
            return cur.rowcount > 0

    def clear(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM memory_items")
