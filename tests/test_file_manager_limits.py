"""Tests de los limites de escaneo y del manejo de errores de lectura.

Cubren los casos de error explicitos agregados en file_manager.py: limites de
cantidad de archivos, previews truncados y degradacion ante archivos ilegibles
o PDFs corruptos.
"""

from __future__ import annotations

import pathlib

import pytest

from audit_logger import AuditLogger
from file_manager import (
    MAX_FILES_PER_SCAN,
    PDF_PREVIEW_MAX_CHARS,
    PDF_PREVIEW_MAX_PAGES,
    TEXT_PREVIEW_MAX_CHARS,
    _read_pdf_preview,
    _safe_read_text,
    build_file_items,
    scan_directory,
)


def _read_events(log_path: pathlib.Path) -> list[dict]:
    import json

    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


def test_scan_directory_raises_when_root_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        scan_directory(str(tmp_path / "no_existe"))


def test_scan_directory_non_recursive_skips_subdirs(tmp_path):
    (tmp_path / "top.txt").write_text("x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.txt").write_text("y")

    recursive = scan_directory(str(tmp_path), recursive=True)
    flat = scan_directory(str(tmp_path), recursive=False)

    assert {p.name for p in recursive} == {"top.txt", "deep.txt"}
    assert {p.name for p in flat} == {"top.txt"}


def test_scan_directory_truncates_at_max_files_and_logs(tmp_path):
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text("x")

    log_path = tmp_path / "audit.jsonl"
    logger = AuditLogger(str(log_path))

    paths = scan_directory(str(tmp_path), logger=logger, max_files=4)

    assert len(paths) == 4
    events = _read_events(log_path)
    assert [e["event_type"] for e in events] == ["SCAN_LIMIT_REACHED"]
    assert events[0]["payload"]["max_files"] == 4


def test_scan_directory_does_not_log_when_under_limit(tmp_path):
    (tmp_path / "only.txt").write_text("x")
    log_path = tmp_path / "audit.jsonl"
    logger = AuditLogger(str(log_path))

    paths = scan_directory(str(tmp_path), logger=logger, max_files=MAX_FILES_PER_SCAN)

    assert len(paths) == 1
    assert not log_path.exists() or "SCAN_LIMIT_REACHED" not in log_path.read_text()


def test_safe_read_text_truncates_to_limit(tmp_path):
    path = tmp_path / "grande.txt"
    path.write_text("a" * (TEXT_PREVIEW_MAX_CHARS + 500))

    assert len(_safe_read_text(path)) == TEXT_PREVIEW_MAX_CHARS


def test_safe_read_text_returns_none_for_empty_file(tmp_path):
    path = tmp_path / "vacio.txt"
    path.write_text("   \n\n  ")

    assert _safe_read_text(path) is None


def test_safe_read_text_logs_and_returns_none_for_directory(tmp_path):
    # Leer un directorio como archivo de texto es un error de I/O real.
    target = tmp_path / "soy_directorio"
    target.mkdir()
    log_path = tmp_path / "audit.jsonl"
    logger = AuditLogger(str(log_path))

    assert _safe_read_text(target, logger=logger) is None

    events = _read_events(log_path)
    assert events[0]["event_type"] == "FILE_READ_ERROR"
    assert events[0]["payload"]["path"] == str(target)


def test_safe_read_text_error_log_does_not_leak_content(tmp_path):
    target = tmp_path / "secreto"
    target.mkdir()
    log_path = tmp_path / "audit.jsonl"
    logger = AuditLogger(str(log_path))

    _safe_read_text(target, logger=logger)

    raw = log_path.read_text()
    assert "exception" in raw
    # Solo se registra ruta + tipo de excepcion, nunca contenido leido.
    assert "TEXT_PREVIEW" not in raw


def test_read_pdf_preview_handles_corrupt_pdf(tmp_path):
    bad = tmp_path / "roto.pdf"
    bad.write_bytes(b"no soy un pdf")
    log_path = tmp_path / "audit.jsonl"
    logger = AuditLogger(str(log_path))

    assert _read_pdf_preview(bad, logger=logger) is None

    events = _read_events(log_path)
    assert events[0]["event_type"] == "PDF_READ_ERROR"


def test_build_file_items_keeps_going_after_unreadable_file(tmp_path):
    ok = tmp_path / "ok.txt"
    ok.write_text("contenido")
    unreadable = tmp_path / "ilegible.txt"
    unreadable.mkdir()  # un directorio con extension .txt fuerza el error de lectura

    log_path = tmp_path / "audit.jsonl"
    logger = AuditLogger(str(log_path))
    items = build_file_items(str(tmp_path), [ok, unreadable], logger=logger)

    assert len(items) == 2
    by_name = {i.filename: i for i in items}
    assert by_name["ok.txt"].preview_text == "contenido"
    assert by_name["ilegible.txt"].preview_text is None
    assert "FILE_READ_ERROR" in log_path.read_text()


def test_build_file_items_relpath_none_outside_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "afuera.txt"
    outside.write_text("x")

    items = build_file_items(str(root), [outside])

    assert items[0].relpath is None


def test_pdf_preview_constants_are_sane():
    # Guarda de regresion: los limites existen y son positivos/razonables.
    assert PDF_PREVIEW_MAX_PAGES == 3
    assert PDF_PREVIEW_MAX_CHARS == 2000
    assert TEXT_PREVIEW_MAX_CHARS == 1200
    assert MAX_FILES_PER_SCAN >= 1000
