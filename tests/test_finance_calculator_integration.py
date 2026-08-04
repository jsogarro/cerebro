"""Tests for finance agents calculator tool integration.

Verifies that ValuationAgent and FinancialAnalysisAgent produce exact
computed values when structured parameters are provided, and that
those values appear in both the output and the prompt sent to the model.
"""

import pytest

from src.agents.finance_agents import FinancialAnalysisAgent, ValuationAgent
from src.agents.models import AgentTask
from src.core.config import settings


@pytest.fixture(autouse=True)
def _no_live_provider_routing(monkeypatch):
    """These agents execute a plain, plan-less ``AgentTask``. Without this,
    a real MULTI_PROVIDER_ROUTING_ENABLED/OPENROUTER_API_KEY in the test
    environment routes execute() through a live ModelRouter instead of the
    _PromptCapturingGemini mock below, making these tests nondeterministic
    and dependent on a paid network call (this file previously hung the
    full suite on an unbounded live request)."""
    monkeypatch.setattr(settings, "MULTI_PROVIDER_ROUTING_ENABLED", False)


class _PromptCapturingGemini:
    """Mock Gemini service that captures the prompt for assertion."""

    def __init__(self):
        self.last_prompt = None

    async def generate_content(self, prompt: str) -> str:
        self.last_prompt = prompt
        return "LLM analysis incorporating the precomputed values"


def _task(agent_type: str, query: str, parameters: dict | None = None) -> AgentTask:
    input_data = {"query": query}
    if parameters:
        input_data["parameters"] = parameters
    return AgentTask(id="t1", agent_type=agent_type, input_data=input_data)


# ValuationAgent tests


async def test_valuation_agent_with_dcf_params_returns_exact_computed_dcf():
    """ValuationAgent with DCF params returns exact computed.dcf value."""
    mock_gemini = _PromptCapturingGemini()
    agent = ValuationAgent(gemini_service=mock_gemini)

    params = {"fcf_next": 100, "growth_rate": 0.05, "discount_rate": 0.10}
    result = await agent.execute(_task("valuation", "Value this company", params))

    assert result.status == "success"
    assert "computed" in result.output
    assert "dcf" in result.output["computed"]

    dcf_data = result.output["computed"]["dcf"]
    assert dcf_data["present_value"] == 2000.0
    assert dcf_data["method"] == "gordon_growth"
    assert dcf_data["inputs"]["fcf_next"] == 100
    assert dcf_data["inputs"]["growth_rate"] == 0.05
    assert dcf_data["inputs"]["discount_rate"] == 0.10


async def test_valuation_agent_with_dcf_params_includes_exact_value_in_prompt():
    """ValuationAgent includes exact DCF value in the prompt sent to the model."""
    mock_gemini = _PromptCapturingGemini()
    agent = ValuationAgent(gemini_service=mock_gemini)

    params = {"fcf_next": 100, "growth_rate": 0.05, "discount_rate": 0.10}
    await agent.execute(_task("valuation", "Value this company", params))

    assert mock_gemini.last_prompt is not None
    assert "Precomputed exact values:" in mock_gemini.last_prompt

    # Verify JSON structure is in prompt
    assert '"dcf"' in mock_gemini.last_prompt
    assert '"present_value": 2000.0' in mock_gemini.last_prompt


async def test_valuation_agent_with_explicit_dcf_operation():
    """ValuationAgent handles explicit operation='dcf' parameter."""
    mock_gemini = _PromptCapturingGemini()
    agent = ValuationAgent(gemini_service=mock_gemini)

    params = {
        "operation": "dcf",
        "fcf_next": 50,
        "growth_rate": 0.03,
        "discount_rate": 0.08,
    }
    result = await agent.execute(_task("valuation", "DCF valuation", params))

    assert result.status == "success"
    assert "computed" in result.output
    assert result.output["computed"]["dcf"]["present_value"] == 1000.0


async def test_valuation_agent_without_params_has_no_computed():
    """ValuationAgent without parameters produces no computed field."""
    mock_gemini = _PromptCapturingGemini()
    agent = ValuationAgent(gemini_service=mock_gemini)

    result = await agent.execute(_task("valuation", "Value this company", None))

    assert result.status == "success"
    assert "computed" not in result.output
    assert "Precomputed exact values:" not in mock_gemini.last_prompt


async def test_valuation_agent_with_incomplete_dcf_params_has_no_computed():
    """ValuationAgent with missing DCF params gracefully skips computation."""
    mock_gemini = _PromptCapturingGemini()
    agent = ValuationAgent(gemini_service=mock_gemini)

    params = {"fcf_next": 100, "growth_rate": 0.05}  # missing discount_rate
    result = await agent.execute(_task("valuation", "Value this", params))

    assert result.status == "success"
    assert "computed" not in result.output


