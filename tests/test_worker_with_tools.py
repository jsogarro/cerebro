"""Integration tests for workers with tool registry."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.analytics_agents import DataAnalysisAgent
from src.agents.models import AgentTask
from src.agents.tools.arithmetic_tool import ArithmeticTool
from src.agents.tools.mediation import InMemoryToolAuditStore
from src.agents.tools.registry import ToolRegistry
from src.core.capabilities import CAPABILITY_GRANTS_CONTEXT_KEY
from src.core.config import settings
from src.core.contracts import CapabilityGrant, SensitivityClass, TrustClassification
from src.core.tools import ToolOutcome

NOW = datetime.now(UTC)


def _tool_call_response(name: str, arguments: dict[str, object]) -> str:
    """Build the exact response envelope the worker is expected to accept."""
    return json.dumps(
        {"tool_call": {"name": name, "arguments": arguments}},
        separators=(",", ":"),
    )


def _task_with_context(context: dict[str, object] | None = None) -> AgentTask:
    return AgentTask(
        id="ephemeral-task-id",
        agent_type="data_analysis",
        input_data={"query": "Calculate the result"},
        context=context or {},
    )


def _arithmetic_grant() -> CapabilityGrant:
    return CapabilityGrant(
        grant_id="grant-arithmetic",
        run_id="run-from-context",
        task_id="task-from-context",
        capability_scope="scope-arithmetic",
        tool_name="arithmetic",
        tool_versions=("1.0.0",),
        sensitivity=SensitivityClass.READ_ONLY,
        max_input_trust=TrustClassification.APPLICATION,
        requires_approval=False,
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
    )


def _registry_with_audit_store() -> tuple[ToolRegistry, InMemoryToolAuditStore]:
    audit_store = InMemoryToolAuditStore()
    registry = ToolRegistry(audit_store=audit_store)
    registry.register(ArithmeticTool())
    return registry, audit_store


def _capture_boundary_outcomes(
    registry: ToolRegistry, outcomes: list[ToolOutcome]
) -> None:
    """Replace the registry boundary method with an outcome-recording spy."""
    real_invoke = registry.boundary.invoke

    async def invoke_and_capture(**kwargs: object) -> ToolOutcome:
        outcome = await real_invoke(**kwargs)
        outcomes.append(outcome)
        return outcome

    registry.boundary.invoke = invoke_and_capture  # type: ignore[method-assign]


@pytest.fixture(autouse=True)
def _no_live_provider_routing(monkeypatch):
    """These workers execute a plain, plan-less ``AgentTask``. Without this,
    a real MULTI_PROVIDER_ROUTING_ENABLED/OPENROUTER_API_KEY in the test
    environment routes execute() through a live ModelRouter instead of the
    mock_gemini_service fixture below, making these tests nondeterministic
    and dependent on a paid network call."""
    monkeypatch.setattr(settings, "MULTI_PROVIDER_ROUTING_ENABLED", False)


@pytest.fixture
def mock_gemini_service():
    """Mock Gemini service."""
    service = MagicMock()
    service.generate_content = AsyncMock(
        return_value="Analysis: The data shows a clear upward trend."
    )
    return service


class TestWorkerWithTools:
    """Test suite for workers using tool registry."""

    @pytest.mark.asyncio
    async def test_worker_registers_tools(self, mock_gemini_service):
        """Test that worker registers its tools."""
        agent = DataAnalysisAgent()
        agent.gemini_service = mock_gemini_service

        # Access registry to trigger registration
        registry = agent._get_tool_registry()

        assert registry.has_tool("arithmetic")
        assert len(registry) == 1

    @pytest.mark.asyncio
    async def test_worker_tools_available_in_prompt(self, mock_gemini_service):
        """Test that tools are listed in the prompt."""
        agent = DataAnalysisAgent()
        agent.gemini_service = mock_gemini_service

        task = AgentTask(
            id="test-task",
            agent_type="data_analysis",
            input_data={"query": "Analyze revenue growth"},
        )

        await agent.execute(task)

        # Check that generate_content was called with tool info
        assert mock_gemini_service.generate_content.called
        prompt = mock_gemini_service.generate_content.call_args[0][0]
        assert "Available tools:" in prompt
        assert "arithmetic" in prompt

    @pytest.mark.asyncio
    async def test_worker_without_tools_no_tool_block(self, mock_gemini_service):
        """Test that workers without tools don't get tool block in prompt."""
        from src.agents.llm_worker_base import LLMWorkerAgentBase
        from src.agents.models import AgentTask

        class SimpleWorker(LLMWorkerAgentBase):
            agent_type = "simple_worker"

            def _build_prompt(self, query: str, task: AgentTask) -> str:
                return f"Simple prompt: {query}"

        agent = SimpleWorker()
        agent.gemini_service = mock_gemini_service

        task = AgentTask(
            id="test-task",
            agent_type="simple_worker",
            input_data={"query": "Test query"},
        )

        await agent.execute(task)

        # Check that generate_content was called without tool info
        prompt = mock_gemini_service.generate_content.call_args[0][0]
        assert "Available tools:" not in prompt

    @pytest.mark.asyncio
    async def test_worker_tool_registry_cached(self, mock_gemini_service):
        """Test that tool registry is cached across calls."""
        agent = DataAnalysisAgent()
        agent.gemini_service = mock_gemini_service

        registry1 = agent._get_tool_registry()
        registry2 = agent._get_tool_registry()

        # Should be the same instance
        assert registry1 is registry2

    @pytest.mark.asyncio
    async def test_worker_executes_successfully_with_tools(self, mock_gemini_service):
        """Test that worker executes successfully with tools registered."""
        agent = DataAnalysisAgent()
        agent.gemini_service = mock_gemini_service

        task = AgentTask(
            id="test-task",
            agent_type="data_analysis",
            input_data={"query": "Analyze Q1 revenue: $500k vs Q2: $750k"},
        )

        result = await agent.execute(task)

        assert result.status == "success"
        assert "content" in result.output
        assert result.confidence > 0

    @pytest.mark.asyncio
    async def test_multiple_workers_independent_registries(self, mock_gemini_service):
        """Test that different worker instances have independent registries."""
        agent1 = DataAnalysisAgent()
        agent1.gemini_service = mock_gemini_service

        agent2 = DataAnalysisAgent()
        agent2.gemini_service = mock_gemini_service

        registry1 = agent1._get_tool_registry()
        registry2 = agent2._get_tool_registry()

        # Should be different instances
        assert registry1 is not registry2

    @pytest.mark.asyncio
    async def test_worker_register_tools_hook_called_once(self, mock_gemini_service):
        """Test that _register_tools is only called once per agent instance."""
        call_count = 0

        class TrackedWorker(DataAnalysisAgent):
            def _register_tools(self, registry):
                nonlocal call_count
                call_count += 1
                super()._register_tools(registry)

        agent = TrackedWorker()
        agent.gemini_service = mock_gemini_service

        # Access registry multiple times
        agent._get_tool_registry()
        agent._get_tool_registry()
        agent._get_tool_registry()

        # Should only be called once (on first access)
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_the_tool_loop_routes_through_the_boundary(self, mock_gemini_service):
        """A model tool call must mint an outcome through the shared boundary."""
        mock_gemini_service.generate_content.side_effect = [
            _tool_call_response("arithmetic", {"expression": "2 + 3"}),
            "The result is 5.",
        ]
        agent = DataAnalysisAgent()
        agent.gemini_service = mock_gemini_service
        registry, _audit_store = _registry_with_audit_store()
        agent._tool_registry = registry

        tool = registry.get("arithmetic")
        assert tool is not None
        implementation = tool._execute_impl
        implementation_spy = AsyncMock(wraps=implementation)
        tool._execute_impl = implementation_spy  # type: ignore[method-assign]
        outcomes: list[ToolOutcome] = []
        _capture_boundary_outcomes(registry, outcomes)

        result = await agent.execute(_task_with_context())

        assert result.status == "success"
        assert result.output["content"] == "The result is 5."
        assert len(outcomes) == 1
        assert isinstance(outcomes[0], ToolOutcome)
        assert outcomes[0].succeeded
        assert outcomes[0].invocation.tool_name == "arithmetic"
        assert implementation_spy.await_count == 1

    @pytest.mark.asyncio
    async def test_tool_loop_propagates_durable_identity_and_grants(
        self, mock_gemini_service
    ):
        """Task context, not ephemeral worker state, authorizes the tool call."""
        mock_gemini_service.generate_content.side_effect = [
            _tool_call_response("arithmetic", {"expression": "2 + 3"}),
            "The result is 5.",
        ]
        grant = _arithmetic_grant()
        task = _task_with_context(
            {
                "run_id": "run-from-context",
                "task_id": "task-from-context",
                "attempt_id": "attempt-from-context",
                "organization_id": "00000000-0000-0000-0000-0000000000aa",
                CAPABILITY_GRANTS_CONTEXT_KEY: (grant,),
            }
        )
        agent = DataAnalysisAgent()
        agent.gemini_service = mock_gemini_service
        registry, _audit_store = _registry_with_audit_store()
        agent._tool_registry = registry
        real_invoke = registry.boundary.invoke
        invoke = AsyncMock(wraps=real_invoke)
        registry.boundary.invoke = invoke  # type: ignore[method-assign]

        result = await agent.execute(task)

        assert result.status == "success"
        assert invoke.await_args is not None
        assert {
            name: invoke.await_args.kwargs[name]
            for name in (
                "run_id",
                "task_id",
                "attempt_id",
                "organization_id",
            )
        } == {
            "run_id": "run-from-context",
            "task_id": "task-from-context",
            "attempt_id": "attempt-from-context",
            "organization_id": "00000000-0000-0000-0000-0000000000aa",
        }
        assert invoke.await_args.kwargs["grants"] == [grant]

    @pytest.mark.asyncio
    async def test_ordinary_model_text_is_preserved_exactly(self, mock_gemini_service):
        """A non-envelope response remains byte-for-byte worker output."""
        ordinary_text = "  Keep this answer exactly.\n\n"
        mock_gemini_service.generate_content.return_value = ordinary_text
        agent = DataAnalysisAgent()
        agent.gemini_service = mock_gemini_service

        result = await agent.execute(_task_with_context())

        assert result.status == "success"
        assert result.output["content"] == ordinary_text
        assert result.output["analysis"] == ordinary_text
        assert mock_gemini_service.generate_content.await_count == 1

    @pytest.mark.asyncio
    async def test_malformed_tool_call_fails_closed(self, mock_gemini_service):
        """A tool-call-shaped object with the wrong schema is not executed."""
        mock_gemini_service.generate_content.return_value = json.dumps(
            {"tool_call": {"name": "arithmetic", "arguments": []}}
        )
        agent = DataAnalysisAgent()
        agent.gemini_service = mock_gemini_service
        registry, _audit_store = _registry_with_audit_store()
        agent._tool_registry = registry
        outcomes: list[ToolOutcome] = []
        _capture_boundary_outcomes(registry, outcomes)

        result = await agent.execute(_task_with_context())

        assert result.status == "failed"
        assert result.output["tool_error"]["code"] == "malformed_tool_call"
        assert "content" not in result.output
        assert outcomes == []
        assert mock_gemini_service.generate_content.await_count == 1

    @pytest.mark.asyncio
    async def test_unknown_tool_call_fails_closed(self, mock_gemini_service):
        """A valid envelope naming no registered tool returns an honest error."""
        mock_gemini_service.generate_content.return_value = _tool_call_response(
            "not_registered", {}
        )
        agent = DataAnalysisAgent()
        agent.gemini_service = mock_gemini_service
        registry, _audit_store = _registry_with_audit_store()
        agent._tool_registry = registry
        outcomes: list[ToolOutcome] = []
        _capture_boundary_outcomes(registry, outcomes)

        result = await agent.execute(_task_with_context())

        assert result.status == "failed"
        assert result.output["tool_result"]["success"] is False
        assert result.output["tool_result"]["error"] == ("Unknown tool: not_registered")
        assert "content" not in result.output
        assert outcomes == []

    @pytest.mark.asyncio
    async def test_denied_tool_result_is_not_fabricated_as_success(
        self, mock_gemini_service
    ):
        """A denied durable call stops before any model final-answer turn."""
        mock_gemini_service.generate_content.return_value = _tool_call_response(
            "arithmetic", {"expression": "2 + 3"}
        )
        agent = DataAnalysisAgent()
        agent.gemini_service = mock_gemini_service
        registry, _audit_store = _registry_with_audit_store()
        agent._tool_registry = registry
        outcomes: list[ToolOutcome] = []
        _capture_boundary_outcomes(registry, outcomes)
        task = _task_with_context(
            {
                "run_id": "run-from-context",
                "task_id": "task-from-context",
                "attempt_id": "attempt-from-context",
                "organization_id": "org-from-context",
            }
        )

        result = await agent.execute(task)

        assert result.status == "failed"
        assert result.output["tool_result"]["success"] is False
        assert "denied" in result.output["tool_result"]["error"]
        assert "content" not in result.output
        assert mock_gemini_service.generate_content.await_count == 1
        assert len(outcomes) == 1
        assert outcomes[0].status.value == "denied"

    @pytest.mark.asyncio
    async def test_failed_tool_result_is_not_fabricated_as_success(
        self, mock_gemini_service
    ):
        """A tool failure is returned as a structured failure, never a final answer."""
        mock_gemini_service.generate_content.return_value = _tool_call_response(
            "arithmetic", {"expression": "1 / 0"}
        )
        agent = DataAnalysisAgent()
        agent.gemini_service = mock_gemini_service
        registry, _audit_store = _registry_with_audit_store()
        agent._tool_registry = registry
        outcomes: list[ToolOutcome] = []
        _capture_boundary_outcomes(registry, outcomes)

        result = await agent.execute(_task_with_context())

        assert result.status == "failed"
        assert result.output["tool_result"]["success"] is False
        assert "failed" in result.output["tool_result"]["error"]
        assert "content" not in result.output
        assert mock_gemini_service.generate_content.await_count == 1
        assert len(outcomes) == 1
        assert outcomes[0].succeeded is False

    @pytest.mark.asyncio
    async def test_tool_loop_hard_limits_a_second_tool_call(self, mock_gemini_service):
        """The optional follow-up turn cannot start an unbounded tool loop."""
        mock_gemini_service.generate_content.side_effect = [
            _tool_call_response("arithmetic", {"expression": "2 + 3"}),
            _tool_call_response("arithmetic", {"expression": "4 + 5"}),
        ]
        agent = DataAnalysisAgent()
        agent.gemini_service = mock_gemini_service
        registry, _audit_store = _registry_with_audit_store()
        agent._tool_registry = registry
        outcomes: list[ToolOutcome] = []
        _capture_boundary_outcomes(registry, outcomes)

        result = await agent.execute(_task_with_context())

        assert result.status == "failed"
        assert result.output["tool_error"]["code"] == "tool_loop_limit"
        assert result.output["tool_result"]["success"] is True
        assert "content" not in result.output
        assert mock_gemini_service.generate_content.await_count == 2
        assert len(outcomes) == 1
