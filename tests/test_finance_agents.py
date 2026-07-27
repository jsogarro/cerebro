"""Tests for the finance agent domain.

Covers the three finance worker agents, their factory registration, the
FinanceSupervisor, and MASR routing of finance queries to the finance domain.
All agents are LLM-reasoning only (no external API keys / datasets).
"""

import pytest

from src.agents.factory import AgentFactory
from src.agents.finance_agents import (
    FinancialAnalysisAgent,
    RiskAssessmentAgent,
    ValuationAgent,
)
from src.agents.models import AgentTask
from src.agents.supervisors.finance_supervisor import FinanceSupervisor
from src.agents.supervisors.supervisor_factory import SupervisorFactory
from src.ai_brain.router.masr import MASRouter
from src.api.services.component_catalog import build_application_component_registry
from src.core.kernel.component_keys import SUPERVISOR_KEYS


class _FakeGemini:
    async def generate_content(self, prompt: str) -> str:
        return "Detailed financial reasoning based on the provided figures."


def _task(query: str) -> AgentTask:
    return AgentTask(id="t1", agent_type="finance", input_data={"query": query})


@pytest.mark.parametrize(
    "agent_type,cls",
    [
        ("financial_analysis", FinancialAnalysisAgent),
        ("valuation", ValuationAgent),
        ("risk_assessment", RiskAssessmentAgent),
    ],
)
def test_finance_agents_registered_in_factory(agent_type, cls) -> None:
    agent = AgentFactory.create_agent(agent_type)
    assert isinstance(agent, cls)
    assert agent.get_agent_type() == agent_type


async def test_agent_execute_uses_llm_content() -> None:
    agent = FinancialAnalysisAgent(gemini_service=_FakeGemini())
    result = await agent.execute(_task("Current assets 500, current liabilities 250"))
    assert result.status == "success"
    assert result.output["content"] == (
        "Detailed financial reasoning based on the provided figures."
    )
    assert result.confidence >= 0.8
    assert await agent.validate_result(result) is True


async def test_agent_empty_query_is_error() -> None:
    agent = ValuationAgent(gemini_service=_FakeGemini())
    result = await agent.execute(_task("   "))
    assert result.status != "success" or not result.output.get("content")


async def test_agent_without_llm_returns_wellformed_fallback() -> None:
    agent = RiskAssessmentAgent(gemini_service=None)
    result = await agent.execute(_task("Assess a tech-concentrated portfolio"))
    assert result.status == "success"
    assert result.output["agent_type"] == "risk_assessment"
    assert result.output["content"]  # non-empty


def test_finance_supervisor_registered_and_has_workers() -> None:
    factory = SupervisorFactory()
    spec = factory.get_supervisor_spec("finance")
    assert spec is not None
    assert spec.supervisor_class is FinanceSupervisor

    supervisor = FinanceSupervisor()
    assert supervisor.supervisor_type == "finance"
    assert set(supervisor.worker_definitions) == {
        "financial_analysis",
        "valuation",
        "risk_assessment",
    }
    assert supervisor.workflow_graph is not None


async def test_masr_routes_finance_queries_to_finance_supervisor() -> None:
    router = MASRouter()
    dcf = await router.route(
        "Value this company with a DCF: FCF 100M growing 5%, discount rate 10%",
        context={},
    )
    risk = await router.route(
        "Assess the risks of a portfolio concentrated in tech equities", context={}
    )
    assert dcf.agent_allocation.supervisor_type == "finance"
    assert risk.agent_allocation.supervisor_type == "finance"


def test_application_catalog_includes_finance_supervisor() -> None:
    registry = build_application_component_registry()

    assert registry.resolve(SUPERVISOR_KEYS["finance"]) is FinanceSupervisor
