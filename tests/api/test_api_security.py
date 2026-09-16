"""Tests de seguridad y de gestión de sesiones de la API.

Cubren las barreras que impiden que la API se convierta en un agente sin
control: validación del directorio raíz, obligatoriedad del token y el
comportamiento del worker ante timeout del HITL.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.config import (
    ApiConfig,
    ConfigurationError,
    validate_root_dir,
)
from api.events import EventBroker
from api.sessions import SessionManager

TOKEN = "token-de-test-suficientemente-largo"


@pytest.fixture
def client(api_config):
    app = create_app(api_config)
    with TestClient(app) as test_client:
        yield test_client


# --- validación de root_dir ------------------------------------------------


@pytest.mark.parametrize(
    "sensitive",
    ["/", "/etc", "/usr", "/bin", "/sys", "/proc", "/dev", "/var", "/boot", "/lib"],
)
def test_validate_root_rejects_system_paths(sensitive):
    """Rutas del sistema: error de configuración con daño difícil de revertir."""
    with pytest.raises(ConfigurationError):
        validate_root_dir(sensitive)


@pytest.mark.parametrize("nested", ["/etc/nginx", "/usr/local", "/var/tmp"])
def test_validate_root_rejects_paths_inside_system_dirs(nested):
    """Un subdirectorio del sistema es igual de peligroso que el propio root."""
    with pytest.raises(ConfigurationError):
        validate_root_dir(nested)


def test_validate_root_rejects_symlinked_system_path():
    """/bin suele ser un symlink a /usr/bin: la denylist debe resolverse antes."""
    target = Path("/bin")
    if not target.is_symlink():
        pytest.skip("/bin no es un symlink en este entorno")

    with pytest.raises(ConfigurationError):
        validate_root_dir("/bin")


def test_validate_root_rejects_home():
    with pytest.raises(ConfigurationError, match="home del usuario"):
        validate_root_dir(str(Path.home()))


@pytest.mark.parametrize("sub", [".ssh", ".aws", ".gnupg", ".kube", ".docker"])
def test_validate_root_rejects_credential_dirs(sub):
    """El agente lee previews: un root con claves filtraría su contenido."""
    target = Path.home() / sub
    if not target.exists():
        pytest.skip(f"{target} no existe en este entorno")

    with pytest.raises(ConfigurationError, match="credenciales"):
        validate_root_dir(str(target))


def test_validate_root_rejects_nested_path_inside_credentials(tmp_path):
    fake_home = tmp_path / "home"
    (fake_home / ".ssh" / "deep").mkdir(parents=True)
    # Se parchea el home para no depender del entorno real.

    original = Path.home
    try:
        Path.home = classmethod(lambda cls: fake_home)  # type: ignore[assignment]
        with pytest.raises(ConfigurationError, match="credenciales"):
            validate_root_dir(str(fake_home / ".ssh" / "deep"))
    finally:
        Path.home = original  # type: ignore[assignment]


def test_validate_root_rejects_relative_path():
    with pytest.raises(ConfigurationError, match="absoluta"):
        validate_root_dir("relative/path")


def test_validate_root_rejects_missing_path(tmp_path):
    with pytest.raises(ConfigurationError, match="no existe"):
        validate_root_dir(str(tmp_path / "fantasma"))


def test_validate_root_rejects_a_file(tmp_path):
    a_file = tmp_path / "archivo.txt"
    a_file.write_text("x")

    with pytest.raises(ConfigurationError, match="no es un directorio"):
        validate_root_dir(str(a_file))


def test_validate_root_accepts_a_normal_directory(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert validate_root_dir(str(workspace)) == workspace.resolve()


def test_validate_root_resolves_symlinks_before_checking():
    """Un symlink a /etc no debe colarse como si fuera un root propio."""
    with pytest.raises(ConfigurationError):
        validate_root_dir("/tmp/../etc")


def test_validate_root_enforces_allowed_roots(tmp_path):
    allowed = tmp_path / "permitido"
    allowed.mkdir()
    other = tmp_path / "otro"
    other.mkdir()

    assert validate_root_dir(str(allowed / ".." / "permitido"), (allowed,)) == allowed.resolve()
    with pytest.raises(ConfigurationError, match="COWORK_ALLOWED_ROOTS"):
        validate_root_dir(str(other), (allowed,))


def test_validate_root_rejects_sibling_with_similar_prefix(tmp_path):
    """`/x/root_evil` no es descendiente de `/x/root` (falso amigo del prefijo)."""
    allowed = tmp_path / "root"
    allowed.mkdir()
    evil = tmp_path / "root_evil"
    evil.mkdir()

    with pytest.raises(ConfigurationError, match="COWORK_ALLOWED_ROOTS"):
        validate_root_dir(str(evil), (allowed,))


def test_api_rejects_disallowed_root(tmp_path, api_config):
    """La validación se aplica en el endpoint, no solo en el helper."""
    allowed = tmp_path / "permitido"
    allowed.mkdir()
    other = tmp_path / "otro"
    other.mkdir()
    config = ApiConfig(**{**api_config.__dict__, "allowed_roots": (allowed,)})
    app = create_app(config)

    with TestClient(app) as client:
        ok = client.post(
            "/sessions",
            json={"root_dir": str(allowed)},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        bad = client.post(
            "/sessions",
            json={"root_dir": str(other)},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

    assert ok.status_code == 201
    assert bad.status_code == 400
    assert "COWORK_ALLOWED_ROOTS" in bad.json()["detail"]


def test_api_rejects_root_that_does_not_exist(client, tmp_path, auth_headers):
    response = client.post(
        "/sessions", json={"root_dir": str(tmp_path / "nope")}, headers=auth_headers
    )

    assert response.status_code == 400


# --- obligatoriedad del token ---------------------------------------------


def test_config_refuses_to_start_without_token(monkeypatch):
    """Sin token la API quedaría abierta: debe fallar al arrancar."""
    monkeypatch.delenv("COWORK_API_TOKEN", raising=False)
    monkeypatch.delenv("COWORK_ALLOW_INSECURE", raising=False)

    with pytest.raises(ConfigurationError, match="COWORK_API_TOKEN"):
        ApiConfig.from_env().validate()


def test_config_allows_explicit_insecure_opt_in(monkeypatch):
    monkeypatch.delenv("COWORK_API_TOKEN", raising=False)
    monkeypatch.setenv("COWORK_ALLOW_INSECURE", "true")

    config = ApiConfig.from_env()
    config.validate()

    assert config.authentication_required is False


def test_config_rejects_short_token():
    config = ApiConfig(
        token="corto",
        allowed_roots=(),
        cors_origins=(),
        state_dir=Path("/tmp/x"),
        approval_timeout_s=60,
        allow_insecure=False,
        max_sessions=5,
    )

    with pytest.raises(ConfigurationError, match="demasiado corto"):
        config.validate()


def test_create_app_refuses_to_start_without_token(monkeypatch, tmp_path):
    monkeypatch.delenv("COWORK_ALLOW_INSECURE", raising=False)
    monkeypatch.setenv("COWORK_STATE_DIR", str(tmp_path / "state"))
    config = ApiConfig(
        token=None,
        allowed_roots=(),
        cors_origins=(),
        state_dir=tmp_path / "state",
        approval_timeout_s=60,
        allow_insecure=False,
        max_sessions=5,
    )

    with pytest.raises(ConfigurationError):
        create_app(config)


def test_insecure_mode_serves_without_token(tmp_path, root_dir):
    """El opt-in explícito habilita el modo sin auth para desarrollo local."""
    config = ApiConfig(
        token=None,
        allowed_roots=(),
        cors_origins=(),
        state_dir=tmp_path / "state",
        approval_timeout_s=10,
        allow_insecure=True,
        max_sessions=5,
    )
    app = create_app(config)

    with TestClient(app) as client:
        health = client.get("/health")
        created = client.post("/sessions", json={"root_dir": str(root_dir), "dry_run": True})

    assert health.json()["authentication_required"] is False
    assert created.status_code == 201


def test_cors_is_not_wildcard():
    """`*` con credenciales expondría la API a cualquier web abierta."""
    from api.config import DEFAULT_CORS_ORIGINS

    assert "*" not in DEFAULT_CORS_ORIGINS


def test_cors_allows_configured_origin(tmp_path, root_dir):
    config = ApiConfig(
        token=TOKEN,
        allowed_roots=(),
        cors_origins=("http://localhost:1420",),
        state_dir=tmp_path / "state",
        approval_timeout_s=10,
        allow_insecure=False,
        max_sessions=5,
    )
    app = create_app(config)

    with TestClient(app) as client:
        response = client.options(
            "/sessions",
            headers={
                "Origin": "http://localhost:1420",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert response.headers.get("access-control-allow-origin") == "http://localhost:1420"


def test_cors_blocks_unconfigured_origin(tmp_path):
    config = ApiConfig(
        token=TOKEN,
        allowed_roots=(),
        cors_origins=("http://localhost:1420",),
        state_dir=tmp_path / "state",
        approval_timeout_s=10,
        allow_insecure=False,
        max_sessions=5,
    )
    app = create_app(config)

    with TestClient(app) as client:
        response = client.options(
            "/sessions",
            headers={
                "Origin": "https://sitio-malicioso.example",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert "access-control-allow-origin" not in response.headers


# --- gestión de sesiones y timeout del HITL --------------------------------


def _manager(tmp_path, approval_timeout_s=10):
    config = ApiConfig(
        token=TOKEN,
        allowed_roots=(),
        cors_origins=(),
        state_dir=tmp_path / "state",
        approval_timeout_s=approval_timeout_s,
        allow_insecure=False,
        max_sessions=5,
    )
    return SessionManager(config, broker=EventBroker())


def _await_approval_request(session, timeout=30.0):
    """Espera a que el worker llegue a ``awaiting_approval``.

    ``decide`` falla con 409 si la sesión todavía no pidió aprobación, así que
    los tests necesitan sincronizarse con el hilo antes de resolver el HITL.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if session.state == "awaiting_approval":
            return
        if session.state in {"failed", "completed", "rejected"}:
            raise AssertionError(f"La sesión terminó en {session.state} sin pedir aprobación")
        time.sleep(0.02)
    raise AssertionError(f"La sesión nunca pidió aprobación (estado: {session.state})")


