"""
Tests for Literature Review Agent.

Following TDD principles - tests written before implementation.
"""

from unittest.mock import AsyncMock

import pytest

from src.agents.models import AgentResult, AgentTask
from src.agents.schemas.literature_review import (
    AcademicSource,
    LiteratureAnalysisSchema,
)


def _mock_gemini_two_call(sources, key_findings=None, research_gaps=None,
                          methodologies_used=None, quality_assessment="Good"):
    """Build an AsyncMock for gemini_service whose generate_structured_content
    returns the source list on the first call and the analysis on the second.

    Production calls generate_structured_content twice in sequence:
    1. _search_sources_structured(prompt, SourceListSchema) -> only `.sources` is read
    2. _analyze_sources_structured(prompt, LiteratureAnalysisSchema) -> reads
       .key_findings, .research_gaps, .methodologies_used, .quality_assessment

    LiteratureAnalysisSchema satisfies both shapes (it has .sources too).
    """
    mock = AsyncMock()
    mock.generate_structured_content = AsyncMock(side_effect=[
        LiteratureAnalysisSchema(sources=sources),
        LiteratureAnalysisSchema(
            key_findings=key_findings or [],
            research_gaps=research_gaps or [],
            methodologies_used=methodologies_used or [],
            quality_assessment=quality_assessment,
        ),
    ])
    return mock


