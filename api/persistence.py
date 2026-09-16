"""Persistencia de sesiones y de su historial de eventos.

Hasta ahora el estado vivía solo en memoria: al reiniciar el backend se perdían
todas las sesiones, y con ellas el plan, la decisión HITL y la bitácora que el
usuario estaba mirando en el frontend. Este módulo lo vuelve durable sin tocar
el núcleo del agente: la capa HTTP es la única que conoce el concepto de
"sesión", así que es aquí donde se guarda.

Se escriben dos artefactos por sesión, ambos bajo ``COWORK_STATE_DIR``:

- ``<session_id>.json``: *snapshot* del estado (un solo objeto, se reescribe).
- ``<session_id>.events.jsonl``: stream de eventos append-only, para que el
  replay del WebSocket siga funcionando tras un reinicio.

El ``session_id`` se valida antes de usarlo para construir rutas. Es un id que
genera el propio servidor, pero un fichero de estado manipulado a mano no
debería poder convertirse en path traversal, así que se rechaza cualquier id
que no sea un identificador simple.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from api.schemas import AgentEvent, ApprovalView, RenameView

# Los ids válidos son uuid4 con guiones, pero se acepta cualquier identificador
# simple. Lo importante es que no contenga separadores de ruta, ``.`` ni ``..``:
# así un snapshot corrupto no puede escribir fuera de STATE_DIR.
_SAFE_SESSION_ID = re.compile(r"^[0-9A-Za-z_-]{1,64}$")

# Estados que implican un worker vivo. Si el backend muere mientras una sesión
# está en uno de estos, al volver no hay hilo que la continúe: se marca como
# `expired` en lugar de mentir diciendo que sigue corriendo.
ACTIVE_STATES = frozenset(
    {"created", "scanning", "planning", "awaiting_approval", "approved", "executing"}
)


class SessionSnapshot(BaseModel):
    """Estado persistible de una sesión.

    Es deliberadamente un subconjunto de ``Session``: nada de threads, eventos
    de sincronización ni loggers. Solo lo que hace falta para reconstruir la
    sesión (y contársela al usuario) después de un reinicio.
    """

    session_id: str
    root_dir: str
    recursive: bool
    dry_run: bool
    name: str | None = None

    state: str
    created_at: str
    updated_at: str
    file_count: int = 0
    event_count: int = 0

    plan: dict | None = None
    approval: ApprovalView | None = None
    applied: list[RenameView] = Field(default_factory=list)
    error: str | None = None

    def to_json(self) -> str:
        return self.model_dump_json()


def is_safe_session_id(session_id: str) -> bool:
    """Indica si ``session_id`` es seguro para interpolar en una ruta."""
    return bool(_SAFE_SESSION_ID.match(session_id))


class SessionStore:
    """Almacén en disco de snapshots y eventos, con escritura atómica.

    Cada método es tolerante a fallos del disco: la API sigue funcionando si la
    persistencia falla, porque perder el histórico es menos grave que abortar
    una sesión en curso. Los errores se devuelven al llamante para que queden
    en el audit log en lugar de propagarse.
    """

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)
        # Los appends de eventos pueden llegar desde el hilo del worker; el
        # lock evita que dos líneas se mezclen en el mismo fichero.
        self._lock = threading.Lock()

    # --- rutas --------------------------------------------------------------

    def _state_path(self, session_id: str) -> Path:
        return self.state_dir / f"{session_id}.json"

    def _events_path(self, session_id: str) -> Path:
        return self.state_dir / f"{session_id}.events.jsonl"

    # --- snapshots ----------------------------------------------------------

    def save(self, snapshot: SessionSnapshot) -> None:
        """Escribe el snapshot de forma atómica (tmp + ``os.replace``).

        La escritura atómica importa porque un snapshot truncado por un corte a
        mitad es peor que no tener snapshot: al cargarlo no se sabría si el
        plan estaba aprobado o no.
        """
        if not is_safe_session_id(snapshot.session_id):
            raise ValueError(f"session_id no válido: {snapshot.session_id!r}")

        path = self._state_path(snapshot.session_id)
        payload = snapshot.to_json()
        with self._lock:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, path)

    def load_all(self) -> tuple[list[SessionSnapshot], list[str]]:
        """Carga todos los snapshots. Devuelve ``(snapshots, problemas)``.

        Un fichero corrupto no debe impedir arrancar el servidor: se omite y se
        describe el motivo para que quede registrado.
        """
        snapshots: list[SessionSnapshot] = []
        problems: list[str] = []

        if not self.state_dir.is_dir():
            return snapshots, problems

        for path in sorted(self.state_dir.glob("*.json")):
            try:
                snapshots.append(SessionSnapshot.model_validate_json(path.read_text("utf-8")))
            except (OSError, UnicodeDecodeError, ValidationError, ValueError) as exc:
                problems.append(f"{path.name}: {type(exc).__name__}: {exc}")

        return snapshots, problems

    def delete(self, session_id: str) -> None:
        """Borra los artefactos de una sesión. No falla si ya no están."""
        if not is_safe_session_id(session_id):
            return
        with self._lock:
            for path in (self._state_path(session_id), self._events_path(session_id)):
                with contextlib.suppress(FileNotFoundError):
                    path.unlink()

    # --- eventos ------------------------------------------------------------

    def append_event(self, session_id: str, event: AgentEvent) -> None:
        """Añade un evento al stream append-only de la sesión."""
        if not is_safe_session_id(session_id):
            return
        path = self._events_path(session_id)
        line = json.dumps(event.model_dump(), ensure_ascii=False)
        with self._lock:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            # Un solo write por evento: así cada línea llega completa al
            # fichero y un lector nunca ve JSON partido.
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def load_events(self, session_id: str, since: int = 0) -> tuple[list[AgentEvent], list[str]]:
        """Lee los eventos con ``seq`` mayor que ``since``."""
        events: list[AgentEvent] = []
        problems: list[str] = []

        if not is_safe_session_id(session_id):
            return events, problems

        path = self._events_path(session_id)
        if not path.is_file():
            return events, problems

        try:
            lines = path.read_text("utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(f"{path.name}: {type(exc).__name__}: {exc}")
            return events, problems

        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                event = AgentEvent.model_validate_json(line)
            except (ValidationError, ValueError) as exc:
                # Una línea cortada por un corte de luz al final del fichero es
                # el caso esperable; se descarta y se sigue.
                problems.append(f"{path.name}:{number}: {type(exc).__name__}: {exc}")
                continue
            if event.seq > since:
                events.append(event)

        return events, problems
