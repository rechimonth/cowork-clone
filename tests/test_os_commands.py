import pathlib

import pytest

from audit_logger import AuditLogger
from models import CreateDirAction, ExecutionPlan, RenameAction
from os_commands import _ensure_within_root, _find_unique_path, execute_plan


def test_ensure_within_root_accepts_inside_paths(tmp_path):
    (tmp_path / "sub").mkdir()
    _ensure_within_root(tmp_path, tmp_path / "sub" / "a.txt")
    _ensure_within_root(tmp_path, tmp_path)


def test_ensure_within_root_rejects_parent_traversal(tmp_path):
    outside = tmp_path.parent / "fuera.txt"
    with pytest.raises(ValueError):
        _ensure_within_root(tmp_path, tmp_path / ".." / "fuera.txt")
    with pytest.raises(ValueError):
        _ensure_within_root(tmp_path, outside)


def test_ensure_within_root_rejects_sibling_prefix(tmp_path):
    # /root_evil comparte prefijo textual con /root pero no es un descendiente.
    root = tmp_path / "root"
    root.mkdir()
    evil = tmp_path / "root_evil"
    evil.mkdir()
    with pytest.raises(ValueError):
        _ensure_within_root(root, evil / "x.txt")


def test_find_unique_path_returns_same_when_free(tmp_path):
    dst = tmp_path / "libre.txt"
    assert _find_unique_path(dst) == dst


def test_find_unique_path_appends_counter_preserving_extension(tmp_path):
    dst = tmp_path / "archivo.txt"
    dst.write_text("a")
    assert _find_unique_path(dst).name == "archivo_1.txt"

    (tmp_path / "archivo_1.txt").write_text("b")
    assert _find_unique_path(dst).name == "archivo_2.txt"


def test_find_unique_path_handles_many_collisions(tmp_path):
    dst = tmp_path / "a.txt"
    dst.write_text("x")
    for i in range(1, 50):
        (tmp_path / f"a_{i}.txt").write_text("x")
    # base + a_1..a_49 ocupados -> el primero libre es a_50.txt
    assert _find_unique_path(dst).name == "a_50.txt"


def test_find_unique_path_without_extension(tmp_path):
    dst = tmp_path / "sin_ext"
    dst.write_text("a")
    assert _find_unique_path(dst).name == "sin_ext_1"


def test_execute_plan_resolves_collision_and_logs(tmp_path):
    src = tmp_path / "origen.txt"
    src.write_text("nuevo")
    existing = tmp_path / "PDFs" / "destino.txt"
    existing.parent.mkdir()
    existing.write_text("viejo")

    log_path = tmp_path / "audit.jsonl"
    logger = AuditLogger(str(log_path))

    plan = ExecutionPlan(
        summary="test",
        create_dirs=[CreateDirAction(dir_path=str(existing.parent))],
        rename_files=[RenameAction(src=str(src), dst=str(existing))],
    )

    applied = execute_plan(plan, str(tmp_path), logger=logger)

    final_dst = pathlib.Path(applied[0][1])
    assert final_dst.name == "destino_1.txt"
    assert final_dst.read_text() == "nuevo"
    # El archivo preexistente NO se pierde.
    assert existing.read_text() == "viejo"
    assert "RENAME_COLLISION_RESOLVED" in log_path.read_text()


def test_execute_plan_rejects_unsafe_operation(tmp_path):
    plan = ExecutionPlan(summary="x")
    plan.create_dirs.append(
        CreateDirAction(dir_path=str(tmp_path / "d"), reason=None)
    )
    plan.rename_files.append(
        RenameAction(src=str(tmp_path / "a"), dst=str(tmp_path / "b"))
    )
    # Se fuerza un tipo no permitido para verificar la whitelist.
    plan.rename_files[0].type = "delete"
    with pytest.raises(ValueError):
        execute_plan(plan, str(tmp_path))
