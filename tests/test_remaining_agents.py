"""
Tests for remaining agents: Methodology, Synthesis, and Citation.

Following TDD principles - tests written before implementation.
"""

from unittest.mock import AsyncMock

import pytest

from src.agents.models import AgentResult, AgentTask
from src.agents.schemas.citation import CitationSchema, FormattedCitation
from src.agents.schemas.methodology import MethodologySchema
from src.agents.schemas.synthesis import SynthesisSchema
from src.core.config import settings


@pytest.fixture(autouse=True)
def _no_live_provider_routing(monkeypatch):
    """These agents execute a plain, plan-less ``AgentTask``. Without this,
    a real MULTI_PROVIDER_ROUTING_ENABLED/OPENROUTER_API_KEY in the test
    environment routes execute() through a live ModelRouter instead of the
    mock gemini fixtures below, making these tests nondeterministic and
    dependent on a paid network call."""
    monkeypatch.setattr(settings, "MULTI_PROVIDER_ROUTING_ENABLED", False)


class TestMethodologyAgent:
    """Test cases for Methodology Agent."""

    @pytest.mark.asyncio
    async def test_execute_methodology_analysis(self):
        """Test successful methodology analysis execution."""
        from src.agents.methodology_agent import MethodologyAgent

        mock_gemini = AsyncMock()
        mock_gemini.generate_structured_content = AsyncMock(
            return_value=MethodologySchema(
                research_design="Mixed methods approach",
                data_collection_methods=[
                    "Surveys",
                    "Interviews",
                    "Document analysis",
                ],
                sampling_strategy="Stratified random sampling",
                analysis_approaches=[
                    "Statistical analysis",
                    "Thematic analysis",
                ],
                validity_measures=["Triangulation", "Member checking"],
                ethical_considerations=["Informed consent", "Data privacy"],
                limitations=["Sample size", "Time constraints"],
                timeline="6 months",
                quality_indicators=[
                    "Reliability",
                    "Validity",
                    "Generalizability",
                ],
            )
        )

        agent = MethodologyAgent(gemini_service=mock_gemini)

        task = AgentTask(
            id="meth-001",
            agent_type="methodology",
            input_data={
                "research_question": "How does AI impact workplace productivity?",
                "research_type": "mixed",
                "scope": "organizational",
            },
            context={"project_id": "proj-001"},
        )

        result = await agent.execute(task)

        assert result.task_id == "meth-001"
        assert result.status == "success"
        assert "research_design" in result.output
        assert "data_collection_methods" in result.output
        assert len(result.output["data_collection_methods"]) > 0
        assert result.confidence > 0.7

    @pytest.mark.asyncio
    async def test_validate_result(self):
        """Test methodology result validation."""
        from src.agents.methodology_agent import MethodologyAgent

        agent = MethodologyAgent()

        # Valid result
        valid_result = AgentResult(
            task_id="test-001",
            status="success",
            output={
                "research_design": "Experimental",
                "data_collection_methods": ["Experiment"],
                "analysis_approaches": ["Statistical"],
            },
            confidence=0.8,
            execution_time=1.0,
            metadata={},
        )

        assert await agent.validate_result(valid_result)


