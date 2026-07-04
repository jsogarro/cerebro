"""Tests for agent robustness quick-wins (Issue #...).

Covers four items:
1. Tier-aware task threading for structured calls
2. VerificationAgent empty-content skip
3. ComparativeAnalysisAgent graceful degradation for <2 items
4. Truncation-aware structured retry
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, Field

from src.agents.comparative_analysis_agent import ComparativeAnalysisAgent
from src.agents.llm_worker_base import LLMWorkerAgentBase
from src.agents.models import AgentTask
from src.ai_brain.providers.base_provider import ModelResponse


class _TestSchema(BaseModel):
    """Simple test schema for structured generation (prefixed with _ to avoid pytest collection)."""

    result: str = Field(description="Test result")
    count: int = Field(description="Test count")


class _TestWorkerForTier(LLMWorkerAgentBase):
    """Concrete test worker for tier override testing (prefixed with _ to avoid pytest collection)."""

    agent_type = "test_worker"

    def _build_prompt(self, query: str, task: AgentTask) -> str:
        return f"Test: {query}"


class TestTierOverridePrecedence:
    """Test tier override precedence: explicit > task-derived > balanced."""

    @pytest.mark.asyncio
    async def test_explicit_tier_overrides_task(self):
        """Explicit tier= parameter should override task-derived tier."""
        agent = _TestWorkerForTier()

        # Task with high complexity (would derive "complex" tier)
        task = AgentTask(
            id="test-001",
            agent_type="test_worker",
            input_data={"query": "Test", "complexity_score": 0.9},
        )

        with patch("src.core.config.settings") as mock_settings:
            mock_settings.MULTI_PROVIDER_ROUTING_ENABLED = True
            mock_settings.OPENROUTER_API_KEY = "test-key"
            mock_settings.OPENROUTER_ENDPOINT = "https://test"
            mock_settings.OPENROUTER_TIER_MAPPING = {}

            mock_router = MagicMock()
            mock_response = ModelResponse(
                request_id="test-req",
                content='{"result": "ok", "count": 1}',
                success=True,
                finish_reason="stop",
            )
            mock_router.route_and_generate = AsyncMock(return_value=mock_response)

            agent._model_router = mock_router

            # Call with explicit tier="simple" (should override task's 0.9 complexity)
            result = await agent._generate_structured_with_routing(
                "Test prompt", _TestSchema, task=task, tier="simple"
            )

            # Verify the router was called with tier="simple", not "complex"
            call_args = mock_router.route_and_generate.call_args
            assert call_args is not None
            request = call_args[0][0]  # First positional arg is ModelRequest
            assert request.metadata["tier"] == "simple"
            assert result.result == "ok"

    @pytest.mark.asyncio
    async def test_task_derived_tier_when_no_explicit(self):
        """Task-derived tier should be used when no explicit tier provided."""
        agent = _TestWorkerForTier()

        # Low complexity task -> "simple" tier
        task = AgentTask(
            id="test-002",
            agent_type="test_worker",
            input_data={"query": "Test", "complexity_score": 0.2},
        )

        with patch("src.core.config.settings") as mock_settings:
            mock_settings.MULTI_PROVIDER_ROUTING_ENABLED = True
            mock_settings.OPENROUTER_API_KEY = "test-key"
            mock_settings.OPENROUTER_ENDPOINT = "https://test"
            mock_settings.OPENROUTER_TIER_MAPPING = {}

            mock_router = MagicMock()
            mock_response = ModelResponse(
                request_id="test-req",
                content='{"result": "ok", "count": 2}',
                success=True,
                finish_reason="stop",
            )
            mock_router.route_and_generate = AsyncMock(return_value=mock_response)
            agent._model_router = mock_router

            result = await agent._generate_structured_with_routing(
                "Test prompt", _TestSchema, task=task
            )

            call_args = mock_router.route_and_generate.call_args
            request = call_args[0][0]
            assert request.metadata["tier"] == "simple"  # Derived from complexity 0.2
            assert result.count == 2

    @pytest.mark.asyncio
    async def test_balanced_default_when_no_task_no_tier(self):
        """Should default to 'balanced' when neither task nor tier provided."""
        agent = _TestWorkerForTier()

        with patch("src.core.config.settings") as mock_settings:
            mock_settings.MULTI_PROVIDER_ROUTING_ENABLED = True
            mock_settings.OPENROUTER_API_KEY = "test-key"
            mock_settings.OPENROUTER_ENDPOINT = "https://test"
            mock_settings.OPENROUTER_TIER_MAPPING = {}

            mock_router = MagicMock()
            mock_response = ModelResponse(
                request_id="test-req",
                content='{"result": "balanced", "count": 3}',
                success=True,
                finish_reason="stop",
            )
            mock_router.route_and_generate = AsyncMock(return_value=mock_response)
            agent._model_router = mock_router

            result = await agent._generate_structured_with_routing(
                "Test prompt", _TestSchema, task=None, tier=None
            )

            call_args = mock_router.route_and_generate.call_args
            request = call_args[0][0]
            assert request.metadata["tier"] == "balanced"
            assert result.result == "balanced"


class TestVerificationEmptyContentSkip:
    """Test that empty content is gracefully skipped before creating verification task."""

    @pytest.mark.asyncio
    async def test_empty_content_skip_structured_log(self):
        """Empty content should log and skip verification, not error."""
        from src.agents.supervisors.analytics_supervisor import AnalyticsSupervisor

        supervisor = AnalyticsSupervisor(
            gemini_service=MagicMock(), cache_client=MagicMock()
        )

        # Test with empty string
        result = await supervisor._run_verification("")
        assert result["verdict"] == "pass"
        assert "No content to verify" in result["report"]
        assert result["issues"] == []

        # Test with whitespace-only string
        result2 = await supervisor._run_verification("   \n  ")
        assert result2["verdict"] == "pass"
        assert "No content to verify" in result2["report"]


class TestComparativeAnalysisGracefulDegradation:
    """Test that comparative analysis degrades gracefully for <2 items."""

    @pytest.mark.asyncio
    async def test_zero_items_structured_success(self):
        """Zero items should return structured success, not error."""
        agent = ComparativeAnalysisAgent(
            gemini_service=MagicMock(), cache_client=MagicMock()
        )

        task = AgentTask(
            id="test-comp-001",
            agent_type="comparative_analysis",
            input_data={"items": [], "criteria": ["accuracy", "speed"]},
        )

        result = await agent.execute(task)

        assert result.status == "success"  # Structured success, not error
        assert result.confidence == 0.0
        assert result.metadata.get("insufficient_items") is True
        assert result.metadata.get("item_count") == 0
        assert (
            "Cannot perform comparative analysis with zero items"
            in result.output["synthesis"]
        )

    @pytest.mark.asyncio
    async def test_one_item_structured_success(self):
        """One item should return single-item analysis, not error."""
        agent = ComparativeAnalysisAgent(
            gemini_service=MagicMock(), cache_client=MagicMock()
        )

        task = AgentTask(
            id="test-comp-002",
            agent_type="comparative_analysis",
            input_data={
                "items": [{"name": "AlphaModel", "accuracy": 0.95}],
                "criteria": ["accuracy"],
            },
        )

        result = await agent.execute(task)

        assert result.status == "success"
        assert result.confidence == 0.5
        assert result.metadata.get("insufficient_items") is True
        assert result.metadata.get("item_count") == 1
        assert "AlphaModel" in result.output["synthesis"]
        assert "standalone analysis" in result.output["synthesis"]

    # NOTE: test_two_items_normal_path removed - the <2 items check happens early
    # in execute(), so 2 items will proceed to normal analysis path.
    # The 0-item and 1-item tests above adequately verify graceful degradation.


class TestTruncationAwareRetry:
    """Test truncation-aware retry for structured generation."""

    @pytest.mark.asyncio
    async def test_retry_on_finish_reason_length(self):
        """Should retry with doubled max_tokens when finish_reason=length."""
        agent = _TestWorkerForTier()

        task = AgentTask(
            id="test-retry-001",
            agent_type="test_worker",
            input_data={"query": "Test", "complexity_score": 0.5},
        )

        with patch("src.core.config.settings") as mock_settings:
            mock_settings.MULTI_PROVIDER_ROUTING_ENABLED = True
            mock_settings.OPENROUTER_API_KEY = "test-key"
            mock_settings.OPENROUTER_ENDPOINT = "https://test"
            mock_settings.OPENROUTER_TIER_MAPPING = {}

            mock_router = MagicMock()

            # First call: truncated (finish_reason="length")
            truncated_response = ModelResponse(
                request_id="test-req-1",
                content='{"result": "truncat',  # Incomplete JSON
                success=True,
                finish_reason="length",  # KEY: truncation signal
            )

            # Second call (retry): success
            retry_response = ModelResponse(
                request_id="test-req-2",
                content='{"result": "complete", "count": 5}',
                success=True,
                finish_reason="stop",
            )

            mock_router.route_and_generate = AsyncMock(
                side_effect=[truncated_response, retry_response]
            )
            agent._model_router = mock_router

            result = await agent._generate_structured_with_routing(
                "Test prompt", _TestSchema, task=task, max_tokens=100
            )

            # Should have made TWO calls: initial + retry
            assert mock_router.route_and_generate.call_count == 2

            # Second call should have doubled max_tokens
            second_call_args = mock_router.route_and_generate.call_args_list[1]
            retry_request = second_call_args[0][0]
            assert retry_request.max_tokens == 200  # 100 * 2

            # Final result should be the retry success
            assert result.result == "complete"
            assert result.count == 5

    @pytest.mark.asyncio
    async def test_retry_on_parse_failure(self):
        """Should retry when initial parse/validation fails."""
        agent = _TestWorkerForTier()

        task = AgentTask(
            id="test-retry-002",
            agent_type="test_worker",
            input_data={"query": "Test", "complexity_score": 0.5},
        )

        with patch("src.core.config.settings") as mock_settings:
            mock_settings.MULTI_PROVIDER_ROUTING_ENABLED = True
            mock_settings.OPENROUTER_API_KEY = "test-key"
            mock_settings.OPENROUTER_ENDPOINT = "https://test"
            mock_settings.OPENROUTER_TIER_MAPPING = {}

            mock_router = MagicMock()

            # First call: unparseable JSON
            bad_json_response = ModelResponse(
                request_id="test-req-1",
                content='{"result": "bad", count: INVALID}',  # Invalid JSON
                success=True,
                finish_reason="stop",
            )

            # Second call: valid
            good_response = ModelResponse(
                request_id="test-req-2",
                content='{"result": "fixed", "count": 10}',
                success=True,
                finish_reason="stop",
            )

            mock_router.route_and_generate = AsyncMock(
                side_effect=[bad_json_response, good_response]
            )
            agent._model_router = mock_router

            result = await agent._generate_structured_with_routing(
                "Test prompt", _TestSchema, task=task, max_tokens=500
            )

            # Should retry after parse failure
            assert mock_router.route_and_generate.call_count == 2

            # Retry should double tokens
            retry_request = mock_router.route_and_generate.call_args_list[1][0][0]
            assert retry_request.max_tokens == 1000  # 500 * 2

            assert result.result == "fixed"

    @pytest.mark.asyncio
    async def test_retry_capped_at_8000(self):
        """Retry max_tokens should be capped at 8000."""
        agent = _TestWorkerForTier()

        with patch("src.core.config.settings") as mock_settings:
            mock_settings.MULTI_PROVIDER_ROUTING_ENABLED = True
            mock_settings.OPENROUTER_API_KEY = "test-key"
            mock_settings.OPENROUTER_ENDPOINT = "https://test"
            mock_settings.OPENROUTER_TIER_MAPPING = {}

            mock_router = MagicMock()

            # Truncated response
            truncated = ModelResponse(
                request_id="test-req-1",
                content='{"result": "truncat',
                success=True,
                finish_reason="length",
            )

            # Retry success
            retry_ok = ModelResponse(
                request_id="test-req-2",
                content='{"result": "ok", "count": 99}',
                success=True,
                finish_reason="stop",
            )

            mock_router.route_and_generate = AsyncMock(
                side_effect=[truncated, retry_ok]
            )
            agent._model_router = mock_router

            # Start with 6000 tokens -> retry should be capped at 8000, not 12000
            _ = await agent._generate_structured_with_routing(
                "Test prompt", _TestSchema, task=None, max_tokens=6000
            )

            retry_request = mock_router.route_and_generate.call_args_list[1][0][0]
            assert retry_request.max_tokens == 8000  # Capped, not 12000

    @pytest.mark.asyncio
    async def test_no_retry_when_already_at_cap(self):
        """Should NOT retry when original max_tokens >= 8000."""
        agent = _TestWorkerForTier()

        with patch("src.core.config.settings") as mock_settings:
            mock_settings.MULTI_PROVIDER_ROUTING_ENABLED = True
            mock_settings.OPENROUTER_API_KEY = "test-key"
            mock_settings.OPENROUTER_ENDPOINT = "https://test"
            mock_settings.OPENROUTER_TIER_MAPPING = {}

            mock_gemini = MagicMock()
            mock_gemini.generate_structured_content = AsyncMock(
                return_value=_TestSchema(result="gemini_fallback", count=0)
            )

            mock_router = MagicMock()
            truncated = ModelResponse(
                request_id="test-req-1",
                content='{"result": "truncat',
                success=True,
                finish_reason="length",
            )
            mock_router.route_and_generate = AsyncMock(return_value=truncated)

            agent._model_router = mock_router
            agent.gemini_service = mock_gemini

            result = await agent._generate_structured_with_routing(
                "Test prompt", _TestSchema, task=None, max_tokens=8000
            )

            # Should only call OpenRouter ONCE (no retry because already at cap)
            assert mock_router.route_and_generate.call_count == 1

            # Should fall back to Gemini
            assert result.result == "gemini_fallback"
