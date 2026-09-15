"""Tests del sandbox reproducible y de la extraccion de previews PDF reales.

`experimental/sandbox_builder.py` genera un PDF minimo valido sin dependencias
externas; se reutiliza aqui para ejercitar el camino real de `pypdf` en
`file_manager._read_pdf_preview` en lugar de simularlo.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from experimental import sandbox_builder
from file_manager import _read_pdf_preview, scan_directory


def test_build_sandbox_creates_expected_tree(tmp_path):
    paths = sandbox_builder.build_sandbox(str(tmp_path / "sandbox"))

    assert len(paths) == 5
    assert all(p.exists() for p in paths)
    assert (tmp_path / "sandbox" / "nested/level1/level2/resumen.txt").is_file()
    assert (tmp_path / "sandbox" / "pdf/factura_2026-01-15.pdf").is_file()


def test_build_sandbox_is_idempotent_and_non_destructive(tmp_path):
    base = str(tmp_path / "sandbox")
    sandbox_builder.build_sandbox(base)

    target = tmp_path / "sandbox" / "txt" / "notas.txt"
    target.write_text("EDITADO POR EL USUARIO")

    sandbox_builder.build_sandbox(base)

    # Un segundo build no debe pisar archivos existentes.
    assert target.read_text() == "EDITADO POR EL USUARIO"


def test_read_pdf_preview_extracts_real_text(tmp_path):
    paths = sandbox_builder.build_sandbox(str(tmp_path / "sandbox"))
    pdf = next(p for p in paths if p.suffix == ".pdf")

    preview = _read_pdf_preview(pdf)

    assert preview is not None
    assert "Factura demo sandbox" in preview


def test_read_pdf_preview_respects_max_pages(tmp_path):
    paths = sandbox_builder.build_sandbox(str(tmp_path / "sandbox"))
    pdf = next(p for p in paths if p.suffix == ".pdf")

    # max_chars muy bajo fuerza el corte por longitud.
    preview = _read_pdf_preview(pdf, max_chars=10)

    assert preview is not None
    assert len(preview) <= 10


def test_scan_directory_finds_all_sandbox_files(tmp_path):
    base = tmp_path / "sandbox"
    sandbox_builder.build_sandbox(str(base))

    found = scan_directory(str(base), recursive=True)

    assert {p.name for p in found} == {
        "notas.txt",
        "datos.csv",
        "README.md",
        "factura_2026-01-15.pdf",
        "resumen.txt",
    }


def test_scan_directory_on_sandbox_pdf_is_readable(tmp_path):
    base = tmp_path / "sandbox"
    sandbox_builder.build_sandbox(str(base))

    pdf = next(p for p in scan_directory(str(base), recursive=True) if p.suffix == ".pdf")

    assert _read_pdf_preview(pdf) is not None


def test_sandbox_builder_writes_bytes_for_pdf(tmp_path):
    paths = sandbox_builder.build_sandbox(str(tmp_path / "sb"))
    pdf = next(p for p in paths if p.suffix == ".pdf")

    assert pdf.read_bytes().startswith(b"%PDF")


@pytest.mark.parametrize("relative", ["txt/notas.txt", "csv/datos.csv", "md/README.md"])
def test_sandbox_text_fixtures_are_utf8(tmp_path, relative):
    base = tmp_path / "sb"
    sandbox_builder.build_sandbox(str(base))

    # No debe lanzar UnicodeDecodeError: los fixtures son UTF-8.
    (base / relative).read_text(encoding="utf-8")
