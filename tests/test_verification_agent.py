"""Tests for the cross-cutting verification (critic) agent."""

from src.agents.factory import AgentFactory
from src.agents.models import AgentTask
from src.agents.verification_agent import VerificationAgent
from src.api.services.agent_execution_service import AgentExecutionService
from src.models.agent_api_models import AgentType


class _FakeGemini:
    def __init__(self) -> None:
        self.last_prompt = ""

    async def generate_content(self, prompt: str) -> str:
        self.last_prompt = prompt
        return "VERDICT: REVISE\nISSUES: 1. Claimed 2+2=5 (high) -> should be 4."


def test_verification_agent_registered() -> None:
    agent = AgentFactory.create_agent("verification")
    assert isinstance(agent, VerificationAgent)
    assert agent.get_agent_type() == "verification"
    assert AgentType.VERIFICATION == "verification"


async def test_verification_reviews_provided_content() -> None:
    gem = _FakeGemini()
    agent = VerificationAgent(gemini_service=gem)
    result = await agent.execute(
        AgentTask(
            id="t",
            agent_type="verification",
            input_data={"query": "review this", "content": "The sum 2+2 equals 5."},
        ),
    )
    assert result.status == "success"
    assert "VERDICT" in result.output["content"]
    # It reviews the supplied content, not just the query.
    assert "2+2 equals 5" in gem.last_prompt


async def test_agent_catalog_matches_enum() -> None:
    service = AgentExecutionService()
    agents = await service.get_agent_list()
    assert len(agents) == len(AgentType)
    assert any(a.agent_type == AgentType.VERIFICATION for a in agents)
