"""Tests for the deterministic finance-math tool and calculator agent."""

import math

import pytest

from src.agents.factory import AgentFactory
from src.agents.financial_calculator_agent import FinancialCalculatorAgent
from src.agents.models import AgentTask
from src.agents.tools import finance_math as fm
from src.models.agent_api_models import AgentType


def test_dcf_gordon_growth_exact() -> None:
    # 100 / (0.10 - 0.05) = 2000
    assert fm.dcf_gordon_growth(100, 0.05, 0.10).present_value == 2000.0


def test_dcf_requires_discount_gt_growth() -> None:
    with pytest.raises(ValueError):
        fm.dcf_gordon_growth(100, 0.10, 0.10)


def test_npv_exact() -> None:
    # -100 + 60/1.1 + 60/1.21 = 4.13223...
    result = fm.npv(0.10, [-100, 60, 60])
    assert math.isclose(result.npv, 4.1322, abs_tol=1e-3)


def test_financial_ratios() -> None:
    r = fm.financial_ratios(
        {
            "current_assets": 500,
            "current_liabilities": 250,
            "inventory": 100,
            "total_debt": 300,
            "total_equity": 400,
            "net_income": 80,
            "revenue": 1000,
        },
    ).ratios
    assert r["current_ratio"] == 2.0
    assert r["quick_ratio"] == 1.6
    assert r["debt_to_equity"] == 0.75
    assert r["net_margin"] == 0.08
    assert r["return_on_equity"] == 0.2


def test_ratios_reports_missing_inputs() -> None:
    out = fm.financial_ratios({"foo": 1})
    assert out.ratios == {}
    assert out.notes


def test_compound_interest_exact() -> None:
    # 1000 * (1 + 0.05/1)^(1*2) = 1102.5
    assert fm.compound_interest(1000, 0.05, 2, 1) == 1102.5


def test_amortization() -> None:
    result = fm.loan_amortization(10000, 0.06, 12)
    assert math.isclose(result.monthly_payment, 860.66, abs_tol=0.01)
    assert result.total_interest > 0


def test_stats_exact() -> None:
    s = fm.descriptive_stats([2, 4, 4, 4, 5, 5, 7, 9])
    assert s.mean == 5.0
    assert s.median == 4.5
    assert s.stdev == 2.0


def test_calculate_unknown_operation_raises() -> None:
    with pytest.raises(ValueError):
        fm.calculate("bogus", {})


def test_calculator_agent_registered() -> None:
    agent = AgentFactory.create_agent("financial_calculator")
    assert isinstance(agent, FinancialCalculatorAgent)
    assert AgentType.FINANCIAL_CALCULATOR == "financial-calculator"


async def test_calculator_agent_computes_dcf() -> None:
    agent = FinancialCalculatorAgent()
    result = await agent.execute(
        AgentTask(
            id="t",
            agent_type="financial_calculator",
            input_data={
                "parameters": {
                    "operation": "dcf",
                    "fcf_next": 100,
                    "growth_rate": 0.05,
                    "discount_rate": 0.10,
                },
            },
        ),
    )
    assert result.status == "success"
    assert result.confidence == 1.0
    assert result.output["result"]["present_value"] == 2000.0


async def test_calculator_agent_missing_operation_errors() -> None:
    agent = FinancialCalculatorAgent()
    result = await agent.execute(
        AgentTask(id="t", agent_type="financial_calculator", input_data={}),
    )
    assert result.status != "success" or "result" not in result.output
