"""Integration tests for workers with tool registry."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.analytics_agents import DataAnalysisAgent
from src.agents.models import AgentTask


@pytest.fixture
def mock_gemini_service():
    """Mock Gemini service."""
    service = MagicMock()
    service.generate_content = AsyncMock(
        return_value="Analysis: The data shows a clear upward trend.",
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
