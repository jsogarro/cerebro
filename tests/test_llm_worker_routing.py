"""Tests for LLMWorkerAgentBase multi-provider routing integration."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.llm_worker_base import LLMWorkerAgentBase
from src.agents.models import AgentTask


class TestWorkerAgent(LLMWorkerAgentBase):
    """Concrete test implementation of LLMWorkerAgentBase."""

    agent_type = "test_worker"

    def _build_prompt(self, query: str, task: AgentTask) -> str:
        return f"Test prompt for: {query}"


@pytest.fixture
def test_agent():
    """Create test worker agent instance."""
    return TestWorkerAgent()


@pytest.fixture
def test_task():
    """Create test AgentTask."""
    return AgentTask(
        id="test-task-001",
        agent_type="test_worker",
        input_data={"query": "What is AI?", "complexity_score": 0.5},
    )


@pytest.fixture
def mock_gemini_service():
    """Mock GeminiService for fallback testing."""
    mock_service = MagicMock()
    mock_service.generate_content = AsyncMock(
        return_value="Response from GeminiService"
    )
    return mock_service


@pytest.fixture
def mock_model_response():
    """Mock ModelResponse from OpenRouter."""
    from src.ai_brain.providers.base_provider import ModelResponse

    return ModelResponse(
        request_id="test-req-001",
        content="Response from OpenRouter",
        model_name="anthropic/claude-sonnet-4.6",
        provider="openrouter",
        completion_tokens=10,
        prompt_tokens=5,
        total_tokens=15,
        latency_ms=250,
        processing_time_ms=250,
        confidence_score=0.90,
        success=True,
        cost_estimate=0.00015,
        finish_reason="stop",
    )


class TestDefaultBehavior:
    """Test default behavior when multi-provider routing is OFF."""

    @pytest.mark.asyncio
    async def test_flag_off_uses_gemini(
        self, test_agent, test_task, mock_gemini_service
    ):
        """With flag OFF, should route through GeminiService (current behavior)."""
        with patch("src.core.config.settings") as mock_settings:
            mock_settings.MULTI_PROVIDER_ROUTING_ENABLED = False
            mock_settings.GEMINI_API_KEY = "test-key"

            with patch(
                "src.services.gemini_service.GeminiService",
                return_value=mock_gemini_service,
            ):
                result = await test_agent.execute(test_task)

                assert result.status == "success"
                assert result.output["content"] == "Response from GeminiService"
                mock_gemini_service.generate_content.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_api_key_uses_gemini(
        self, test_agent, test_task, mock_gemini_service
    ):
        """Without OPENROUTER_API_KEY, should route through GeminiService."""
        with patch("src.core.config.settings") as mock_settings:
            mock_settings.MULTI_PROVIDER_ROUTING_ENABLED = True
            mock_settings.OPENROUTER_API_KEY = None  # No key
            mock_settings.GEMINI_API_KEY = "test-key"

            with patch(
                "src.services.gemini_service.GeminiService",
                return_value=mock_gemini_service,
            ):
                result = await test_agent.execute(test_task)

                assert result.status == "success"
                assert result.output["content"] == "Response from GeminiService"
                mock_gemini_service.generate_content.assert_called_once()


class TestOpenRouterRouting:
    """Test routing through OpenRouter when enabled."""

    @pytest.mark.asyncio
    async def test_flag_on_with_key_uses_openrouter(
        self, test_agent, test_task, mock_model_response
    ):
        """With flag ON + API key, should route through OpenRouter."""
        with patch("src.core.config.settings") as mock_settings:
            mock_settings.MULTI_PROVIDER_ROUTING_ENABLED = True
            mock_settings.OPENROUTER_API_KEY = "test-openrouter-key"
            mock_settings.OPENROUTER_ENDPOINT = (
                "https://openrouter.ai/api/v1/chat/completions"
            )
            mock_settings.OPENROUTER_TIER_MAPPING = {
                "simple": "deepseek/deepseek-chat",
                "balanced": "anthropic/claude-sonnet-4.6",
                "complex": "anthropic/claude-sonnet-4.6",
            }

            # Mock ModelRouter
            mock_router = MagicMock()
            mock_router.route_and_generate = AsyncMock(return_value=mock_model_response)

            with patch("src.ai_brain.providers.ModelRouter", return_value=mock_router):
                result = await test_agent.execute(test_task)

                assert result.status == "success"
                assert result.output["content"] == "Response from OpenRouter"
                assert result.confidence == 0.90
                mock_router.route_and_generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_tier_mapping_simple(self, test_agent, mock_model_response):
        """Simple complexity should map to simple tier (cost-efficient model)."""
        simple_task = AgentTask(
            id="simple-task",
            agent_type="test_worker",
            input_data={"query": "What is 2+2?", "complexity_score": 0.2},
        )

        with patch("src.core.config.settings") as mock_settings:
            mock_settings.MULTI_PROVIDER_ROUTING_ENABLED = True
            mock_settings.OPENROUTER_API_KEY = "test-key"
            mock_settings.OPENROUTER_ENDPOINT = (
                "https://openrouter.ai/api/v1/chat/completions"
            )
            mock_settings.OPENROUTER_TIER_MAPPING = {
                "simple": "deepseek/deepseek-chat",
                "balanced": "anthropic/claude-sonnet-4.6",
                "complex": "anthropic/claude-sonnet-4.6",
            }

            mock_router = MagicMock()
            mock_router.route_and_generate = AsyncMock(return_value=mock_model_response)

            with patch("src.ai_brain.providers.ModelRouter", return_value=mock_router):
                result = await test_agent.execute(simple_task)

                # Verify tier determination logic
                tier = test_agent._determine_tier(simple_task)
                assert tier == "simple"
                assert result.status == "success"

    @pytest.mark.asyncio
    async def test_tier_mapping_complex(self, test_agent, mock_model_response):
        """High complexity should map to complex tier (quality model)."""
        complex_task = AgentTask(
            id="complex-task",
            agent_type="test_worker",
            input_data={"query": "Analyze quantum computing", "complexity_score": 0.85},
        )

        with patch("src.core.config.settings") as mock_settings:
            mock_settings.MULTI_PROVIDER_ROUTING_ENABLED = True
            mock_settings.OPENROUTER_API_KEY = "test-key"
            mock_settings.OPENROUTER_ENDPOINT = (
                "https://openrouter.ai/api/v1/chat/completions"
            )
            mock_settings.OPENROUTER_TIER_MAPPING = {
                "simple": "deepseek/deepseek-chat",
                "balanced": "anthropic/claude-sonnet-4.6",
                "complex": "anthropic/claude-sonnet-4.6",
            }

            mock_router = MagicMock()
            mock_router.route_and_generate = AsyncMock(return_value=mock_model_response)

            with patch("src.ai_brain.providers.ModelRouter", return_value=mock_router):
                result = await test_agent.execute(complex_task)

                # Verify tier determination logic
                tier = test_agent._determine_tier(complex_task)
                assert tier == "complex"
                assert result.status == "success"


class TestGracefulFallback:
    """Test graceful fallback to GeminiService when OpenRouter fails."""

    @pytest.mark.asyncio
    async def test_openrouter_failure_falls_back_to_gemini(
        self, test_agent, test_task, mock_gemini_service
    ):
        """OpenRouter failure should fall back to GeminiService."""
        with patch("src.core.config.settings") as mock_settings:
            mock_settings.MULTI_PROVIDER_ROUTING_ENABLED = True
            mock_settings.OPENROUTER_API_KEY = "test-key"
            mock_settings.OPENROUTER_ENDPOINT = (
                "https://openrouter.ai/api/v1/chat/completions"
            )
            mock_settings.OPENROUTER_TIER_MAPPING = {
                "simple": "deepseek/deepseek-chat",
                "balanced": "anthropic/claude-sonnet-4.6",
                "complex": "anthropic/claude-sonnet-4.6",
            }
            mock_settings.GEMINI_API_KEY = "test-gemini-key"

            # Mock ModelRouter to raise exception
            mock_router = MagicMock()
            mock_router.route_and_generate = AsyncMock(
                side_effect=Exception("OpenRouter API error")
            )

            with (
                patch("src.ai_brain.providers.ModelRouter", return_value=mock_router),
                patch(
                    "src.services.gemini_service.GeminiService",
                    return_value=mock_gemini_service,
                ),
            ):
                result = await test_agent.execute(test_task)

                # Should succeed via fallback
                assert result.status == "success"
                assert result.output["content"] == "Response from GeminiService"
                mock_gemini_service.generate_content.assert_called_once()

    @pytest.mark.asyncio
    async def test_openrouter_error_response_falls_back(
        self, test_agent, test_task, mock_gemini_service
    ):
        """OpenRouter error response should trigger fallback."""
        from src.ai_brain.providers.base_provider import ModelResponse

        error_response = ModelResponse(
            request_id="test-req-001",
            content="",
            model_name="",
            provider="openrouter",
            success=False,
            error_message="Rate limit exceeded",
            error_type="rate_limit",
            latency_ms=100,
            confidence_score=0.0,
        )

        with patch("src.core.config.settings") as mock_settings:
            mock_settings.MULTI_PROVIDER_ROUTING_ENABLED = True
            mock_settings.OPENROUTER_API_KEY = "test-key"
            mock_settings.OPENROUTER_ENDPOINT = (
                "https://openrouter.ai/api/v1/chat/completions"
            )
            mock_settings.OPENROUTER_TIER_MAPPING = {
                "simple": "deepseek/deepseek-chat",
                "balanced": "anthropic/claude-sonnet-4.6",
                "complex": "anthropic/claude-sonnet-4.6",
            }
            mock_settings.GEMINI_API_KEY = "test-gemini-key"

            # Mock ModelRouter to return error response
            mock_router = MagicMock()
            mock_router.route_and_generate = AsyncMock(return_value=error_response)

            with (
                patch("src.ai_brain.providers.ModelRouter", return_value=mock_router),
                patch(
                    "src.services.gemini_service.GeminiService",
                    return_value=mock_gemini_service,
                ),
            ):
                result = await test_agent.execute(test_task)

                # Should succeed via fallback
                assert result.status == "success"
                assert result.output["content"] == "Response from GeminiService"


class TestTierDetermination:
    """Test tier determination logic."""

    def test_determine_tier_simple(self, test_agent):
        """Complexity < 0.3 should map to simple tier."""
        task = AgentTask(
            id="test",
            agent_type="test_worker",
            input_data={"query": "Test", "complexity_score": 0.15},
        )
        assert test_agent._determine_tier(task) == "simple"

    def test_determine_tier_balanced(self, test_agent):
        """0.3 <= Complexity < 0.7 should map to balanced tier."""
        task = AgentTask(
            id="test",
            agent_type="test_worker",
            input_data={"query": "Test", "complexity_score": 0.5},
        )
        assert test_agent._determine_tier(task) == "balanced"

    def test_determine_tier_complex(self, test_agent):
        """Complexity >= 0.7 should map to complex tier."""
        task = AgentTask(
            id="test",
            agent_type="test_worker",
            input_data={"query": "Test", "complexity_score": 0.85},
        )
        assert test_agent._determine_tier(task) == "complex"

    def test_determine_tier_default(self, test_agent):
        """Missing complexity_score should default to balanced."""
        task = AgentTask(
            id="test", agent_type="test_worker", input_data={"query": "Test"}
        )
        assert test_agent._determine_tier(task) == "balanced"


class TestRegressionGuard:
    """Regression guard: default behavior is byte-for-byte current."""

    @pytest.mark.asyncio
    async def test_default_config_uses_gemini_only(
        self, test_agent, test_task, mock_gemini_service
    ):
        """Default config (flag OFF, no key) should use only GeminiService."""
        with patch("src.core.config.settings") as mock_settings:
            # Default configuration
            mock_settings.MULTI_PROVIDER_ROUTING_ENABLED = False
            mock_settings.OPENROUTER_API_KEY = None
            mock_settings.GEMINI_API_KEY = "test-key"

            with patch(
                "src.services.gemini_service.GeminiService",
                return_value=mock_gemini_service,
            ):
                result = await test_agent.execute(test_task)

                # Verify ONLY GeminiService was called
                assert result.status == "success"
                assert result.output["content"] == "Response from GeminiService"
                mock_gemini_service.generate_content.assert_called_once()

                # Verify ModelRouter was NOT instantiated
                assert not hasattr(test_agent, "_model_router")
