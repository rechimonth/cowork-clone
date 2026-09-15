"""Human-in-the-Loop: presentacion del plan y aprobacion del usuario.

Todo el I/O es inyectable (``input_fn`` / ``output_fn``) para poder sustituir
la terminal por una peticion HTTP o un evento WebSocket en la futura API.
Las decisiones por defecto son de rechazo: timeout, EOF o entradas invalidas
repetidas abortan la operacion en lugar de aprobarla.
"""

from __future__ import annotations

import contextlib
import select
import sys
from collections.abc import Callable
from dataclasses import dataclass
from io import UnsupportedOperation

from models import ExecutionPlan

# Tiempo máximo (segundos) que se espera la decisión del humano antes de
# abortar por seguridad. Un agente desatendido no debe quedar bloqueado.
APPROVAL_TIMEOUT_S = 60

# Entradas inválidas consecutivas toleradas antes de abortar por seguridad.
MAX_INVALID_INPUTS = 3


@dataclass(frozen=True)
class ApprovalDecision:
    """Resultado del HITL.

    ``decision`` es el motivo explicito ("approved", "rejected", "timeout",
    "eof", "invalid_input_limit") para que quede registrado en el audit log.
    Es *falsy* cuando no fue aprobado, de modo que ``if not decision`` es una
    forma segura de comprobar la aprobacion.
    """

    approved: bool
    decision: str

    def __bool__(self) -> bool:
        return self.approved


def _risk_for_rename(src: str, dst: str) -> str:
    """Clasifica el riesgo de un renombre. En este MVP siempre es bajo.

    Un renombre no destruye datos; el riesgo subiria si el sistema admitiera
    sobrescrituras, cosa que la capa de ejecucion evita resolviendo colisiones.
    """
    if src == dst:
        return "sin cambios"
    return "bajo"


def _risk_for_mkdir() -> str:
    """Clasifica el riesgo de crear una carpeta: siempre bajo (es aditivo)."""
    return "bajo"


def format_plan(plan: ExecutionPlan) -> str:
    """Resumen corto del plan (cantidad de renombres y carpetas)."""
    return "\n".join(
        [
            "PLAN PROPUESTO",
            "",
            f"Renombres: {len(plan.rename_files)}",
            f"Carpetas: {len(plan.create_dirs)}",
        ]
    )


def format_plan_details(plan: ExecutionPlan) -> str:
    """Detalle accion por accion, con motivo y nivel de riesgo de cada una.

    Es lo que ve el humano al elegir ``[V]`` antes de aprobar o rechazar.
    """
    lines: list[str] = ["DETALLE DEL PLAN", ""]

    for dir_action in plan.create_dirs:
        lines.extend(
            [
                "ORIGEN: mkdir",
                f"DESTINO: {dir_action.dir_path}",
                f"MOTIVO: {dir_action.reason or 'No especificado'}",
                f"RIESGO: {_risk_for_mkdir()}",
                "",
            ]
        )

    for rename_action in plan.rename_files:
        lines.extend(
            [
                f"ORIGEN: {rename_action.src}",
                f"DESTINO: {rename_action.dst}",
                f"MOTIVO: {rename_action.reason or 'No especificado'}",
                f"RIESGO: {_risk_for_rename(rename_action.src, rename_action.dst)}",
                "",
            ]
        )

    if len(lines) == 2:
        lines.append("Sin acciones.")

    return "\n".join(lines).rstrip()


APPROVAL_PROMPT = "\n[A] Aprobar  [R] Rechazar  [V] Ver detalles: "


def _read_answer(
    input_fn: Callable[[str], str],
    prompt: str,
    timeout_s: int,
    output_fn: Callable[[str], None],
) -> tuple[str | None, str]:
    """Lee una respuesta del humano con timeout.

    Devuelve ``(respuesta, status)`` con ``status`` en ``{"ok", "timeout",
    "eof"}``. El timeout requiere ``select`` sobre el stdin real; si no hay TTY
    o ``select`` no está disponible se cae a una lectura directa (documentado
    como fallback, no bloqueante solo cuando el OS lo permite) y el control de
    timeout queda en manos del proveedor inyectado.
    """
    # Un input_fn inyectado (p. ej. HTTP/WebSocket en la futura API) gestiona
    # sus propios timeouts: aquí solo respetamos su resultado.
    if input_fn is not input:
        try:
            return input_fn(prompt), "ok"
        except EOFError:
            return None, "eof"

    fd = None
    if sys.stdin is not None:
        try:
            fd = sys.stdin.fileno()
        except (AttributeError, ValueError, OSError, UnsupportedOperation):
            fd = None

    if fd is not None:
        output_fn(prompt)
        with contextlib.suppress(OSError, ValueError):
            sys.stdout.flush()
        try:
            ready, _, _ = select.select([fd], [], [], timeout_s)
        except (OSError, ValueError):
            ready = [fd]  # sin select fiable: leemos sin timeout
        if not ready:
            return None, "timeout"
        line = sys.stdin.readline()
        if line == "":
            return None, "eof"
        return line.strip(), "ok"

    # Fallback sin fd/TTY: lectura directa.
    try:
        return input_fn(prompt), "ok"
    except EOFError:
        return None, "eof"


def request_user_approval(
    plan: ExecutionPlan,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    timeout_s: int = APPROVAL_TIMEOUT_S,
    approval_provider: Callable[[ExecutionPlan], ApprovalDecision] | None = None,
) -> ApprovalDecision:
    """Pide aprobación humana del plan.

    Es el punto HITL del agente. ``output_fn`` y ``input_fn`` son inyectables
    para desacoplar la I/O de la terminal: en la futura API FastAPI/Tauri la
    aprobación puede llegar por HTTP o WebSocket vía ``approval_provider``.

    Seguridad por defecto: ante timeout, EOF o demasiadas entradas inválidas se
    rechaza la operación (nunca se aprueba por omisión). La decisión queda
    registrada con un valor explícito ("timeout", "eof", "invalid_input_limit")
    para que aparezca en el audit log.
    """
    if approval_provider is not None:
        return approval_provider(plan)

    output_fn(format_plan(plan))

    invalid_inputs = 0
    while True:
        answer, status = _read_answer(input_fn, APPROVAL_PROMPT, timeout_s, output_fn)

        if status == "timeout":
            output_fn(f"\nSin respuesta en {timeout_s}s. Se rechaza la operación por seguridad.")
            return ApprovalDecision(approved=False, decision="timeout")

        if status == "eof":
            output_fn("\nEntrada cerrada (EOF). Se rechaza la operación por seguridad.")
            return ApprovalDecision(approved=False, decision="eof")

        ans = (answer or "").strip().lower()

        if ans == "a":
            return ApprovalDecision(approved=True, decision="approved")
        if ans == "r":
            return ApprovalDecision(approved=False, decision="rejected")
        if ans == "v":
            output_fn("")
            output_fn(format_plan_details(plan))
            invalid_inputs = 0
            continue

        invalid_inputs += 1
        if invalid_inputs >= MAX_INVALID_INPUTS:
            output_fn(
                f"\n{MAX_INVALID_INPUTS} entradas inválidas seguidas. "
                "Se rechaza la operación por seguridad."
            )
            return ApprovalDecision(approved=False, decision="invalid_input_limit")

        output_fn("Entrada inválida. Usá A, R o V.")
