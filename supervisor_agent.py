from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from browser_agent import BrowserAgent, BrowserSafetyError, BrowserSnapshot
from file_manager import build_file_items, scan_directory
from models import ExecutionPlan, PlannerInput
from planner import plan_actions


@dataclass(frozen=True)
class SupervisorReport:
    file_plan: ExecutionPlan | None
    browser_snapshot: BrowserSnapshot | None
    notes: list[str]


class SupervisorAgent:
    """Coordinates planning and read-only inspection without executing actions."""

    def __init__(self, *, browser_agent: BrowserAgent | None = None):
        self.browser_agent = browser_agent or BrowserAgent()

    def plan_files(self, root_dir: str, *, recursive: bool = True) -> ExecutionPlan:
        root = str(Path(root_dir).resolve())
        paths = scan_directory(root, recursive=recursive)
        items = build_file_items(root, paths)
        return plan_actions(PlannerInput(root_dir=root, files=items)).plan

    def inspect_url(self, url: str) -> BrowserSnapshot:
        return self.browser_agent.inspect_page(url)

    def create_report(
        self,
        *,
        root_dir: str | None = None,
        url: str | None = None,
        recursive: bool = True,
    ) -> SupervisorReport:
        notes: list[str] = []
        file_plan: ExecutionPlan | None = None
        browser_snapshot: BrowserSnapshot | None = None

        if root_dir:
            try:
                file_plan = self.plan_files(root_dir, recursive=recursive)
                notes.append("Plan de archivos generado. No fue ejecutado.")
            except Exception as exc:
                notes.append(f"No se pudo generar plan de archivos: {exc}")

        if url:
            try:
                browser_snapshot = self.inspect_url(url)
                notes.append("Página inspeccionada en modo solo lectura.")
            except BrowserSafetyError as exc:
                notes.append(f"BrowserAgent bloqueó la URL: {exc}")
            except Exception as exc:
                notes.append(f"No se pudo inspeccionar URL: {exc}")

        if not root_dir and not url:
            notes.append("No se recibió root_dir ni url.")

        notes.append("Toda ejecución debe pasar por Plan -> HITL -> TransactionManager -> Auditoría.")
        return SupervisorReport(file_plan=file_plan, browser_snapshot=browser_snapshot, notes=notes)
