"""
Tests for Comparative Analysis Agent.

Following TDD principles - tests written before implementation.
"""

import json
from unittest.mock import AsyncMock

import pytest

from src.agents.comparative_analysis_agent import ComparativeAnalysisAgent
from src.agents.models import AgentTask


class TestComparativeAnalysisAgent:
    """Test cases for Comparative Analysis Agent."""

    @pytest.mark.asyncio
    async def test_execute_comparative_analysis(self) -> None:
        """Test successful comparative analysis execution."""
        # Create mock Gemini service
        mock_gemini = AsyncMock()
        mock_gemini.generate_content = AsyncMock(
            return_value=json.dumps(
                {
                    "comparative_analysis": {
                        "items_compared": ["Method A", "Method B", "Method C"],
                        "comparison_criteria": [
                            "Accuracy",
                            "Speed",
                            "Cost",
                            "Scalability",
                        ],
                        "comparison_matrix": {
                            "Method A": {
                                "Accuracy": 0.95,
                                "Speed": 0.7,
                                "Cost": 0.3,
                                "Scalability": 0.8,
                            },
                            "Method B": {
                                "Accuracy": 0.88,
                                "Speed": 0.9,
                                "Cost": 0.6,
                                "Scalability": 0.9,
                            },
                            "Method C": {
                                "Accuracy": 0.92,
                                "Speed": 0.5,
                                "Cost": 0.8,
                                "Scalability": 0.6,
                            },
                        },
                        "strengths_weaknesses": {
                            "Method A": {
                                "strengths": ["High accuracy", "Good scalability"],
                                "weaknesses": ["High cost"],
                            },
                            "Method B": {
                                "strengths": ["Fast", "Scalable"],
                                "weaknesses": ["Lower accuracy"],
                            },
                            "Method C": {
                                "strengths": ["Low cost"],
                                "weaknesses": ["Slow", "Limited scalability"],
                            },
                        },
                        "recommendations": [
                            "Method A recommended for high-accuracy requirements",
                            "Method B optimal for real-time applications",
                            "Method C suitable for budget-constrained projects",
                        ],
                        "trade_offs": [
                            "Accuracy vs Speed trade-off between Method A and B",
                            "Cost vs Performance trade-off with Method C",
                        ],
                    }
                }
            )
        )

        agent = ComparativeAnalysisAgent(gemini_service=mock_gemini)

        task = AgentTask(
            id="comp-001",
            agent_type="comparative_analysis",
            input_data={
                "items": ["Method A", "Method B", "Method C"],
                "criteria": ["Accuracy", "Speed", "Cost", "Scalability"],
                "context": "Research methods comparison",
            },
            context={"project_id": "proj-001"},
        )

        result = await agent.execute(task)

        assert result.task_id == "comp-001"
        assert result.status == "success"
        assert "comparison_matrix" in result.output
        assert "recommendations" in result.output
        assert result.confidence > 0.7
        assert len(result.output["items_compared"]) == 3

    @pytest.mark.asyncio
    async def test_comparison_matrix_generation(self) -> None:
        """Test generation of comparison matrix."""
        mock_gemini = AsyncMock()
        mock_gemini.generate_content = AsyncMock(
            return_value=json.dumps(
                {
                    "comparative_analysis": {
                        "items_compared": ["Approach 1", "Approach 2"],
                        "comparison_criteria": ["Effectiveness", "Efficiency"],
                        "comparison_matrix": {
                            "Approach 1": {"Effectiveness": 0.8, "Efficiency": 0.6},
                            "Approach 2": {"Effectiveness": 0.7, "Efficiency": 0.9},
                        },
                        "strengths_weaknesses": {
                            "Approach 1": {
                                "strengths": ["More effective"],
                                "weaknesses": ["Less efficient"],
                            },
                            "Approach 2": {
                                "strengths": ["More efficient"],
                                "weaknesses": ["Less effective"],
                            },
                        },
                        "recommendations": ["Choose based on priority"],
                        "trade_offs": ["Effectiveness vs Efficiency"],
                    }
                }
            )
        )

        agent = ComparativeAnalysisAgent(gemini_service=mock_gemini)

        task = AgentTask(
            id="comp-002",
            agent_type="comparative_analysis",
            input_data={
                "items": ["Approach 1", "Approach 2"],
                "criteria": ["Effectiveness", "Efficiency"],
            },
            context={},
        )

        result = await agent.execute(task)

        matrix = result.output["comparison_matrix"]
        assert "Approach 1" in matrix
        assert "Approach 2" in matrix
        # Values are normalized, so check relative ordering
        assert (
            matrix["Approach 1"]["Effectiveness"]
            > matrix["Approach 2"]["Effectiveness"]
        )
        assert matrix["Approach 2"]["Efficiency"] > matrix["Approach 1"]["Efficiency"]

    @pytest.mark.asyncio
    async def test_multi_criteria_ranking(self) -> None:
        """Test ranking items across multiple criteria."""
        mock_gemini = AsyncMock()
        mock_gemini.generate_content = AsyncMock(
            return_value=json.dumps(
                {
                    "comparative_analysis": {
                        "items_compared": ["Option A", "Option B", "Option C"],
                        "comparison_criteria": ["Quality", "Cost", "Time"],
                        "comparison_matrix": {
                            "Option A": {"Quality": 0.9, "Cost": 0.3, "Time": 0.4},
                            "Option B": {"Quality": 0.7, "Cost": 0.8, "Time": 0.9},
                            "Option C": {"Quality": 0.8, "Cost": 0.6, "Time": 0.7},
                        },
                        "rankings": {
                            "Quality": ["Option A", "Option C", "Option B"],
                            "Cost": ["Option B", "Option C", "Option A"],
                            "Time": ["Option B", "Option C", "Option A"],
                            "overall": ["Option B", "Option C", "Option A"],
                        },
                        "strengths_weaknesses": {
                            "Option A": {
                                "strengths": ["Best quality"],
                                "weaknesses": ["Expensive", "Slow"],
                            },
                            "Option B": {
                                "strengths": ["Cost-effective", "Fast"],
                                "weaknesses": ["Lower quality"],
                            },
                            "Option C": {
                                "strengths": ["Balanced"],
                                "weaknesses": ["No standout features"],
                            },
                        },
                        "recommendations": ["Option B for balanced performance"],
                        "trade_offs": ["Quality vs Cost/Time"],
                    }
                }
            )
        )

        agent = ComparativeAnalysisAgent(gemini_service=mock_gemini)

        task = AgentTask(
            id="comp-003",
            agent_type="comparative_analysis",
            input_data={
                "items": ["Option A", "Option B", "Option C"],
                "criteria": ["Quality", "Cost", "Time"],
                "weighted_criteria": {"Quality": 0.5, "Cost": 0.3, "Time": 0.2},
            },
            context={},
        )

        result = await agent.execute(task)

        assert "rankings" in result.output
        rankings = result.output["rankings"]
        assert "Quality" in rankings
        assert "overall" in rankings
        assert len(rankings["overall"]) == 3

    @pytest.mark.asyncio
    async def test_trade_off_analysis(self) -> None:
        """Test identification of trade-offs between options."""
        mock_gemini = AsyncMock()
        mock_gemini.generate_content = AsyncMock(
            return_value=json.dumps(
                {
                    "comparative_analysis": {
                        "items_compared": ["Solution X", "Solution Y"],
                        "comparison_criteria": ["Performance", "Complexity"],
                        "comparison_matrix": {
                            "Solution X": {"Performance": 0.95, "Complexity": 0.9},
                            "Solution Y": {"Performance": 0.7, "Complexity": 0.3},
                        },
                        "strengths_weaknesses": {
                            "Solution X": {
                                "strengths": ["High performance"],
                                "weaknesses": ["Complex"],
                            },
                            "Solution Y": {
                                "strengths": ["Simple"],
                                "weaknesses": ["Lower performance"],
                            },
                        },
                        "trade_offs": [
                            "Performance vs Complexity: 25% performance gain requires 3x complexity",
                            "Implementation time: Solution X requires 2x development time",
                            "Maintenance cost: Solution Y is 60% cheaper to maintain",
                        ],
                        "recommendations": ["Consider project constraints"],
                    }
                }
            )
        )

        agent = ComparativeAnalysisAgent(gemini_service=mock_gemini)

        task = AgentTask(
            id="comp-004",
            agent_type="comparative_analysis",
            input_data={
                "items": ["Solution X", "Solution Y"],
                "criteria": ["Performance", "Complexity"],
                "analyze_tradeoffs": True,
            },
            context={},
        )

        result = await agent.execute(task)

        trade_offs = result.output["trade_offs"]
        assert len(trade_offs) > 0
        assert any("Performance" in t for t in trade_offs)
        assert any("Complexity" in t for t in trade_offs)

    @pytest.mark.asyncio
    async def test_empty_items_handling(self) -> None:
        """Test handling of empty items list."""
        agent = ComparativeAnalysisAgent()

        task = AgentTask(
            id="comp-005",
            agent_type="comparative_analysis",
            input_data={"items": [], "criteria": ["Test"]},
            context={},
        )

        result = await agent.execute(task)

        assert result.status == "failed"
        assert "error" in result.output
        assert result.confidence == 0.0
