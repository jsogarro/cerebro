"""
Tests for Content and Analytics Supervisors

Validates that content and analytics supervisors:
1. Instantiate correctly with the BaseSupervisor signature
2. Register worker types appropriately
3. Execute domain-appropriate workflows end-to-end
4. Are properly registered in the supervisor factory and routing
"""

from datetime import UTC, datetime

import pytest

from src.agents.models import AgentTask
from src.agents.supervisors.analytics_supervisor import AnalyticsSupervisor
from src.agents.supervisors.base_supervisor import BaseSupervisor, SupervisionState
from src.agents.supervisors.content_supervisor import ContentSupervisor
from src.agents.supervisors.supervisor_factory import (
    SupervisorCapability,
    SupervisorFactory,
    SupervisorSpecification,
)
from src.ai_brain.integration.masr_supervisor_bridge import SupervisorConfiguration
from src.ai_brain.router.cost_optimizer import ModelSpec, ModelTier, OptimizationResult
from src.ai_brain.router.masr import (
    AgentAllocation,
    CollaborationMode,
    RoutingDecision,
)
from src.ai_brain.router.query_analyzer import ComplexityAnalysis, ComplexityLevel
from src.api.services.component_catalog import build_application_component_registry
from src.core.kernel.component_keys import SUPERVISOR_KEYS


class _ExtensionSupervisor(BaseSupervisor):
    def __init__(
        self,
        gemini_service=None,
        cache_client=None,
        config=None,
    ) -> None:
        super().__init__(
            supervisor_type="extension",
            domain="extension",
            gemini_service=gemini_service,
            cache_client=cache_client,
            config=config,
        )

    def _register_worker_types(self) -> None:
        self.worker_definitions = {}

    def _build_workflow_graph(self) -> None:
        self.workflow_graph = None

    async def _coordinate_workers(
        self,
        state: SupervisionState,
        task: AgentTask,
    ) -> SupervisionState:
        return state


def _make_routing_decision(
    supervisor_type: str,
    worker_types: list[str],
    collaboration_mode: CollaborationMode,
    level: ComplexityLevel,
    *,
    max_parallel: int = 2,
    estimated_cost: float = 0.01,
    estimated_quality: float = 0.88,
    confidence_score: float = 0.85,
) -> RoutingDecision:
    """Build a complete, valid RoutingDecision for bridge/translator tests."""
    model = ModelSpec(
        name="gemini-2.5-flash",
        provider="google",
        tier=ModelTier.STANDARD,
        cost_per_1k_tokens=0.001,
        avg_latency_ms=500,
        context_window=1_000_000,
    )
    return RoutingDecision(
        query_id=f"test_{supervisor_type}_query",
        timestamp=datetime.now(UTC),
        complexity_analysis=ComplexityAnalysis(
            score=0.5,
            level=level,
            factors=None,  # type: ignore[arg-type]
            domains=[],
        ),
        optimization_result=OptimizationResult(primary_model=model),
        collaboration_mode=collaboration_mode,
        agent_allocation=AgentAllocation(
            supervisor_type=supervisor_type,
            worker_types=worker_types,
            worker_count=len(worker_types),
            max_parallel=max_parallel,
            timeout_seconds=300,
        ),
        estimated_cost=estimated_cost,
        estimated_latency_ms=1500,
        estimated_quality=estimated_quality,
        confidence_score=confidence_score,
    )


class TestContentSupervisor:
    """Test suite for ContentSupervisor."""

    @pytest.fixture
    def content_supervisor(self):
        """Create a ContentSupervisor instance."""
        return ContentSupervisor()

    def test_initialization(self, content_supervisor):
        """Test ContentSupervisor initializes with correct defaults."""
        assert content_supervisor.supervisor_type == "content"
        assert content_supervisor.domain == "content"
        assert content_supervisor.content_format == "article"
        assert content_supervisor.target_audience == "general"
        assert content_supervisor.max_editing_rounds == 2

    def test_initialization_with_custom_config(self):
        """Test ContentSupervisor accepts custom configuration."""
        config = {
            "content_format": "blog",
            "target_audience": "technical",
            "max_editing_rounds": 3,
            "quality_threshold": 0.9,
        }
        supervisor = ContentSupervisor(config=config)
        assert supervisor.content_format == "blog"
        assert supervisor.target_audience == "technical"
        assert supervisor.max_editing_rounds == 3
        assert supervisor.quality_threshold == 0.9

    def test_worker_definitions_registered(self, content_supervisor):
        """Test ContentSupervisor registers appropriate worker types."""
        expected_workers = {"content_planning", "drafting", "editing", "optimization"}
        registered_workers = set(content_supervisor.worker_definitions.keys())
        assert expected_workers == registered_workers

    def test_worker_definition_properties(self, content_supervisor):
        """Test worker definitions have required properties."""
        for worker_type, worker_def in content_supervisor.worker_definitions.items():
            assert worker_def.worker_type == worker_type
            assert worker_def.agent_class is not None
            assert worker_def.specialization
            assert len(worker_def.capabilities) > 0
            assert worker_def.avg_execution_time_ms > 0
            assert 0 < worker_def.reliability_score <= 1.0
            assert 0 < worker_def.quality_score <= 1.0

    def test_workflow_graph_built(self, content_supervisor):
        """Test ContentSupervisor builds a LangGraph workflow."""
        assert content_supervisor.workflow_graph is not None

    @pytest.mark.asyncio
    async def test_execute_returns_result(self, content_supervisor):
        """Test ContentSupervisor.execute returns an AgentResult."""
        task = AgentTask(
            id="test_content_task",
            agent_type="content",
            input_data={"query": "Write an article about Python testing"},
        )

        result = await content_supervisor.execute(task)

        assert result is not None
        assert result.task_id == task.id
        assert result.status in ["completed", "partial", "failed"]
        assert "supervision_quality" in result.output
        assert "coordination_metadata" in result.output


