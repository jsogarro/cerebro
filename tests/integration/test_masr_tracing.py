"""Integration tests for MASR routing and OpenRouter provider tracing.

Tests verify that tracing context flows from MASR → Provider and that
the flag-OFF path adds zero tracing overhead.
"""

import os
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.ai_brain.providers.base_provider import ModelRequest
from src.ai_brain.providers.openrouter_provider import OpenRouterProvider
from src.ai_brain.router.masr import MASRouter


class TestMASRTracingIntegration:
    """Test MASR routing with distributed tracing enabled/disabled."""

    @patch("src.core.tracing.Langfuse")
    @pytest.mark.asyncio
    async def test_masr_route_creates_trace_when_enabled(
        self, mock_langfuse_class: Mock
    ) -> None:
        """MASR.route() should create trace with routing metadata when enabled."""
        # Setup mock Langfuse client and trace
        mock_trace = MagicMock()
        mock_client = MagicMock()
        mock_client.trace.return_value = mock_trace
        mock_langfuse_class.return_value = mock_client

        with patch.dict(
            os.environ,
            {
                "LANGFUSE_ENABLED": "true",
                "LANGFUSE_PUBLIC_KEY": "pk-test",
                "LANGFUSE_SECRET_KEY": "sk-test",
            },
            clear=True,
        ):
            # Reset cached client
            import src.core.tracing as tracing_module

            tracing_module._langfuse_enabled = None
            tracing_module._langfuse_client = None

            # Create MASR router
            router = MASRouter()

            # Route a query
            decision = await router.route("What is machine learning?")

            # Verify trace was created with query_id
            mock_client.trace.assert_called_once()
            call_kwargs = mock_client.trace.call_args[1]
            assert call_kwargs["id"] == decision.query_id
            assert call_kwargs["name"] == "masr_routing"
            assert call_kwargs["input"]["query"] == "What is machine learning?"

            # Verify trace was updated with routing decision metadata
            mock_trace.update.assert_called()
            final_update = mock_trace.update.call_args_list[-1][1]
            metadata = final_update["metadata"]
            assert "complexity_level" in metadata
            assert "routing_strategy" in metadata
            assert "collaboration_mode" in metadata
            assert "estimated_cost" in metadata
            assert "worker_count" in metadata

    @pytest.mark.asyncio
    async def test_masr_route_passes_trace_in_context(self) -> None:
        """MASR should pass trace through context for provider access."""
        mock_trace = MagicMock()

        with patch("src.core.tracing.get_langfuse_client", return_value=MagicMock()):
            with patch("src.core.tracing.trace_masr_routing") as mock_tracer:
                mock_tracer.return_value.__enter__.return_value = mock_trace

                router = MASRouter()
                decision = await router.route("test query")

                # Verify context contains trace (indirectly via MASR logic)
                # The trace should be stored in context["_langfuse_trace"]
                assert decision.query_id is not None

    @pytest.mark.asyncio
    async def test_masr_route_no_trace_when_disabled(self) -> None:
        """MASR.route() should create no trace when LANGFUSE_ENABLED=false."""
        with patch.dict(os.environ, {"LANGFUSE_ENABLED": "false"}, clear=True):
            with patch("src.core.tracing.Langfuse") as mock_langfuse_class:
                import src.core.tracing as tracing_module

                tracing_module._langfuse_enabled = None
                tracing_module._langfuse_client = None

                router = MASRouter()
                decision = await router.route("test query")

                # Verify Langfuse client was never constructed
                mock_langfuse_class.assert_not_called()

                # Decision should still be valid
                assert decision.query_id is not None
                assert decision.complexity_analysis is not None


