"""
Tests for Agent Factory.

Following TDD principles - tests written before implementation.
"""

from unittest.mock import AsyncMock

import pytest


class TestAgentFactory:
    """Test cases for Agent Factory."""

    def test_create_literature_review_agent(self):
        """Test creation of Literature Review Agent."""
        from src.agents.factory import AgentFactory
        from src.agents.literature_review_agent import LiteratureReviewAgent

        config = {"gemini_service": AsyncMock(), "cache_client": AsyncMock()}

        agent = AgentFactory.create_agent("literature_review", config)

        assert isinstance(agent, LiteratureReviewAgent)
        assert agent.gemini_service == config["gemini_service"]
        assert agent.cache_client == config["cache_client"]

    def test_create_agent_with_minimal_config(self):
        """Test agent creation with minimal configuration."""
        from src.agents.factory import AgentFactory
        from src.agents.literature_review_agent import LiteratureReviewAgent

        agent = AgentFactory.create_agent("literature_review", {})

        assert isinstance(agent, LiteratureReviewAgent)
        assert agent.gemini_service is None
        assert agent.cache_client is None

    def test_invalid_agent_type(self):
        """Test handling of invalid agent type."""
        from src.agents.factory import AgentFactory

        with pytest.raises(ValueError) as exc_info:
            AgentFactory.create_agent("invalid_agent", {})

        assert "Unknown agent type" in str(exc_info.value)

    def test_get_all_agents(self):
        """Test getting all available agents."""
        from src.agents.factory import AgentFactory

        config = {"gemini_service": AsyncMock(), "cache_client": AsyncMock()}

        agents = AgentFactory.get_all_agents(config)

        assert isinstance(agents, list)
        assert len(agents) >= 1  # At least Literature Review Agent

        # Check agent types
        agent_types = [agent.get_agent_type() for agent in agents]
        assert "literature_review" in agent_types

    def test_get_available_agent_types(self):
        """Test getting list of available agent types."""
        from src.agents.factory import AgentFactory

        agent_types = AgentFactory.get_available_agent_types()

        assert isinstance(agent_types, list)
        assert "literature_review" in agent_types
        # When other agents are implemented:
        # assert "comparative_analysis" in agent_types
        # assert "methodology" in agent_types
        # assert "synthesis" in agent_types
        # assert "citation" in agent_types

    def test_create_agent_with_custom_config(self):
        """Test agent creation with custom configuration."""
        from src.agents.factory import AgentFactory

        custom_config = {
            "gemini_service": AsyncMock(),
            "cache_client": AsyncMock(),
            "custom_setting": "test_value",
        }

        agent = AgentFactory.create_agent("literature_review", custom_config)

        assert agent.config.get("custom_setting") == "test_value"

    def test_factory_singleton_pattern(self):
        """Test that factory can be used as a singleton if needed."""
        from src.agents.factory import AgentFactory

        factory1 = AgentFactory()
        factory2 = AgentFactory()

        # Both should have access to the same agent registry
        assert factory1.get_agent_registry() == factory2.get_agent_registry()

    def test_register_custom_agent(self):
        """Test registering a custom agent type."""
        from src.agents.base import BaseAgent
        from src.agents.factory import AgentFactory
        from src.agents.models import AgentResult, AgentTask

        # Create a custom agent
        class CustomAgent(BaseAgent):
            async def execute(self, task: AgentTask) -> AgentResult:
                return AgentResult(
                    task_id=task.id,
                    status="success",
                    output={"custom": "result"},
                    confidence=1.0,
                    execution_time=0.1,
                    metadata={},
                )

            async def validate_result(self, result: AgentResult) -> bool:
                return True

            def get_agent_type(self) -> str:
                return "custom"

        # Register the custom agent
        AgentFactory.register_agent("custom", CustomAgent)

        # Create an instance
        agent = AgentFactory.create_agent("custom", {})

        assert isinstance(agent, CustomAgent)
        assert agent.get_agent_type() == "custom"

        # Verify it's in the registry
        assert "custom" in AgentFactory.get_available_agent_types()
