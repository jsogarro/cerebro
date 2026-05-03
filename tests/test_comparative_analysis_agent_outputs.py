"""
Tests for Comparative Analysis Agent output variants and validation.
"""

import json
from unittest.mock import AsyncMock

import pytest

from src.agents.comparative_analysis_agent import ComparativeAnalysisAgent
from src.agents.models import AgentResult, AgentTask


class TestComparativeAnalysisAgentOutputs:
    """Test output-specific comparative analysis behavior."""

    @pytest.mark.asyncio
    async def test_visual_comparison_output(self) -> None:
        """Test generation of visual comparison data."""
        mock_gemini = AsyncMock()
        mock_gemini.generate_content = AsyncMock(
            return_value=json.dumps(
                {
                    "comparative_analysis": {
                        "items_compared": ["Tool 1", "Tool 2"],
                        "comparison_criteria": ["Feature A", "Feature B"],
                        "comparison_matrix": {
                            "Tool 1": {"Feature A": 0.8, "Feature B": 0.6},
                            "Tool 2": {"Feature A": 0.7, "Feature B": 0.9},
                        },
                        "visual_data": {
                            "chart_type": "radar",
                            "data_points": {
                                "Tool 1": [0.8, 0.6],
                                "Tool 2": [0.7, 0.9],
                            },
                            "labels": ["Feature A", "Feature B"],
                        },
                        "strengths_weaknesses": {
                            "Tool 1": {
                                "strengths": ["Better Feature A"],
                                "weaknesses": ["Weaker Feature B"],
                            },
                            "Tool 2": {
                                "strengths": ["Better Feature B"],
                                "weaknesses": ["Weaker Feature A"],
                            },
                        },
                        "recommendations": ["Tool 2 for Feature B priority"],
                        "trade_offs": ["Feature trade-off exists"],
                    }
                }
            )
        )

        agent = ComparativeAnalysisAgent(gemini_service=mock_gemini)

        task = AgentTask(
            id="comp-006",
            agent_type="comparative_analysis",
            input_data={
                "items": ["Tool 1", "Tool 2"],
                "criteria": ["Feature A", "Feature B"],
                "generate_visual": True,
            },
            context={},
        )

        result = await agent.execute(task)

        assert "visual_data" in result.output
        visual = result.output["visual_data"]
        assert visual["chart_type"] == "radar"
        assert "data_points" in visual

    @pytest.mark.asyncio
    async def test_confidence_scoring(self) -> None:
        """Test confidence scoring based on comparison completeness."""
        mock_gemini_high = AsyncMock()
        mock_gemini_high.generate_content = AsyncMock(
            return_value=json.dumps(
                {
                    "comparative_analysis": {
                        "items_compared": ["A", "B", "C", "D"],
                        "comparison_criteria": ["C1", "C2", "C3", "C4"],
                        "comparison_matrix": {
                            "A": {"C1": 0.9, "C2": 0.8, "C3": 0.7, "C4": 0.6},
                            "B": {"C1": 0.8, "C2": 0.9, "C3": 0.6, "C4": 0.7},
                            "C": {"C1": 0.7, "C2": 0.6, "C3": 0.9, "C4": 0.8},
                            "D": {"C1": 0.6, "C2": 0.7, "C3": 0.8, "C4": 0.9},
                        },
                        "strengths_weaknesses": {
                            "A": {"strengths": ["S1"], "weaknesses": ["W1"]},
                            "B": {"strengths": ["S2"], "weaknesses": ["W2"]},
                            "C": {"strengths": ["S3"], "weaknesses": ["W3"]},
                            "D": {"strengths": ["S4"], "weaknesses": ["W4"]},
                        },
                        "recommendations": ["R1", "R2", "R3"],
                        "trade_offs": ["T1", "T2", "T3", "T4"],
                    }
                }
            )
        )

        agent_high = ComparativeAnalysisAgent(gemini_service=mock_gemini_high)

        task = AgentTask(
            id="comp-007",
            agent_type="comparative_analysis",
            input_data={
                "items": ["A", "B", "C", "D"],
                "criteria": ["C1", "C2", "C3", "C4"],
            },
            context={},
        )

        result_high = await agent_high.execute(task)
        assert result_high.confidence > 0.85

        mock_gemini_low = AsyncMock()
        mock_gemini_low.generate_content = AsyncMock(
            return_value=json.dumps(
                {
                    "comparative_analysis": {
                        "items_compared": ["X", "Y"],
                        "comparison_criteria": ["Criterion"],
                        "comparison_matrix": {
                            "X": {"Criterion": 0.6},
                            "Y": {"Criterion": 0.4},
                        },
                        "strengths_weaknesses": {
                            "X": {"strengths": ["S"], "weaknesses": []},
                            "Y": {"strengths": [], "weaknesses": ["W"]},
                        },
                        "recommendations": [],
                        "trade_offs": [],
                    }
                }
            )
        )

        agent_low = ComparativeAnalysisAgent(gemini_service=mock_gemini_low)

        task_low = AgentTask(
            id="comp-008",
            agent_type="comparative_analysis",
            input_data={"items": ["X", "Y"], "criteria": ["Criterion"]},
            context={},
        )

        result_low = await agent_low.execute(task_low)
        assert result_low.confidence < result_high.confidence
        assert result_low.confidence < 0.8

    @pytest.mark.asyncio
    async def test_validate_result(self) -> None:
        """Test result validation."""
        agent = ComparativeAnalysisAgent()

        valid_result = AgentResult(
            task_id="test-001",
            status="success",
            output={
                "items_compared": ["A", "B"],
                "comparison_matrix": {"A": {"C1": 0.5}, "B": {"C1": 0.6}},
                "recommendations": ["Use B"],
            },
            confidence=0.8,
            execution_time=1.0,
            metadata={},
        )

        assert await agent.validate_result(valid_result)

        invalid_result = AgentResult(
            task_id="test-002",
            status="success",
            output={"items_compared": ["A", "B"]},
            confidence=0.8,
            execution_time=1.0,
            metadata={},
        )

        assert not await agent.validate_result(invalid_result)

    @pytest.mark.asyncio
    async def test_contextual_comparison(self) -> None:
        """Test comparison with specific context considerations."""
        mock_gemini = AsyncMock()
        mock_gemini.generate_content = AsyncMock(
            return_value=json.dumps(
                {
                    "comparative_analysis": {
                        "items_compared": ["Framework A", "Framework B"],
                        "comparison_criteria": [
                            "Learning Curve",
                            "Performance",
                            "Community",
                        ],
                        "context_considerations": [
                            "Team has experience with similar frameworks",
                            "Performance is critical for this application",
                            "Long-term support needed",
                        ],
                        "comparison_matrix": {
                            "Framework A": {
                                "Learning Curve": 0.8,
                                "Performance": 0.9,
                                "Community": 0.7,
                            },
                            "Framework B": {
                                "Learning Curve": 0.6,
                                "Performance": 0.7,
                                "Community": 0.9,
                            },
                        },
                        "contextual_recommendation": (
                            "Given performance requirements, Framework A is preferred "
                            "despite steeper learning curve"
                        ),
                        "strengths_weaknesses": {
                            "Framework A": {
                                "strengths": ["High performance"],
                                "weaknesses": ["Smaller community"],
                            },
                            "Framework B": {
                                "strengths": ["Large community"],
                                "weaknesses": ["Lower performance"],
                            },
                        },
                        "recommendations": ["Framework A for this specific context"],
                        "trade_offs": ["Performance vs Community support"],
                    }
                }
            )
        )

        agent = ComparativeAnalysisAgent(gemini_service=mock_gemini)

        task = AgentTask(
            id="comp-009",
            agent_type="comparative_analysis",
            input_data={
                "items": ["Framework A", "Framework B"],
                "criteria": ["Learning Curve", "Performance", "Community"],
                "context": {
                    "requirements": ["High performance", "Long-term project"],
                    "constraints": ["Team size: small", "Timeline: flexible"],
                },
            },
            context={"project_type": "web_application"},
        )

        result = await agent.execute(task)

        assert "context_considerations" in result.output
        assert "contextual_recommendation" in result.output
        assert result.output["contextual_recommendation"] is not None
