"""Gestión de sesiones del agente expuestas por la API.

Cada sesión envuelve un :class:`main.CoworkAgent` y lo ejecuta en un **hilo
aparte**, porque el agente es síncrono y bloqueante (escaneo, llamadas al LLM,
``rename``). El request HTTP que crea la sesión retorna enseguida con un
``session_id``; el progreso se sigue por WebSocket.

El HITL es el punto interesante: el worker se detiene en
:meth:`Session.wait_for_approval` esperando un ``threading.Event`` que dispara el
endpoint ``POST /sessions/{id}/approval``. Si nadie decide dentro del timeout,
el worker **rechaza** la operación por seguridad, igual que hace el CLI.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ai_engine import FileAnalysisProvider, OfflineFileAnalysisProvider
from api.config import ApiConfig
from api.events import EventBroker
from api.schemas import (
    ApprovalView,
    CreateDirView,
    PlanView,
    RenameView,
    SessionView,
)
from audit_logger import AuditLogger
from main import AgentCallbacks, CoworkAgent
from models import ExecutionPlan
from user_validation import ApprovalDecision


class SessionNotFoundError(KeyError):
    """No existe una sesión con ese id."""


class SessionConflictError(RuntimeError):
    """La operación no es válida en el estado actual de la sesión."""


class SessionCapacityError(RuntimeError):
    """Se alcanzó el máximo de sesiones configurado."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _plan_view(plan: ExecutionPlan) -> PlanView:
    return PlanView(
        plan_id=plan.plan_id,
        execution_id=plan.execution_id,
        created_at=plan.created_at,
        summary=plan.summary,
        create_dirs=[CreateDirView(dir_path=a.dir_path, reason=a.reason) for a in plan.create_dirs],
        rename_files=[RenameView(src=a.src, dst=a.dst, reason=a.reason) for a in plan.rename_files],
        allowed_operations=list(plan.allowed_operations),
        rename_count=len(plan.rename_files),
        mkdir_count=len(plan.create_dirs),
    )


