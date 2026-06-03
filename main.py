from __future__ import annotations

import argparse
import os
from pathlib import Path

from audit_logger import AuditLogger
from file_manager import build_file_items, scan_directory
from models import PlannerInput
from os_commands import execute_plan
from planner import plan_actions
from user_validation import format_plan, request_user_approval


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="cowork-clone MVP: scan -> plan -> HITL -> execute"
    )
    parser.add_argument("root_dir", help="Directorio a escanear")
    parser.add_argument(
        "--no-recursive", action="store_true", help="No escanear recursivamente"
    )
    parser.add_argument(
        "--log", default="./cowork-clone.log", help="Ruta del log de auditoría"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="No ejecutar cambios locales"
    )

    args = parser.parse_args(argv)

    root_dir = str(Path(args.root_dir).resolve())
    recursive = not args.no_recursive
    dry_run = bool(args.dry_run)

    logger = AuditLogger(os.path.abspath(args.log))
    logger.log_event(
        "START", {"root_dir": root_dir, "recursive": recursive, "dry_run": dry_run}
    )

    # 1) Scan
    paths = scan_directory(root_dir, recursive=recursive)
    items = build_file_items(root_dir, paths)

    # 2) Plan
    planner_input = PlannerInput(root_dir=root_dir, files=items)
    logger.log_event("FILES_SCANNED", {"count": len(items)})

    plan_output = plan_actions(planner_input)
    plan = plan_output.plan

    logger.log_event("PLAN_GENERATED", {"summary": plan.summary})

    if dry_run:
        print("\nDRY RUN ACTIVADO")
        print(format_plan(plan))
        logger.log_event(
            "DRY_RUN",
            {
                "summary": plan.summary,
                "plan_id": plan.plan_id,
                "execution_id": plan.execution_id,
            },
        )
        return

    # 3) HITL
    approval = request_user_approval(plan)
    logger.log_event(
        "USER_APPROVAL",
        {"approved": approval.approved, "decision": approval.decision},
    )

    if not approval.approved:
        print("\nOperación cancelada por el usuario. No se ejecutó nada.")
        return

    # 4) Execute safe plan
    execute_plan(plan, root_dir=root_dir)
    logger.log_event(
        "EXECUTION_DONE",
        {
            "summary": plan.summary,
            "plan_id": plan.plan_id,
            "execution_id": plan.execution_id,
        },
    )

    print("\nEjecución completada.")


if __name__ == "__main__":
    main()

