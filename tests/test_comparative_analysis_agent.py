"""
Tests for Comparative Analysis Agent.

Following TDD principles - tests written before implementation.
"""

import json
from unittest.mock import AsyncMock

import pytest

from src.agents.models import AgentResult, AgentTask


class TestComparativeAnalysisAgent:
    """Test cases for Comparative Analysis Agent."""

    @pytest.mark.asyncio
    async def test_execute_comparative_analysis(self):
        """Test successful comparative analysis execution."""
        from src.agents.comparative_analysis_agent import ComparativeAnalysisAgent

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
    async def test_comparison_matrix_generation(self):
        """Test generation of comparison matrix."""
        from src.agents.comparative_analysis_agent import ComparativeAnalysisAgent

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
    async def test_multi_criteria_ranking(self):
        """Test ranking items across multiple criteria."""
        from src.agents.comparative_analysis_agent import ComparativeAnalysisAgent

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
    async def test_trade_off_analysis(self):
        """Test identification of trade-offs between options."""
        from src.agents.comparative_analysis_agent import ComparativeAnalysisAgent

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
    async def test_empty_items_handling(self):
        """Test handling of empty items list."""
        from src.agents.comparative_analysis_agent import ComparativeAnalysisAgent

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

    @pytest.mark.asyncio
    async def test_visual_comparison_output(self):
        """Test generation of visual comparison data."""
        from src.agents.comparative_analysis_agent import ComparativeAnalysisAgent

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
                            "data_points": {"Tool 1": [0.8, 0.6], "Tool 2": [0.7, 0.9]},
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
    async def test_confidence_scoring(self):
        """Test confidence scoring based on comparison completeness."""
        from src.agents.comparative_analysis_agent import ComparativeAnalysisAgent

        # Test with comprehensive comparison
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

        # Test with minimal comparison
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
        assert result_low.confidence < 0.7

    @pytest.mark.asyncio
    async def test_validate_result(self):
        """Test result validation."""
        from src.agents.comparative_analysis_agent import ComparativeAnalysisAgent

        agent = ComparativeAnalysisAgent()

        # Valid result
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

        # Invalid result - missing required fields
        invalid_result = AgentResult(
            task_id="test-002",
            status="success",
            output={"items_compared": ["A", "B"]},  # Missing comparison_matrix
            confidence=0.8,
            execution_time=1.0,
            metadata={},
        )

        assert not await agent.validate_result(invalid_result)

    @pytest.mark.asyncio
    async def test_contextual_comparison(self):
        """Test comparison with specific context considerations."""
        from src.agents.comparative_analysis_agent import ComparativeAnalysisAgent

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
                        "contextual_recommendation": "Given performance requirements, Framework A is preferred despite steeper learning curve",
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
