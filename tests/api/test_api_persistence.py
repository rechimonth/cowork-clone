"""Tests de la persistencia de sesiones entre reinicios del backend.

Lo que se comprueba aquí es que un reinicio no borre el trabajo del usuario: la
sesión, su plan, la decisión HITL y el historial de eventos deben sobrevivir. Y
lo contrario también: que una sesión que quedó a medias no se reanude sola ni
quede eternamente "en curso".
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.persistence import (
    ACTIVE_STATES,
    SessionSnapshot,
    SessionStore,
    is_safe_session_id,
)
from api.schemas import AgentEvent
from api.sessions import SessionManager

TOKEN = "token-de-test-suficientemente-largo"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _snapshot(session_id: str = "sid-1", **overrides) -> SessionSnapshot:
    base = {
        "session_id": session_id,
        "root_dir": "/tmp/ejemplo",
        "recursive": True,
        "dry_run": False,
        "state": "completed",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:01:00+00:00",
    }
    base.update(overrides)
    return SessionSnapshot(**base)


def _event(seq: int, message: str = "hola") -> AgentEvent:
    return AgentEvent(seq=seq, ts="2026-01-01T00:00:00+00:00", type="log", message=message)


# --- almacén ----------------------------------------------------------------


def test_snapshot_roundtrip(tmp_path):
    store = SessionStore(tmp_path)
    store.save(_snapshot(event_count=7, applied=[]))

    loaded, problems = store.load_all()

    assert problems == []
    assert len(loaded) == 1
    assert loaded[0].session_id == "sid-1"
    assert loaded[0].event_count == 7


def test_events_roundtrip_and_since_filter(tmp_path):
    store = SessionStore(tmp_path)
    for seq in range(1, 6):
        store.append_event("sid-1", _event(seq))

    everything, problems = store.load_events("sid-1")
    assert problems == []
    assert [e.seq for e in everything] == [1, 2, 3, 4, 5]

    tail, _ = store.load_events("sid-1", since=3)
    assert [e.seq for e in tail] == [4, 5]


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path):
    store = SessionStore(tmp_path)
    store.save(_snapshot())

    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_unsafe_session_id_is_rejected_and_writes_nothing(tmp_path):
    """Un id con separadores no debe poder escapar del STATE_DIR."""
    store = SessionStore(tmp_path / "state")
    (tmp_path / "state").mkdir()

    assert not is_safe_session_id("../evil")
    with pytest.raises(ValueError):
        store.save(_snapshot(session_id="../evil"))

    # El id inseguro tampoco debe usarse para leer ni escribir eventos.
    store.append_event("../evil", _event(1))
    assert not (tmp_path / "evil.json").exists()
    assert not (tmp_path / "evil.events.jsonl").exists()


def test_corrupt_snapshot_is_reported_but_does_not_abort_load(tmp_path):
    store = SessionStore(tmp_path)
    store.save(_snapshot(session_id="buena"))
    (tmp_path / "rota.json").write_text("{ esto no es json", encoding="utf-8")

    loaded, problems = store.load_all()

    assert [s.session_id for s in loaded] == ["buena"]
    assert any("rota.json" in problem for problem in problems)


def test_truncated_event_line_is_skipped(tmp_path):
    """Un corte a mitad de escritura deja una línea rota; el resto debe servir.

    Una escritura interrumpida no deja salto de línea, así que el siguiente
    append se concatena a esa misma línea. El resultado es una línea corrupta
    que se descarta: se pierden los eventos de esa línea, nunca el fichero
    entero ni el arranque del servidor.
    """
    store = SessionStore(tmp_path)
    store.append_event("sid-1", _event(1))
    with open(tmp_path / "sid-1.events.jsonl", "a", encoding="utf-8") as handle:
        handle.write('{"seq": 2, "ts": "x"')  # sin \n: queda cortada
    store.append_event("sid-1", _event(3))  # se concatena a la línea rota
    store.append_event("sid-1", _event(4))  # esta ya es una línea nueva y válida

    events, problems = store.load_events("sid-1")

    assert [e.seq for e in events] == [1, 4]
    assert problems, "el problema debe quedar registrado, no silenciado"


def test_unsafe_id_is_ignored_by_delete_and_load_events(tmp_path):
    """Las rutas derivadas de un id inseguro no deben tocarse ni leerse."""
    store = SessionStore(tmp_path)
    (tmp_path / "evil.events.jsonl").write_text(
        json.dumps(_event(1).model_dump()) + "\n", encoding="utf-8"
    )

    events, problems = store.load_events("../evil")

    assert events == []
    assert problems == []
    # delete no debe borrar un fichero que no le corresponde.
    store.delete("../evil")
    assert (tmp_path / "evil.events.jsonl").exists()


def test_blank_lines_in_event_stream_are_ignored(tmp_path):
    """Un stream con líneas vacías debe leerse sin ruido en los problemas."""
    store = SessionStore(tmp_path)
    store.append_event("sid-1", _event(1))
    with open(tmp_path / "sid-1.events.jsonl", "a", encoding="utf-8") as handle:
        handle.write("\n\n")
    store.append_event("sid-1", _event(2))

    events, problems = store.load_events("sid-1")

    assert [e.seq for e in events] == [1, 2]
    assert problems == []


def test_unreadable_encoding_in_event_file_is_reported(tmp_path):
    """Un stream con bytes inválidos se reporta en vez de tumbar el arranque.

    Es el caso de un fichero de estado corrompido por otra herramienta: se
    pierde su historial, pero el servidor arranca y el resto de sesiones siguen
    funcionando.
    """
    store = SessionStore(tmp_path)
    (tmp_path / "sid-1.events.jsonl").write_bytes(b"\xff\xfe\x00 basura binaria\n")

    events, problems = store.load_events("sid-1")

    assert events == []
    assert problems, "el fallo de lectura debe quedar registrado"


def test_missing_state_dir_is_not_an_error(tmp_path):
    """Un primer arranque no tiene STATE_DIR todavía; no es un fallo."""
    store = SessionStore(tmp_path / "inexistente")

    snapshots, problems = store.load_all()
    events, event_problems = store.load_events("sid-1")

    assert snapshots == [] and problems == []
    assert events == [] and event_problems == []


def test_audit_log_files_are_not_mistaken_for_snapshots(tmp_path):
    """El STATE_DIR también guarda audit logs; no deben intentar parsearse."""
    store = SessionStore(tmp_path)
    store.save(_snapshot(session_id="sid-1"))
    (tmp_path / "sid-1.jsonl").write_text('{"event_type": "X"}\n', encoding="utf-8")
    (tmp_path / "sid-1.events.jsonl").write_text(
        json.dumps(_event(1).model_dump()) + "\n", encoding="utf-8"
    )

    loaded, problems = store.load_all()

    assert problems == []
    assert [s.session_id for s in loaded] == ["sid-1"]


def test_persistence_failure_does_not_break_the_session(api_config, root_dir, tmp_path, caplog):
    """Si el disco falla, la sesión sigue viva: perder histórico no es fatal.

    Se apunta el STATE_DIR a un fichero existente para que ``mkdir`` falle, que
    es el caso realista de disco lleno o permisos mal puestos.
    """
    bloqueado = tmp_path / "no_es_un_directorio"
    bloqueado.write_text("ocupado", encoding="utf-8")

    manager = SessionManager(api_config, store=SessionStore(bloqueado))
    with caplog.at_level("WARNING"):
        session = manager.create(root_dir, recursive=True, dry_run=True, name=None)
        session.emit("log", "un evento mas")

    # La sesión existe y funciona aunque no se haya podido persistir.
    assert manager.get(session.session_id).state == "created"
    assert session.event_count > 0
    assert any("No se pudo persistir" in record.getMessage() for record in caplog.records)


def test_delete_removes_both_artifacts(tmp_path):
    store = SessionStore(tmp_path)
    store.save(_snapshot())
    store.append_event("sid-1", _event(1))

    store.delete("sid-1")

    assert not (tmp_path / "sid-1.json").exists()
    assert not (tmp_path / "sid-1.events.jsonl").exists()
    # Borrar dos veces no debe fallar.
    store.delete("sid-1")


# --- restauración en el manager ---------------------------------------------


def _drive_to_completion(client, session_id):
    """Sigue el ciclo por WebSocket y aprueba el plan, como haría la UI."""
    with client.websocket_connect(f"/sessions/{session_id}/ws?token={TOKEN}") as ws:
        for _ in range(300):
            message = ws.receive_json()
            if message["type"] == "state" and message["data"].get("state") == "awaiting_approval":
                break
        client.post(f"/sessions/{session_id}/approval", json={"decision": "approve"}, headers=AUTH)
        for _ in range(300):
            message = ws.receive_json()
            if message["type"] == "state" and message["data"].get("state") == "completed":
                return
    raise AssertionError("la sesión no llegó a completarse")


def test_completed_session_survives_restart(api_config, root_dir):
    """El caso central: cerrar y reabrir el backend no pierde la sesión.

    Es la prueba de que la persistencia sirve para algo: el plan, la decisión
    HITL y los renombres aplicados siguen ahí después del reinicio. El cliente
    se usa como context manager para que el lifespan ate el broker a su loop,
    igual que un servidor real.
    """
    with TestClient(create_app(api_config)) as first:
        session = first.post("/sessions", json={"root_dir": str(root_dir)}, headers=AUTH).json()
        session_id = session["session_id"]
        _drive_to_completion(first, session_id)

    # Segundo arranque sobre el MISMO state_dir: es el reinicio.
    with TestClient(create_app(api_config)) as second:
        restored = second.get(f"/sessions/{session_id}", headers=AUTH).json()

    assert restored["state"] == "completed"
    assert restored["plan"] is not None
    assert restored["approval"]["approved"] is True
    assert restored["applied"], "los renombres aplicados deben sobrevivir"
    assert restored["event_count"] > 0


def test_restored_session_does_not_resume_execution(api_config, root_dir):
    """Recargar no debe relanzar el agente: reintentar un rename es peligroso."""
    with TestClient(create_app(api_config)) as first:
        session = first.post(
            "/sessions", json={"root_dir": str(root_dir), "dry_run": True}, headers=AUTH
        ).json()

    app = create_app(api_config)
    with TestClient(app) as second:
        sessions = second.get("/sessions", headers=AUTH).json()["sessions"]

    restored = next(s for s in sessions if s["session_id"] == session["session_id"])
    assert app.state.manager.get(restored["session_id"]).running is False


def test_interrupted_session_becomes_expired(api_config, root_dir):
    """Una sesión que quedó esperando aprobación no puede seguir 'viva'."""
    store = SessionStore(api_config.state_dir)
    api_config.state_dir.mkdir(parents=True, exist_ok=True)
    store.save(
        _snapshot(
            session_id="interrumpida",
            root_dir=str(root_dir),
            state="awaiting_approval",
        )
    )

    app = create_app(api_config)
    client = TestClient(app)
    restored = client.get("/sessions/interrumpida", headers=AUTH).json()

    assert restored["state"] == "expired"
    assert restored["error"]
    client.close()


def test_interrupted_session_cannot_be_approved(api_config, root_dir):
    """Aprobar una sesión interrumpida debe fallar, no fingir que se ejecuta."""
    api_config.state_dir.mkdir(parents=True, exist_ok=True)
    SessionStore(api_config.state_dir).save(
        _snapshot(session_id="interrumpida", root_dir=str(root_dir), state="awaiting_approval")
    )

    client = TestClient(create_app(api_config))
    response = client.post(
        "/sessions/interrumpida/approval", json={"decision": "approve"}, headers=AUTH
    )

    assert response.status_code == 409
    client.close()


@pytest.mark.parametrize("state", sorted(ACTIVE_STATES))
def test_every_active_state_is_expired_on_restart(api_config, root_dir, state):
    """Ningún estado con worker vivo puede sobrevivir como si siguiera vivo."""
    api_config.state_dir.mkdir(parents=True, exist_ok=True)
    SessionStore(api_config.state_dir).save(
        _snapshot(session_id="s", root_dir=str(root_dir), state=state)
    )

    client = TestClient(create_app(api_config))
    restored = client.get("/sessions/s", headers=AUTH).json()

    assert restored["state"] == "expired"
    client.close()


@pytest.mark.parametrize("state", ["completed", "failed", "rejected"])
def test_terminal_states_are_preserved_on_restart(api_config, root_dir, state):
    """Los estados terminales sí deben conservarse tal cual."""
    api_config.state_dir.mkdir(parents=True, exist_ok=True)
    SessionStore(api_config.state_dir).save(
        _snapshot(session_id="s", root_dir=str(root_dir), state=state)
    )

    client = TestClient(create_app(api_config))
    restored = client.get("/sessions/s", headers=AUTH).json()

    assert restored["state"] == state
    client.close()


def test_session_with_vanished_root_is_dropped(api_config, tmp_path):
    """Si el directorio ya no existe, la sesión no debe reaparecer."""
    gone = tmp_path / "desaparecido"
    gone.mkdir()
    api_config.state_dir.mkdir(parents=True, exist_ok=True)
    SessionStore(api_config.state_dir).save(
        _snapshot(session_id="huerfana", root_dir=str(gone), state="completed")
    )
    gone.rmdir()

    app = create_app(api_config)
    client = TestClient(app)

    assert (client.get("/sessions", headers=AUTH).json()["sessions"]) == []
    assert client.get("/sessions/huerfana", headers=AUTH).status_code == 404
    client.close()


def test_restore_without_store_is_a_noop(api_config):
    """Sin almacén configurado no hay nada que recargar, y no debe fallar."""
    manager = SessionManager(api_config, store=None)

    assert manager.restore() == []
    assert manager.list() == []


def test_snapshot_with_unsafe_id_is_skipped_on_restore(api_config, root_dir):
    """Un snapshot escrito a mano con un id inseguro no debe cargarse.

    El id se valida al construir rutas, así que un fichero así solo puede
    aparecer si alguien lo creó por fuera; se descarta igualmente.
    """
    api_config.state_dir.mkdir(parents=True, exist_ok=True)
    payload = _snapshot(session_id="..%2Fevil", root_dir=str(root_dir), state="completed")
    # Se escribe a mano: SessionStore.save rechazaría este id.
    (api_config.state_dir / "colado.json").write_text(payload.model_dump_json(), encoding="utf-8")

    manager = SessionManager(api_config, store=SessionStore(api_config.state_dir))
    problems = manager.restore()

    assert manager.list() == []
    assert any("no seguro" in problem for problem in problems)


def test_restore_revalidates_root_against_allowed_roots(api_config, root_dir, tmp_path):
    """Un root que ya no está permitido no debe recargarse.

    Simula que entre reinicios se endureció ``COWORK_ALLOWED_ROOTS``: la sesión
    antigua no puede seguir operando sobre un directorio hoy prohibido.
    """
    api_config.state_dir.mkdir(parents=True, exist_ok=True)
    SessionStore(api_config.state_dir).save(
        _snapshot(session_id="vieja", root_dir=str(root_dir), state="completed")
    )

    permitido = tmp_path / "otro_sitio"
    permitido.mkdir()
    endurecida = api_config.__class__(**{**api_config.__dict__, "allowed_roots": (permitido,)})
    app = create_app(endurecida)

    assert (TestClient(app).get("/sessions", headers=AUTH).json()["sessions"]) == []


def test_engineered_snapshot_cannot_point_at_system_root(api_config, tmp_path):
    """Un snapshot manipulado a mano no debe reabrir /etc."""
    api_config.state_dir.mkdir(parents=True, exist_ok=True)
    SessionStore(api_config.state_dir).save(
        _snapshot(session_id="maliciosa", root_dir="/etc", state="completed")
    )

    app = create_app(api_config)
    assert (TestClient(app).get("/sessions", headers=AUTH).json()["sessions"]) == []


# --- replay del WebSocket tras reinicio -------------------------------------


def test_websocket_replays_history_after_restart(api_config, root_dir):
    """Un cliente que se reconecta tras un reinicio debe recuperar el historial."""
    api_config.state_dir.mkdir(parents=True, exist_ok=True)
    store = SessionStore(api_config.state_dir)
    store.save(_snapshot(session_id="s", root_dir=str(root_dir), state="completed"))
    for seq in range(1, 4):
        store.append_event("s", _event(seq))

    client = TestClient(create_app(api_config))
    with client.websocket_connect(f"/sessions/s/ws?token={TOKEN}") as ws:
        seen = [ws.receive_json()["seq"] for _ in range(3)]

    assert seen == [1, 2, 3]
    client.close()


def test_new_events_continue_the_persisted_sequence(api_config, root_dir):
    """La numeración no debe reiniciarse tras recargar el historial."""
    api_config.state_dir.mkdir(parents=True, exist_ok=True)
    store = SessionStore(api_config.state_dir)
    store.save(
        _snapshot(session_id="s", root_dir=str(root_dir), state="awaiting_approval", event_count=5)
    )
    for seq in range(1, 6):
        store.append_event("s", _event(seq))

    app = create_app(api_config)
    client = TestClient(app)
    # mark_interrupted emite un evento de estado sobre el historial cargado.
    events = client.get("/sessions/s/events", headers=AUTH).json()

    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs)), "no puede haber seq duplicados"
    assert max(seqs) > 5
    client.close()


def test_deleting_session_removes_its_state_files(api_config, root_dir):
    """Borrar una sesión debe limpiar el disco, no solo la memoria."""
    client = TestClient(create_app(api_config))
    session = client.post(
        "/sessions", json={"root_dir": str(root_dir), "dry_run": True}, headers=AUTH
    ).json()
    session_id = session["session_id"]

    # DELETE se rechaza mientras el worker corre (409), así que primero se
    # espera a que la sesión llegue a un estado terminal.
    for _ in range(200):
        state = client.get(f"/sessions/{session_id}", headers=AUTH).json()["state"]
        if state in {"completed", "failed", "rejected"}:
            break
        time.sleep(0.05)
    assert state == "completed"

    response = client.delete(f"/sessions/{session_id}", headers=AUTH)

    assert response.status_code == 204
    assert not (api_config.state_dir / f"{session_id}.json").exists()
    assert not (api_config.state_dir / f"{session_id}.events.jsonl").exists()
    # Y tras un reinicio tampoco debe volver.
    assert (
        TestClient(create_app(api_config)).get("/sessions", headers=AUTH).json()["sessions"] == []
    )
    client.close()
