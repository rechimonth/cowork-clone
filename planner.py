from __future__ import annotations

from models import PlannerInput, PlannerOutput
from ai_engine import propose_execution_plan


def plan_actions(planner_input: PlannerInput) -> PlannerOutput:
    # MVP: heurísticas internas (ai_engine) para asegurar consistencia.
    return propose_execution_plan(planner_input)

