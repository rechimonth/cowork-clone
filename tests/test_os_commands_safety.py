"""Tests de path traversal, colisiones de nombres y limites del sandbox.

`os_commands.py` es la unica capa autorizada a tocar el filesystem, asi que
concentra el riesgo real del agente. Estos tests verifican las dos defensas
clave: el confinamiento al ROOT y la resolucion segura de colisiones.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from experimental import browser_agent
from models import CreateDirAction, ExecutionPlan, RenameAction
from os_commands import (
    MAX_UNIQUE_PATH_ATTEMPTS,
    _ensure_within_root,
    _find_unique_path,
    execute_plan,
)

# ------------------------- defensa contra path traversal ----------------------


@pytest.mark.parametrize(
    "evil",
    [
        "../fuera.txt",
        "../../etc/passwd",
        "sub/../../escape.txt",
        "/etc/shadow",
        "/etc/passwd",
    ],
)
def test_ensure_within_root_rejects_traversal(tmp_path, evil):
    root = tmp_path / "root"
    root.mkdir()

    with pytest.raises((ValueError, PermissionError)):
        _ensure_within_root(root, root / evil)


def test_ensure_within_root_allows_nested_path(tmp_path):
    root = tmp_path / "root"
    nested = root / "a" / "b"
    nested.mkdir(parents=True)

    # No debe lanzar para un path legitimo dentro del root.
    _ensure_within_root(root, nested / "archivo.txt")


def test_ensure_within_root_rejects_symlink_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "afuera"
    outside.mkdir()
    link = root / "atajo"
    link.symlink_to(outside)

    # El symlink resuelve fuera del root y debe ser bloqueado.
    with pytest.raises((ValueError, PermissionError)):
        _ensure_within_root(root, link / "archivo.txt")


def test_execute_plan_aborts_before_touching_fs_on_traversal(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "origen.txt").write_text("dato")
    plan = ExecutionPlan(
        summary="intento malicioso de escape",
        rename_files=[
            RenameAction(
                type="rename",
                src=str(root / "origen.txt"),
                dst=str(root / ".." / "escape.txt"),
                reason="intento malicioso",
            )
        ],
    )

    with pytest.raises((ValueError, PermissionError)):
        execute_plan(plan, root_dir=str(root))

    # Ningun archivo fue creado ni movido fuera del root.
    assert not (tmp_path / "escape.txt").exists()
    assert (root / "origen.txt").exists()


# --------------------------- colisiones de nombres ----------------------------


def test_find_unique_path_returns_original_when_free(tmp_path):
    dst = tmp_path / "libre.txt"

    assert _find_unique_path(dst) == dst


def test_find_unique_path_appends_counter(tmp_path):
    dst = tmp_path / "archivo.txt"
    dst.write_text("ocupado")

    assert _find_unique_path(dst) == tmp_path / "archivo_1.txt"


def test_find_unique_path_increments_until_free(tmp_path):
    dst = tmp_path / "archivo.txt"
    dst.write_text("0")
    (tmp_path / "archivo_1.txt").write_text("1")
    (tmp_path / "archivo_2.txt").write_text("2")

    assert _find_unique_path(dst) == tmp_path / "archivo_3.txt"


def test_find_unique_path_preserves_extension(tmp_path):
    dst = tmp_path / "informe.pdf"
    dst.write_bytes(b"%PDF-1.4")
    (tmp_path / "informe_1.pdf").write_bytes(b"%PDF-1.4")

    result = _find_unique_path(dst)

    assert result.suffix == ".pdf"
    assert result.name == "informe_2.pdf"


def test_find_unique_path_raises_after_max_attempts(tmp_path):
    dst = tmp_path / "archivo.txt"
    dst.write_text("0")
    for i in range(1, MAX_UNIQUE_PATH_ATTEMPTS + 1):
        (tmp_path / f"archivo_{i}.txt").write_text("x")

    with pytest.raises(FileExistsError):
        _find_unique_path(dst)


def test_rename_collision_does_not_lose_any_file(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    src = root / "origen.txt"
    dst = root / "destino.txt"
    src.write_text("CONTENIDO_ORIGEN")
    dst.write_text("CONTENIDO_DESTINO")

    plan = ExecutionPlan(
        summary="renombre con colision",
        rename_files=[RenameAction(type="rename", src=str(src), dst=str(dst), reason="colision")],
    )

    execute_plan(plan, root_dir=str(root))

    # El destino original se preserva intacto y el origen llega a un nombre unico.
    assert dst.read_text() == "CONTENIDO_DESTINO"
    assert not src.exists()
    assert (root / "destino_1.txt").read_text() == "CONTENIDO_ORIGEN"


# ------------------------------ sandbox de URLs ------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://ejemplo.com/datos",
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "gopher://interno/",
    ],
)
def test_browser_agent_rejects_non_http_schemes(url):
    with pytest.raises(browser_agent.BrowserSafetyError):
        browser_agent.assert_safe_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://sitio.com/login",
        "https://sitio.com/checkout",
        "https://sitio.com/payment/confirm",
        "https://sitio.com/account/settings",
        "https://banco.com/auth/token",
    ],
)
def test_browser_agent_blocks_sensitive_paths(url):
    with pytest.raises(browser_agent.BrowserSafetyError):
        browser_agent.assert_safe_url(url)


@pytest.mark.parametrize(
    "url",
    ["https://docs.python.org/3/", "http://localhost:8000/api"],
)
def test_browser_agent_allows_plain_reading_urls(url):
    # No debe lanzar: son URLs de lectura publica.
    browser_agent.assert_safe_url(url)


# ------------------------------ directorios ----------------------------------


def test_execute_plan_creates_directory(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    plan = ExecutionPlan(
        summary="crear carpeta de categoria",
        create_dirs=[
            CreateDirAction(type="mkdir", dir_path=str(root / "PDFs"), reason="categoria")
        ],
    )

    execute_plan(plan, root_dir=str(root))

    assert (root / "PDFs").is_dir()


def test_execute_plan_rejects_unknown_operation(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    plan = ExecutionPlan.model_construct(
        summary="operacion no permitida",
        create_dirs=[],
        rename_files=[
            RenameAction.model_construct(
                type="delete", src=str(root / "a.txt"), dst=str(root / "b.txt")
            )
        ],
    )

    with pytest.raises(ValueError):
        execute_plan(plan, root_dir=str(root))

    # La whitelist aborto el plan antes de tocar nada.
    assert not (root / "b.txt").exists()
