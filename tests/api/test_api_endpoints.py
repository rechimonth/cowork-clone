"""Tests de los endpoints HTTP de la API.

Ejercitan la app real (routing, validación pydantic, serialización, códigos de
estado) contra un filesystem temporal, sin mocks de la lógica del agente.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from api.app import create_app

pytestmark = pytest.mark.usefixtures("api_config")


@pytest.fixture
def client(api_config):
    app = create_app(api_config)
    with TestClient(app) as test_client:
        yield test_client


def _wait_for_state(client, session_id, headers, states, timeout=20.0):
    """Espera a que la sesión alcance uno de ``states``."""
    deadline = time.monotonic() + timeout
    view = None
    while time.monotonic() < deadline:
        view = client.get(f"/sessions/{session_id}", headers=headers).json()
        if view["state"] in states:
            return view
        time.sleep(0.05)
    raise AssertionError(
        f"La sesión no llegó a {states}; quedó en {view['state'] if view else '?'}"
    )


#: Estados que indican que el ciclo del agente terminó. Deben coincidir con el
#: complemento de ``api.persistence.ACTIVE_STATES``.
_TERMINAL_STATES = {"completed", "rejected", "failed", "expired"}


def test_health_is_public(client):
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["authentication_required"] is True


def test_create_session_requires_token(client, root_dir):
    response = client.post("/sessions", json={"root_dir": str(root_dir)})

    assert response.status_code == 401


def test_create_session_rejects_wrong_token(client, root_dir):
    response = client.post(
        "/sessions",
        json={"root_dir": str(root_dir)},
        headers={"Authorization": "Bearer token-incorrecto-largo-pero-falso"},
    )

    assert response.status_code == 401


def test_create_session_sets_bearer_challenge_header(client, root_dir):
    response = client.post("/sessions", json={"root_dir": str(root_dir)})

    assert response.headers.get("www-authenticate") == "Bearer"


def test_full_dry_run_cycle(client, root_dir, auth_headers):
    """Ciclo completo en dry-run: escanea, planifica y no toca el filesystem."""
    before = sorted(p.name for p in root_dir.iterdir())

    created = client.post(
        "/sessions",
        json={"root_dir": str(root_dir), "dry_run": True},
        headers=auth_headers,
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    view = _wait_for_state(client, session_id, auth_headers, {"completed", "failed"})

    assert view["state"] == "completed"
    assert view["file_count"] == 2
    assert view["plan"] is not None
    assert view["error"] is None
    # Dry-run: nada se movió.
    assert sorted(p.name for p in root_dir.iterdir()) == before


def test_plan_endpoint_exposes_actionable_detail(client, root_dir, auth_headers):
    created = client.post(
        "/sessions", json={"root_dir": str(root_dir), "dry_run": True}, headers=auth_headers
    )
    session_id = created.json()["session_id"]
    _wait_for_state(client, session_id, auth_headers, {"completed", "failed"})

    response = client.get(f"/sessions/{session_id}/plan", headers=auth_headers)

    assert response.status_code == 200
    plan = response.json()["plan"]
    assert plan["rename_count"] == len(plan["rename_files"])
    assert plan["mkdir_count"] == len(plan["create_dirs"])
    assert plan["allowed_operations"] == ["mkdir", "rename"]
    assert plan["summary"]


def test_approve_executes_the_plan(client, root_dir, auth_headers):
    created = client.post("/sessions", json={"root_dir": str(root_dir)}, headers=auth_headers)
    session_id = created.json()["session_id"]

    view = _wait_for_state(client, session_id, auth_headers, {"awaiting_approval", "failed"})
    assert view["state"] == "awaiting_approval"

    approved = client.post(
        f"/sessions/{session_id}/approval", json={"decision": "approve"}, headers=auth_headers
    )
    assert approved.status_code == 200
    assert approved.json()["approved"] is True

    final = _wait_for_state(client, session_id, auth_headers, {"completed", "failed"})
    assert final["state"] == "completed"
    assert final["applied"], "debía aplicarse al menos un renombre"
    # El archivo terminó dentro de la carpeta PDFs.
    assert (root_dir / "PDFs" / "informe.pdf").exists()


def test_reject_leaves_filesystem_untouched(client, root_dir, auth_headers):
    before = sorted(p.name for p in root_dir.iterdir())
    created = client.post("/sessions", json={"root_dir": str(root_dir)}, headers=auth_headers)
    session_id = created.json()["session_id"]
    _wait_for_state(client, session_id, auth_headers, {"awaiting_approval", "failed"})

    rejected = client.post(
        f"/sessions/{session_id}/approval", json={"decision": "reject"}, headers=auth_headers
    )
    assert rejected.status_code == 200
    assert rejected.json()["approved"] is False

    final = _wait_for_state(client, session_id, auth_headers, {"rejected", "failed"})
    assert final["state"] == "rejected"
    assert final["applied"] == []
    assert sorted(p.name for p in root_dir.iterdir()) == before


def test_double_approval_returns_conflict(client, root_dir, auth_headers):
    created = client.post("/sessions", json={"root_dir": str(root_dir)}, headers=auth_headers)
    session_id = created.json()["session_id"]
    _wait_for_state(client, session_id, auth_headers, {"awaiting_approval", "failed"})

    first = client.post(
        f"/sessions/{session_id}/approval", json={"decision": "approve"}, headers=auth_headers
    )
    second = client.post(
        f"/sessions/{session_id}/approval", json={"decision": "approve"}, headers=auth_headers
    )

    assert first.status_code == 200
    assert second.status_code == 409


def test_approval_before_plan_returns_conflict(client, root_dir, auth_headers):
    """Aprobar una sesión que aún no pidió aprobación no debe colar."""
    session = client.post(
        "/sessions", json={"root_dir": str(root_dir), "dry_run": True}, headers=auth_headers
    ).json()
    _wait_for_state(client, session["session_id"], auth_headers, {"completed", "failed"})

    response = client.post(
        f"/sessions/{session['session_id']}/approval",
        json={"decision": "approve"},
        headers=auth_headers,
    )

    assert response.status_code == 409


def test_approval_rejects_unknown_verb(client, root_dir, auth_headers):
    """El verbo está acotado por pydantic: no se acepta texto libre."""
    session = client.post(
        "/sessions", json={"root_dir": str(root_dir)}, headers=auth_headers
    ).json()
    _wait_for_state(client, session["session_id"], auth_headers, {"awaiting_approval", "failed"})

    response = client.post(
        f"/sessions/{session['session_id']}/approval",
        json={"decision": "quizas"},
        headers=auth_headers,
    )

    assert response.status_code == 422


def test_unknown_session_returns_404(client, auth_headers):
    response = client.get("/sessions/no-existe", headers=auth_headers)

    assert response.status_code == 404


def test_list_sessions_reflects_created_sessions(client, root_dir, auth_headers):
    client.post(
        "/sessions",
        json={"root_dir": str(root_dir), "dry_run": True, "name": "mi tarea"},
        headers=auth_headers,
    )

    response = client.get("/sessions", headers=auth_headers)

    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["name"] == "mi tarea"


def test_delete_session_removes_it(client, root_dir, auth_headers):
    session = client.post(
        "/sessions", json={"root_dir": str(root_dir), "dry_run": True}, headers=auth_headers
    ).json()
    _wait_for_state(client, session["session_id"], auth_headers, {"completed", "failed"})

    deleted = client.delete(f"/sessions/{session['session_id']}", headers=auth_headers)

    assert deleted.status_code == 204
    assert client.get(f"/sessions/{session['session_id']}", headers=auth_headers).status_code == 404


def test_no_event_is_emitted_after_the_terminal_state(client, root_dir, auth_headers):
    """El estado terminal es el último evento de la sesión.

    Es la invariante de la que depende ``Session.running``: si después del
    estado terminal no queda nada por emitir, entonces un estado terminal
    implica que el worker ya hizo todo su trabajo y la sesión se puede borrar
    sin esperar a que el hilo muera.

    Este test es determinista a propósito. La versión original de la regresión
    miraba el 409 de ``DELETE``, pero esa carrera necesita la carga de CI para
    reproducirse y aquí pasaba siempre: un test que no falla con el código roto
    no protege nada. Comprobar la invariante sí lo hace.
    """
    session = client.post(
        "/sessions", json={"root_dir": str(root_dir), "dry_run": True}, headers=auth_headers
    ).json()
    session_id = session["session_id"]
    _wait_for_state(client, session_id, auth_headers, {"completed", "failed"})

    events = client.get(f"/sessions/{session_id}/events", headers=auth_headers).json()

    terminal = [
        index
        for index, event in enumerate(events)
        if event["type"] == "state" and event["data"].get("state") in _TERMINAL_STATES
    ]
    assert terminal, "la sesión debería haber emitido un estado terminal"
    assert terminal[-1] == len(events) - 1, (
        "se emitió un evento después del estado terminal, así que 'running' "
        "no puede deducirse del estado"
    )


def test_delete_unknown_session_returns_404(client, auth_headers):
    assert client.delete("/sessions/no-existe", headers=auth_headers).status_code == 404


def test_events_endpoint_supports_since(client, root_dir, auth_headers):
    session = client.post(
        "/sessions", json={"root_dir": str(root_dir), "dry_run": True}, headers=auth_headers
    ).json()
    session_id = session["session_id"]
    _wait_for_state(client, session_id, auth_headers, {"completed", "failed"})

    all_events = client.get(f"/sessions/{session_id}/events", headers=auth_headers).json()
    assert all_events

    cutoff = all_events[0]["seq"]
    later = client.get(
        f"/sessions/{session_id}/events", params={"since": cutoff}, headers=auth_headers
    ).json()

    assert all(e["seq"] > cutoff for e in later)
    assert len(later) < len(all_events)


def test_events_are_monotonic_and_typed(client, root_dir, auth_headers):
    session = client.post(
        "/sessions", json={"root_dir": str(root_dir), "dry_run": True}, headers=auth_headers
    ).json()
    session_id = session["session_id"]
    _wait_for_state(client, session_id, auth_headers, {"completed", "failed"})

    events = client.get(f"/sessions/{session_id}/events", headers=auth_headers).json()
    seqs = [e["seq"] for e in events]

    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)
    assert {"scan", "plan", "state"} <= {e["type"] for e in events}


def test_session_view_has_no_absolute_internal_paths_in_dry_run(client, root_dir, auth_headers):
    """El root se expone, pero solo el que el cliente ya conocía."""
    session = client.post(
        "/sessions", json={"root_dir": str(root_dir), "dry_run": True}, headers=auth_headers
    ).json()
    view = _wait_for_state(client, session["session_id"], auth_headers, {"completed", "failed"})

    assert view["root_dir"] == str(root_dir.resolve())


def test_recursive_false_is_honored(client, root_dir, auth_headers):
    nested = root_dir / "sub"
    nested.mkdir()
    (nested / "hondo.txt").write_text("x")

    session = client.post(
        "/sessions",
        json={"root_dir": str(root_dir), "recursive": False, "dry_run": True},
        headers=auth_headers,
    ).json()
    view = _wait_for_state(client, session["session_id"], auth_headers, {"completed", "failed"})

    # Raíz: notas.txt + informe.pdf. El de `sub/` no cuenta.
    assert view["file_count"] == 2


def test_session_limit_returns_429(tmp_path, root_dir):
    """El límite de sesiones protege memoria y descriptores."""
    from api.config import ApiConfig

    config = ApiConfig(
        token="token-de-test-suficientemente-largo",
        allowed_roots=(),
        cors_origins=("http://localhost:1420",),
        state_dir=tmp_path / "state",
        approval_timeout_s=10,
        allow_insecure=False,
        max_sessions=1,
    )
    headers = {"Authorization": "Bearer token-de-test-suficientemente-largo"}
    app = create_app(config)

    with TestClient(app) as client:
        first = client.post(
            "/sessions", json={"root_dir": str(root_dir), "dry_run": True}, headers=headers
        )
        second = client.post(
            "/sessions", json={"root_dir": str(root_dir), "dry_run": True}, headers=headers
        )

    assert first.status_code == 201
    assert second.status_code == 429
