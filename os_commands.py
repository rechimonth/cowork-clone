from __future__ import annotations

import os
import pathlib
import shutil
from typing import Iterable

from models import ExecutionPlan


SAFE_OPERATIONS = {"mkdir", "rename"}


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

    Nota: la verificación es sobre la ruta *resuelta*. Si un componente
    intermedio se reemplazara por un symlink entre esta validación y el
    ``rename``/``mkdir`` real existiría una ventana TOCTOU; en este MVP los
    planes se ejecutan de forma inmediata y secuencial tras la aprobación, por
    lo que el riesgo es aceptable y está documentado en el PR.
    """
    root_dir = root_dir.resolve()
    target = target.resolve()
    if root_dir not in target.parents and root_dir != target:
        raise ValueError(f"Ruta fuera del root permitido: {target}")


def execute_plan(plan: ExecutionPlan, root_dir: str) -> None:
    root_path = pathlib.Path(root_dir).resolve()

    # Validación de whitelist
    for op in [*map(lambda x: x.type, plan.create_dirs), *map(lambda x: x.type, plan.rename_files)]:
        if op not in SAFE_OPERATIONS:
            raise ValueError(f"Operación no permitida en plan: {op}")

    # (1) Crear carpetas
    for a in plan.create_dirs:
        dir_path = pathlib.Path(a.dir_path)
        _ensure_within_root(root_path, dir_path)
        dir_path.mkdir(parents=True, exist_ok=True)

    # (2) Renombrar archivos
    for a in plan.rename_files:
        src = pathlib.Path(a.src)
        dst = pathlib.Path(a.dst)

        _ensure_within_root(root_path, src)
        _ensure_within_root(root_path, dst)

        if not src.exists():
            raise FileNotFoundError(f"No existe src para renombre: {src}")

        if dst.exists():
            raise FileExistsError(
                f"Destino ya existe, abortando para seguridad: {dst}"
            )

        # rename atómico a nivel filesystem (si es el mismo device)
        src.rename(dst)

