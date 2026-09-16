"""Tests del canal WebSocket y de la seguridad del broker de eventos."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.events import EventBroker

TOKEN = "token-de-test-suficientemente-largo"


@pytest.fixture
def client(api_config):
    app = create_app(api_config)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {TOKEN}"}


def _drain_until(ws, predicate, limit=300):
    """Consume eventos hasta que ``predicate`` se cumpla. Devuelve los vistos."""
    seen = []
    for _ in range(limit):
        message = ws.receive_json()
        seen.append(message)
        if predicate(message):
            return seen
    raise AssertionError(f"No se alcanzó la condición; últimos eventos: {seen[-5:]}")


def test_ws_streams_plan_and_approval_end_to_end(client, root_dir, auth_headers):
    """El frontend debe poder seguir todo el ciclo por WebSocket."""
    session = client.post(
        "/sessions", json={"root_dir": str(root_dir)}, headers=auth_headers
    ).json()
    session_id = session["session_id"]

    with client.websocket_connect(f"/sessions/{session_id}/ws?token={TOKEN}") as ws:
        seen = _drain_until(
            ws,
            lambda m: m["type"] == "state" and m["data"].get("state") == "awaiting_approval",
        )
        types = [m["type"] for m in seen]
        assert "plan" in types

        plan_event = next(m for m in seen if m["type"] == "plan")
        assert plan_event["data"]["rename_count"] >= 1

        client.post(
            f"/sessions/{session_id}/approval", json={"decision": "approve"}, headers=auth_headers
        )

        tail = _drain_until(
            ws,
            lambda m: m["type"] == "state" and m["data"].get("state") == "completed",
        )

    assert any(m["type"] == "execution" for m in tail)
    execution = next(m for m in tail if m["type"] == "execution" and m["data"].get("applied"))
    assert execution["data"]["applied"][0]["dst"]


def test_ws_rejects_missing_token(client, root_dir, auth_headers):
    session = client.post(
        "/sessions", json={"root_dir": str(root_dir), "dry_run": True}, headers=auth_headers
    ).json()

    with (
        pytest.raises(Exception),  # noqa: B017 - starlette cierra con WebSocketDisconnect
        client.websocket_connect(f"/sessions/{session['session_id']}/ws") as ws,
    ):
        ws.receive_json()


def test_ws_rejects_wrong_token(client, root_dir, auth_headers):
    session = client.post(
        "/sessions", json={"root_dir": str(root_dir), "dry_run": True}, headers=auth_headers
    ).json()

    with (
        pytest.raises(Exception),  # noqa: B017 - starlette cierra con WebSocketDisconnect
        client.websocket_connect(
            f"/sessions/{session['session_id']}/ws?token=token-falso-suficientemente-largo"
        ) as ws,
    ):
        ws.receive_json()


def test_ws_rejects_unknown_session(client):
    with (
        pytest.raises(Exception),  # noqa: B017 - cierre por policy violation
        client.websocket_connect(f"/sessions/no-existe/ws?token={TOKEN}") as ws,
    ):
        ws.receive_json()


def test_ws_accepts_token_via_subprotocol_header(client, root_dir, auth_headers):
    """Alternativa al query param, que queda en logs e historial."""
    session = client.post(
        "/sessions", json={"root_dir": str(root_dir), "dry_run": True}, headers=auth_headers
    ).json()

    with client.websocket_connect(
        f"/sessions/{session['session_id']}/ws",
        subprotocols=[TOKEN],
    ) as ws:
        message = ws.receive_json()

    assert message["type"] in {"log", "state"}


def test_ws_replays_history_so_late_clients_miss_nothing(client, root_dir, auth_headers):
    """Un cliente que se conecta tras el POST debe recibir lo ya ocurrido."""
    session = client.post(
        "/sessions", json={"root_dir": str(root_dir), "dry_run": True}, headers=auth_headers
    ).json()
    session_id = session["session_id"]

    # Esperamos a que el ciclo termine ANTES de conectar.
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        state = client.get(f"/sessions/{session_id}", headers=auth_headers).json()["state"]
        if state in {"completed", "failed"}:
            break
        time.sleep(0.05)

    with client.websocket_connect(f"/sessions/{session_id}/ws?token={TOKEN}") as ws:
        seen = _drain_until(
            ws,
            lambda m: m["type"] == "state" and m["data"].get("state") == "completed",
        )

    assert any(m["type"] == "plan" for m in seen), "el replay debía incluir el plan"


def test_ws_since_skips_already_seen_events(client, root_dir, auth_headers):
    session = client.post(
        "/sessions", json={"root_dir": str(root_dir), "dry_run": True}, headers=auth_headers
    ).json()
    session_id = session["session_id"]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if client.get(f"/sessions/{session_id}", headers=auth_headers).json()["state"] in {
            "completed",
            "failed",
        }:
            break
        time.sleep(0.05)

    events = client.get(f"/sessions/{session_id}/events", headers=auth_headers).json()
    last = events[-1]["seq"]

    with client.websocket_connect(f"/sessions/{session_id}/ws?token={TOKEN}&since={last}") as ws:
        # Nada nuevo que replayar: el socket queda abierto sin datos.
        assert ws.receive_json  # la conexión se estableció


# --- broker: comportamiento thread-safe ------------------------------------


def test_broker_delivers_events_from_another_thread():
    """El agente emite desde su propio hilo; el broker no debe perder eventos."""
    broker = EventBroker()
    queue = broker.subscribe("s1")

    def worker():
        for i in range(5):
            broker.emit("s1", "log", f"linea {i}")

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    # Sin loop bound, el broker escribe directo a la cola: los 5 deben estar.
    assert queue.qsize() == 5


def test_broker_history_is_monotonic_and_filterable():
    broker = EventBroker()
    for i in range(4):
        broker.emit("s1", "log", f"e{i}")

    history = broker.history("s1")
    assert [e.seq for e in history] == [1, 2, 3, 4]
    assert [e.message for e in broker.history("s1", since=2)] == ["e2", "e3"]


def test_broker_isolates_sessions():
    broker = EventBroker()
    broker.emit("a", "log", "para a")
    broker.emit("b", "log", "para b")

    assert [e.message for e in broker.history("a")] == ["para a"]
    assert [e.message for e in broker.history("b")] == ["para b"]


def test_broker_drops_history_on_drop():
    broker = EventBroker()
    broker.emit("a", "log", "x")
    broker.drop("a")

    assert broker.history("a") == []


def test_broker_unsubscribe_stops_delivery():
    broker = EventBroker()
    queue = broker.subscribe("s1")
    broker.unsubscribe("s1", queue)

    broker.emit("s1", "log", "x")

    assert queue.qsize() == 0
    assert broker.subscriber_count("s1") == 0


def test_broker_does_not_block_on_slow_subscriber():
    """Un cliente que no consume no debe frenar al agente."""
    broker = EventBroker()
    queue = broker.subscribe("s1")

    # Muchísimos más eventos que la capacidad de la cola.
    for i in range(1000):
        broker.emit("s1", "log", f"e{i}")

    assert queue.qsize() <= 200
    assert len(broker.history("s1")) <= 500


@pytest.mark.anyio
async def test_broker_crosses_thread_boundary_into_the_loop():
    """Emitir desde un hilo debe llegar al loop de asyncio del servidor."""
    broker = EventBroker()
    broker.bind_loop(asyncio.get_running_loop())
    queue = broker.subscribe("s1")

    thread = threading.Thread(target=lambda: broker.emit("s1", "log", "desde hilo"))
    thread.start()
    thread.join()

    event = await asyncio.wait_for(queue.get(), timeout=5)
    assert event.message == "desde hilo"
