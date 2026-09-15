from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass
from typing import Iterable

from pypdf import PdfReader
from pypdf.errors import PdfReadError, PdfStreamError

from audit_logger import AuditLogger
from models import FileItem


TEXT_EXTS = {".txt", ".md", ".csv", ".log"}
PDF_EXTS = {".pdf"}

# Máximo de caracteres de preview para archivos de texto: suficiente para que el
# LLM clasifique el documento sin enviar el archivo completo al modelo.
TEXT_PREVIEW_MAX_CHARS = 1200

# Máximo de caracteres de preview para PDFs. Los PDFs son más verbosos por
# página, así que se permite un preview algo mayor que en texto plano.
PDF_PREVIEW_MAX_CHARS = 2000

# Solo se procesan las primeras páginas del PDF para acotar el costo de
# extracción de texto (los PDFs largos dominan el tiempo de escaneo).
PDF_PREVIEW_MAX_PAGES = 3


def _log_file_error(
    logger: AuditLogger | None,
    event_type: str,
    path: pathlib.Path,
    exc: BaseException,
) -> None:
    """Registra un error de lectura sin volcar el contenido del archivo.

    Solo se guarda la ruta y la representación de la excepción; nunca el texto
    leído ni potenciales datos sensibles que contenga el documento.
    """
    if not logger:
        return
    logger.log_event(
        event_type,
        {"path": str(path), "exception": f"{type(exc).__name__}: {exc}"},
    )


def _safe_read_text(
    path: pathlib.Path,
    max_chars: int = TEXT_PREVIEW_MAX_CHARS,
    logger: AuditLogger | None = None,
) -> str | None:
    try:
        # Intentamos UTF-8 primero
        data = path.read_text(encoding="utf-8", errors="ignore")
        data = data.strip()
        return data[:max_chars] if data else None
    except (IOError, OSError, UnicodeDecodeError) as exc:
        _log_file_error(logger, "FILE_READ_ERROR", path, exc)
        return None


def _read_pdf_preview(
    path: pathlib.Path,
    max_chars: int = PDF_PREVIEW_MAX_CHARS,
    logger: AuditLogger | None = None,
) -> str | None:
    try:
        reader = PdfReader(str(path))
        texts: list[str] = []
        for i, page in enumerate(reader.pages):
            if i >= PDF_PREVIEW_MAX_PAGES:  # preview limitado para performance
                break
            try:
                t = page.extract_text() or ""
            except (PdfReadError, PdfStreamError, KeyError, ValueError) as exc:
                # Una página corrupta no debe descartar el resto del PDF.
                _log_file_error(logger, "PDF_PAGE_EXTRACT_ERROR", path, exc)
                t = ""
            t = t.strip()
            if t:
                texts.append(t)
            joined = "\n".join(texts)
            if len(joined) >= max_chars:
                break
        joined = "\n".join(texts).strip()
        return joined[:max_chars] if joined else None
    except (IOError, OSError, PdfReadError, PdfStreamError, ValueError) as exc:
        _log_file_error(logger, "PDF_READ_ERROR", path, exc)
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


def build_file_items(
    root_dir: str,
    paths: Iterable[pathlib.Path],
    logger: AuditLogger | None = None,
) -> list[FileItem]:
    root = pathlib.Path(root_dir).resolve()
    items: list[FileItem] = []

    for p in paths:
        ext = p.suffix.lower()
        rel = None
        try:
            rel = str(p.resolve().relative_to(root))
        except ValueError:
            # El archivo no está bajo el root (p. ej. symlink que escapa).
            rel = None

        preview = None
        if ext in TEXT_EXTS:
            preview = _safe_read_text(p, logger=logger)
        elif ext in PDF_EXTS:
            preview = _read_pdf_preview(p, logger=logger)

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

