from main import main
from models import ExecutionPlan


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
