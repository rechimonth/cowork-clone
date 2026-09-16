"""Backend FastAPI del agente cowork-clone.

Expone el ciclo scan -> plan -> HITL -> execute por HTTP y WebSocket, para que
un frontend (React dentro de Tauri) pueda dirigir el agente sin bloquear una
terminal. El núcleo del agente no cambia: esta capa solo lo orquesta mediante
``CoworkAgent`` y sus callbacks inyectables.

Ejecución local::

    export COWORK_API_TOKEN="un-secreto-largo"
    uvicorn api.app:create_app --factory --port 8000
"""

from __future__ import annotations

import asyncio
import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.config import ApiConfig, ConfigurationError, validate_root_dir
from api.events import EventBroker
from api.persistence import SessionStore
from api.schemas import (
    AgentEvent,
    ApprovalRequest,
    ApprovalResponse,
    CreateSessionRequest,
    HealthResponse,
    PlanResponse,
    SessionsResponse,
    SessionView,
)
from api.sessions import (
    SessionCapacityError,
    SessionConflictError,
    SessionManager,
    SessionNotFoundError,
)

VERSION = "0.3.0"

# Latido del WebSocket: mantiene viva la conexión y detecta clientes muertos.
WS_PING_INTERVAL_S = 15.0


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token de API ausente o inválido",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _extract_bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return None
    return value


