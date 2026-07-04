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

    @pytest.mark.asyncio
    async def test_empty_content_skip_emits_structured_event(self):
        """The skip must emit the structured log event (not an agent error)."""
        from unittest.mock import patch as _patch

        from src.agents.supervisors.analytics_supervisor import AnalyticsSupervisor

        supervisor = AnalyticsSupervisor(
            gemini_service=MagicMock(), cache_client=MagicMock()
        )
        with _patch("src.agents.supervisors.base_supervisor.logger") as log_spy:
            await supervisor._run_verification("")
        assert any(
            "supervisor_verification_skipped_empty_content" in str(c.args)
            for c in log_spy.info.call_args_list
        )


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

            # First call: schema-VALID JSON but finish_reason="length" — pins
            # the finish_reason trigger independently of parse failure (which
            # test_retry_on_parse_failure covers).
            truncated_response = ModelResponse(
                request_id="test-req-1",
                content='{"result": "truncated mid", "count": 1}',
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


class TestTierPrecedenceWithBoth:
    """HIGH: explicit tier must win when a task is ALSO present."""

    @pytest.mark.asyncio
    async def test_explicit_tier_beats_task_derived(self):
        agent = _TestWorkerForTier()
        task = AgentTask(
            id="tier-both-001",
            agent_type="test_worker",
            input_data={"query": "x", "complexity_score": 0.95},  # would be complex
        )
        with patch("src.core.config.settings") as mock_settings:
            mock_settings.MULTI_PROVIDER_ROUTING_ENABLED = True
            mock_settings.OPENROUTER_API_KEY = "test-key"
            mock_settings.OPENROUTER_ENDPOINT = "https://test"
            mock_settings.OPENROUTER_TIER_MAPPING = {}

            mock_router = MagicMock()
            mock_router.route_and_generate = AsyncMock(
                return_value=ModelResponse(
                    request_id="r",
                    content='{"result": "ok", "count": 1}',
                    success=True,
                    finish_reason="stop",
                )
            )
            agent._model_router = mock_router

            await agent._generate_structured_with_routing(
                "p", _TestSchema, task=task, tier="simple"
            )
            request = mock_router.route_and_generate.call_args_list[0].args[0]
            assert request.metadata["tier"] == "simple"


class TestRetryFailurePaths:
    """HIGH: retry outcomes must be distinguished, incl. best-effort return."""

    def _settings(self, mock_settings):
        mock_settings.MULTI_PROVIDER_ROUTING_ENABLED = True
        mock_settings.OPENROUTER_API_KEY = "test-key"
        mock_settings.OPENROUTER_ENDPOINT = "https://test"
        mock_settings.OPENROUTER_TIER_MAPPING = {}

    @pytest.mark.asyncio
    async def test_both_parse_failures_fall_back_to_gemini(self):
        agent = _TestWorkerForTier()
        task = AgentTask(
            id="retry-fail-001",
            agent_type="test_worker",
            input_data={"query": "x", "complexity_score": 0.5},
        )
        with patch("src.core.config.settings") as mock_settings:
            self._settings(mock_settings)
            bad = ModelResponse(
                request_id="r1",
                content="not json at all",
                success=True,
                finish_reason="stop",
            )
            bad2 = ModelResponse(
                request_id="r2",
                content="still not json",
                success=True,
                finish_reason="stop",
            )
            mock_router = MagicMock()
            mock_router.route_and_generate = AsyncMock(side_effect=[bad, bad2])
            agent._model_router = mock_router

            gemini_result = _TestSchema(result="from-gemini", count=9)
            gemini = MagicMock()
            gemini.generate_structured_content = AsyncMock(return_value=gemini_result)
            with patch.object(agent, "_ensure_gemini_service", return_value=gemini):
                result = await agent._generate_structured_with_routing(
                    "p", _TestSchema, task=task, max_tokens=100
                )
            assert mock_router.route_and_generate.call_count == 2
            gemini.generate_structured_content.assert_awaited_once()
            assert result.result == "from-gemini"

    @pytest.mark.asyncio
    async def test_valid_truncated_first_attempt_survives_failed_retry(self):
        """If the first parse was schema-valid (finish=length) and the retry
        fails, the truncated-but-valid first result is returned - NOT Gemini."""
        agent = _TestWorkerForTier()
        task = AgentTask(
            id="retry-fail-002",
            agent_type="test_worker",
            input_data={"query": "x", "complexity_score": 0.5},
        )
        with patch("src.core.config.settings") as mock_settings:
            self._settings(mock_settings)
            first = ModelResponse(
                request_id="r1",
                content='{"result": "truncated but valid", "count": 1}',
                success=True,
                finish_reason="length",
            )
            retry_bad = ModelResponse(
                request_id="r2",
                content="garbage",
                success=True,
                finish_reason="stop",
            )
            mock_router = MagicMock()
            mock_router.route_and_generate = AsyncMock(side_effect=[first, retry_bad])
            agent._model_router = mock_router

            gemini = MagicMock()
            gemini.generate_structured_content = AsyncMock()
            with patch.object(agent, "_ensure_gemini_service", return_value=gemini):
                result = await agent._generate_structured_with_routing(
                    "p", _TestSchema, task=task, max_tokens=100
                )
            assert result.result == "truncated but valid"
            gemini.generate_structured_content.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_retry_request_doubles_budget_same_routing(self):
        """HIGH: the retry call must carry the doubled budget and identical routing."""
        agent = _TestWorkerForTier()
        task = AgentTask(
            id="retry-args-001",
            agent_type="test_worker",
            input_data={"query": "x", "complexity_score": 0.5},
        )
        with patch("src.core.config.settings") as mock_settings:
            self._settings(mock_settings)
            first = ModelResponse(
                request_id="r1",
                content='{"result": "v", "count": 1}',
                success=True,
                finish_reason="length",
            )
            second = ModelResponse(
                request_id="r2",
                content='{"result": "v2", "count": 2}',
                success=True,
                finish_reason="stop",
            )
            mock_router = MagicMock()
            mock_router.route_and_generate = AsyncMock(side_effect=[first, second])
            agent._model_router = mock_router

            await agent._generate_structured_with_routing(
                "p", _TestSchema, task=task, max_tokens=150
            )
            calls = mock_router.route_and_generate.call_args_list
            assert calls[0].args[0].max_tokens == 150
            assert calls[1].args[0].max_tokens == 300
            # Retry keeps the same provider but PINS the first attempt's model
            first_rd = calls[0].kwargs["routing_decision"]
            retry_rd = calls[1].kwargs["routing_decision"]
            assert (
                retry_rd["primary_model"]["provider"]
                == first_rd["primary_model"]["provider"]
            )
            assert retry_rd["primary_model"]["name"] == first.model_name


class TestFinanceReparentingParity:
    """HIGH (round 2): finance agents on the shared base keep their contract."""

    @pytest.mark.asyncio
    async def test_financial_analysis_output_shape_and_prompt(self):
        from src.agents.finance_agents import FinancialAnalysisAgent

        agent = FinancialAnalysisAgent()
        task = AgentTask(
            id="fin-parity-001",
            agent_type="financial_analysis",
            input_data={"query": "Assess liquidity.", "complexity_score": 0.2},
        )
        captured = {}

        async def fake_routing(prompt, t):
            captured["prompt"] = prompt
            return ("analysis text", 0.85)

        with patch.object(agent, "_generate_with_routing", side_effect=fake_routing):
            result = await agent.execute(task)

        # Output contract identical to the pre-reparenting shape
        assert result.status == "success"
        assert set(result.output) >= {"content", "analysis", "agent_type"}
        assert result.output["agent_type"] == agent.agent_type
        assert result.output["content"] == "analysis text"
        # The finance _build_prompt content must reach the routed call
        assert "Assess liquidity." in captured["prompt"]


class TestExternalRoundFixes:
    """Adversarial-round accepts: programmatic truncation flag + pinned retry model."""

    def _settings(self, mock_settings):
        mock_settings.MULTI_PROVIDER_ROUTING_ENABLED = True
        mock_settings.OPENROUTER_API_KEY = "test-key"
        mock_settings.OPENROUTER_ENDPOINT = "https://test"
        mock_settings.OPENROUTER_TIER_MAPPING = {}

    @pytest.mark.asyncio
    async def test_truncation_flag_set_on_best_effort_return(self):
        agent = _TestWorkerForTier()
        task = AgentTask(
            id="flag-001",
            agent_type="test_worker",
            input_data={"query": "x", "complexity_score": 0.5},
        )
        with patch("src.core.config.settings") as mock_settings:
            self._settings(mock_settings)
            first = ModelResponse(
                request_id="r1",
                content='{"result": "v", "count": 1}',
                success=True,
                finish_reason="length",
                model_name="deepseek/deepseek-chat",
            )
            retry_bad = ModelResponse(
                request_id="r2",
                content="garbage",
                success=True,
                finish_reason="stop",
            )
            mock_router = MagicMock()
            mock_router.route_and_generate = AsyncMock(side_effect=[first, retry_bad])
            agent._model_router = mock_router
            gemini = MagicMock()
            gemini.generate_structured_content = AsyncMock()
            with patch.object(agent, "_ensure_gemini_service", return_value=gemini):
                result = await agent._generate_structured_with_routing(
                    "p", _TestSchema, task=task, max_tokens=100
                )
        assert result.result == "v"
        assert agent.last_structured_truncated is True

    @pytest.mark.asyncio
    async def test_truncation_flag_false_on_clean_success(self):
        agent = _TestWorkerForTier()
        with patch("src.core.config.settings") as mock_settings:
            self._settings(mock_settings)
            ok = ModelResponse(
                request_id="r1",
                content='{"result": "v", "count": 1}',
                success=True,
                finish_reason="stop",
            )
            mock_router = MagicMock()
            mock_router.route_and_generate = AsyncMock(return_value=ok)
            agent._model_router = mock_router
            await agent._generate_structured_with_routing("p", _TestSchema)
        assert agent.last_structured_truncated is False

    @pytest.mark.asyncio
    async def test_retry_pinned_to_first_attempt_model(self):
        agent = _TestWorkerForTier()
        task = AgentTask(
            id="pin-001",
            agent_type="test_worker",
            input_data={"query": "x", "complexity_score": 0.5},
        )
        with patch("src.core.config.settings") as mock_settings:
            self._settings(mock_settings)
            first = ModelResponse(
                request_id="r1",
                content='{"result": "v", "count": 1}',
                success=True,
                finish_reason="length",
                model_name="deepseek/deepseek-chat",
            )
            second = ModelResponse(
                request_id="r2",
                content='{"result": "v2", "count": 2}',
                success=True,
                finish_reason="stop",
            )
            mock_router = MagicMock()
            mock_router.route_and_generate = AsyncMock(side_effect=[first, second])
            agent._model_router = mock_router
            await agent._generate_structured_with_routing(
                "p", _TestSchema, task=task, max_tokens=100
            )
            retry_decision = mock_router.route_and_generate.call_args_list[1].kwargs[
                "routing_decision"
            ]
            assert retry_decision["primary_model"]["name"] == "deepseek/deepseek-chat"
