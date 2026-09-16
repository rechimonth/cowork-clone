"""Tests de robustez del orquestador, rollback y parseo de fechas.

Cubren los caminos de fallo/limite que no ejercitaban los tests existentes:
rollback de cadenas de renombres y de mkdir no vacio, transacciones no
iniciadas, y extraccion de fechas del nombre de archivo (entrada del planner).
"""

from __future__ import annotations

import pytest

from ai_engine import _extract_date_from_filename, build_prompt
from models import FileItem, PlannerInput
from transaction_manager import TransactionManager


def test_record_action_requires_begin_transaction(tmp_path):
    tm = TransactionManager(root_dir=str(tmp_path))

    with pytest.raises(RuntimeError):
        tm.record_action(action_type="mkdir", source_path=None, target_path="x")


def test_commit_discards_recorded_actions(tmp_path):
    created = tmp_path / "dir"
    tm = TransactionManager(root_dir=str(tmp_path))
    tm.begin_transaction()
    tm.record_action(action_type="mkdir", source_path=None, target_path=str(created))
    created.mkdir()

    tm.commit()
    tm.rollback()

    # Tras commit no hay nada que revertir: el directorio permanece.
    assert created.exists()


def test_rollback_skips_non_empty_directory(tmp_path):
    non_empty = tmp_path / "lleno"
    non_empty.mkdir()
    (non_empty / "dato.txt").write_text("importante")

    tm = TransactionManager(root_dir=str(tmp_path))
    tm.begin_transaction()
    tm.record_action(action_type="mkdir", source_path=None, target_path=str(non_empty))

    tm.rollback()

    # Rollback no es destructivo: nunca borra contenido del usuario.
    assert (non_empty / "dato.txt").read_text() == "importante"


def test_rollback_of_rename_is_skipped_on_collision(tmp_path):
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("origen")
    dst.write_text("destino")

    tm = TransactionManager(root_dir=str(tmp_path))
    tm.begin_transaction()
    # Estado inconsistente: ambos existen, por lo que revertir pisaria `dst`.
    tm.record_action(action_type="rename", source_path=str(src), target_path=str(dst))

    tm.rollback()

    assert dst.read_text() == "destino"
    assert src.read_text() == "origen"


def test_rollback_ignores_action_without_paths(tmp_path):
    tm = TransactionManager(root_dir=str(tmp_path))
    tm.begin_transaction()
    tm.record_action(action_type="rename", source_path=None, target_path=None)

    tm.rollback()

    assert tm._active is False


def test_rollback_when_not_active_is_noop(tmp_path):
    tm = TransactionManager(root_dir=str(tmp_path))
    tm.rollback()  # no debe lanzar

    assert tm._active is False


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("2026-01-15 factura.pdf", "20260115"),
        ("2026_01_15_factura.pdf", "20260115"),
        ("factura20260115.pdf", "20260115"),
        ("factura sin fecha.pdf", None),
    ],
)
def test_extract_date_from_filename(filename, expected):
    assert _extract_date_from_filename(filename) == expected


def test_build_prompt_truncates_long_previews():
    item = FileItem(
        path="/root/x.pdf",
        filename="x.pdf",
        ext=".pdf",
        preview_text="A" * 5000,
    )
    prompt = build_prompt(PlannerInput(root_dir="/root", files=[item]))

    # El preview se recorta antes de entrar al prompt (control de tokens).
    assert "A" * 5000 not in prompt
    assert "ROOT: /root" in prompt


def test_build_prompt_handles_missing_preview():
    item = FileItem(path="/root/y.csv", filename="y.csv", ext=".csv")
    prompt = build_prompt(PlannerInput(root_dir="/root", files=[item]))

    assert "'preview_text': None" in prompt