class TestSynthesisAgent:
    """Test cases for Synthesis Agent."""

    @pytest.mark.asyncio
    async def test_execute_synthesis(self):
        """Test successful synthesis execution."""
        from src.agents.synthesis_agent import SynthesisAgent

        mock_gemini = AsyncMock()
        mock_gemini.generate_structured_content = AsyncMock(
            return_value=SynthesisSchema(
                integrated_findings=[
                    "Finding 1 from multiple sources",
                    "Finding 2 synthesized across agents",
                ],
                cross_agent_patterns=[
                    "Pattern A identified across literature and methodology",
                    "Pattern B from comparative analysis",
                ],
                conflict_resolutions=["Resolved conflict between Agent 1 and Agent 2"],
                meta_insights=["Higher-order insight from synthesis"],
                comprehensive_narrative="Complete synthesis narrative...",
                confidence_assessment="High confidence in synthesis",
            )
        )

        agent = SynthesisAgent(gemini_service=mock_gemini)

        task = AgentTask(
            id="synth-001",
            agent_type="synthesis",
            input_data={
                "agent_outputs": {
                    "literature_review": {"sources": ["S1", "S2"], "findings": ["F1"]},
                    "comparative_analysis": {
                        "comparison": "Result",
                        "ranking": ["A", "B"],
                    },
                    "methodology": {"design": "Mixed", "methods": ["Survey"]},
                }
            },
            context={"project_id": "proj-001"},
        )

        result = await agent.execute(task)

        assert result.task_id == "synth-001"
        assert result.status == "success"
        assert "integrated_findings" in result.output
        assert "comprehensive_narrative" in result.output
        assert len(result.output["integrated_findings"]) > 0
        assert result.confidence > 0.7

    @pytest.mark.asyncio
    async def test_validate_result(self):
        """Test synthesis result validation."""
        from src.agents.synthesis_agent import SynthesisAgent

        agent = SynthesisAgent()

        # Valid result
        valid_result = AgentResult(
            task_id="test-001",
            status="success",
            output={
                "integrated_findings": ["Finding 1"],
                "comprehensive_narrative": "Narrative",
                "meta_insights": ["Insight"],
            },
            confidence=0.8,
            execution_time=1.0,
            metadata={},
        )

        assert await agent.validate_result(valid_result)


class TestCitationAgent:
    """Test cases for Citation & Verification Agent."""

    @pytest.mark.asyncio
    async def test_execute_citation_formatting(self):
        """Test successful citation formatting."""
        from src.agents.citation_agent import CitationAgent

        mock_gemini = AsyncMock()
        mock_gemini.generate_structured_content = AsyncMock(
            return_value=CitationSchema(
                citations=[
                    FormattedCitation(
                        citation_text="Smith, J. (2024). AI in Healthcare. Journal of AI, 10(2), 45-67.",
                        source_id="source_0",
                    ),
                    FormattedCitation(
                        citation_text="Doe, A. (2023). Machine Learning. Tech Review, 5(1), 12-28.",
                        source_id="source_1",
                    ),
                ],
                style="APA",
                total_sources=2,
            )
        )

        agent = CitationAgent(gemini_service=mock_gemini)

        task = AgentTask(
            id="cite-001",
            agent_type="citation",
            input_data={
                "sources": [
                    {
                        "title": "AI in Healthcare",
                        "author": "Smith, J.",
                        "year": 2024,
                        "journal": "Journal of AI",
                    },
                    {
                        "title": "Machine Learning",
                        "author": "Doe, A.",
                        "year": 2023,
                        "journal": "Tech Review",
                    },
                ],
                "style": "APA",
            },
            context={"project_id": "proj-001"},
        )

        result = await agent.execute(task)

        assert result.task_id == "cite-001"
        assert result.status == "success"
        assert "formatted_citations" in result.output
        assert "bibliography" in result.output
        assert len(result.output["formatted_citations"]) == 2
        assert result.output["citation_style"] == "APA"
        # Confidence is now the average of two honest completeness
        # fractions: both sources were formatted (2/2), but zero were
        # verified (0/2) -- these fixtures use the singular "author" key,
        # which _verify_single_source's required-fields check does not
        # recognize (it checks "authors"), so both sources are missing a
        # required field and fail verification. (2/2 + 0/2) / 2 = 0.5.
        #
        # This assertion previously read `> 0.7`, which the pre-fix
        # _calculate_confidence satisfied via a flat 0.5 baseline plus a
        # +0.25 all-formatted bonus regardless of verification outcome
        # (0.75 total) -- crediting confidence for zero real verification.
        # The rewritten assertion is stronger: it pins the exact honest
        # value rather than a loose floor, and that value now actually
        # reflects that verification failed for both sources.
        assert result.confidence == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_validate_result(self):
        """Test citation result validation."""
        from src.agents.citation_agent import CitationAgent

        agent = CitationAgent()

        # Valid result
        valid_result = AgentResult(
            task_id="test-001",
            status="success",
            output={
                "formatted_citations": ["Citation 1"],
                "bibliography": ["Citation 1"],
                "citation_style": "APA",
            },
            confidence=0.8,
            execution_time=1.0,
            metadata={},
        )

        assert await agent.validate_result(valid_result)
