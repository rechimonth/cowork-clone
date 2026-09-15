from audit_logger import AuditLogger
from main import AgentCallbacks, CoworkAgent, main
from models import ExecutionPlan
from user_validation import ApprovalDecision


def test_execution_plan_allows_only_safe_ops():
    plan = ExecutionPlan(summary="x")
    assert plan.allowed_operations == ["mkdir", "rename"]


def test_dry_run_does_not_execute(monkeypatch, tmp_path, capsys):
    src = tmp_path / "2026-01-02 factura.pdf"
    src.write_bytes(b"%PDF-1.4")
    log_path = tmp_path / "audit.jsonl"

    def fail_execute(*args, **kwargs):
        raise AssertionError("execute_plan must not run during dry-run")

    monkeypatch.setattr("main.execute_plan", fail_execute)

    main([str(tmp_path), "--dry-run", "--log", str(log_path)])

    captured = capsys.readouterr()
    assert "DRY RUN ACTIVADO" in captured.out
    assert "PLAN PROPUESTO" in captured.out
    assert src.exists()
    assert not (tmp_path / "PDFs").exists()


def test_agent_exposes_separate_steps(tmp_path):
    (tmp_path / "2026-01-02 factura.pdf").write_bytes(b"%PDF-1.4")

    agent = CoworkAgent(root_dir=str(tmp_path), dry_run=False)
    items = agent.scan()
    assert len(items) == 1

    plan = agent.plan_actions()
    assert isinstance(plan, ExecutionPlan)


def test_agent_uses_injected_callbacks_and_approval_provider(tmp_path):
    src = tmp_path / "2026-01-02 factura.pdf"
    src.write_bytes(b"%PDF-1.4")
    log_path = tmp_path / "audit.jsonl"

    outputs: list[str] = []
    approvals: list[ExecutionPlan] = []

    def provider(plan: ExecutionPlan) -> ApprovalDecision:
        approvals.append(plan)
        return ApprovalDecision(approved=False, decision="http_api")

    agent = CoworkAgent(
        root_dir=str(tmp_path),
        logger=AuditLogger(str(log_path)),
        callbacks=AgentCallbacks(
            output_fn=outputs.append,
            approval_provider=provider,
        ),
    )
    agent.run()

    assert approvals, "el agent debe delegar el HITL en approval_provider"
    log_text = log_path.read_text()
    assert "USER_APPROVAL" in log_text and "http_api" in log_text
    # Rechazado => no se ejecuta la reorganización.
    assert not (tmp_path / "PDFs").exists()
    assert src.exists()
    assert any("cancelada" in line for line in outputs)


def test_agent_executes_when_approved(tmp_path):
    src = tmp_path / "2026-01-02 factura.pdf"
    src.write_bytes(b"%PDF-1.4")

    agent = CoworkAgent(
        root_dir=str(tmp_path),
        callbacks=AgentCallbacks(
            approval_provider=lambda plan: ApprovalDecision(True, "approved")
        ),
    )
    agent.run()

    assert not src.exists()
    assert list((tmp_path / "PDFs").glob("*.pdf"))


def test_agent_requires_plan_before_execute(tmp_path):
    import pytest

    agent = CoworkAgent(root_dir=str(tmp_path))
    with pytest.raises(ValueError):
        agent.execute()