async def test_valuation_agent_with_invalid_dcf_params_has_no_computed():
    """ValuationAgent with invalid DCF params (e.g. discount <= growth) skips computation."""
    mock_gemini = _PromptCapturingGemini()
    agent = ValuationAgent(gemini_service=mock_gemini)

    params = {"fcf_next": 100, "growth_rate": 0.10, "discount_rate": 0.05}
    result = await agent.execute(_task("valuation", "Value this", params))

    assert result.status == "success"
    assert "computed" not in result.output


# FinancialAnalysisAgent tests


async def test_financial_analysis_agent_with_values_returns_exact_computed_ratios():
    """FinancialAnalysisAgent with values params returns exact computed.ratios."""
    mock_gemini = _PromptCapturingGemini()
    agent = FinancialAnalysisAgent(gemini_service=mock_gemini)

    params = {
        "values": {
            "current_assets": 500,
            "current_liabilities": 250,
            "inventory": 100,
            "total_debt": 300,
            "total_equity": 600,
        }
    }
    result = await agent.execute(_task("financial_analysis", "Analyze health", params))

    assert result.status == "success"
    assert "computed" in result.output
    assert "ratios" in result.output["computed"]

    ratios_data = result.output["computed"]["ratios"]
    assert ratios_data["ratios"]["current_ratio"] == 2.0
    assert ratios_data["ratios"]["quick_ratio"] == 1.6
    assert ratios_data["ratios"]["debt_to_equity"] == 0.5


async def test_financial_analysis_agent_includes_exact_ratios_in_prompt():
    """FinancialAnalysisAgent includes exact ratios in the prompt sent to the model."""
    mock_gemini = _PromptCapturingGemini()
    agent = FinancialAnalysisAgent(gemini_service=mock_gemini)

    params = {
        "values": {
            "current_assets": 500,
            "current_liabilities": 250,
        }
    }
    await agent.execute(_task("financial_analysis", "Analyze", params))

    assert mock_gemini.last_prompt is not None
    assert "Precomputed exact values:" in mock_gemini.last_prompt
    assert '"ratios"' in mock_gemini.last_prompt
    assert '"current_ratio": 2.0' in mock_gemini.last_prompt


async def test_financial_analysis_agent_without_params_has_no_computed():
    """FinancialAnalysisAgent without parameters produces no computed field."""
    mock_gemini = _PromptCapturingGemini()
    agent = FinancialAnalysisAgent(gemini_service=mock_gemini)

    result = await agent.execute(_task("financial_analysis", "Analyze this", None))

    assert result.status == "success"
    assert "computed" not in result.output
    assert "Precomputed exact values:" not in mock_gemini.last_prompt


async def test_financial_analysis_agent_with_empty_values_has_no_computed():
    """FinancialAnalysisAgent with empty values dict gracefully skips computation."""
    mock_gemini = _PromptCapturingGemini()
    agent = FinancialAnalysisAgent(gemini_service=mock_gemini)

    params = {"values": {}}
    result = await agent.execute(_task("financial_analysis", "Analyze", params))

    assert result.status == "success"
    assert "computed" not in result.output


async def test_financial_analysis_agent_with_non_dict_values_has_no_computed():
    """FinancialAnalysisAgent with non-dict values gracefully skips computation."""
    mock_gemini = _PromptCapturingGemini()
    agent = FinancialAnalysisAgent(gemini_service=mock_gemini)

    params = {"values": "not a dict"}
    result = await agent.execute(_task("financial_analysis", "Analyze", params))

    assert result.status == "success"
    assert "computed" not in result.output


# Backward compatibility tests


async def test_existing_finance_tests_still_pass():
    """Verify existing finance agent tests still pass (no parameters)."""
    mock_gemini = _PromptCapturingGemini()
    agent = ValuationAgent(gemini_service=mock_gemini)

    result = await agent.execute(_task("valuation", "Value this company with DCF"))

    assert result.status == "success"
    assert (
        result.output["content"] == "LLM analysis incorporating the precomputed values"
    )
    assert "computed" not in result.output


async def test_llm_worker_base_precompute_default_returns_none():
    """LLMWorkerAgentBase._precompute returns None by default."""
    from src.agents.llm_worker_base import LLMWorkerAgentBase

    class TestAgent(LLMWorkerAgentBase):
        agent_type = "test"

        def _build_prompt(self, query: str, task: AgentTask) -> str:
            return "test prompt"

    agent = TestAgent()
    task = _task("test", "test query")
    assert agent._precompute(task) is None