class TestAnalyticsSupervisor:
    """Test suite for AnalyticsSupervisor."""

    @pytest.fixture
    def analytics_supervisor(self):
        """Create an AnalyticsSupervisor instance."""
        return AnalyticsSupervisor()

    def test_initialization(self, analytics_supervisor):
        """Test AnalyticsSupervisor initializes with correct defaults."""
        assert analytics_supervisor.supervisor_type == "analytics"
        assert analytics_supervisor.domain == "analytics"
        assert analytics_supervisor.analysis_depth == "comprehensive"
        assert analytics_supervisor.confidence_level == 0.95
        assert analytics_supervisor.max_modeling_iterations == 3

    def test_initialization_with_custom_config(self):
        """Test AnalyticsSupervisor accepts custom configuration."""
        config = {
            "analysis_depth": "exploratory",
            "confidence_level": 0.99,
            "max_modeling_iterations": 5,
            "quality_threshold": 0.92,
        }
        supervisor = AnalyticsSupervisor(config=config)
        assert supervisor.analysis_depth == "exploratory"
        assert supervisor.confidence_level == 0.99
        assert supervisor.max_modeling_iterations == 5
        assert supervisor.quality_threshold == 0.92

    def test_worker_definitions_registered(self, analytics_supervisor):
        """Test AnalyticsSupervisor registers appropriate worker types."""
        expected_workers = {
            "data_analysis",
            "statistical_modeling",
            "insight_synthesis",
        }
        registered_workers = set(analytics_supervisor.worker_definitions.keys())
        assert expected_workers == registered_workers

    def test_worker_definition_properties(self, analytics_supervisor):
        """Test worker definitions have required properties."""
        for worker_type, worker_def in analytics_supervisor.worker_definitions.items():
            assert worker_def.worker_type == worker_type
            assert worker_def.agent_class is not None
            assert worker_def.specialization
            assert len(worker_def.capabilities) > 0
            assert worker_def.avg_execution_time_ms > 0
            assert 0 < worker_def.reliability_score <= 1.0
            assert 0 < worker_def.quality_score <= 1.0

    def test_workflow_graph_built(self, analytics_supervisor):
        """Test AnalyticsSupervisor builds a LangGraph workflow."""
        assert analytics_supervisor.workflow_graph is not None

    @pytest.mark.asyncio
    async def test_execute_returns_result(self, analytics_supervisor):
        """Test AnalyticsSupervisor.execute returns an AgentResult."""
        task = AgentTask(
            id="test_analytics_task",
            agent_type="analytics",
            input_data={"query": "Analyze user engagement trends"},
        )

        result = await analytics_supervisor.execute(task)

        assert result is not None
        assert result.task_id == task.id
        assert result.status in ["completed", "partial", "failed"]
        assert "supervision_quality" in result.output
        assert "coordination_metadata" in result.output