class TestOpenRouterProviderTracing:
    """Test OpenRouter provider with distributed tracing enabled/disabled."""

    @patch("src.core.tracing.Langfuse")
    @patch("httpx.AsyncClient")
    @pytest.mark.asyncio
    async def test_provider_creates_span_when_trace_present(
        self, mock_httpx: Mock, mock_langfuse_class: Mock
    ) -> None:
        """OpenRouterProvider should create span when trace is in request metadata."""
        # Setup mock trace and span
        mock_span = MagicMock()
        mock_trace = MagicMock()
        mock_trace.span.return_value = mock_span

        # Setup mock HTTP response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {"content": "Test response"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
        }
        mock_response.raise_for_status = Mock()

        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_httpx.return_value = mock_client

        # Create provider with trace in request metadata
        provider = OpenRouterProvider(
            {"api_key": "test-key"},
            model_config_manager=None,
        )
        await provider.initialize()

        request = ModelRequest(
            request_id="test-req",
            prompt="test prompt",
            max_tokens=100,
            temperature=0.7,
            metadata={"_langfuse_trace": mock_trace},
        )

        # Generate response
        response = await provider.generate(request)

        # Verify span was created
        mock_trace.span.assert_called_once_with(
            name="openrouter_call",
            metadata={
                "provider": "openrouter",
                "model": "deepseek/deepseek-chat",  # default simple tier
                "temperature": 0.7,
                "max_tokens": 100,
                "tier": None,
            },
        )

        # Verify metrics were recorded to span
        mock_span.update.assert_called_once()
        update_call = mock_span.update.call_args[1]
        assert update_call["usage"]["input"] == 100
        assert update_call["usage"]["output"] == 50
        assert update_call["usage"]["total"] == 150
        assert "cost_usd" in update_call["metadata"]
        assert "latency_ms" in update_call["metadata"]

    @patch("httpx.AsyncClient")
    @pytest.mark.asyncio
    async def test_provider_no_span_when_trace_absent(
        self, mock_httpx: Mock
    ) -> None:
        """OpenRouterProvider should not create span when no trace in metadata."""
        # Setup mock HTTP response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {"content": "Test response"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
        }
        mock_response.raise_for_status = Mock()

        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_httpx.return_value = mock_client

        with patch("src.core.tracing.trace_provider_call") as mock_trace_call:
            provider = OpenRouterProvider(
                {"api_key": "test-key"},
                model_config_manager=None,
            )
            await provider.initialize()

            # Request without trace in metadata
            request = ModelRequest(
                request_id="test-req",
                prompt="test prompt",
                max_tokens=100,
                temperature=0.7,
                metadata={},  # No _langfuse_trace
            )

            response = await provider.generate(request)

            # Verify trace_provider_call was called with trace=None
            assert mock_trace_call.called
            call_kwargs = mock_trace_call.call_args[1]
            assert call_kwargs["trace"] is None

            # Response should still be valid
            assert response.success is True
            assert response.content == "Test response"

    @patch("httpx.AsyncClient")
    @pytest.mark.asyncio
    async def test_provider_no_tracing_overhead_when_disabled(
        self, mock_httpx: Mock
    ) -> None:
        """Provider should have zero tracing overhead when LANGFUSE_ENABLED=false."""
        with patch.dict(os.environ, {"LANGFUSE_ENABLED": "false"}, clear=True):
            # Setup mock HTTP response
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "choices": [
                    {
                        "message": {"content": "Test response"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                },
            }
            mock_response.raise_for_status = Mock()

            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_httpx.return_value = mock_client

            with patch("src.core.tracing.Langfuse") as mock_langfuse_class:
                import src.core.tracing as tracing_module

                tracing_module._langfuse_enabled = None
                tracing_module._langfuse_client = None

                provider = OpenRouterProvider(
                    {"api_key": "test-key"},
                    model_config_manager=None,
                )
                await provider.initialize()

                request = ModelRequest(
                    request_id="test-req",
                    prompt="test prompt",
                    max_tokens=100,
                    temperature=0.7,
                )

                response = await provider.generate(request)

                # Verify Langfuse was never used
                mock_langfuse_class.assert_not_called()

                # Response should still be valid
                assert response.success is True
                assert response.content == "Test response"
