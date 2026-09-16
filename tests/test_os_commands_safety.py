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
    _find_unique_name_at,
    _find_unique_path,
    _move_exclusive,
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


# --------------------------- cierre de la ventana TOCTOU ----------------------
#
# El ataque: entre la validacion de la ruta y la operacion real, sustituir un
# componente por un symlink que apunte fuera del root. La defensa no es volver a
# validar (siempre queda un hueco), sino no volver a resolver la ruta: navegar
# con openat + O_NOFOLLOW y operar relativo al descriptor ya abierto.


def test_open_contained_rejects_symlink_swapped_after_resolution(tmp_path, monkeypatch):
    """El symlink colocado en plena navegacion no se sigue.

    Se intercepta ``os.open`` para ganar la carrera justo cuando se va a abrir el
    componente intermedio: es el instante exacto que antes quedaba desprotegido.
    ``O_NOFOLLOW`` debe hacer fallar la apertura con ELOOP en vez de seguir el
    enlace hacia ``outside``.
    """
    import os
    import shutil

    from os_commands import _open_contained

    root = tmp_path / "root"
    (root / "a" / "b").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()

    real_open = os.open

    def hostile_open(path, flags, *args, **kwargs):
        if path == "b" and kwargs.get("dir_fd") is not None:
            shutil.rmtree(root / "a" / "b")
            (root / "a" / "b").symlink_to(outside)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", hostile_open)

    with (
        pytest.raises((ValueError, OSError)),
        _open_contained(root, root / "a" / "b" / "moved.txt"),
    ):
        pass

    assert not (outside / "moved.txt").exists()


def test_execute_plan_does_not_follow_symlink_created_during_validation(tmp_path, monkeypatch):
    """Ningun archivo escapa aunque el atacante actue tras la validacion.

    Ataque de extremo a extremo: se sustituye el directorio destino por un
    symlink enganchandose a ``_ensure_within_root``, que es la validacion previa.
    El plan debe abortar y el archivo debe quedar donde estaba.
    """
    import shutil

    import os_commands

    root = tmp_path / "root"
    (root / "a" / "b").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    src = root / "a" / "file.txt"
    src.write_text("SECRETO")

    original = os_commands._ensure_within_root

    def hooked(root_dir, target):
        original(root_dir, target)
        if str(target).endswith("moved.txt"):
            shutil.rmtree(root / "a" / "b")
            (root / "a" / "b").symlink_to(outside)

    monkeypatch.setattr(os_commands, "_ensure_within_root", hooked)

    plan = ExecutionPlan(
        summary="carrera hacia fuera del root",
        rename_files=[
            RenameAction(
                type="rename",
                src=str(src),
                dst=str(root / "a" / "b" / "moved.txt"),
                reason="intento de escape",
            )
        ],
    )

    with pytest.raises((ValueError, PermissionError, OSError)):
        execute_plan(plan, root_dir=str(root))

    assert not (outside / "moved.txt").exists()
    assert src.read_text() == "SECRETO"


def test_move_exclusive_refuses_to_overwrite_existing_target(tmp_path):
    """El destino no se pisa aunque la comprobacion previa diga que esta libre.

    Simula la carrera contraria: alguien crea el destino despues de comprobarlo.
    Al enlazar con ``os.link``, que es atomico, el kernel devuelve EEXIST en vez
    de perder el archivo. Sin la comprobacion previa la creacion gana, y el
    contenido original debe seguir intacto.
    """
    import errno
    import os

    root = tmp_path / "root"
    root.mkdir()
    src = root / "origen.txt"
    src.write_text("CONTENIDO_ORIGEN")
    dst = root / "destino.txt"
    dst.write_text("CONTENIDO_DESTINO")

    src_fd = os.open(root, os.O_RDONLY)
    try:
        with pytest.raises(OSError) as excinfo:
            _move_exclusive(src_fd, "origen.txt", src_fd, "destino.txt")
        assert excinfo.value.errno == errno.EEXIST
    finally:
        os.close(src_fd)

    # Ningun archivo se perdio: el origen sigue con su contenido.
    assert src.read_text() == "CONTENIDO_ORIGEN"
    assert dst.read_text() == "CONTENIDO_DESTINO"


def test_find_unique_name_at_does_not_trust_broken_symlink(tmp_path):
    """Un symlink roto ocupa el nombre: no cuenta como libre.

    Con ``Path.exists()`` un symlink roto devuelve False y el nombre pareceria
    disponible, dejando que el renombre posterior fallara o pisara el enlace.
    """
    import os

    root = tmp_path / "root"
    root.mkdir()
    (root / "archivo.txt").symlink_to(tmp_path / "no-existe")

    fd = os.open(root, os.O_RDONLY)
    try:
        assert _find_unique_name_at(fd, "archivo.txt") == "archivo_1.txt"
    finally:
        os.close(fd)


