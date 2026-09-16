"""Punto de entrada del agente: ciclo scan -> plan -> HITL -> execute.

La orquestacion vive en :class:`CoworkAgent` y todo el I/O (salida y
aprobacion) pasa por :class:`AgentCallbacks`. Eso permite reutilizar el mismo
ciclo desde el CLI actual o, en el futuro, desde FastAPI / un frontend Tauri
sin bloquear la terminal.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from audit_logger import AuditLogger
from file_manager import build_file_items, scan_directory
from models import ExecutionPlan, FileItem, PlannerInput
from os_commands import execute_plan
from planner import FileAnalysisProvider, plan_actions
from user_validation import ApprovalDecision, format_plan, request_user_approval


@dataclass
class AgentCallbacks:
    """Puntos de I/O inyectables del agente.

    Aislar la entrada/salida de la terminal es lo que permite reutilizar el
    mismo orquestador desde una API (FastAPI) o un frontend Tauri: en ese caso
    ``output_fn`` emite eventos (p. ej. por WebSocket) y ``approval_provider``
    resuelve el HITL con una petición HTTP en lugar de bloquear en stdin.
    """

    output_fn: Callable[[str], None] = print
    input_fn: Callable[[str], str] = input
    approval_provider: Callable[[ExecutionPlan], ApprovalDecision] | None = None


@dataclass
class CoworkAgent:
    """Orquestador del ciclo scan -> plan -> HITL -> execute.

    Los pasos están expuestos por separado para que un servidor pueda
    ejecutarlos de a uno (por ejemplo, pausando en ``request_approval`` hasta
    recibir la aprobación del frontend).
    """

    root_dir: str
    recursive: bool = True
    dry_run: bool = False
    logger: AuditLogger | None = None
    callbacks: AgentCallbacks = field(default_factory=AgentCallbacks)
    llm: FileAnalysisProvider | None = None

    plan: ExecutionPlan | None = None
    items: list[FileItem] = field(default_factory=list)

    def _log(self, event_type: str, payload: dict | None = None) -> None:
        if self.logger is not None:
            self.logger.log_event(event_type, payload)

    def _out(self, message: str) -> None:
        self.callbacks.output_fn(message)

    def scan(self) -> list[FileItem]:
        """Escanea el root y construye los items con sus previews.

        Es idempotente: vuelve a poblar ``self.items`` en cada llamada.
        """
        paths = scan_directory(self.root_dir, recursive=self.recursive, logger=self.logger)
        self.items = build_file_items(self.root_dir, paths, logger=self.logger)
        self._log("FILES_SCANNED", {"count": len(self.items)})
        return self.items

    def plan_actions(self) -> ExecutionPlan:
        """Genera el plan a partir de los items escaneados.

        Requiere haber llamado antes a :meth:`scan`.
        """
        planner_input = PlannerInput(root_dir=self.root_dir, files=self.items)
        self.plan = plan_actions(planner_input, llm=self.llm).plan
        self._log("PLAN_GENERATED", {"summary": self.plan.summary})
        return self.plan

    def request_approval(self, plan: ExecutionPlan | None = None) -> ApprovalDecision:
        """Pide la aprobacion humana del plan (HITL).

        Usa ``callbacks.approval_provider`` si esta definido; en caso contrario
        delega en el prompt de terminal de :func:`request_user_approval`, que
        aplica timeout y aborta por seguridad ante la duda.
        """
        target = plan or self.plan
        if target is None:
            raise ValueError("No hay plan para aprobar: ejecutá plan_actions() antes.")

        decision = request_user_approval(
            target,
            input_fn=self.callbacks.input_fn,
            output_fn=self.callbacks.output_fn,
            approval_provider=self.callbacks.approval_provider,
        )
        self._log(
            "USER_APPROVAL",
            {"approved": decision.approved, "decision": decision.decision},
        )
        return decision

    def execute(self, plan: ExecutionPlan | None = None) -> list[tuple[str, str]]:
        """Ejecuta el plan aprobado y devuelve los renombres aplicados.

        Al abrirse camino por la whitelist de ``os_commands`` solo puede crear
        carpetas y renombrar; nunca borra ni sale del root.
        """
        target = plan or self.plan
        if target is None:
            raise ValueError("No hay plan para ejecutar: ejecutá plan_actions() antes.")

        # Se invoca el nombre global del módulo (no una referencia local) para
        # que execute_plan siga siendo parcheable desde los tests.
        applied = execute_plan(target, root_dir=self.root_dir, logger=self.logger)
        self._log(
            "EXECUTION_DONE",
            {
                "summary": target.summary,
                "plan_id": target.plan_id,
                "execution_id": target.execution_id,
            },
        )
        self._out("\nEjecución completada.")
        return applied

    def run(self) -> ExecutionPlan | None:
        """Ciclo completo scan -> plan -> HITL -> execute.

        Returns:
            El plan propuesto. Se devuelve tambien cuando es un dry-run o cuando
            el humano rechaza, para que el llamador pueda inspeccionarlo.
        """
        self.scan()
        plan = self.plan_actions()

        if self.dry_run:
            self._out("\nDRY RUN ACTIVADO")
            self._out(format_plan(plan))
            self._log(
                "DRY_RUN",
                {
                    "summary": plan.summary,
                    "plan_id": plan.plan_id,
                    "execution_id": plan.execution_id,
                },
            )
            return plan

        approval = self.request_approval(plan)
        if not approval.approved:
            self._out("\nOperación cancelada por el usuario. No se ejecutó nada.")
            return plan

        self.execute(plan)
        return plan


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="cowork-clone MVP: scan -> plan -> HITL -> execute"
    )
    parser.add_argument("root_dir", help="Directorio a escanear")
    parser.add_argument("--no-recursive", action="store_true", help="No escanear recursivamente")
    parser.add_argument("--log", default="./cowork-clone.log", help="Ruta del log de auditoría")
    parser.add_argument("--dry-run", action="store_true", help="No ejecutar cambios locales")

    args = parser.parse_args(argv)

    root_dir = str(Path(args.root_dir).resolve())
    recursive = not args.no_recursive
    dry_run = bool(args.dry_run)

    logger = AuditLogger(os.path.abspath(args.log))
    logger.log_event("START", {"root_dir": root_dir, "recursive": recursive, "dry_run": dry_run})

    CoworkAgent(
        root_dir=root_dir,
        recursive=recursive,
        dry_run=dry_run,
        logger=logger,
    ).run()


if __name__ == "__main__":
    main()
