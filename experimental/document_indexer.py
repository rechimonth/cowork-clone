from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ai_engine import FileAnalysis, FileAnalysisProvider, OfflineFileAnalysisProvider
from file_manager import build_file_items, scan_directory


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class DocumentRecord:
    document_id: str
    path: str
    filename: str
    ext: str | None
    size_bytes: int | None
    modified_at: str | None
    indexed_at: str
    category: str
    suggested_name: str
    reason: str
    metadata: dict[str, Any]


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn


def init_documents_db(db_path: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id TEXT NOT NULL UNIQUE,
                path TEXT NOT NULL UNIQUE,
                filename TEXT NOT NULL,
                ext TEXT,
                size_bytes INTEGER,
                modified_at TEXT,
                indexed_at TEXT NOT NULL,
                category TEXT NOT NULL,
                suggested_name TEXT NOT NULL,
                reason TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_path ON documents(path);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_category ON documents(category);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_ext ON documents(ext);")


def _modified_at(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat().replace("+00:00", "Z")
    except OSError:
        return None


def _row_to_record(row: sqlite3.Row) -> DocumentRecord:
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except json.JSONDecodeError:
        metadata = {}
    return DocumentRecord(
        document_id=row["document_id"],
        path=row["path"],
        filename=row["filename"],
        ext=row["ext"],
        size_bytes=row["size_bytes"],
        modified_at=row["modified_at"],
        indexed_at=row["indexed_at"],
        category=row["category"],
        suggested_name=row["suggested_name"],
        reason=row["reason"],
        metadata=metadata,
    )


class DocumentIndexer:
    """Indexes local documents into SQLite without executing file actions."""

    def __init__(
        self,
        db_path: str,
        *,
        provider: FileAnalysisProvider | None = None,
    ):
        self.db_path = db_path
        self.provider = provider or OfflineFileAnalysisProvider()
        init_documents_db(db_path)

    def _classify(self, filename: str, preview_text: str | None, ext: str | None) -> FileAnalysis:
        try:
            return self.provider.classify_document(filename, preview_text, ext)
        except Exception:
            return OfflineFileAnalysisProvider().classify_document(filename, preview_text, ext)

    def index_document(self, path: str, *, root_dir: str | None = None) -> DocumentRecord:
        p = Path(path).resolve()
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"No se pudo indexar: {path}")

        root = Path(root_dir).resolve() if root_dir else p.parent.resolve()
        item = build_file_items(str(root), [p])[0]
        analysis = self._classify(item.filename, item.preview_text, item.ext)
        existing_id = self._existing_document_id(item.path)

        metadata = {
            "relpath": item.relpath,
            "preview_available": bool(item.preview_text),
            "preview_length": len(item.preview_text or ""),
        }
        record = DocumentRecord(
            document_id=existing_id or str(uuid4()),
            path=item.path,
            filename=item.filename,
            ext=item.ext,
            size_bytes=item.size_bytes,
            modified_at=_modified_at(p),
            indexed_at=utc_now_iso(),
            category=analysis.category,
            suggested_name=analysis.suggested_name,
            reason=analysis.reason,
            metadata=metadata,
        )
        self._persist(record)
        return record

    def _existing_document_id(self, path: str) -> str | None:
        with _connect(self.db_path) as conn:
            row = conn.execute("SELECT document_id FROM documents WHERE path = ?", (path,)).fetchone()
        return row["document_id"] if row else None

    def _persist(self, record: DocumentRecord) -> None:
        with _connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO documents (
                    document_id, path, filename, ext, size_bytes, modified_at, indexed_at,
                    category, suggested_name, reason, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    filename = excluded.filename,
                    ext = excluded.ext,
                    size_bytes = excluded.size_bytes,
                    modified_at = excluded.modified_at,
                    indexed_at = excluded.indexed_at,
                    category = excluded.category,
                    suggested_name = excluded.suggested_name,
                    reason = excluded.reason,
                    metadata_json = excluded.metadata_json
                """,
                (
                    record.document_id,
                    record.path,
                    record.filename,
                    record.ext,
                    record.size_bytes,
                    record.modified_at,
                    record.indexed_at,
                    record.category,
                    record.suggested_name,
                    record.reason,
                    json.dumps(record.metadata, ensure_ascii=False, sort_keys=True),
                ),
            )

    def index_directory(self, root_dir: str, *, recursive: bool = True) -> list[DocumentRecord]:
        root = Path(root_dir).resolve()
        paths = scan_directory(str(root), recursive=recursive)
        records: list[DocumentRecord] = []
        for path in paths:
            try:
                records.append(self.index_document(str(path), root_dir=str(root)))
            except Exception:
                continue
        return records

    def get_document(self, path: str) -> DocumentRecord | None:
        p = str(Path(path).resolve())
        with _connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM documents WHERE path = ?", (p,)).fetchone()
        return _row_to_record(row) if row else None

    def search_documents(self, query: str, *, limit: int = 20) -> list[DocumentRecord]:
        like = f"%{query}%"
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM documents
                WHERE filename LIKE ?
                   OR path LIKE ?
                   OR category LIKE ?
                   OR reason LIKE ?
                   OR metadata_json LIKE ?
                ORDER BY indexed_at DESC, id DESC
                LIMIT ?
                """,
                (like, like, like, like, like, max(0, limit)),
            ).fetchall()
        return [_row_to_record(row) for row in rows]


def index_directory(
    db_path: str,
    root_dir: str,
    *,
    recursive: bool = True,
    provider: FileAnalysisProvider | None = None,
) -> list[DocumentRecord]:
    return DocumentIndexer(db_path, provider=provider).index_directory(root_dir, recursive=recursive)


def search_documents(db_path: str, query: str, *, limit: int = 20) -> list[DocumentRecord]:
    return DocumentIndexer(db_path).search_documents(query, limit=limit)
