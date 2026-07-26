"""The scenario picker: the roleplays a session can start.

A read-only listing of the registry (app/agents/scenarios.py). The persona and
opening line are deliberately omitted - they are the Tutor's instructions, not the
learner's menu - so the public shape stays a title, a level and the Redemittel the
scenario practises.
"""

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.agents.scenarios import Scenario, list_scenarios

router = APIRouter(tags=["session"])


class ScenarioSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Stable slug used to start a session on this scenario.")
    title: str = Field(description="German title shown in the picker.")
    level: str = Field(description="CEFR rung the Tutor is pinned to, e.g. 'A2'.")
    redemittel: list[str] = Field(
        description="The chunks this scenario is meant to practise."
    )

    @classmethod
    def of(cls, scenario: Scenario) -> "ScenarioSummary":
        return cls(
            id=scenario.id,
            title=scenario.title,
            level=scenario.level,
            redemittel=list(scenario.redemittel),
        )


@router.get(
    "/scenarios",
    response_model=list[ScenarioSummary],
    summary="List the roleplay scenarios a session can start",
)
async def get_scenarios() -> list[ScenarioSummary]:
    return [ScenarioSummary.of(s) for s in list_scenarios()]
