"""Tests for research agent LLMWorkerAgentBase migration and routing readiness.

Verifies that the 5 migrated research agents (LiteratureReviewAgent,
ComparativeAnalysisAgent, MethodologyAgent, SynthesisAgent, CitationAgent)
now inherit from LLMWorkerAgentBase and use _ensure_gemini_service() for
service initialization (the foundation for multi-provider routing).
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.agents.citation_agent import CitationAgent
from src.agents.comparative_analysis_agent import ComparativeAnalysisAgent
from src.agents.literature_review_agent import LiteratureReviewAgent
from src.agents.llm_worker_base import LLMWorkerAgentBase
from src.agents.methodology_agent import MethodologyAgent
from src.agents.models import AgentTask
from src.agents.synthesis_agent import SynthesisAgent


@pytest.mark.asyncio
class TestResearchAgentsMigration:
    """Test suite for research agent migration to LLMWorkerAgentBase."""

    def test_literature_review_inherits_from_worker_base(self):
        """LiteratureReviewAgent inherits from LLMWorkerAgentBase."""
        agent = LiteratureReviewAgent()
        assert isinstance(agent, LLMWorkerAgentBase)

    def test_comparative_analysis_inherits_from_worker_base(self):
        """ComparativeAnalysisAgent inherits from LLMWorkerAgentBase."""
        agent = ComparativeAnalysisAgent()
        assert isinstance(agent, LLMWorkerAgentBase)

    def test_methodology_inherits_from_worker_base(self):
        """MethodologyAgent inherits from LLMWorkerAgentBase."""
        agent = MethodologyAgent()
        assert isinstance(agent, LLMWorkerAgentBase)

    def test_synthesis_inherits_from_worker_base(self):
        """SynthesisAgent inherits from LLMWorkerAgentBase."""
        agent = SynthesisAgent()
        assert isinstance(agent, LLMWorkerAgentBase)

    def test_citation_inherits_from_worker_base(self):
        """CitationAgent inherits from LLMWorkerAgentBase."""
        agent = CitationAgent()
        assert isinstance(agent, LLMWorkerAgentBase)

    async def test_literature_review_routes_through_generate_with_routing(self):
        """LiteratureReviewAgent routes generation through _generate_with_routing."""
        agent = LiteratureReviewAgent()
        agent.gemini_service = None  # Force fallback path

        with patch.object(
            agent, "_generate_with_routing", return_value=('{"sources": []}', 0.8),
        ) as mock_routing:
            task = AgentTask(
                id="test",
                agent_type="literature_review",
                input_data={
                    "query": "test query",
                    "domains": ["ai"],
                    "max_sources": 5,
                },
            )

            result = await agent.execute(task)

            # Verify _generate_with_routing was called
            assert mock_routing.called
            # Verify result has expected structure
            assert result.status == "success"
            assert "sources_found" in result.output

    async def test_comparative_analysis_routes_through_generate_with_routing(self):
        """ComparativeAnalysisAgent routes generation through _generate_with_routing."""
        agent = ComparativeAnalysisAgent()

        with patch.object(
            agent,
            "_generate_with_routing",
            return_value=(
                '{"comparison_matrix": {}, "trade_offs": [], "recommendations": []}',
                0.8,
            ),
        ) as mock_routing:
            task = AgentTask(
                id="test",
                agent_type="comparative_analysis",
                input_data={"items": ["item1", "item2"], "criteria": ["criterion1"]},
            )

            result = await agent.execute(task)

            # Verify _generate_with_routing was called
            assert mock_routing.called
            # Verify result has expected structure (shape contract preserved)
            assert result.status == "success"
            assert "comparison_matrix" in result.output
            assert "recommendations" in result.output
            assert result.confidence >= 0.0

    async def test_methodology_uses_ensure_gemini_service(self):
        """MethodologyAgent uses _ensure_gemini_service for lazy init."""
        agent = MethodologyAgent()
        agent.gemini_service = None
        mock_gemini = Mock()
        mock_gemini.generate_structured_content = AsyncMock(
            return_value=Mock(
                model_dump=lambda: {
                    "research_design": "test",
                    "data_collection_methods": [],
                    "sampling_strategy": "test",
                    "analysis_approaches": [],
                    "limitations": [],
                    "ethical_considerations": [],
                },
            ),
        )

        with patch.object(
            agent, "_ensure_gemini_service", return_value=mock_gemini,
        ) as mock_ensure:
            task = AgentTask(
                id="test",
                agent_type="methodology",
                input_data={"research_question": "test question"},
            )

            result = await agent.execute(task)

            # Verify _ensure_gemini_service was called
            assert mock_ensure.called
            # Verify result has expected structure (uses actual schema fields)
            assert result.status == "success"
            assert "research_design" in result.output

    async def test_synthesis_uses_ensure_gemini_service(self):
        """SynthesisAgent uses _ensure_gemini_service for lazy init."""
        agent = SynthesisAgent()
        mock_gemini = Mock()
        mock_gemini.generate_structured_content = AsyncMock(
            return_value=Mock(
                model_dump=lambda: {
                    "integrated_findings": [],
                    "consensus_points": [],
                    "conflicts_identified": [],
                    "synthesis_narrative": "test",
                },
            ),
        )

        with patch.object(
            agent, "_ensure_gemini_service", return_value=mock_gemini,
        ) as mock_ensure:
            task = AgentTask(
                id="test",
                agent_type="synthesis",
                input_data={"agent_outputs": {"agent1": {"result": "test"}}},
            )

            result = await agent.execute(task)

            # Verify _ensure_gemini_service was called
            assert mock_ensure.called
            # Verify result has expected structure
            assert result.status == "success"
            assert "integrated_findings" in result.output

    async def test_citation_result_shape_preserved(self):
        """CitationAgent preserves result shape after migration."""
        agent = CitationAgent()
        task = AgentTask(
            id="test",
            agent_type="citation",
            input_data={
                "sources": [{"title": "Test", "authors": ["Author"], "year": 2024}],
                "style": "APA",
            },
        )

        result = await agent.execute(task)

        # Verify result has expected structure (shape contract preserved)
        assert result.status == "success"
        assert "formatted_citations" in result.output
        assert result.confidence >= 0.0


class TestNoDirectTextGeneration:
    """Source-level guard: no migrated agent calls gemini.generate_content directly."""

    def test_no_direct_text_generation_remains(self):
        import inspect

        import src.agents.citation_agent as cit
        import src.agents.comparative_analysis_agent as comp
        import src.agents.literature_review_agent as lit
        import src.agents.methodology_agent as meth
        import src.agents.synthesis_agent as syn

        for module in (lit, comp, meth, syn, cit):
            source = inspect.getsource(module)
            assert "gemini.generate_content(" not in source, module.__name__
