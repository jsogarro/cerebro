"""Tests for real Content/Analytics worker agents.

The Content and Analytics supervisors previously used placeholder
``agent_class=type(...)`` workers that took no constructor args, so worker
instantiation failed and the workers did no real LLM work. This adds real worker
agents (registered in the factory and wired into the supervisors).
"""

import pytest

from src.agents.analytics_agents import (
    DataAnalysisAgent,
    InsightSynthesisAgent,
    StatisticalModelingAgent,
)
from src.agents.content_agents import (
    ContentPlanningAgent,
    DraftingAgent,
    EditingAgent,
    OptimizationAgent,
)
from src.agents.factory import AgentFactory
from src.agents.models import AgentTask
from src.agents.supervisors.analytics_supervisor import AnalyticsSupervisor
from src.agents.supervisors.content_supervisor import ContentSupervisor


class _FakeGemini:
    async def generate_content(self, prompt: str) -> str:
        return "Real generated content for the request."


ALL = {
    "content_planning": ContentPlanningAgent,
    "drafting": DraftingAgent,
    "editing": EditingAgent,
    "optimization": OptimizationAgent,
    "data_analysis": DataAnalysisAgent,
    "statistical_modeling": StatisticalModelingAgent,
    "insight_synthesis": InsightSynthesisAgent,
}


@pytest.mark.parametrize("agent_type,cls", list(ALL.items()))
def test_agents_registered_in_factory(agent_type, cls) -> None:
    agent = AgentFactory.create_agent(agent_type)
    assert isinstance(agent, cls)
    assert agent.get_agent_type() == agent_type


async def test_agent_execute_returns_real_content() -> None:
    agent = DraftingAgent(gemini_service=_FakeGemini())
    result = await agent.execute(
        AgentTask(id="t", agent_type="drafting", input_data={"query": "write X"}),
    )
    assert result.status == "success"
    assert result.output["content"] == "Real generated content for the request."
    assert await agent.validate_result(result) is True


def test_content_supervisor_uses_real_worker_classes() -> None:
    sup = ContentSupervisor()
    for wt, cls in {
        "content_planning": ContentPlanningAgent,
        "drafting": DraftingAgent,
        "editing": EditingAgent,
        "optimization": OptimizationAgent,
    }.items():
        wd = sup.worker_definitions[wt]
        assert wd.agent_class is cls  # real class, not a placeholder type()


def test_analytics_supervisor_uses_real_worker_classes() -> None:
    sup = AnalyticsSupervisor()
    for wt, cls in {
        "data_analysis": DataAnalysisAgent,
        "statistical_modeling": StatisticalModelingAgent,
        "insight_synthesis": InsightSynthesisAgent,
    }.items():
        wd = sup.worker_definitions[wt]
        assert wd.agent_class is cls


def test_worker_classes_are_instantiable_with_service_args() -> None:
    """The placeholder bug: type(...) took no args and failed to instantiate."""
    for cls in ALL.values():
        agent = cls(gemini_service=None, cache_client=None, config={})
        assert agent is not None
