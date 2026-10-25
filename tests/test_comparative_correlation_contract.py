"""The comparative agent's correlation input contract.

Contract: ``comparison_data={criterion_name: [numbers]}``. The agent hands the
numeric series of the first two criteria to the statistics tool as ``x``/``y``
through the real MCP tool boundary; any other shape is rejected with a
``ValueError`` rather than answered with stand-in content.

These tests run the real ``MCPClient`` statistics tool and therefore need scipy
(the ``stats`` extra); they fail rather than skip when it is missing.
"""

from __future__ import annotations

from typing import Any

import pytest
import scipy  # noqa: F401  (ImportError here is a failure, not a skip)

from src.agents.comparative_analysis_agent import ComparativeAnalysisAgent
from src.agents.integrations.mcp_integration import MCPIntegration
from src.agents.models import AgentTask
from src.mcp.client import MCPClient

CRITERIA = ["cost", "speed"]
GOOD = {"cost": [1.0, 2.0, 3.0, 4.0], "speed": [2.0, 4.0, 6.0, 8.1]}


def _agent(client: MCPClient | None = None) -> ComparativeAnalysisAgent:
    integration = MCPIntegration(mcp_client=client or MCPClient())
    return ComparativeAnalysisAgent(config={"mcp_integration": integration})


def _input(comparison_data: Any) -> dict[str, Any]:
    return {
        "items": ["A", "B"],
        "criteria": CRITERIA,
        "comparison_data": comparison_data,
    }


@pytest.mark.asyncio
async def test_variables_kwarg_is_rejected_at_the_tool_boundary() -> None:
    """Pins the old behaviour: criterion NAMES sent as ``variables`` are refused."""
    integration = MCPIntegration(mcp_client=MCPClient(), enable_fallback=False)

    result = await integration.analyze_statistics(
        operation="correlation", variables=CRITERIA
    )

    assert result["success"] is False
    assert result["error_code"] == "invalid_input"


@pytest.mark.asyncio
async def test_correlation_reaches_the_tool_with_two_numeric_series() -> None:
    result = await _agent()._perform_statistical_comparison(_input(GOOD))

    assert "correlation_analysis" in result["tests_performed"]
    assert result["correlation_analysis"]["pearson_r"] == pytest.approx(
        0.99999, abs=1e-3
    )
    assert result["descriptive_stats"]["count"] == 2


@pytest.mark.asyncio
async def test_a_degraded_correlation_does_not_reach_the_output() -> None:
    client = MCPClient()

    async def _down(operation: str, **kwargs: Any) -> dict[str, Any]:
        if operation == "correlation":
            raise RuntimeError("statistics tool unreachable")
        return await MCPClient.analyze_statistics(client, operation, **kwargs)

    client.analyze_statistics = _down  # type: ignore[method-assign]

    result = await _agent(client)._perform_statistical_comparison(_input(GOOD))

    assert "correlation_analysis" not in result["tests_performed"]
    assert result["correlation_analysis"] == {}


@pytest.mark.asyncio
async def test_empty_comparison_data_yields_no_correlation_not_a_fabrication() -> None:
    result = await _agent()._perform_statistical_comparison(_input({}))

    assert result["correlation_analysis"] == {}
    assert result["descriptive_stats"] == {}
    assert result["tests_performed"] == []
    assert result["data_quality"] == "limited"
    assert "fallback" not in result


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param([1, 2, 3], id="non-dict"),
        pytest.param("cost", id="string"),
        pytest.param(None, id="none"),
        pytest.param({"cost": GOOD["cost"]}, id="missing-criterion-key"),
        pytest.param({"cost": [1, 2, 3], "speed": [1, 2]}, id="unequal-lengths"),
        pytest.param({"cost": [1], "speed": [2]}, id="too-few-points"),
        pytest.param({"cost": [1, "2", 3], "speed": [1, 2, 3]}, id="string-value"),
        pytest.param({"cost": [1, True, 3], "speed": [1, 2, 3]}, id="bool-value"),
        pytest.param({"cost": [1, None, 3], "speed": [1, 2, 3]}, id="none-value"),
        pytest.param({"cost": 5, "speed": [1, 2, 3]}, id="scalar-series"),
        pytest.param({"cost": [1, float("nan")], "speed": [1, 2]}, id="nan-value"),
    ],
)
@pytest.mark.asyncio
async def test_malformed_comparison_data_is_rejected_loudly(bad: Any) -> None:
    with pytest.raises(ValueError, match="comparison_data"):
        await _agent()._perform_statistical_comparison(_input(bad))


@pytest.mark.asyncio
async def test_execute_reports_a_malformed_shape_as_a_failed_result() -> None:
    agent = _agent()

    async def _no_search(input_data: dict[str, Any]) -> dict[str, Any]:
        return {}

    agent._search_comparative_studies = _no_search  # type: ignore[method-assign]
    task = AgentTask(
        id="corr-1",
        agent_type="comparative_analysis",
        input_data=_input({"cost": [1, 2]}),
        context={},
    )

    result = await agent.execute(task)

    assert result.status == "failed"
    assert result.output["error_type"] == "ValueError"