def create_app(config: ApiConfig | None = None) -> FastAPI:
    """Construye la aplicación. Es el factory que usa uvicorn."""
    cfg = config or ApiConfig.from_env()
    cfg.validate()
    cfg.state_dir.mkdir(parents=True, exist_ok=True)

    broker = EventBroker()
    store = SessionStore(cfg.state_dir)
    manager = SessionManager(cfg, broker=broker, store=store)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # El broker emite desde hilos del agente; necesita el loop para
        # reencolar los eventos que van a los WebSocket.
        broker.bind_loop(asyncio.get_running_loop())
        yield
        manager.shutdown()

    app = FastAPI(
        title="cowork-clone API",
        version=VERSION,
        description="Agente autónomo de organización de archivos con HITL",
        lifespan=lifespan,
    )
    app.state.config = cfg
    app.state.broker = broker
    app.state.manager = manager
    app.state.store = store

    # Recarga las sesiones persistidas antes de aceptar tráfico. Se hace aquí y
    # no en `lifespan` para que los tests con TestClient (que no ejecutan
    # lifespan) vean el mismo estado que un servidor real.
    for problem in manager.restore():
        logging.getLogger(__name__).warning("Sesión persistida descartada: %s", problem)

    # CORS solo para los orígenes configurados (dev server de Vite y webview de
    # Tauri). Nunca `*`: el agente ejecuta cambios reales en el filesystem.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cfg.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # --- autenticación ------------------------------------------------------

    def require_token(request: Request) -> None:
        """Valida el bearer token comparando en tiempo constante.

        ``hmac.compare_digest`` evita filtrar el token carácter a carácter por
        diferencias de tiempo de comparación.
        """
        if not cfg.authentication_required:
            return
        provided = _extract_bearer(request)
        if provided is None or not hmac.compare_digest(provided, cfg.token or ""):
            raise _unauthorized()

    # --- traducción de errores ---------------------------------------------

    @app.exception_handler(SessionNotFoundError)
    async def _not_found(_request: Request, exc: SessionNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": f"Sesión no encontrada: {exc}"})

    @app.exception_handler(SessionConflictError)
    async def _conflict(_request: Request, exc: SessionConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(SessionCapacityError)
    async def _capacity(_request: Request, exc: SessionCapacityError) -> JSONResponse:
        return JSONResponse(status_code=429, content={"detail": str(exc)})

    # --- endpoints ----------------------------------------------------------

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            version=VERSION,
            authentication_required=cfg.authentication_required,
            approval_timeout_s=cfg.approval_timeout_s,
        )

    @app.post(
        "/sessions",
        response_model=SessionView,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_token)],
    )
    async def create_session(payload: CreateSessionRequest) -> SessionView:
        """Crea una sesión y arranca el agente en segundo plano.

        Retorna de inmediato con el estado ``created``; el progreso llega por
        ``/sessions/{id}/events``.
        """
        try:
            root = validate_root_dir(payload.root_dir, cfg.allowed_roots)
        except ConfigurationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        session = manager.create(
            root,
            recursive=payload.recursive,
            dry_run=payload.dry_run,
            name=payload.name,
        )
        session.start()
        return session.view()

    @app.get(
        "/sessions",
        response_model=SessionsResponse,
        dependencies=[Depends(require_token)],
    )
    async def list_sessions() -> SessionsResponse:
        return SessionsResponse(sessions=[s.view() for s in manager.list()])

    @app.get(
        "/sessions/{session_id}",
        response_model=SessionView,
        dependencies=[Depends(require_token)],
    )
    async def get_session(session_id: str) -> SessionView:
        return manager.get(session_id).view()

    @app.get(
        "/sessions/{session_id}/plan",
        response_model=PlanResponse,
        dependencies=[Depends(require_token)],
    )
    async def get_plan(session_id: str) -> PlanResponse:
        """Devuelve el plan. 409 si todavía no se generó."""
        session = manager.get(session_id)
        if session.plan is None:
            raise SessionConflictError(
                f"La sesión {session_id} todavía no generó un plan (estado: {session.state})"
            )
        return PlanResponse(
            session_id=session_id,
            state=session.state,  # type: ignore[arg-type]
            plan=session.view().plan,  # type: ignore[arg-type]
        )

    @app.post(
        "/sessions/{session_id}/approval",
        response_model=ApprovalResponse,
        dependencies=[Depends(require_token)],
    )
    async def approve(session_id: str, payload: ApprovalRequest) -> ApprovalResponse:
        """Resuelve el HITL. Despierta al worker que esperaba la decisión."""
        session = manager.get(session_id)
        decision = session.decide(payload.decision)
        return ApprovalResponse(
            session_id=session_id,
            approved=decision.approved,
            decision=decision.decision,
            state=session.state,  # type: ignore[arg-type]
        )

    @app.delete(
        "/sessions/{session_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_token)],
    )
    async def delete_session(session_id: str) -> None:
        manager.delete(session_id)

    @app.get(
        "/sessions/{session_id}/events",
        response_model=list[AgentEvent],
        dependencies=[Depends(require_token)],
    )
    async def get_events(session_id: str, since: int = Query(default=0, ge=0)) -> list[AgentEvent]:
        """Historial de eventos. ``?since=<seq>`` para reconectar sin repetir."""
        manager.get(session_id)
        return broker.history(session_id, since=since)

    # --- WebSocket ----------------------------------------------------------

    @app.websocket("/sessions/{session_id}/ws")
    async def session_ws(websocket: WebSocket, session_id: str) -> None:
        """Stream de progreso del agente.

        El navegador no permite enviar cabeceras en un WebSocket, así que el
        token viaja como query param o como subprotocolo. Se acepta solo tras
        validarlo, y se cierra con 1008 (policy violation) si no es válido.
        """
        if cfg.authentication_required:
            provided = websocket.query_params.get("token")
            if provided is None:
                # `Sec-WebSocket-Protocol` es la alternativa que no queda en los
                # logs del servidor ni en el historial de navegación.
                provided = websocket.headers.get("sec-websocket-protocol")
            if provided is None or not hmac.compare_digest(provided, cfg.token or ""):
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return

        try:
            manager.get(session_id)
        except SessionNotFoundError:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        await websocket.accept()

        raw_since = websocket.query_params.get("since", "0")
        try:
            since = int(raw_since)
        except ValueError:
            since = 0

        queue = broker.subscribe(session_id)
        try:
            # Replay: sin esto, un cliente que se conecta después del POST se
            # perdería los eventos ya emitidos.
            for event in broker.history(session_id, since=since):
                await websocket.send_json(event.model_dump())

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=WS_PING_INTERVAL_S)
                except TimeoutError:
                    # Latido para detectar conexiones muertas.
                    await websocket.send_json(
                        {"type": "ping", "seq": 0, "ts": "", "message": "", "data": {}}
                    )
                    continue
                await websocket.send_json(event.model_dump())
        except WebSocketDisconnect:
            pass
        except (RuntimeError, ValueError):
            # Conexión cerrada por el cliente mientras se enviaba.
            pass
        finally:
            broker.unsubscribe(session_id, queue)

    return app