def test_session_rejects_approval_when_not_awaiting(tmp_path):
    from api.sessions import SessionConflictError

    manager = _manager(tmp_path)
    session = manager.create(tmp_path, recursive=True, dry_run=False, name=None)

    assert session.state == "created"
    with pytest.raises(SessionConflictError):
        session.decide("approve")


def test_session_unknown_id_raises(tmp_path):
    from api.sessions import SessionNotFoundError

    manager = _manager(tmp_path)

    with pytest.raises(SessionNotFoundError):
        manager.get("no-existe")


def test_session_enforces_capacity(tmp_path):
    from api.sessions import SessionCapacityError

    config = ApiConfig(
        token=TOKEN,
        allowed_roots=(),
        cors_origins=(),
        state_dir=tmp_path / "state",
        approval_timeout_s=10,
        allow_insecure=False,
        max_sessions=2,
    )
    manager = SessionManager(config, broker=EventBroker())

    for _ in range(2):
        manager.create(tmp_path, recursive=True, dry_run=True, name=None)
    with pytest.raises(SessionCapacityError):
        manager.create(tmp_path, recursive=True, dry_run=True, name=None)


def test_hitl_times_out_and_rejects(tmp_path, root_dir):
    """Si nadie aprueba a tiempo, el worker rechaza en lugar de esperar para siempre."""
    manager = _manager(tmp_path, approval_timeout_s=1)
    session = manager.create(root_dir, recursive=True, dry_run=False, name=None)
    session.start()
    session.wait(timeout=30)

    assert session.state == "rejected"
    assert session.approval is not None
    assert session.approval.decision == "timeout"
    assert session.approval.approved is False
    assert session.applied == []


