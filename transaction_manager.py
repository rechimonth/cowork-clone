from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass
from typing import Literal

from models import ExecutionPlan


ActionType = Literal["mkdir", "rename"]


@dataclass
class RecordedAction:
    action_type: ActionType
    source_path: str | None
    target_path: str | None


class TransactionManager:
    """Gestiona un rollback seguro basado en acciones registradas.

    Regla: solo puede revertir lo que fue previamente registrado.
    """

    def __init__(self, *, root_dir: str):
        self.root_dir = pathlib.Path(root_dir).resolve()
        self._actions: list[RecordedAction] = []
        self._active = False

    def begin_transaction(self) -> None:
        self._actions = []
        self._active = True

    def record_action(
        self,
        *,
        action_type: ActionType,
        source_path: str | None,
        target_path: str | None,
    ) -> None:
        if not self._active:
            raise RuntimeError("Transaction no iniciada")
        self._actions.append(
            RecordedAction(
                action_type=action_type,
                source_path=source_path,
                target_path=target_path,
            )
        )

    def commit(self) -> None:
        self._actions = []
        self._active = False

    def _ensure_within_root(self, p: pathlib.Path) -> None:
        p = p.resolve()
        if self.root_dir != p and self.root_dir not in p.parents:
            raise ValueError(f"Ruta fuera del root permitido: {p}")

    def rollback(self) -> None:
        if not self._active:
            return

        # revertir en orden inverso
        while self._actions:
            a = self._actions.pop()
            try:
                if a.action_type == "rename":
                    if not a.source_path or not a.target_path:
                        continue
                    src = pathlib.Path(a.source_path)
                    dst = pathlib.Path(a.target_path)
                    self._ensure_within_root(src)
                    self._ensure_within_root(dst)

                    # Si el estado actual es dst, revertimos moviendo a src
                    if dst.exists() and not src.exists():
                        dst.rename(src)
                    elif dst.exists() and src.exists():
                        # Colisión: rollback seguro aborta esa acción.
                        # (No borra ni sobreescribe.)
                        continue

                elif a.action_type == "mkdir":
                    if not a.target_path:
                        continue
                    d = pathlib.Path(a.target_path)
                    self._ensure_within_root(d)

                    # Revertir mkdir: intentar borrar solo si está vacío.
                    if d.exists() and d.is_dir():
                        try:
                            if not any(d.iterdir()):
                                d.rmdir()
                        except OSError:
                            # No forzar rollback destructivo
                            pass

            except Exception:
                # rollback debe continuar, no detenerse
                continue

        self._active = False

