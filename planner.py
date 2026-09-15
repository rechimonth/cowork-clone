from __future__ import annotations

from ai_engine import propose_execution_plan
from models import PlannerInput, PlannerOutput


def plan_actions(planner_input: PlannerInput) -> PlannerOutput:
    # MVP: heurísticas internas (ai_engine) para asegurar consistencia.
    return propose_execution_plan(planner_input)