class TestLiteratureReviewAgent:
    """Test cases for Literature Review Agent."""

    @pytest.mark.asyncio
    async def test_execute_literature_review(self):
        """Test successful literature review execution."""
        from src.agents.literature_review_agent import LiteratureReviewAgent

        mock_gemini = _mock_gemini_two_call(
            sources=[
                AcademicSource(title="AI in Healthcare", authors=["Smith J"], year=2024, relevance_score=0.95),
                AcademicSource(title="Machine Learning Applications", authors=["Doe A"], year=2023, relevance_score=0.88),
            ],
            key_findings=[
                "AI improves diagnostic accuracy by 30%",
                "ML models reduce processing time significantly",
            ],
            methodologies_used=["Systematic review", "Meta-analysis"],
            research_gaps=["Limited long-term studies", "Lack of diverse datasets"],
            quality_assessment="High quality sources with peer review",
        )

        agent = LiteratureReviewAgent(gemini_service=mock_gemini)

        task = AgentTask(
            id="lit-001",
            agent_type="literature_review",
            input_data={
                "query": "AI applications in healthcare",
                "domains": ["AI", "Healthcare"],
                "max_sources": 50,
            },
            context={"project_id": "proj-001"},
        )

        result = await agent.execute(task)

        assert result.task_id == "lit-001"
        assert result.status == "success"
        assert "sources_found" in result.output
        assert len(result.output["sources_found"]) == 2
        assert result.confidence > 0.5
        assert "key_findings" in result.output
        assert "research_gaps" in result.output

    @pytest.mark.asyncio
    async def test_source_ranking(self):
        """Test that sources are ranked by relevance."""
        from src.agents.literature_review_agent import LiteratureReviewAgent

        mock_gemini = _mock_gemini_two_call(
            sources=[
                AcademicSource(title="Paper C", authors=["C"], year=2020, relevance_score=0.5),
                AcademicSource(title="Paper A", authors=["A"], year=2024, relevance_score=0.95),
                AcademicSource(title="Paper B", authors=["B"], year=2023, relevance_score=0.75),
            ],
            key_findings=["Finding 1"],
            methodologies_used=["Review"],
            research_gaps=["Gap 1"],
            quality_assessment="Good",
        )

        agent = LiteratureReviewAgent(gemini_service=mock_gemini)

        task = AgentTask(
            id="lit-002",
            agent_type="literature_review",
            input_data={"query": "Test query", "domains": ["Test"]},
            context={},
        )

        result = await agent.execute(task)

        sources = result.output["sources_found"]
        assert sources[0]["title"] == "Paper A"
        assert sources[1]["title"] == "Paper B"
        assert sources[2]["title"] == "Paper C"

    @pytest.mark.asyncio
    async def test_research_gap_identification(self):
        """Test identification of research gaps."""
        from src.agents.literature_review_agent import LiteratureReviewAgent

        mock_gemini = _mock_gemini_two_call(
            sources=[
                AcademicSource(title="Study 1", authors=["Author1"], year=2024, relevance_score=0.9),
            ],
            key_findings=["Current state of research"],
            methodologies_used=["Survey"],
            research_gaps=[
                "No longitudinal studies found",
                "Limited geographical diversity",
                "Lack of interdisciplinary approaches",
            ],
            quality_assessment="Moderate",
        )

        agent = LiteratureReviewAgent(gemini_service=mock_gemini)

        task = AgentTask(
            id="lit-003",
            agent_type="literature_review",
            input_data={"query": "Research gaps test", "domains": ["Research"]},
            context={},
        )

        result = await agent.execute(task)

        gaps = result.output["research_gaps"]
        assert len(gaps) == 3
        assert "longitudinal" in gaps[0].lower()
        assert result.output["gap_analysis"] is not None

    @pytest.mark.asyncio
    async def test_empty_query_handling(self):
        """Test handling of empty or invalid queries."""
        from src.agents.literature_review_agent import LiteratureReviewAgent

        agent = LiteratureReviewAgent()

        task = AgentTask(
            id="lit-004",
            agent_type="literature_review",
            input_data={"query": "", "domains": []},
            context={},
        )

        result = await agent.execute(task)

        assert result.status == "failed"
        assert "error" in result.output
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_caching_literature_search(self):
        """Test that literature searches are cached."""
        from src.agents.literature_review_agent import LiteratureReviewAgent

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.setex = AsyncMock()

        mock_gemini = _mock_gemini_two_call(
            sources=[
                AcademicSource(title="Cached", authors=["A"], year=2024, relevance_score=0.9),
            ],
            key_findings=["Finding"],
            methodologies_used=["Method"],
            research_gaps=["Gap"],
            quality_assessment="Good",
        )

        agent = LiteratureReviewAgent(
            gemini_service=mock_gemini, cache_client=mock_cache
        )

        task = AgentTask(
            id="lit-005",
            agent_type="literature_review",
            input_data={"query": "Cacheable query", "domains": ["Test"]},
            context={},
        )

        await agent.execute(task)

        mock_cache.get.assert_called_once()
        mock_cache.setex.assert_called_once()
        cache_call_args = mock_cache.setex.call_args
        assert cache_call_args[0][1] == 86400  # 24 hours TTL

    @pytest.mark.asyncio
    async def test_confidence_scoring(self):
        """Test confidence scoring based on source quality and quantity."""
        from src.agents.literature_review_agent import LiteratureReviewAgent

        mock_gemini_high = _mock_gemini_two_call(
            sources=[
                AcademicSource(title=f"Paper {i}", authors=["A"], year=2024, relevance_score=0.9)
                for i in range(20)
            ],
            key_findings=["Finding 1", "Finding 2", "Finding 3"],
            methodologies_used=["Systematic review", "Meta-analysis"],
            research_gaps=["Gap 1"],
            quality_assessment="High quality peer-reviewed sources",
        )

        agent_high = LiteratureReviewAgent(gemini_service=mock_gemini_high)

        task = AgentTask(
            id="lit-006",
            agent_type="literature_review",
            input_data={
                "query": "High confidence test",
                "domains": ["Test"],
                "max_sources": 50,
            },
            context={},
        )

        result_high = await agent_high.execute(task)
        assert result_high.confidence > 0.7

        mock_gemini_low = _mock_gemini_two_call(
            sources=[
                AcademicSource(title="Paper 1", authors=["A"], year=2020, relevance_score=0.5),
            ],
            key_findings=["Finding 1"],
            methodologies_used=["Basic review"],
            research_gaps=["Many gaps"],
            quality_assessment="Limited sources available",
        )

        agent_low = LiteratureReviewAgent(gemini_service=mock_gemini_low)

        result_low = await agent_low.execute(task)
        assert result_low.confidence < result_high.confidence

    @pytest.mark.asyncio
    async def test_validate_result(self):
        """Test result validation."""
        from src.agents.literature_review_agent import LiteratureReviewAgent

        agent = LiteratureReviewAgent()

        valid_result = AgentResult(
            task_id="test-001",
            status="success",
            output={
                "sources_found": [{"title": "Test"}],
                "key_findings": ["Finding"],
                "research_gaps": ["Gap"],
            },
            confidence=0.8,
            execution_time=1.0,
            metadata={},
        )

        assert await agent.validate_result(valid_result)

        invalid_result = AgentResult(
            task_id="test-002",
            status="success",
            output={"sources_found": []},  # Missing key_findings and research_gaps
            confidence=0.8,
            execution_time=1.0,
            metadata={},
        )

        assert not await agent.validate_result(invalid_result)

    @pytest.mark.asyncio
    async def test_gemini_error_handling(self):
        """Test graceful degradation when Gemini service errors out.

        The structured-output flow swallows individual call failures inside
        _search_sources_structured / _analyze_sources_structured (returning
        empty lists / dicts). The agent then proceeds with the fallback
        analysis. Verify the agent stays alive and reports zero sources
        rather than propagating the error.
        """
        from src.agents.literature_review_agent import LiteratureReviewAgent

        mock_gemini = AsyncMock()
        mock_gemini.generate_structured_content = AsyncMock(
            side_effect=Exception("Gemini API error")
        )

        agent = LiteratureReviewAgent(gemini_service=mock_gemini)

        task = AgentTask(
            id="lit-007",
            agent_type="literature_review",
            input_data={"query": "Test query", "domains": ["Test"]},
            context={},
        )

        result = await agent.execute(task)

        # Production swallows source-search errors and returns empty sources
        assert result.status == "success"
        assert result.output["sources_found"] == []
        assert result.confidence < 0.7

    @pytest.mark.asyncio
    async def test_depth_level_handling(self):
        """Test that agent respects research depth level."""
        from src.agents.literature_review_agent import LiteratureReviewAgent
        from src.models.research_project import ResearchDepth

        mock_gemini = _mock_gemini_two_call(
            sources=[
                AcademicSource(title=f"Paper {i}", authors=["A"], year=2024, relevance_score=0.8)
                for i in range(100)
            ],
            key_findings=["Many findings"],
            methodologies_used=["Comprehensive"],
            research_gaps=["Few gaps"],
            quality_assessment="Extensive coverage",
        )

        agent = LiteratureReviewAgent(gemini_service=mock_gemini)

        task = AgentTask(
            id="lit-008",
            agent_type="literature_review",
            input_data={
                "query": "Exhaustive test",
                "domains": ["Test"],
                "depth_level": ResearchDepth.EXHAUSTIVE.value,
                "max_sources": 200,
            },
            context={},
        )

        result = await agent.execute(task)

        assert len(result.output["sources_found"]) > 50
        assert "exhaustive" in result.output.get("search_strategy", "").lower()