class TestSupervisorFactoryRegistration:
    """Test supervisor factory registration."""

    @pytest.fixture
    def factory(self):
        """Create a SupervisorFactory instance."""
        return SupervisorFactory()

    def test_content_supervisor_registered(self, factory):
        """Test ContentSupervisor is registered in factory."""
        spec = factory.get_supervisor_spec("content")
        assert spec is not None
        assert spec.supervisor_type == "content"
        assert spec.supervisor_class == ContentSupervisor
        assert spec.domain == "content"

    def test_analytics_supervisor_registered(self, factory):
        """Test AnalyticsSupervisor is registered in factory."""
        spec = factory.get_supervisor_spec("analytics")
        assert spec is not None
        assert spec.supervisor_type == "analytics"
        assert spec.supervisor_class == AnalyticsSupervisor
        assert spec.domain == "analytics"

    def test_research_supervisor_still_registered(self, factory):
        """Test ResearchSupervisor remains registered."""
        spec = factory.get_supervisor_spec("research")
        assert spec is not None
        assert spec.supervisor_type == "research"
        assert spec.domain == "research"

    def test_all_three_supervisors_available(self, factory):
        """Test all three supervisors are available."""
        available = factory.get_available_supervisors()
        supervisor_types = {spec.supervisor_type for spec in available}
        assert {"research", "content", "analytics"} <= supervisor_types

    @pytest.mark.asyncio
    async def test_factory_creates_content_supervisor(self, factory):
        """Test factory can create ContentSupervisor instance."""
        from src.ai_brain.integration.masr_supervisor_bridge import (
            SupervisorConfiguration,
        )

        config = SupervisorConfiguration(
            supervisor_type="content",
            domain="content",
            worker_allocation=["content_planning", "drafting"],
            quality_threshold=0.85,
            max_refinement_rounds=2,
            timeout_seconds=300,
        )

        supervisor = await factory.create_supervisor_from_config(config)
        assert supervisor is not None
        assert isinstance(supervisor, ContentSupervisor)

    @pytest.mark.asyncio
    async def test_factory_creates_analytics_supervisor(self, factory):
        """Test factory can create AnalyticsSupervisor instance."""
        config = SupervisorConfiguration(
            supervisor_type="analytics",
            domain="analytics",
            worker_allocation=["data_analysis", "statistical_modeling"],
            quality_threshold=0.90,
            max_refinement_rounds=3,
            timeout_seconds=300,
        )

        supervisor = await factory.create_supervisor_from_config(config)
        assert supervisor is not None
        assert isinstance(supervisor, AnalyticsSupervisor)

    @pytest.mark.asyncio
    async def test_factory_creates_registered_non_builtin_supervisor(self, factory):
        factory.register_supervisor(
            SupervisorSpecification(
                supervisor_type="extension",
                supervisor_class=_ExtensionSupervisor,
                domain="extension",
                capabilities={SupervisorCapability.SERVICE},
            )
        )

        supervisor = await factory.create_supervisor_from_config(
            SupervisorConfiguration(
                supervisor_type="extension",
                domain="extension",
                worker_allocation=[],
                quality_threshold=0.8,
                max_refinement_rounds=1,
                timeout_seconds=60,
            )
        )

        assert isinstance(supervisor, _ExtensionSupervisor)

    def test_factory_rejects_builtin_supervisor_override(self, factory):
        original = factory.get_supervisor_spec("research")

        with pytest.raises(
            ValueError,
            match="Built-in supervisor 'research' cannot be overridden",
        ):
            factory.register_supervisor(
                SupervisorSpecification(
                    supervisor_type="research",
                    supervisor_class=_ExtensionSupervisor,
                    domain="extension",
                    capabilities={SupervisorCapability.SERVICE},
                )
            )

        assert factory.get_supervisor_spec("research") is original


class TestMASRBridgeIntegration:
    """Test MASR bridge can route to content and analytics supervisors."""

    @pytest.mark.asyncio
    async def test_bridge_routes_to_content_supervisor(self):
        """Translator maps a content routing decision to the content supervisor."""
        from src.ai_brain.integration.masr_supervisor_bridge import (
            RoutingDecisionTranslator,
        )

        routing_decision = _make_routing_decision(
            supervisor_type="content",
            worker_types=["content_planning", "drafting"],
            collaboration_mode=CollaborationMode.PARALLEL,
            level=ComplexityLevel.MODERATE,
        )

        config = RoutingDecisionTranslator().translate(routing_decision)
        assert config.supervisor_type == "content"
        assert config.domain in ["content", "research"]  # May fall back to research

    @pytest.mark.asyncio
    async def test_bridge_routes_to_analytics_supervisor(self):
        """Translator maps an analytics routing decision to the analytics supervisor."""
        from src.ai_brain.integration.masr_supervisor_bridge import (
            RoutingDecisionTranslator,
        )

        routing_decision = _make_routing_decision(
            supervisor_type="analytics",
            worker_types=["data_analysis", "statistical_modeling"],
            collaboration_mode=CollaborationMode.HIERARCHICAL,
            level=ComplexityLevel.COMPLEX,
            max_parallel=1,
            estimated_cost=0.012,
            estimated_quality=0.89,
            confidence_score=0.87,
        )

        config = RoutingDecisionTranslator().translate(routing_decision)
        assert config.supervisor_type == "analytics"
        assert config.domain in ["analytics", "research"]  # May fall back to research


class TestPrimaryPathRouting:
    """MASR routes domain queries to the content/analytics supervisor types, and the
    direct-execution registry can resolve them (not just fall back to research)."""

    @pytest.mark.asyncio
    async def test_router_routes_content_and_analytics_queries(self):
        from src.ai_brain.router.masr import MASRouter

        router = MASRouter()
        content = await router.route(
            "Write a blog post about the benefits of meditation", context={}
        )
        analytics = await router.route(
            "Analyze quarterly sales data and identify revenue trends", context={}
        )
        assert content.agent_allocation.supervisor_type == "content"
        assert analytics.agent_allocation.supervisor_type == "analytics"

    def test_application_catalog_includes_new_supervisor_domains(self):
        """The application catalog must resolve content and analytics supervisors."""
        registry = build_application_component_registry()

        assert registry.resolve(SUPERVISOR_KEYS["content"]) is ContentSupervisor
        assert registry.resolve(SUPERVISOR_KEYS["analytics"]) is AnalyticsSupervisor
