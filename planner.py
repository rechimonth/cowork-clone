from __future__ import annotations

from ai_engine import FileAnalysisProvider, propose_execution_plan
from models import PlannerInput, PlannerOutput


def plan_actions(
    planner_input: PlannerInput,
    llm: FileAnalysisProvider | None = None,
) -> PlannerOutput:
    """Genera el plan de ejecución.

    ``llm`` permite inyectar el proveedor de clasificación. Sin argumento se
    conserva el comportamiento histórico (``OllamaProvider`` por defecto en
    ``propose_execution_plan``). Un llamador que necesite latencia acotada y
    determinismo —la API HTTP, por ejemplo— puede pasar
    ``OfflineFileAnalysisProvider`` y evitar depender de la red.
    """
    return propose_execution_plan(planner_input, llm=llm)
