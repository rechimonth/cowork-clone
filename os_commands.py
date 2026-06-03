from __future__ import annotations

import os
import pathlib
import shutil
from typing import Iterable

from models import ExecutionPlan


SAFE_OPERATIONS = {"mkdir", "rename"}


def _ensure_within_root(root_dir: pathlib.Path, target: pathlib.Path) -> None:
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

