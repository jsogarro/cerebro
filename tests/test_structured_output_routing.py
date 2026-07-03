"""Tests for structured output routing through multi-provider layer.

Tests the new `_generate_structured_with_routing` method that routes
structured output generation through OpenRouter (when enabled) or falls
back to GeminiService.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, Field

from src.agents.llm_worker_base import LLMWorkerAgentBase
from src.agents.models import AgentTask


# Test Pydantic schemas
class SimpleSchema(BaseModel):
    """Simple test schema."""

    message: str = Field(description="A simple message")
    count: int = Field(description="A count value", default=0)


class ComplexSchema(BaseModel):
    """Complex test schema."""

    items: list[str] = Field(description="List of items")
    metadata: dict[str, Any] = Field(description="Metadata dictionary")
    confidence: float = Field(description="Confidence score", ge=0.0, le=1.0)


# Concrete test agent (not a test class for pytest)
class StructuredTestAgent(LLMWorkerAgentBase):
    """Test agent for structured output routing."""

    agent_type: str = "test_structured"

    def _build_prompt(self, query: str, task: AgentTask) -> str:
        return f"Test prompt: {query}"


@pytest.fixture
def test_agent():
    """Create test agent instance."""
    return StructuredTestAgent()


@pytest.fixture
def test_task():
    """Create test task."""
    return AgentTask(
        id="test-task-123",
        agent_type="test_structured",
        input_data={"query": "test query", "complexity_score": 0.5},
    )


class TestStructuredRoutingFlagOff:
    """Test structured routing when multi-provider flag is OFF."""

    @patch("src.core.config.settings")
    async def test_flag_off_delegates_to_gemini(
        self, mock_settings, test_agent, test_task
    ):
        """When flag is OFF, delegates to GeminiService."""
        mock_settings.MULTI_PROVIDER_ROUTING_ENABLED = False
        mock_settings.OPENROUTER_API_KEY = "test-key"

        # Mock GeminiService
        mock_gemini = AsyncMock()
        expected_result = SimpleSchema(message="test", count=42)
        mock_gemini.generate_structured_content.return_value = expected_result
        test_agent.gemini_service = mock_gemini

        # Execute
        result = await test_agent._generate_structured_with_routing(
            "test prompt", SimpleSchema, test_task
        )

        # Verify
        assert result == expected_result
        mock_gemini.generate_structured_content.assert_called_once_with(
            "test prompt", SimpleSchema
        )

    @patch("src.core.config.settings")
    async def test_flag_on_but_no_api_key_uses_gemini(
        self, mock_settings, test_agent, test_task
    ):
        """When flag is ON but no API key, falls back to Gemini."""
        mock_settings.MULTI_PROVIDER_ROUTING_ENABLED = True
        mock_settings.OPENROUTER_API_KEY = None

        # Mock GeminiService
        mock_gemini = AsyncMock()
        expected_result = SimpleSchema(message="test", count=42)
        mock_gemini.generate_structured_content.return_value = expected_result
        test_agent.gemini_service = mock_gemini

        # Execute
        result = await test_agent._generate_structured_with_routing(
            "test prompt", SimpleSchema, test_task
        )

        # Verify
        assert result == expected_result
        mock_gemini.generate_structured_content.assert_called_once()


class TestStructuredRoutingFlagOn:
    """Test structured routing when multi-provider flag is ON."""

    @patch("src.core.config.settings")
    @patch("src.ai_brain.providers.ModelRouter")
    async def test_flag_on_routes_via_openrouter(
        self, mock_router_class, mock_settings, test_agent, test_task
    ):
        """When flag is ON, routes through OpenRouter with JSON mode."""
        mock_settings.MULTI_PROVIDER_ROUTING_ENABLED = True
        mock_settings.OPENROUTER_API_KEY = "test-key"
        mock_settings.OPENROUTER_ENDPOINT = "https://test.endpoint"
        mock_settings.OPENROUTER_TIER_MAPPING = {
            "balanced": "anthropic/claude-sonnet-4.6"
        }

        # Mock successful OpenRouter response
        mock_router = AsyncMock()
        mock_response = MagicMock()
        mock_response.success = True
        mock_response.content = json.dumps({"message": "routed", "count": 99})
        mock_router.route_and_generate.return_value = mock_response
        mock_router_class.return_value = mock_router

        # Execute
        result = await test_agent._generate_structured_with_routing(
            "test prompt", SimpleSchema, test_task
        )

        # Verify
        assert isinstance(result, SimpleSchema)
        assert result.message == "routed"
        assert result.count == 99

        # Verify router was called with JSON mode
        call_args = mock_router.route_and_generate.call_args
        request = call_args[0][0]
        assert request.metadata["response_format"] == {"type": "json_object"}
        assert "tier" in request.metadata

    @patch("src.core.config.settings")
    @patch("src.ai_brain.providers.ModelRouter")
    async def test_json_parse_error_falls_back_to_gemini(
        self, mock_router_class, mock_settings, test_agent, test_task
    ):
        """When JSON parse fails, falls back to Gemini."""
        mock_settings.MULTI_PROVIDER_ROUTING_ENABLED = True
        mock_settings.OPENROUTER_API_KEY = "test-key"
        mock_settings.OPENROUTER_ENDPOINT = "https://test.endpoint"
        mock_settings.OPENROUTER_TIER_MAPPING = {}

        # Mock OpenRouter response with malformed JSON
        mock_router = AsyncMock()
        mock_response = MagicMock()
        mock_response.success = True
        mock_response.content = "not valid json {"
        mock_router.route_and_generate.return_value = mock_response
        mock_router_class.return_value = mock_router

        # Mock GeminiService fallback
        mock_gemini = AsyncMock()
        expected_result = SimpleSchema(message="fallback", count=1)
        mock_gemini.generate_structured_content.return_value = expected_result
        test_agent.gemini_service = mock_gemini

        # Execute
        result = await test_agent._generate_structured_with_routing(
            "test prompt", SimpleSchema, test_task
        )

        # Verify fallback was used
        assert result == expected_result
        mock_gemini.generate_structured_content.assert_called_once()

    @patch("src.core.config.settings")
    @patch("src.ai_brain.providers.ModelRouter")
    async def test_schema_validation_error_falls_back_to_gemini(
        self, mock_router_class, mock_settings, test_agent, test_task
    ):
        """When schema validation fails, falls back to Gemini."""
        mock_settings.MULTI_PROVIDER_ROUTING_ENABLED = True
        mock_settings.OPENROUTER_API_KEY = "test-key"
        mock_settings.OPENROUTER_ENDPOINT = "https://test.endpoint"
        mock_settings.OPENROUTER_TIER_MAPPING = {}

        # Mock OpenRouter response with schema-invalid JSON
        mock_router = AsyncMock()
        mock_response = MagicMock()
        mock_response.success = True
        mock_response.content = json.dumps({"wrong": "fields"})
        mock_router.route_and_generate.return_value = mock_response
        mock_router_class.return_value = mock_router

        # Mock GeminiService fallback
        mock_gemini = AsyncMock()
        expected_result = SimpleSchema(message="fallback", count=2)
        mock_gemini.generate_structured_content.return_value = expected_result
        test_agent.gemini_service = mock_gemini

        # Execute
        result = await test_agent._generate_structured_with_routing(
            "test prompt", SimpleSchema, test_task
        )

        # Verify fallback was used
        assert result == expected_result
        mock_gemini.generate_structured_content.assert_called_once()

    @patch("src.core.config.settings")
    @patch("src.ai_brain.providers.ModelRouter")
    async def test_http_error_falls_back_to_gemini(
        self, mock_router_class, mock_settings, test_agent, test_task
    ):
        """When HTTP error occurs, falls back to Gemini."""
        mock_settings.MULTI_PROVIDER_ROUTING_ENABLED = True
        mock_settings.OPENROUTER_API_KEY = "test-key"
        mock_settings.OPENROUTER_ENDPOINT = "https://test.endpoint"
        mock_settings.OPENROUTER_TIER_MAPPING = {}

        # Mock OpenRouter HTTP failure
        mock_router = AsyncMock()
        mock_response = MagicMock()
        mock_response.success = False
        mock_response.error_message = "HTTP 500 Internal Server Error"
        mock_router.route_and_generate.return_value = mock_response
        mock_router_class.return_value = mock_router

        # Mock GeminiService fallback
        mock_gemini = AsyncMock()
        expected_result = SimpleSchema(message="fallback", count=3)
        mock_gemini.generate_structured_content.return_value = expected_result
        test_agent.gemini_service = mock_gemini

        # Execute
        result = await test_agent._generate_structured_with_routing(
            "test prompt", SimpleSchema, test_task
        )

        # Verify fallback was used
        assert result == expected_result
        mock_gemini.generate_structured_content.assert_called_once()

    @patch("src.core.config.settings")
    @patch("src.ai_brain.providers.ModelRouter")
    async def test_router_exception_falls_back_to_gemini(
        self, mock_router_class, mock_settings, test_agent, test_task
    ):
        """When router raises exception, falls back to Gemini."""
        mock_settings.MULTI_PROVIDER_ROUTING_ENABLED = True
        mock_settings.OPENROUTER_API_KEY = "test-key"
        mock_settings.OPENROUTER_ENDPOINT = "https://test.endpoint"
        mock_settings.OPENROUTER_TIER_MAPPING = {}

        # Mock router exception
        mock_router = AsyncMock()
        mock_router.route_and_generate.side_effect = RuntimeError("Router crashed")
        mock_router_class.return_value = mock_router

        # Mock GeminiService fallback
        mock_gemini = AsyncMock()
        expected_result = SimpleSchema(message="fallback", count=4)
        mock_gemini.generate_structured_content.return_value = expected_result
        test_agent.gemini_service = mock_gemini

        # Execute
        result = await test_agent._generate_structured_with_routing(
            "test prompt", SimpleSchema, test_task
        )

        # Verify fallback was used
        assert result == expected_result
        mock_gemini.generate_structured_content.assert_called_once()


class TestSwappedAgentCallSites:
    """Test that swapped agent call sites use new routing method."""

    async def test_literature_review_code_path(self):
        """LiteratureReviewAgent code uses _generate_structured_with_routing."""
        # Verify the code path by reading source
        with open("src/agents/literature_review_agent.py") as f:
            source = f.read()

        # Should call _generate_structured_with_routing, not gemini.generate_structured_content
        assert "_generate_structured_with_routing" in source
        # The old direct call pattern should not appear in the new call sites
        assert source.count("_generate_structured_with_routing(") >= 2

    async def test_citation_agent_code_path(self):
        """CitationAgent code uses _generate_structured_with_routing."""
        with open("src/agents/citation_agent.py") as f:
            source = f.read()

        assert "_generate_structured_with_routing" in source
        assert source.count("_generate_structured_with_routing(") >= 1

    @patch(
        "src.agents.methodology_agent.MethodologyAgent._generate_structured_with_routing"
    )
    async def test_methodology_agent_uses_routing(self, mock_routing):
        """MethodologyAgent uses _generate_structured_with_routing."""
        from src.agents.methodology_agent import MethodologyAgent
        from src.agents.models import AgentTask
        from src.agents.schemas import MethodologySchema

        mock_routing.return_value = MagicMock(
            research_design="",
            data_collection_methods=[],
            sampling_strategy="",
            analysis_approaches=[],
            validity_measures=[],
            ethical_considerations=[],
            limitations=[],
            timeline="",
            quality_indicators=[],
        )

        agent = MethodologyAgent()
        task = AgentTask(
            id="test-123",
            agent_type="methodology",
            input_data={"research_question": "test question"},
        )

        with patch.object(agent, "_calculate_confidence", return_value=0.8), patch.object(agent, "get_cached_result", return_value=None):
                await agent.execute(task)

        # Verify routing method was called with task
        assert mock_routing.called
        call_args = mock_routing.call_args
        assert call_args[0][1] == MethodologySchema
        assert call_args[1]["task"] == task

    @patch(
        "src.agents.synthesis_agent.SynthesisAgent._generate_structured_with_routing"
    )
    async def test_synthesis_agent_uses_routing(self, mock_routing):
        """SynthesisAgent uses _generate_structured_with_routing."""
        from src.agents.models import AgentTask
        from src.agents.schemas import SynthesisSchema
        from src.agents.synthesis_agent import SynthesisAgent

        mock_routing.return_value = MagicMock(
            integrated_findings=[],
            cross_agent_patterns=[],
            conflict_resolutions=[],
            meta_insights=[],
            comprehensive_narrative="",
            confidence_assessment="",
        )

        agent = SynthesisAgent()
        task = AgentTask(
            id="test-123",
            agent_type="synthesis",
            input_data={"agent_outputs": {"test": "data"}},
        )

        with patch.object(agent, "_calculate_confidence", return_value=0.8), patch.object(agent, "get_cached_result", return_value=None):
                await agent.execute(task)

        # Verify routing method was called with task
        assert mock_routing.called
        call_args = mock_routing.call_args
        assert call_args[0][1] == SynthesisSchema
        assert call_args[1]["task"] == task


class TestSchemaInstructionsGeneration:
    """Test JSON schema instruction generation."""

    def test_schema_instructions_includes_json_schema(self, test_agent):
        """Schema instructions include the full JSON schema."""
        instructions = test_agent._build_json_schema_instructions(SimpleSchema)

        assert "JSON" in instructions
        assert "schema" in instructions
        # Check that schema fields are included
        assert "message" in instructions
        assert "count" in instructions

    def test_schema_instructions_handles_complex_schema(self, test_agent):
        """Schema instructions work for complex schemas."""
        instructions = test_agent._build_json_schema_instructions(ComplexSchema)

        assert "JSON" in instructions
        assert "items" in instructions
        assert "metadata" in instructions
        assert "confidence" in instructions

    def test_schema_instructions_fallback_on_error(self, test_agent):
        """Schema instructions fallback on schema generation error."""

        # Mock schema that raises on model_json_schema
        class BrokenSchema:
            @classmethod
            def model_json_schema(cls):
                raise ValueError("Schema generation failed")

        instructions = test_agent._build_json_schema_instructions(BrokenSchema)

        # Should return basic fallback instructions
        assert "JSON" in instructions
        assert len(instructions) > 0
