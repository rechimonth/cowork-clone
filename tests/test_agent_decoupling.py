"""Tests del desacoplamiento de I/O (Fase 4: preparacion para FastAPI/Tauri).

`CoworkAgent` debe poder ejecutarse sin terminal: la salida va a callbacks
inyectados y la aprobacion puede resolverse desde fuera (p. ej. un endpoint
HTTP). Estos tests simulan ese flujo end-to-end, ejerciendo el codigo real de
escaneo, planificacion y ejecucion.
"""

from __future__ import annotations

import pytest

from main import AgentCallbacks, CoworkAgent
from user_validation import ApprovalDecision


class _Collector:
    """Captura la salida del agente en memoria, como haria un WebSocket."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def __call__(self, message: str) -> None:
        self.messages.append(message)

    @property
    def text(self) -> str:
        return "\n".join(self.messages)


def _make_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "2026-01-15 factura.txt").write_text("contenido")
    (root / "notas.txt").write_text("contenido")
    return root


def test_agent_output_goes_to_injected_callback(tmp_path, capsys):
    root = _make_root(tmp_path)
    out = _Collector()
    agent = CoworkAgent(
        root_dir=str(root),
        dry_run=True,
        callbacks=AgentCallbacks(output_fn=out),
    )

    agent.scan()
    agent.plan_actions()
    agent.run()

    # Nada debe llegar a stdout real: todo pasa por el callback.
    assert capsys.readouterr().out == ""
    assert out.messages


def test_agent_never_reads_terminal_when_approval_provider_is_injected(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "user_validation._read_answer",
        lambda *a, **k: pytest.fail("el agente no debe leer stdin en modo API"),
    )
    monkeypatch.setattr(
        "builtins.input",
        lambda *a, **k: pytest.fail("el agente no debe usar input() en modo API"),
    )
    root = _make_root(tmp_path)
    out = _Collector()
    agent = CoworkAgent(
        root_dir=str(root),
        dry_run=True,
        callbacks=AgentCallbacks(
            output_fn=out,
            approval_provider=lambda plan: ApprovalDecision(approved=True, decision="http_api"),
        ),
    )

    agent.run()

    assert out.messages


def test_agent_can_pause_between_steps(tmp_path):
    """Un servidor puede intercalar su propia logica entre plan y aprobacion."""
    root = _make_root(tmp_path)
    agent = CoworkAgent(
        root_dir=str(root),
        dry_run=True,
        callbacks=AgentCallbacks(output_fn=_Collector(), input_fn=lambda _: "r"),
    )

    agent.scan()
    plan = agent.plan_actions()

    # La pausa ocurre aqui: en produccion el plan viaja al frontend y la
    # decision llega por HTTP. Sin approval_provider se usa el input inyectado,
    # que en este caso rechaza explicitamente.
    assert agent.plan is plan
    decision = agent.request_approval(plan)
    assert decision.approved is False
    assert decision.decision == "rejected"


def test_request_approval_without_plan_raises(tmp_path):
    agent = CoworkAgent(root_dir=str(tmp_path), callbacks=AgentCallbacks(output_fn=_Collector()))

    with pytest.raises(ValueError):
        agent.request_approval()


def test_execute_without_plan_raises(tmp_path):
    agent = CoworkAgent(root_dir=str(tmp_path), callbacks=AgentCallbacks(output_fn=_Collector()))

    with pytest.raises(ValueError):
        agent.execute()


def test_scan_is_idempotent(tmp_path):
    root = _make_root(tmp_path)
    agent = CoworkAgent(root_dir=str(root), callbacks=AgentCallbacks(output_fn=_Collector()))

    first = agent.scan()
    second = agent.scan()

    assert len(first) == len(second) == 2


def test_dry_run_does_not_modify_filesystem(tmp_path):
    root = _make_root(tmp_path)
    before = sorted(p.name for p in root.iterdir())
    agent = CoworkAgent(
        root_dir=str(root),
        dry_run=True,
        callbacks=AgentCallbacks(
            output_fn=_Collector(),
            approval_provider=lambda plan: ApprovalDecision(approved=True, decision="http_api"),
        ),
    )

    agent.run()

    assert sorted(p.name for p in root.iterdir()) == before


def test_rejected_plan_is_not_executed(tmp_path):
    root = _make_root(tmp_path)
    before = sorted(p.name for p in root.iterdir())
    agent = CoworkAgent(
        root_dir=str(root),
        callbacks=AgentCallbacks(
            output_fn=_Collector(),
            approval_provider=lambda plan: ApprovalDecision(approved=False, decision="http_api"),
        ),
    )

    assert agent.run() is not None
    assert sorted(p.name for p in root.iterdir()) == before


def test_callbacks_default_to_terminal_io():
    callbacks = AgentCallbacks()

    assert callbacks.output_fn is print
    assert callbacks.input_fn is input
    assert callbacks.approval_provider is None
