"""
Tests for Literature Review Agent.

Following TDD principles - tests written before implementation.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.agents.models import AgentResult, AgentTask
from src.agents.schemas.literature_review import (
    AcademicSource,
    LiteratureAnalysisSchema,
)
from src.core.config import settings


def _mock_gemini_two_call(
    sources,
    key_findings=None,
    research_gaps=None,
    methodologies_used=None,
    quality_assessment="Good",
):
    """Build an AsyncMock for gemini_service whose generate_structured_content
    returns the source list on the first call and the analysis on the second.

    Production calls generate_structured_content twice in sequence:
    1. _search_sources_structured(prompt, SourceListSchema) -> only `.sources` is read
    2. _analyze_sources_structured(prompt, LiteratureAnalysisSchema) -> reads
       .key_findings, .research_gaps, .methodologies_used, .quality_assessment

    LiteratureAnalysisSchema satisfies both shapes (it has .sources too).
    """
    mock = AsyncMock()
    mock.generate_structured_content = AsyncMock(
        side_effect=[
            LiteratureAnalysisSchema(sources=sources),
            LiteratureAnalysisSchema(
                key_findings=key_findings or [],
                research_gaps=research_gaps or [],
                methodologies_used=methodologies_used or [],
                quality_assessment=quality_assessment,
            ),
        ]
    )
    return mock


@pytest.fixture(autouse=True)
def _no_live_provider_routing(monkeypatch):
    """These agents execute a plain, plan-less ``AgentTask``. Without
    this, a real MULTI_PROVIDER_ROUTING_ENABLED/OPENROUTER_API_KEY in
    the test environment routes execute() through a live ModelRouter
    instead of the mock gemini fixtures below, making these tests
    nondeterministic and dependent on a paid network call. Module-level
    so any future test added outside the class is covered too."""
    monkeypatch.setattr(settings, "MULTI_PROVIDER_ROUTING_ENABLED", False)


class TestLiteratureReviewAgent:
    """Test cases for Literature Review Agent."""

    @pytest.mark.asyncio
    async def test_execute_literature_review(self):
        """Test successful literature review execution."""
        from src.agents.literature_review_agent import LiteratureReviewAgent

        mock_gemini = _mock_gemini_two_call(
            sources=[
                AcademicSource(
                    title="AI in Healthcare",
                    authors=["Smith J"],
                    year=2024,
                    relevance_score=0.95,
                ),
                AcademicSource(
                    title="Machine Learning Applications",
                    authors=["Doe A"],
                    year=2023,
                    relevance_score=0.88,
                ),
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
                AcademicSource(
                    title="Paper C", authors=["C"], year=2020, relevance_score=0.5
                ),
                AcademicSource(
                    title="Paper A", authors=["A"], year=2024, relevance_score=0.95
                ),
                AcademicSource(
                    title="Paper B", authors=["B"], year=2023, relevance_score=0.75
                ),
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
        from src.agents.literature_review_agent import LiteratureReviewAgent

        mock_gemini = _mock_gemini_two_call(
            sources=[
                AcademicSource(
                    title="Study 1", authors=["Author1"], year=2024, relevance_score=0.9
                ),
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
                AcademicSource(
                    title="Cached", authors=["A"], year=2024, relevance_score=0.9
                ),
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
                AcademicSource(
                    title=f"Paper {i}", authors=["A"], year=2024, relevance_score=0.9
                )
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
                AcademicSource(
                    title="Paper 1", authors=["A"], year=2020, relevance_score=0.5
                ),
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
        """A failing model call is reported as a failed task, not as zero sources."""
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

        assert result.status == "failed"
        assert result.output["error"] == "Gemini API error"

    @pytest.mark.asyncio
    async def test_depth_level_handling(self):
        """Test that agent respects research depth level."""
        from src.agents.literature_review_agent import LiteratureReviewAgent
        from src.models.research_project import ResearchDepth

        mock_gemini = _mock_gemini_two_call(
            sources=[
                AcademicSource(
                    title=f"Paper {i}", authors=["A"], year=2024, relevance_score=0.8
                )
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


class TestToolPathSearchesForReal:
    """#165: with a tool path configured, the agent searches through it even
    when an LLM is configured, and never substitutes model-named sources for
    a search that retrieved nothing."""

    SOURCE = {
        "title": "GNNs for inflation",
        "abstract": "We show that graph neural networks forecast inflation.",
        "url": "https://arxiv.org/abs/2401.00001",
    }
    ANALYSIS = (
        '{"literature_analysis": {"key_findings": ["GNNs forecast inflation"], '
        '"research_gaps": ["No out-of-sample test"], "methodologies_used": [], '
        '"quality_assessment": "One retrieved source"}}'
    )

    @staticmethod
    def _agent(search_result=None, search_error=None):
        from unittest.mock import MagicMock

        from src.agents.literature_review_agent import LiteratureReviewAgent

        mcp = MagicMock()
        mcp.search_academic_sources = AsyncMock(
            return_value=search_result, side_effect=search_error
        )
        mcp.build_knowledge_graph = AsyncMock(return_value={"entities": []})
        mcp.format_citations = AsyncMock(return_value={"citations": []})
        gemini = AsyncMock()
        agent = LiteratureReviewAgent(
            gemini_service=gemini, config={"mcp_integration": mcp}
        )
        return agent, mcp, gemini

    @staticmethod
    def _task():
        return AgentTask(
            id="lit-tool-001",
            agent_type="literature_review",
            input_data={"query": "inflation forecasting"},
        )

    @pytest.mark.asyncio
    async def test_retrieved_sources_are_analysed_and_the_llm_names_none(self):
        agent, mcp, gemini = self._agent(
            {
                "success": True,
                "data_source": "mcp_tools",
                "sources": [self.SOURCE],
                "total_found": 1,
            }
        )

        with patch.object(
            agent,
            "_generate_with_routing",
            AsyncMock(return_value=(self.ANALYSIS, 0.9)),
        ) as model:
            result = await agent.execute(self._task())

        mcp.search_academic_sources.assert_awaited_once()
        gemini.generate_structured_content.assert_not_awaited()
        model.assert_awaited_once()
        assert result.status == "success"
        assert result.output["data_source"] == "mcp_tools"
        assert [s["url"] for s in result.output["sources_found"]] == [
            self.SOURCE["url"]
        ]
        assert result.output["key_findings"] == ["GNNs forecast inflation"]

    @pytest.mark.asyncio
    async def test_a_degraded_search_is_recorded_as_no_evidence(self):
        # The shape MCPIntegration returns for a failed call with
        # enable_fallback=True: stand-in sources that were never retrieved.
        agent, _mcp, gemini = self._agent(
            {
                "success": False,
                "degraded": True,
                "fallback": True,
                "data_source": "fallback",
                "tool_outcome": "failed",
                "error_code": "tool_error",
                "sources": [{"title": "Stand-in", "source": "fallback"}],
                "total_found": 1,
            }
        )

        with patch.object(agent, "_generate_with_routing", AsyncMock()) as model:
            result = await agent.execute(self._task())

        gemini.generate_structured_content.assert_not_awaited()
        model.assert_not_awaited()
        assert result.output["data_source"] == "no_evidence"
        assert result.output["sources_found"] == []
        assert result.output["total_sources"] == 0
        assert result.status == "failed"
        assert result.output["error_code"] == "INSUFFICIENT_EVIDENCE"

    @pytest.mark.asyncio
    async def test_a_search_that_raises_is_recorded_as_no_evidence(self):
        agent, _mcp, gemini = self._agent(search_error=ConnectionError("mcp down"))

        with patch.object(agent, "_generate_with_routing", AsyncMock()) as model:
            result = await agent.execute(self._task())

        gemini.generate_structured_content.assert_not_awaited()
        model.assert_not_awaited()
        assert result.output["data_source"] == "no_evidence"
        assert result.output["sources_found"] == []
        assert result.status == "failed"
        assert result.output["error_code"] == "INSUFFICIENT_EVIDENCE"
