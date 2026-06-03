from pathlib import Path

from models import FileItem, PlannerInput
from planner import plan_actions


def test_planner_generates_safe_execution_plan(tmp_path):
    src = tmp_path / "2026-01-15 invoice.pdf"
    src.write_bytes(b"%PDF-1.4")

    items = [
        FileItem(
            path=str(src),
            filename=src.name,
            ext=".pdf",
            preview_text="Factura Amazon",
            size_bytes=src.stat().st_size,
            relpath=src.name,
        )
    ]

    out = plan_actions(PlannerInput(root_dir=str(tmp_path), files=items))
    plan = out.plan

    assert plan.plan_id
    assert plan.execution_id
    assert plan.created_at
    assert plan.allowed_operations == ["mkdir", "rename"]
    assert len(plan.create_dirs) == 1
    assert len(plan.rename_files) == 1
    assert Path(plan.rename_files[0].dst).parent == (tmp_path / "PDFs").resolve()
    assert plan.rename_files[0].type == "rename"
