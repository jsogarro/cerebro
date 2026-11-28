"""Agent handlers catch only what their body can raise; anything else propagates."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel
from pydantic.errors import PydanticInvalidForJsonSchema
from redis.exceptions import RedisError

from src.agents.citation_agent import CitationAgent
from src.agents.communication.consensus_builder import ConsensusBuilder
from src.agents.comparative_analysis_agent import ComparativeAnalysisAgent
from src.agents.comparative_insight_synthesizer import ComparativeInsightSynthesizer
from src.agents.comparative_theory_extractor import TheoryExtractor
from src.agents.finance_agents import FinancialAnalysisAgent
from src.agents.literature_review_agent import LiteratureReviewAgent
from src.agents.llm_worker_base import LLMWorkerAgentBase
from src.agents.methodology_agent import MethodologyAgent
from src.agents.models import AgentResult, AgentTask
from src.agents.supervisors.base_supervisor import BaseSupervisor
from src.agents.synthesis_agent import SynthesisAgent

DEGRADED = {"success": False, "error": "tool down"}
SOURCES = [{"title": "T", "abstract": "A", "authors": ["X"], "year": 2020}]


def _task(agent_type: str, **input_data) -> AgentTask:
    return AgentTask(id="t", agent_type=agent_type, input_data=input_data)


def _result() -> AgentResult:
    return AgentResult(
        task_id="t", status="success", output={}, confidence=1.0, execution_time=0.0
    )


class TestBaseCache:
    @pytest.mark.asyncio
    async def test_redis_failure_on_write_is_logged_not_raised(self):
        agent = CitationAgent(
            cache_client=AsyncMock(setex=AsyncMock(side_effect=RedisError("down")))
        )
        await agent.cache_result("k", _result())

    @pytest.mark.asyncio
    async def test_unexpected_write_error_propagates(self):
        agent = CitationAgent(
            cache_client=AsyncMock(setex=AsyncMock(side_effect=RuntimeError("bug")))
        )
        with pytest.raises(RuntimeError):
            await agent.cache_result("k", _result())

    @pytest.mark.asyncio
    async def test_redis_failure_on_read_is_a_cache_miss(self):
        agent = CitationAgent(
            cache_client=AsyncMock(get=AsyncMock(side_effect=RedisError("down")))
        )
        assert await agent.get_cached_result("k") is None

    @pytest.mark.asyncio
    async def test_unexpected_read_error_propagates(self):
        agent = CitationAgent(
            cache_client=AsyncMock(get=AsyncMock(side_effect=RuntimeError("bug")))
        )
        with pytest.raises(RuntimeError):
            await agent.get_cached_result("k")


class TestMcpCallsAreNotWrapped:
    """MCPIntegration reports tool failure as a degraded result; a raise is a bug."""

    @pytest.mark.asyncio
    async def test_citation_formatting_propagates_a_raise(self):
        agent = CitationAgent()
        agent.mcp_integration = SimpleNamespace(
            format_citations=AsyncMock(side_effect=RuntimeError("bug"))
        )
        with pytest.raises(RuntimeError):
            await agent._format_citations_with_mcp(SOURCES, "APA")

    @pytest.mark.asyncio
    async def test_citation_formatting_degraded_result_uses_fallback(self):
        agent = CitationAgent()
        agent.mcp_integration = SimpleNamespace(
            format_citations=AsyncMock(return_value=DEGRADED)
        )
        agent._format_citations_with_gemini = AsyncMock(return_value={"via": "gemini"})
        assert await agent._format_citations_with_mcp(SOURCES, "APA") == {
            "via": "gemini"
        }

    @pytest.mark.asyncio
    async def test_bibliography_propagates_a_raise(self):
        agent = CitationAgent()
        agent.mcp_integration = SimpleNamespace(
            format_citations=AsyncMock(side_effect=RuntimeError("bug"))
        )
        with pytest.raises(RuntimeError):
            await agent._generate_bibliography_with_mcp(SOURCES, "APA")

    @pytest.mark.asyncio
    async def test_bibliography_degraded_result_formats_locally(self):
        agent = CitationAgent()
        agent.mcp_integration = SimpleNamespace(
            format_citations=AsyncMock(return_value=DEGRADED)
        )
        result = await agent._generate_bibliography_with_mcp(SOURCES, "APA")
        assert result["fallback"] is True

    @pytest.mark.asyncio
    async def test_statistical_comparison_propagates_a_raise(self):
        agent = ComparativeAnalysisAgent()
        agent.mcp_integration = SimpleNamespace(
            analyze_statistics=AsyncMock(side_effect=RuntimeError("bug"))
        )
        with pytest.raises(RuntimeError):
            await agent._perform_statistical_comparison(
                {"comparison_data": {"a": [1.0, 2.0]}, "criteria": ["x"]}
            )

    @pytest.mark.asyncio
    async def test_comparison_graph_propagates_a_raise(self):
        agent = ComparativeAnalysisAgent()
        agent.mcp_integration = SimpleNamespace(
            build_knowledge_graph=AsyncMock(side_effect=RuntimeError("bug"))
        )
        with pytest.raises(RuntimeError):
            await agent._build_comparison_knowledge_graph(
                {"items": ["a"], "criteria": ["c"]}, {}
            )

    @pytest.mark.asyncio
    async def test_comparison_graph_degraded_result_reports_error(self):
        agent = ComparativeAnalysisAgent()
        agent.mcp_integration = SimpleNamespace(
            build_knowledge_graph=AsyncMock(return_value=DEGRADED)
        )
        result = await agent._build_comparison_knowledge_graph(
            {"items": ["a"], "criteria": ["c"]}, {}
        )
        assert result == {"success": False, "error": "tool down"}

    @pytest.mark.asyncio
    async def test_methodology_citations_propagate_a_raise(self):
        mcp = SimpleNamespace(
            format_citations=AsyncMock(side_effect=RuntimeError("bug"))
        )
        research = {
            "success": True,
            "sources": [{"title": "A methodology study", "abstract": "method"}],
        }
        with pytest.raises(RuntimeError):
            await ComparativeInsightSynthesizer().cite_comparison_methodologies(
                research, mcp, lambda m: None, lambda m: None
            )

    @pytest.mark.asyncio
    async def test_comparative_search_propagates_a_raise(self):
        mcp = SimpleNamespace(
            search_academic_sources=AsyncMock(side_effect=RuntimeError("bug"))
        )
        noop = lambda m: None  # noqa: E731
        with pytest.raises(RuntimeError):
            await TheoryExtractor().search_comparative_studies(
                {"items": ["a"], "criteria": ["c"]}, mcp, noop, noop, noop
            )

    @pytest.mark.asyncio
    async def test_comparative_search_degraded_result_uses_fallback(self):
        mcp = SimpleNamespace(search_academic_sources=AsyncMock(return_value=DEGRADED))
        noop = lambda m: None  # noqa: E731
        result = await TheoryExtractor().search_comparative_studies(
            {"items": ["a"], "criteria": ["c"]}, mcp, noop, noop, noop
        )
        assert result["success"] is not True or result.get("fallback")

    @pytest.mark.asyncio
    async def test_literature_graph_and_citations_propagate_a_raise(self):
        agent = LiteratureReviewAgent()
        agent.mcp_integration = SimpleNamespace(
            build_knowledge_graph=AsyncMock(side_effect=RuntimeError("bug")),
            format_citations=AsyncMock(side_effect=RuntimeError("bug")),
        )
        with pytest.raises(RuntimeError):
            await agent._build_literature_knowledge_graph({"sources": SOURCES}, {})
        with pytest.raises(RuntimeError):
            await agent._format_source_citations({"sources": SOURCES})

    @pytest.mark.asyncio
    async def test_literature_search_still_absorbs_connection_errors_only(self):
        agent = LiteratureReviewAgent()
        agent.mcp_integration = SimpleNamespace(
            search_academic_sources=AsyncMock(side_effect=ConnectionError("down"))
        )
        assert (await agent._search_academic_sources({"query": "q"}))[
            "success"
        ] is False
        agent.mcp_integration = SimpleNamespace(
            search_academic_sources=AsyncMock(side_effect=RuntimeError("bug"))
        )
        with pytest.raises(RuntimeError):
            await agent._search_academic_sources({"query": "q"})


class TestModelOutputHandlers:
    @pytest.mark.asyncio
    async def test_analysis_with_unusable_json_falls_back(self):
        agent = ComparativeAnalysisAgent()
        agent._generate_with_routing = AsyncMock(return_value=("not json", 0.5))
        agent._generate_mock_analysis_with_mcp = lambda *a: {"mock": True}
        result = await agent._analyze_comparison_with_gemini(
            {}, {}, {}, _task("comparative_analysis")
        )
        assert result == {"mock": True}

    @pytest.mark.asyncio
    async def test_analysis_propagates_provider_policy_failure(self):
        from src.agents.llm_worker_base import PlanProviderUnavailableError

        agent = ComparativeAnalysisAgent()
        agent._generate_with_routing = AsyncMock(
            side_effect=PlanProviderUnavailableError("no")
        )
        with pytest.raises(PlanProviderUnavailableError):
            await agent._analyze_comparison_with_gemini(
                {}, {}, {}, _task("comparative_analysis")
            )

    @pytest.mark.asyncio
    async def test_literature_analysis_propagates_provider_policy_failure(self):
        from src.agents.llm_worker_base import PlanProviderUnavailableError

        agent = LiteratureReviewAgent()
        agent._generate_with_routing = AsyncMock(
            side_effect=PlanProviderUnavailableError("no")
        )
        with pytest.raises(PlanProviderUnavailableError):
            await agent._analyze_literature_with_gemini(
                {}, {"sources": SOURCES}, _task("literature_review")
            )

    @pytest.mark.asyncio
    async def test_the_agent_has_no_model_source_search(self):
        agent = LiteratureReviewAgent()
        assert not hasattr(agent, "_search_sources_structured")
        assert not hasattr(agent, "_analyze_sources_structured")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("agent_cls", "agent_type", "data"),
        [
            (MethodologyAgent, "methodology", {"research_question": "q"}),
            (SynthesisAgent, "synthesis", {"agent_outputs": {"a": {"x": 1}}}),
        ],
    )
    async def test_generation_failure_is_a_failed_result_not_canned_content(
        self, agent_cls, agent_type, data
    ):
        agent = agent_cls()
        agent._generate_structured_with_routing = AsyncMock(
            side_effect=RuntimeError("llm down")
        )
        result = await agent.execute(_task(agent_type, **data))
        assert result.status == "failed"
        assert result.output["error"] == "llm down"


class TestNarrowedCatches:
    def test_financial_ratios_ignores_non_numeric_input_only(self, monkeypatch):
        agent = FinancialAnalysisAgent()
        bad = _task(
            "financial_analysis",
            parameters={"values": {"net_income": "a", "revenue": "b"}},
        )
        assert agent._precompute(bad) is None

        def boom(values):
            raise RuntimeError("bug")

        monkeypatch.setattr("src.agents.tools.finance_math.financial_ratios", boom)
        good = _task("financial_analysis", parameters={"values": {"revenue": 1.0}})
        with pytest.raises(RuntimeError):
            agent._precompute(good)

    def test_token_measurement_skips_unserializable_but_not_bugs(self, monkeypatch):
        from src.agents.communication.talkhier_message import TalkHierMessage

        monkeypatch.setattr(
            "src.agents.supervisors.base_supervisor.telemetry_enabled", lambda: True
        )
        sup = SimpleNamespace(supervisor_type="s")
        measure = BaseSupervisor._measure_worker_results_tokens
        assert measure(sup, {"w": {(1, 2): "tuple key"}}) == 0

        class Broken(TalkHierMessage):
            def __init__(self):
                pass

            def to_dict(self):
                raise RuntimeError("bug")

        with pytest.raises(RuntimeError):
            measure(sup, {"w": Broken()})

    def test_schema_instructions_fall_back_only_for_schema_errors(self):
        class Unrenderable(BaseModel):
            @classmethod
            def model_json_schema(cls, *a, **k):
                raise PydanticInvalidForJsonSchema("nope")

        class Buggy(BaseModel):
            @classmethod
            def model_json_schema(cls, *a, **k):
                raise RuntimeError("bug")

        agent = MethodologyAgent()
        build = LLMWorkerAgentBase._build_json_schema_instructions
        assert build(agent, Unrenderable) == "Respond with valid JSON."
        with pytest.raises(RuntimeError):
            build(agent, Buggy)

    @pytest.mark.asyncio
    async def test_consensus_evaluation_failure_propagates(self):
        broken = SimpleNamespace(talkhier_content=object())
        with pytest.raises(AttributeError):
            await ConsensusBuilder().evaluate_consensus([broken])