def test_execute_plan_moves_directory_within_root(tmp_path):
    """Las carpetas se mueven igual que los archivos, relativas al descriptor."""
    root = tmp_path / "root"
    (root / "vieja").mkdir(parents=True)
    (root / "vieja" / "dato.txt").write_text("contenido")

    plan = ExecutionPlan(
        summary="renombrar carpeta",
        rename_files=[
            RenameAction(
                type="rename",
                src=str(root / "vieja"),
                dst=str(root / "nueva"),
                reason="categoria",
            )
        ],
    )

    applied = execute_plan(plan, root_dir=str(root))

    assert applied == [(str(root / "vieja"), str(root / "nueva"))]
    assert (root / "nueva" / "dato.txt").read_text() == "contenido"
    assert not (root / "vieja").exists()


def test_execute_plan_raises_when_source_is_missing(tmp_path):
    root = tmp_path / "root"
    root.mkdir()

    plan = ExecutionPlan(
        summary="origen inexistente",
        rename_files=[
            RenameAction(
                type="rename",
                src=str(root / "no-existe.txt"),
                dst=str(root / "destino.txt"),
                reason="x",
            )
        ],
    )

    with pytest.raises(FileNotFoundError):
        execute_plan(plan, root_dir=str(root))


def test_execute_plan_reuses_existing_directory(tmp_path):
    """Crear una carpeta que ya existe no es un error ni la reemplaza."""
    root = tmp_path / "root"
    (root / "PDFs").mkdir(parents=True)
    (root / "PDFs" / "previo.txt").write_text("previo")

    plan = ExecutionPlan(
        summary="categoria ya existente",
        create_dirs=[CreateDirAction(type="mkdir", dir_path=str(root / "PDFs"), reason="c")],
    )

    execute_plan(plan, root_dir=str(root))

    assert (root / "PDFs" / "previo.txt").read_text() == "previo"


def test_create_dir_contained_rejects_symlink_component(tmp_path):
    """Crear una carpeta a traves de un symlink queda bloqueado."""
    from os_commands import _create_dir_contained

    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "atajo").symlink_to(outside)

    with pytest.raises((ValueError, OSError)):
        _create_dir_contained(root, root / "atajo" / "nueva")

    assert not (outside / "nueva").exists()


def test_create_dir_contained_rejects_path_outside_root(tmp_path):
    from os_commands import _create_dir_contained

    root = tmp_path / "root"
    root.mkdir()

    with pytest.raises(ValueError):
        _create_dir_contained(root, tmp_path / "afuera")


def test_ensure_within_root_allows_root_itself(tmp_path):
    """El propio root es un destino valido para la validacion de contención."""
    root = tmp_path / "root"
    root.mkdir()

    _ensure_within_root(root, root)


def test_create_dir_contained_treats_root_as_noop(tmp_path):
    """Pedir el propio root no crea nada ni falla."""
    from os_commands import _create_dir_contained

    root = tmp_path / "root"
    root.mkdir()

    _create_dir_contained(root, root)

    assert root.is_dir()


def test_open_contained_rejects_destination_equal_to_root(tmp_path):
    """El root no es un nombre dentro del root: no hay nada que abrir."""
    from os_commands import _open_contained

    root = tmp_path / "root"
    root.mkdir()

    with pytest.raises(ValueError, match="propio root"), _open_contained(root, root):
        pass


def test_open_contained_rejects_non_directory_component(tmp_path):
    """Un componente que es un archivo regular se rechaza (ENOTDIR)."""
    from os_commands import _open_contained

    root = tmp_path / "root"
    root.mkdir()
    (root / "archivo.txt").write_text("no soy un directorio")

    with (
        pytest.raises(ValueError, match="no confiable"),
        _open_contained(root, root / "archivo.txt" / "dentro.txt"),
    ):
        pass


def test_create_dir_contained_rejects_symlink_swapped_during_navigation(tmp_path, monkeypatch):
    """Un symlink colocado ya iniciada la navegacion no se sigue (mkdir).

    A diferencia del test anterior, aquí el enlace no está al resolver la ruta
    sino que aparece justo cuando se va a abrir el componente. Es la ventana que
    queda después de la validación temprana, y la que cierra ``O_NOFOLLOW``.
    """
    import os
    import shutil

    from os_commands import _create_dir_contained

    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "real").mkdir()

    real_open = os.open

    def hostile_open(path, flags, *args, **kwargs):
        if path == "real" and kwargs.get("dir_fd") is not None:
            shutil.rmtree(root / "real")
            (root / "real").symlink_to(outside)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", hostile_open)

    with pytest.raises((ValueError, OSError)):
        _create_dir_contained(root, root / "real" / "nueva")

    assert not (outside / "nueva").exists()


def test_execute_plan_warns_when_dir_fd_is_unavailable(tmp_path, monkeypatch, caplog):
    """En plataformas sin openat se avisa de que la ventana TOCTOU sigue abierta.

    Windows no soporta ``dir_fd``. Degradar en silencio seria peor que fallar:
    el modo por ruta conserva la validacion de contención, pero pierde la
    garantia atomica, y quien lea los logs debe poder enterarse.
    """
    import os_commands

    monkeypatch.setattr(os_commands, "_SUPPORTS_DIR_FD", False)
    root = tmp_path / "root"
    root.mkdir()

    plan = ExecutionPlan(summary="sin dir_fd", create_dirs=[])

    with caplog.at_level("WARNING", logger="os_commands"):
        execute_plan(plan, root_dir=str(root))

    assert any("ventana TOCTOU" in record.message for record in caplog.records)
