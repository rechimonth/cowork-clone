from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from models import ExecutionPlan


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    decision: str

    def __bool__(self) -> bool:
        return self.approved


def _risk_for_rename(src: str, dst: str) -> str:
    if src == dst:
        return "sin cambios"
    return "bajo"


def _risk_for_mkdir() -> str:
    return "bajo"


def format_plan(plan: ExecutionPlan) -> str:
    return "\n".join(
        [
            "PLAN PROPUESTO",
            "",
            f"Renombres: {len(plan.rename_files)}",
            f"Carpetas: {len(plan.create_dirs)}",
        ]
    )


def format_plan_details(plan: ExecutionPlan) -> str:
    lines: list[str] = ["DETALLE DEL PLAN", ""]

    for action in plan.create_dirs:
        lines.extend(
            [
                "ORIGEN: mkdir",
                f"DESTINO: {action.dir_path}",
                f"MOTIVO: {action.reason or 'No especificado'}",
                f"RIESGO: {_risk_for_mkdir()}",
                "",
            ]
        )

    for action in plan.rename_files:
        lines.extend(
            [
                f"ORIGEN: {action.src}",
                f"DESTINO: {action.dst}",
                f"MOTIVO: {action.reason or 'No especificado'}",
                f"RIESGO: {_risk_for_rename(action.src, action.dst)}",
                "",
            ]
        )

    if len(lines) == 2:
        lines.append("Sin acciones.")

    return "\n".join(lines).rstrip()


def request_user_approval(
    plan: ExecutionPlan,
    input_fn: Callable[[str], str] = input,
) -> ApprovalDecision:
    print(format_plan(plan))

    while True:
        ans = input_fn("\n[A] Aprobar  [R] Rechazar  [V] Ver detalles: ").strip().lower()

        if ans == "a":
            return ApprovalDecision(approved=True, decision="approved")
        if ans == "r":
            return ApprovalDecision(approved=False, decision="rejected")
        if ans == "v":
            print()
            print(format_plan_details(plan))
            continue

        print("Entrada inválida. Usá A, R o V.")