def test_hitl_decision_unblocks_the_worker(tmp_path, root_dir):
    """Aprobar desde otro hilo (como haría el endpoint) desbloquea al agente."""
    manager = _manager(tmp_path, approval_timeout_s=30)
    session = manager.create(root_dir, recursive=True, dry_run=False, name=None)
    session.start()

    # Esperamos a que pida aprobación y decidimos como lo haría el HTTP handler.
    _await_approval_request(session)

    session.decide("approve")
    session.wait(timeout=30)

    assert session.state == "completed"
    assert session.applied
    assert (root_dir / "PDFs" / "informe.pdf").exists()


def test_session_error_is_captured_not_lost(tmp_path, root_dir, monkeypatch):
    """Una excepción en el hilo debe reflejarse en el estado, no perderse."""
    manager = _manager(tmp_path)

    def boom(*_args, **_kwargs):
        raise RuntimeError("fallo simulado del scanner")

    monkeypatch.setattr("api.sessions.CoworkAgent.scan", boom)
    session = manager.create(root_dir, recursive=True, dry_run=True, name=None)
    session.start()
    session.wait(timeout=30)

    assert session.state == "failed"
    assert "fallo simulado" in (session.error or "")
    view = session.view()
    assert view.error is not None
    events = manager.broker.history(session.session_id)
    assert any(e.type == "error" for e in events)


def test_delete_running_session_conflicts(tmp_path, root_dir):
    from api.sessions import SessionConflictError

    manager = _manager(tmp_path, approval_timeout_s=30)
    session = manager.create(root_dir, recursive=True, dry_run=False, name=None)
    session.start()

    try:
        with pytest.raises(SessionConflictError):
            manager.delete(session.session_id)
    finally:
        # El worker está bloqueado esperando el HITL: hay que resolverlo para
        # que el hilo termine y el test no deje nada colgado.
        _await_approval_request(session)
        session.decide("reject")
        session.wait(timeout=30)


def test_delete_finished_session_drops_events(tmp_path, root_dir):
    manager = _manager(tmp_path)
    session = manager.create(root_dir, recursive=True, dry_run=True, name=None)
    session.start()
    session.wait(timeout=30)
    session_id = session.session_id

    manager.delete(session_id)

    assert manager.broker.history(session_id) == []
    with pytest.raises(KeyError):
        manager.get(session_id)


def test_audit_log_records_the_session_lifecycle(tmp_path, root_dir):
    """El audit log debe permitir reconstruir qué pasó y quién lo aprobó."""
    import json

    manager = _manager(tmp_path)
    session = manager.create(root_dir, recursive=True, dry_run=True, name=None)
    session.start()
    session.wait(timeout=30)

    log_path = manager.config.state_dir / f"{session.session_id}.jsonl"
    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    event_types = [r["event_type"] for r in records]

    assert "SESSION_CREATED" in event_types
    assert "PLAN_GENERATED" in event_types
    assert "DRY_RUN" in event_types
    # El audit logger de la sesión agrega el id a *todos* sus registros (incluidos
    # los que emite el núcleo del agente, que no conoce el concepto de sesión),
    # de modo que el log se puede correlacionar sin ambigüedad.
    assert all(r["session_id"] == session.session_id for r in records)