@dataclass
class Session:
    """Estado y sincronización de una ejecución del agente."""

    session_id: str
    root_dir: Path
    recursive: bool
    dry_run: bool
    name: str | None
    broker: EventBroker
    logger: AuditLogger
    approval_timeout_s: int
    llm: FileAnalysisProvider | None = None

    state: str = "created"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    plan: ExecutionPlan | None = None
    file_count: int = 0
    approval: ApprovalView | None = None
    applied: list[tuple[str, str]] = field(default_factory=list)
    error: str | None = None
    event_count: int = 0

    _approval_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _approval_decision: ApprovalDecision | None = field(default=None, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)

    # --- emisión de eventos -------------------------------------------------

    def emit(self, event_type: str, message: str, data: dict | None = None) -> None:
        self.event_count += 1
        self.broker.emit(self.session_id, event_type, message, data)

    def _set_state(self, state: str) -> None:
        self.state = state
        self.updated_at = _now()
        self.emit("state", f"Estado: {state}", {"state": state})

    def _log(self, event_type: str, payload: dict | None = None) -> None:
        self.logger.log_event(event_type, payload)

    # --- ciclo del agente ---------------------------------------------------

    def start(self) -> None:
        """Lanza el ciclo del agente en un hilo de fondo."""
        self._thread = threading.Thread(
            target=self._run, name=f"cowork-session-{self.session_id}", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        agent = CoworkAgent(
            root_dir=str(self.root_dir),
            recursive=self.recursive,
            dry_run=self.dry_run,
            logger=self.logger,
            callbacks=AgentCallbacks(
                output_fn=lambda message: self.emit("log", message),
                approval_provider=self.wait_for_approval,
            ),
            llm=self.llm,
        )

        try:
            self._set_state("scanning")
            self.emit("scan", "Escaneando el directorio…")
            items = agent.scan()
            self.file_count = len(items)
            self.emit(
                "scan",
                f"{self.file_count} archivo(s) analizado(s)",
                {"file_count": self.file_count},
            )

            self._set_state("planning")
            self.emit("log", "Generando el plan…")
            self.plan = agent.plan_actions()
            plan_view = _plan_view(self.plan)
            # El núcleo ya registra PLAN_GENERATED en el audit log; aquí solo se
            # emite al frontend. Solo los eventos `plan` llevan el plan completo
            # en `data`: el progreso va como `log` para que el cliente pueda
            # distinguirlos sin inspeccionar el payload.
            self.emit(
                "plan",
                f"Plan listo: {plan_view.rename_count} renombre(s), "
                f"{plan_view.mkdir_count} carpeta(s)",
                plan_view.model_dump(),
            )

            if self.dry_run:
                self._set_state("completed")
                self.emit("log", "DRY RUN: no se aplicó ningún cambio")
                # El núcleo no llega a su propio registro de DRY_RUN porque la
                # sesión se detiene aquí; lo emite la capa que tomó la decisión.
                self._log("DRY_RUN", {"summary": self.plan.summary, "plan_id": self.plan.plan_id})
                return

            self._set_state("awaiting_approval")
            self.emit(
                "approval",
                "Esperando aprobación humana",
                {"timeout_s": self.approval_timeout_s},
            )

            decision = agent.request_approval(self.plan)
            self.approval = ApprovalView(
                approved=decision.approved,
                decision=decision.decision,
                decided_at=_now(),
            )

            if not decision.approved:
                self._set_state("rejected")
                self.emit(
                    "approval",
                    f"Plan rechazado ({decision.decision}). No se ejecutó nada.",
                    {"approved": False, "decision": decision.decision},
                )
                return

            self._set_state("approved")
            self._set_state("executing")
            self.emit("execution", "Ejecutando el plan aprobado…")
            self.applied = agent.execute(self.plan)
            # El destino final puede diferir del planificado si hubo colisiones.
            self.emit(
                "execution",
                f"{len(self.applied)} renombre(s) aplicado(s)",
                {
                    "applied": [
                        RenameView(src=src, dst=dst).model_dump() for src, dst in self.applied
                    ]
                },
            )
            self._set_state("completed")

        except Exception as exc:  # noqa: BLE001 - frontera del hilo: nada puede escapar
            # El hilo no puede propagar la excepción a nadie: si se pierde, la
            # sesión quedaría "executing" para siempre y el cliente esperando.
            # Se captura deliberadamente en esta frontera y se refleja en el
            # estado + audit log.
            self.error = f"{type(exc).__name__}: {exc}"
            self._log("SESSION_ERROR", {"error": self.error})
            self._set_state("failed")
            self.emit("error", self.error, {"error": self.error})

    # --- HITL ---------------------------------------------------------------

    def wait_for_approval(self, plan: ExecutionPlan) -> ApprovalDecision:
        """Bloquea el worker hasta que llegue la decisión humana o expire.

        Es el ``approval_provider`` del agente. Ante timeout rechaza: el
        principio del proyecto es que la duda nunca aprueba.
        """
        self._approval_event.wait(timeout=self.approval_timeout_s)

        if self._approval_decision is None:
            return ApprovalDecision(approved=False, decision="timeout")
        return self._approval_decision

    def decide(self, decision: str) -> ApprovalDecision:
        """Registra la decisión humana. La llama el endpoint de aprobación."""
        if self.state != "awaiting_approval":
            raise SessionConflictError(
                f"La sesión {self.session_id} no está esperando aprobación (estado: {self.state})"
            )
        if self._approval_event.is_set():
            raise SessionConflictError(f"La sesión {self.session_id} ya tiene una decisión")

        resolved = ApprovalDecision(
            approved=decision == "approve",
            decision="approved" if decision == "approve" else "rejected",
        )
        self._approval_decision = resolved
        self._approval_event.set()
        return resolved

    # --- introspección ------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def wait(self, timeout: float | None = None) -> None:
        """Bloquea hasta que el worker termine. Solo para tests y shutdown."""
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def view(self) -> SessionView:
        return SessionView(
            session_id=self.session_id,
            state=self.state,  # type: ignore[arg-type]
            root_dir=str(self.root_dir),
            recursive=self.recursive,
            dry_run=self.dry_run,
            name=self.name,
            created_at=self.created_at,
            updated_at=self.updated_at,
            file_count=self.file_count,
            plan=_plan_view(self.plan) if self.plan is not None else None,
            approval=self.approval,
            applied=[RenameView(src=src, dst=dst) for src, dst in self.applied],
            error=self.error,
            event_count=self.event_count,
        )


class SessionManager:
    """Registro de sesiones activas, con límite de capacidad."""

    def __init__(
        self,
        config: ApiConfig,
        broker: EventBroker | None = None,
        agent_factory: Callable[..., CoworkAgent] = CoworkAgent,
        llm: FileAnalysisProvider | None = None,
    ) -> None:
        self.config = config
        self.broker = broker or EventBroker()
        self.agent_factory = agent_factory
        # Por defecto la API clasifica offline: una sesión HTTP no puede quedar
        # colgada esperando a que Ollama responda. Para usar el LLM real, pasar
        # ``OllamaProvider()`` explícitamente.
        self.llm = llm if llm is not None else OfflineFileAnalysisProvider()
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(
        self,
        root_dir: Path,
        *,
        recursive: bool,
        dry_run: bool,
        name: str | None,
    ) -> Session:
        with self._lock:
            if len(self._sessions) >= self.config.max_sessions:
                raise SessionCapacityError(
                    f"Límite de {self.config.max_sessions} sesiones alcanzado; "
                    "borrá alguna antes de crear más"
                )

            session_id = str(uuid4())
            session = Session(
                session_id=session_id,
                root_dir=root_dir,
                recursive=recursive,
                dry_run=dry_run,
                name=name,
                broker=self.broker,
                logger=AuditLogger(
                    str(self.config.state_dir / f"{session_id}.jsonl"), session_id=session_id
                ),
                approval_timeout_s=self.config.approval_timeout_s,
                llm=self.llm,
            )
            self._sessions[session_id] = session

        session._log(
            "SESSION_CREATED",
            {"root_dir": str(root_dir), "recursive": recursive, "dry_run": dry_run},
        )
        session.emit("log", f"Sesión creada sobre {root_dir}")
        return session

    def get(self, session_id: str) -> Session:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        return session

    def list(self) -> list[Session]:
        with self._lock:
            return list(self._sessions.values())

    def delete(self, session_id: str) -> None:
        session = self.get(session_id)
        if session.running:
            raise SessionConflictError(
                f"La sesión {session_id} sigue ejecutándose; esperá a que termine"
            )
        with self._lock:
            self._sessions.pop(session_id, None)
        self.broker.drop(session_id)

    def shutdown(self) -> None:
        """Espera a que terminen las sesiones en curso (apagado ordenado)."""
        for session in self.list():
            session.wait(timeout=5)
