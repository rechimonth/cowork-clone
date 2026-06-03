from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass
from typing import Iterable

from pypdf import PdfReader

from models import FileItem


TEXT_EXTS = {".txt", ".md", ".csv", ".log"}
PDF_EXTS = {".pdf"}


def _safe_read_text(path: pathlib.Path, max_chars: int = 1200) -> str | None:
    try:
        # Intentamos UTF-8 primero
        data = path.read_text(encoding="utf-8", errors="ignore")
        data = data.strip()
        return data[:max_chars] if data else None
    except Exception:
        return None


def _read_pdf_preview(path: pathlib.Path, max_chars: int = 2000) -> str | None:
    try:
        reader = PdfReader(str(path))
        texts: list[str] = []
        for i, page in enumerate(reader.pages):
            if i >= 3:  # preview limitado para performance
                break
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            t = t.strip()
            if t:
                texts.append(t)
            joined = "\n".join(texts)
            if len(joined) >= max_chars:
                break
        joined = "\n".join(texts).strip()
        return joined[:max_chars] if joined else None
    except Exception:
        return None


def scan_directory(root_dir: str, recursive: bool = True) -> list[pathlib.Path]:
    root = pathlib.Path(root_dir)
    if not root.exists():
        raise FileNotFoundError(f"No existe el directorio: {root_dir}")

    paths: list[pathlib.Path] = []
    if recursive:
        for p in root.rglob("*"):
            if p.is_file():
                paths.append(p)
    else:
        for p in root.glob("*"):
            if p.is_file():
                paths.append(p)
    return paths


def build_file_items(root_dir: str, paths: Iterable[pathlib.Path]) -> list[FileItem]:
    root = pathlib.Path(root_dir).resolve()
    items: list[FileItem] = []

    for p in paths:
        ext = p.suffix.lower()
        rel = None
        try:
            rel = str(p.resolve().relative_to(root))
        except Exception:
            rel = None

        preview = None
        if ext in TEXT_EXTS:
            preview = _safe_read_text(p)
        elif ext in PDF_EXTS:
            preview = _read_pdf_preview(p)

        items.append(
            FileItem(
                path=str(p.resolve()),
                filename=p.name,
                relpath=rel,
                ext=ext,
                size_bytes=p.stat().st_size if p.exists() else None,
                preview_text=preview,
            )
        )

    return items

