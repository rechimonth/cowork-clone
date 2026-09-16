"""Contratos HTTP de la API.

Son modelos pydantic independientes de ``models.py`` (que describe el dominio
interno del agente). Mantenerlos separados permite que el contrato público
evolucione sin arrastrar cambios al núcleo, y deja explícito qué campos se
exponen hacia el frontend.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from api.config import DEFAULT_APPROVAL_TIMEOUT_S

SessionState = Literal[
    "created",
    "scanning",
    "scanned",
    "planning",
    "awaiting_approval",
    "approved",
    "rejected",
    "executing",
    "completed",
    "failed",
    "expired",
]

AgentEventType = Literal[
    "state",
    "log",
    "scan",
    "plan",
    "approval",
    "execution",
    "error",
]


class CreateSessionRequest(BaseModel):
    """Crea una sesión de agente sobre un directorio raíz."""

    root_dir: str = Field(description="Ruta absoluta del directorio a operar")
    recursive: bool = True
    dry_run: bool = False
    name: str | None = Field(
        default=None, max_length=200, description="Etiqueta libre para mostrar en la UI"
    )


class AgentEvent(BaseModel):
    """Evento emitido por el agente, entregado por WebSocket y por el log."""

    seq: int
    ts: str
    type: AgentEventType
    message: str
    data: dict = Field(default_factory=dict)


class RenameView(BaseModel):
    src: str
    dst: str
    reason: str | None = None


class CreateDirView(BaseModel):
    dir_path: str
    reason: str | None = None


class PlanView(BaseModel):
    """Vista del plan pensada para renderizar en el frontend."""

    plan_id: str | None
    execution_id: str | None
    created_at: str | None
    summary: str
    create_dirs: list[CreateDirView]
    rename_files: list[RenameView]
    allowed_operations: list[str]
    rename_count: int
    mkdir_count: int


class ApprovalView(BaseModel):
    """Decisión HITL resuelta, tal como quedó registrada."""

    approved: bool
    decision: str
    decided_at: str


class SessionView(BaseModel):
    """Estado consultable de una sesión."""

    session_id: str
    state: SessionState
    root_dir: str
    recursive: bool
    dry_run: bool
    name: str | None
    created_at: str
    updated_at: str
    file_count: int
    plan: PlanView | None
    approval: ApprovalView | None
    applied: list[RenameView]
    error: str | None
    event_count: int


class ApprovalRequest(BaseModel):
    """Decisión humana sobre el plan pendiente.

    ``decision`` es un verbo acotado (no texto libre) para que el registro de
    auditoría sea consistente y no dependa del idioma del cliente.
    """

    decision: Literal["approve", "reject"]


class ApprovalResponse(BaseModel):
    session_id: str
    approved: bool
    decision: str
    state: SessionState


class PlanResponse(BaseModel):
    session_id: str
    state: SessionState
    plan: PlanView


class SessionsResponse(BaseModel):
    sessions: list[SessionView]


class ErrorResponse(BaseModel):
    detail: str


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    authentication_required: bool
    approval_timeout_s: int = DEFAULT_APPROVAL_TIMEOUT_S
