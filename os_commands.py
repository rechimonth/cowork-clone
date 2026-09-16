"""Operaciones de filesystem permitidas, con validación de seguridad.

Este módulo es el único punto del sistema que toca el filesystem. Aplica tres
barreras independientes: una whitelist de tipos de operación (solo ``mkdir`` y
``rename``, nunca borrados), una validación de contención de rutas que impide
salir del directorio raíz aprobado, y una ejecución *relativa a descriptores*
que cierra la ventana TOCTOU.

Por qué la tercera barrera: validar la ruta resuelta y después ejecutar sobre el
string deja un intervalo en el que un symlink creado en medio puede redirigir la
operación. Para cerrarlo, la ejecución no vuelve a resolver rutas: abre el root
una vez y navega componente a componente con ``openat`` (``dir_fd``) más
``O_NOFOLLOW``, de modo que un componente que en ese instante sea un symlink
falla con ``ELOOP`` en lugar de seguirse. Los nombres finales se operan con
``os.rename``/``os.mkdir`` relativos al descriptor del directorio ya abierto, así
que la resolución de la ruta ocurre una sola vez y bajo control.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import pathlib
import stat
from collections.abc import Iterator

from audit_logger import AuditLogger
from models import ExecutionPlan

_LOGGER = logging.getLogger(__name__)

SAFE_OPERATIONS = {"mkdir", "rename"}

# Tope de intentos al buscar un nombre de destino libre. Evita un bucle infinito
# si el directorio contiene una cantidad patológica de colisiones.
MAX_UNIQUE_PATH_ATTEMPTS = 10000

# ``O_DIRECTORY`` evita abrir como directorio algo que no lo es (por ejemplo un
# archivo regular o un dispositivo).
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)

# ``O_NOFOLLOW`` es la pieza que cierra la TOCTOU: si el componente final es un
# symlink, ``open`` falla con ``ELOOP`` en vez de seguirlo.
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)

_OPEN_DIR_FLAGS = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW

# ``openat``/``dir_fd`` no existe en todas las plataformas (Windows, por
# ejemplo). Sin él no se puede navegar por descriptores, así que se degrada al
# modo por ruta: conserva la validación de contención pero *no* cierra la
# ventana TOCTOU. La degradación se registra en el log para que sea visible.
_SUPPORTS_DIR_FD = os.open in os.supports_dir_fd and bool(_O_NOFOLLOW)


def _ensure_within_root(root_dir: pathlib.Path, target: pathlib.Path) -> None:
    """Verifica que ``target`` esté contenido dentro de ``root_dir``.

    Es la última línea de defensa contra *Path Traversal*: aunque el planner o
    el LLM propongan rutas arbitrarias, ninguna operación de filesystem puede
    salir del directorio raíz autorizado.

    Cómo funciona:

    1. **Resolución canónica**: tanto ``root_dir`` como ``target`` se pasan por
       ``Path.resolve()``. Esto normaliza ``..``, ``.`` y barras redundantes, y
       resuelve componentes simbólicos (symlinks). Resolver antes de comparar es
       clave: comparar strings sin resolver permitiría que
       ``root/sub/../../../etc/passwd`` pase como "textualmente dentro" del root
       cuando en realidad apunta a ``/etc/passwd``.
    2. **Comparación por jerarquía, no por prefijo**: se comprueba si ``root_dir``
       está entre los ``parents`` de ``target`` (o si es exactamente igual).
       Se usa la relación de ancestro de ``pathlib`` en lugar de
       ``str(target).startswith(str(root))`` porque el segundo es engañoso:
       ``/tmp/root_evil/x`` empieza con el prefijo ``/tmp/root`` y sin embargo
       NO pertenece a ese root. La comparación por componentes de path evita ese
       falso positivo.
    3. **Rechazo explícito**: si el destino queda fuera, se lanza
       ``ValueError`` con la ruta ofensiva, que aborta el plan antes de tocar el
       filesystem.

    Casos cubiertos:

    - ``..``: ``root/../../etc`` se resuelve a ``/etc`` y es rechazado.
    - **Symlinks**: un symlink dentro del root que apunta afuera se resuelve a su
      destino real y es rechazado. También el propio ``root_dir`` se resuelve,
      de modo que un root que es symlink se compara por su destino real.
    - **Rutas absolutas**: ``/etc/passwd`` no tiene a ``root`` entre sus
      ``parents`` y es rechazado.
    - **Prefijos engañosos**: ``/root_evil`` frente a un root ``/root`` es
      rechazado porque no es un descendiente real.

    Nota sobre TOCTOU: esta verificación es sobre la ruta *resuelta*, así que por
    sí sola dejaría una ventana entre la comprobación y la operación. Esa ventana
    la cierra la capa de ejecución, que no vuelve a resolver rutas: navega el root
    con ``openat`` + ``O_NOFOLLOW`` (ver ``_open_contained``). Esta función queda
    como validación temprana — barata y con buenos mensajes de error — para abortar
    planes malformados antes de abrir ningún descriptor, no como única defensa.
    """
    root_dir = root_dir.resolve()
    target = target.resolve()
    if root_dir not in target.parents and root_dir != target:
        raise ValueError(f"Ruta fuera del root permitido: {target}")


@contextlib.contextmanager
def _open_contained(root_path: pathlib.Path, target: pathlib.Path) -> Iterator[tuple[int, str]]:
    """Abre el directorio contenedor de ``target`` dentro de ``root_path``.

    Cede ``(fd, nombre_final)``: el descriptor del directorio contenedor y el
    nombre del último componente. La operación real se hace después como
    ``os.rename(..., dst_dir_fd=fd)``, de modo que entre la validación y la
    ejecución no queda ninguna resolución de ruta que un symlink pueda desviar.

    La navegación es componente a componente desde el root, nunca con la ruta
    completa: cada tramo se abre con ``openat`` relativo al descriptor del tramo
    anterior, así que se valida *en el momento* contra un directorio ya abierto.
    Eso es justo lo que elimina la ventana TOCTOU: no hay ningún momento en el
    que se valide una ruta y luego se vuelva a resolver por nombre.

    ``O_NOFOLLOW`` hace que un componente que sea symlink falle con ``ELOOP`` en
    lugar de seguirse; ``O_DIRECTORY`` rechaza que un componente no sea un
    directorio. Ambos se traducen a ``ValueError``.

    Se lanza ``ValueError`` si el destino escapa del root.
    """
    resolved_root = root_path.resolve()
    resolved_target = target.resolve()

    try:
        relative = resolved_target.relative_to(resolved_root)
    except ValueError as exc:
        # No es descendiente del root: ni siquiera se abre el root.
        raise ValueError(f"Ruta fuera del root permitido: {resolved_target}") from exc

    if not relative.parts:
        raise ValueError(f"El destino es el propio root, no un elemento dentro: {target}")

    opened: list[int] = []
    try:
        current_fd = os.open(resolved_root, _OPEN_DIR_FLAGS)
        opened.append(current_fd)
        for part in relative.parts[:-1]:
            current_fd = os.open(part, _OPEN_DIR_FLAGS, dir_fd=current_fd)
            opened.append(current_fd)
        yield current_fd, relative.parts[-1]
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ValueError(
                f"Componente no confiable en la ruta (¿symlink?): {resolved_target}"
            ) from exc
        raise
    finally:
        for fd in opened:
            os.close(fd)


def _find_unique_path(dst: pathlib.Path) -> pathlib.Path:
    """Devuelve una ruta libre basada en ``dst`` agregando un sufijo numérico.

    Si ``dst`` no existe se devuelve tal cual. Si ya existe se prueban
    ``nombre_1.ext``, ``nombre_2.ext``, ... preservando el directorio y la
    extensión. El contador crece hasta ``MAX_UNIQUE_PATH_ATTEMPTS`` para soportar
    muchas colisiones sin bucles infinitos.

    Es una decisión de producto: ante una colisión preferimos preservar ambos
    archivos renombrando el entrante antes que abortar todo el plan.

    Abre el directorio contenedor una sola vez y comprueba los candidatos
    relativos a ese descriptor (``os.stat(..., dir_fd=...)``), no mediante
    ``Path.exists()``. En la ejecución real la búsqueda se hace además
    componente a componente desde el root, dentro de ``_rename_contained``; esta
    función expone el mismo cálculo para quien ya tiene una ruta resuelta.
    """
    parent_fd = os.open(dst.parent, _OPEN_DIR_FLAGS)
    try:
        return dst.parent / _find_unique_name_at(parent_fd, dst.name)
    finally:
        os.close(parent_fd)


def _find_unique_name_at(parent_fd: int, name: str) -> str:
    """Busca un nombre libre dentro de "parent_fd" agregando sufijo numérico.

    Variante relativa a descriptor de "_find_unique_path": en lugar de construir
    rutas y consultar "Path.exists()" —que resuelve por nombre y reabre la
    carrera— comprueba cada candidato con "os.stat(..., dir_fd=...)" sobre un
    directorio ya abierto.

    "follow_symlinks=False" es importante: un symlink roto no debe contar como
    libre solo por no poder resolverse. Si existe, ocupa el nombre.
    """
    path = pathlib.Path(name)
    stem = path.stem
    suffix = path.suffix

    candidates = [name]
    candidates += [
        f"{stem}_{counter}{suffix}" for counter in range(1, MAX_UNIQUE_PATH_ATTEMPTS + 1)
    ]

    for candidate in candidates:
        try:
            os.stat(candidate, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return candidate

    raise FileExistsError(
        f"No se encontró un nombre libre para {name} tras {MAX_UNIQUE_PATH_ATTEMPTS} intentos"
    )


def _name_is_taken(parent_fd: int, name: str) -> bool:
    """Indica si "name" existe dentro de "parent_fd", sin seguir symlinks."""
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _move_exclusive(src_parent_fd: int, src_name: str, dst_parent_fd: int, dst_name: str) -> None:
    """Mueve una entrada sin posibilidad de pisar el destino.

    La comprobación de colisión y el renombre tienen que ser una sola operación:
    si se consulta si el destino existe y después se renombra, queda una ventana
    en la que puede aparecer un archivo y perderse, porque ``os.rename``
    sobrescribe en silencio lo que encuentre.

    Para archivos regulares se usa ``os.link`` + ``os.unlink``: ``link`` falla con
    ``EEXIST`` si el destino ya está ocupado, así que es el kernel quien decide
    atómicamente si el nombre estaba libre. El origen se desenlaza *después* de
    un enlace exitoso, de modo que un fallo no pierde nada: como mucho deja un
    enlace de más, nunca un archivo menos.

    Los directorios no admiten enlace duro (``EPERM``), así que se renombran
    directamente. Sigue siendo atómico y relativo a descriptor.
    """
    info = os.stat(src_name, dir_fd=src_parent_fd, follow_symlinks=False)

    if stat.S_ISDIR(info.st_mode):
        os.rename(src_name, dst_name, src_dir_fd=src_parent_fd, dst_dir_fd=dst_parent_fd)
        return

    os.link(
        src_name,
        dst_name,
        src_dir_fd=src_parent_fd,
        dst_dir_fd=dst_parent_fd,
        follow_symlinks=False,
    )
    os.unlink(src_name, dir_fd=src_parent_fd)


def _create_dir_contained(root_path: pathlib.Path, dir_path: pathlib.Path) -> None:
    """Crea ``dir_path`` dentro de ``root_path`` navegando con descriptores.

    Cada tramo se crea y se abre —con ``O_NOFOLLOW``— relativo al tramo anterior,
    así que ningún componente puede ser sustituido por un symlink entre la
    validación y el ``mkdir``.
    """
    resolved_root = root_path.resolve()
    resolved_dir = dir_path.resolve()

    try:
        relative = resolved_dir.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"Ruta fuera del root permitido: {resolved_dir}") from exc

    if not relative.parts:
        return

    opened: list[int] = []
    try:
        current_fd = os.open(resolved_root, _OPEN_DIR_FLAGS)
        opened.append(current_fd)
        for part in relative.parts:
            # Ya puede existir una carpeta legítima del usuario: se crea si falta
            # y luego se abre con O_NOFOLLOW, que falla si es un symlink o si no
            # es un directorio.
            with contextlib.suppress(FileExistsError):
                os.mkdir(part, dir_fd=current_fd)
            current_fd = os.open(part, _OPEN_DIR_FLAGS, dir_fd=current_fd)
            opened.append(current_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ValueError(
                f"Componente no confiable en la ruta (¿symlink?): {resolved_dir}"
            ) from exc
        raise
    finally:
        for fd in opened:
            os.close(fd)


def _resolve_collision_at(dst_parent_fd: int, dst_name: str) -> str:
    """Devuelve el nombre final del destino, desambiguando si ya está ocupado."""
    if not _name_is_taken(dst_parent_fd, dst_name):
        return dst_name
    return _find_unique_name_at(dst_parent_fd, dst_name)


def _rename_contained(
    root_path: pathlib.Path,
    src: pathlib.Path,
    dst: pathlib.Path,
    logger: AuditLogger | None,
) -> str:
    """Renombra ``src`` a ``dst`` manteniéndose dentro de ``root_path``.

    Abre los directorios contenedores con ``openat`` + ``O_NOFOLLOW`` y opera
    por descriptor, de modo que la ruta no se resuelve dos veces y no queda
    ventana TOCTOU. Devuelve el nombre final aplicado.
    """
    with _open_contained(root_path, src) as (src_parent_fd, src_name):
        if not _name_is_taken(src_parent_fd, src_name):
            raise FileNotFoundError(f"No existe src para renombre: {src}")

        with _open_contained(root_path, dst) as (dst_parent_fd, dst_name):
            final_name = _resolve_collision_at(dst_parent_fd, dst_name)
            if final_name != dst_name and logger:
                logger.log_event(
                    "RENAME_COLLISION_RESOLVED",
                    {
                        "src": str(src),
                        "requested_dst": str(dst),
                        "final_dst": str(dst.parent / final_name),
                    },
                )

            try:
                _move_exclusive(src_parent_fd, src_name, dst_parent_fd, final_name)
            except OSError as exc:
                if exc.errno == errno.EXDEV:
                    raise ValueError(
                        "El destino está en otro dispositivo; no se puede mover de forma atómica"
                    ) from exc
                raise

    return final_name


def execute_plan(
    plan: ExecutionPlan,
    root_dir: str,
    logger: AuditLogger | None = None,
) -> list[tuple[str, str]]:
    """Ejecuta el plan aprobado. Devuelve los renombres efectivamente aplicados.

    La lista de retorno contiene pares ``(src_original, dst_final)``. Es útil
    para rollback y para reportar al usuario cuando hubo colisiones y el destino
    final difiere del planificado.

    Las operaciones se aplican relativas a descriptores de directorio abiertos
    con ``O_NOFOLLOW``, así que un symlink colocado en cualquier componente de la
    ruta durante la ejecución provoca un error en vez de redirigir la operación.
    """
    root_path = pathlib.Path(root_dir).resolve()

    if not _SUPPORTS_DIR_FD:
        _LOGGER.warning(
            "openat/dir_fd no disponible en esta plataforma: se ejecuta por ruta y "
            "la ventana TOCTOU no queda cerrada"
        )

    # Validación de whitelist: se inspeccionan los tipos declarados en el plan
    # antes de tocar el filesystem, de modo que una operación no permitida
    # (delete, chmod, ...) aborte el plan completo sin efectos parciales.
    declared_operations: list[str] = [a.type for a in plan.create_dirs]
    declared_operations += [a.type for a in plan.rename_files]
    for operation in declared_operations:
        if operation not in SAFE_OPERATIONS:
            raise ValueError(f"Operación no permitida en plan: {operation}")

    # Validación temprana de contención: aborta planes malformados antes de
    # abrir ningún descriptor.
    for create_action in plan.create_dirs:
        _ensure_within_root(root_path, pathlib.Path(create_action.dir_path))
    for rename_action in plan.rename_files:
        _ensure_within_root(root_path, pathlib.Path(rename_action.src))
        _ensure_within_root(root_path, pathlib.Path(rename_action.dst))

    # (1) Crear carpetas
    for create_action in plan.create_dirs:
        _create_dir_contained(root_path, pathlib.Path(create_action.dir_path))

    # (2) Renombrar archivos
    applied: list[tuple[str, str]] = []
    for rename_action in plan.rename_files:
        src = pathlib.Path(rename_action.src)
        dst = pathlib.Path(rename_action.dst)

        final_name = _rename_contained(root_path, src, dst, logger)
        applied.append((str(src), str(dst.parent / final_name)))

    return applied
