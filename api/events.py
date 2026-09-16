"""Bus de eventos entre el worker del agente y los clientes WebSocket.

El agente ejecuta pasos bloqueantes (escaneo, LLM, `rename`) en un **hilo**
aparte, mientras que FastAPI atiende WebSockets en el event loop de asyncio.
Este módulo es la frontera entre ambos mundos: el worker llama a
:meth:`EventBroker.emit` desde su hilo y el broker reencola el evento en el loop
con ``call_soon_threadsafe``, que es la única forma segura de tocar asyncio
desde otro hilo.

Los eventos se guardan además en un buffer por sesión con un ``seq``
monótono, de modo que un cliente que se conecta tarde puede pedir el historial
y no perderse lo ocurrido antes de conectarse.
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from datetime import UTC, datetime

from api.schemas import AgentEvent

# Eventos retenidos por sesión para permitir reconexión sin perder el hilo.
MAX_BUFFERED_EVENTS = 500

# Cola de cada suscriptor. Si un cliente no consume y se llena, se descarta al
# cliente antes que bloquear al worker del agente.
SUBSCRIBER_QUEUE_SIZE = 200


def _now() -> str:
    return datetime.now(UTC).isoformat()


class _SessionChannel:
    """Canales de una sesión: buffer de historial y suscriptores activos."""

    def __init__(self) -> None:
        self.history: deque[AgentEvent] = deque(maxlen=MAX_BUFFERED_EVENTS)
        self.subscribers: set[asyncio.Queue[AgentEvent]] = set()
        self.seq = 0


class EventBroker:
    """Broker thread-safe de eventos por sesión."""

    def __init__(self) -> None:
        self._channels: dict[str, _SessionChannel] = {}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Fija el loop al que se reencolan los eventos emitidos desde hilos."""
        self._loop = loop

    def _channel(self, session_id: str) -> _SessionChannel:
        with self._lock:
            channel = self._channels.get(session_id)
            if channel is None:
                channel = _SessionChannel()
                self._channels[session_id] = channel
            return channel

    def emit(
        self,
        session_id: str,
        event_type: str,
        message: str,
        data: dict | None = None,
    ) -> AgentEvent:
        """Publica un evento. Seguro desde cualquier hilo."""
        channel = self._channel(session_id)

        with self._lock:
            channel.seq += 1
            event = AgentEvent(
                seq=channel.seq,
                ts=_now(),
                type=event_type,  # type: ignore[arg-type]
                message=message,
                data=data or {},
            )
            channel.history.append(event)
            subscribers = tuple(channel.subscribers)

        for queue in subscribers:
            self._deliver(queue, event)

        return event

    def _deliver(self, queue: asyncio.Queue[AgentEvent], event: AgentEvent) -> None:
        """Encola el evento en el loop correcto, sin bloquear al emisor."""
        loop = self._loop
        if loop is None or loop.is_closed():
            # Sin loop (p. ej. en tests unitarios) se escribe directo; asyncio
            # permite encolar desde el mismo hilo del loop sin problemas.
            self._put(queue, event)
            return

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None

        if running is loop:
            self._put(queue, event)
        else:
            loop.call_soon_threadsafe(self._put, queue, event)

    @staticmethod
    def _put(queue: asyncio.Queue[AgentEvent], event: AgentEvent) -> None:
        if queue.full():
            # Cliente lento: se descarta este evento para él en lugar de
            # frenar al agente o crecer sin límite.
            return
        queue.put_nowait(event)

    def history(self, session_id: str, since: int = 0) -> list[AgentEvent]:
        """Eventos con ``seq`` mayor que ``since``, para reconexiones."""
        channel = self._channel(session_id)
        with self._lock:
            return [e for e in channel.history if e.seq > since]

    def subscribe(self, session_id: str) -> asyncio.Queue[AgentEvent]:
        channel = self._channel(session_id)
        queue: asyncio.Queue[AgentEvent] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        with self._lock:
            channel.subscribers.add(queue)
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue[AgentEvent]) -> None:
        channel = self._channel(session_id)
        with self._lock:
            channel.subscribers.discard(queue)

    def subscriber_count(self, session_id: str) -> int:
        channel = self._channel(session_id)
        with self._lock:
            return len(channel.subscribers)

    def drop(self, session_id: str) -> None:
        """Libera el historial de una sesión que ya no se va a consultar."""
        with self._lock:
            self._channels.pop(session_id, None)
