"""Unit tests for Langfuse distributed tracing.

Tests cover:
1. Flag OFF: no-op safety (zero calls, no import errors, immediate return)
2. Flag ON: tracer constructed, spans created with correct payload
3. SDK not installed: graceful fallback with warning
4. Initialization errors: logged and handled
"""

import os
from unittest.mock import MagicMock, Mock, patch

import pytest


class TestTracingFlagOff:
    """Test tracing behavior when LANGFUSE_ENABLED is false or unset."""

    def test_is_langfuse_enabled_returns_false_by_default(self) -> None:
        """LANGFUSE_ENABLED unset should return False."""
        with patch.dict(os.environ, {}, clear=True):
            from src.core.tracing import _is_langfuse_enabled

            # Clear cached state
            import src.core.tracing as tracing_module

            tracing_module._langfuse_enabled = None

            assert _is_langfuse_enabled() is False

    def test_is_langfuse_enabled_returns_false_when_0(self) -> None:
        """LANGFUSE_ENABLED=0 should return False."""
        with patch.dict(os.environ, {"LANGFUSE_ENABLED": "0"}, clear=True):
            from src.core.tracing import _is_langfuse_enabled

            import src.core.tracing as tracing_module

            tracing_module._langfuse_enabled = None

            assert _is_langfuse_enabled() is False

    def test_is_langfuse_enabled_returns_true_when_1(self) -> None:
        """LANGFUSE_ENABLED=1 should return True."""
        with patch.dict(os.environ, {"LANGFUSE_ENABLED": "1"}, clear=True):
            from src.core.tracing import _is_langfuse_enabled

            import src.core.tracing as tracing_module

            tracing_module._langfuse_enabled = None

            assert _is_langfuse_enabled() is True

    def test_get_langfuse_client_returns_none_when_disabled(self) -> None:
        """get_langfuse_client should return None when flag is off."""
        with patch.dict(os.environ, {"LANGFUSE_ENABLED": "false"}, clear=True):
            from src.core.tracing import get_langfuse_client

            import src.core.tracing as tracing_module

            tracing_module._langfuse_enabled = None
            tracing_module._langfuse_client = None

            client = get_langfuse_client()
            assert client is None

    def test_trace_masr_routing_yields_none_when_disabled(self) -> None:
        """trace_masr_routing should yield None when flag is off."""
        with patch.dict(os.environ, {"LANGFUSE_ENABLED": "false"}, clear=True):
            from src.core.tracing import trace_masr_routing

            import src.core.tracing as tracing_module

            tracing_module._langfuse_enabled = None
            tracing_module._langfuse_client = None

            with trace_masr_routing(
                query_id="test-id", query="test query", metadata={}
            ) as trace:
                assert trace is None

    def test_trace_provider_call_yields_none_when_disabled(self) -> None:
        """trace_provider_call should yield None when flag is off."""
        with patch.dict(os.environ, {"LANGFUSE_ENABLED": "false"}, clear=True):
            from src.core.tracing import trace_provider_call

            import src.core.tracing as tracing_module

            tracing_module._langfuse_enabled = None
            tracing_module._langfuse_client = None

            with trace_provider_call(
                trace=None, provider="test", model="test-model"
            ) as span:
                assert span is None

    def test_record_provider_metrics_no_op_when_span_is_none(self) -> None:
        """record_provider_metrics should no-op when span is None."""
        from src.core.tracing import record_provider_metrics

        # Should not raise any errors
        record_provider_metrics(
            span=None,
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=0.01,
            latency_ms=200,
        )


class TestTracingFlagOn:
    """Test tracing behavior when LANGFUSE_ENABLED is true."""

    def test_initialize_langfuse_creates_client(self) -> None:
        """_initialize_langfuse should create Langfuse client when enabled."""
        mock_client = MagicMock()
        mock_client.host = "https://cloud.langfuse.com"

        with patch.dict(
            os.environ,
            {
                "LANGFUSE_ENABLED": "true",
                "LANGFUSE_PUBLIC_KEY": "pk-test",
                "LANGFUSE_SECRET_KEY": "sk-test",
            },
            clear=True,
        ):
            with patch("langfuse.Langfuse", return_value=mock_client) as mock_langfuse_class:
                from src.core.tracing import _initialize_langfuse

                import src.core.tracing as tracing_module

                tracing_module._langfuse_enabled = None
                tracing_module._langfuse_client = None

                client = _initialize_langfuse()

                assert client is not None
                mock_langfuse_class.assert_called_once_with(
                    public_key="pk-test",
                    secret_key="sk-test",
                    host=None,
                )

    @patch("src.core.tracing.Langfuse")
    def test_initialize_langfuse_with_custom_host(
        self, mock_langfuse_class: Mock
    ) -> None:
        """_initialize_langfuse should use custom host when provided."""
        mock_client = MagicMock()
        mock_client.host = "http://localhost:3000"
        mock_langfuse_class.return_value = mock_client

        with patch.dict(
            os.environ,
            {
                "LANGFUSE_ENABLED": "1",
                "LANGFUSE_PUBLIC_KEY": "pk-test",
                "LANGFUSE_SECRET_KEY": "sk-test",
                "LANGFUSE_HOST": "http://localhost:3000",
            },
            clear=True,
        ):
            from src.core.tracing import _initialize_langfuse

            import src.core.tracing as tracing_module

            tracing_module._langfuse_enabled = None
            tracing_module._langfuse_client = None

            client = _initialize_langfuse()

            assert client is not None
            mock_langfuse_class.assert_called_once_with(
                public_key="pk-test",
                secret_key="sk-test",
                host="http://localhost:3000",
            )

    @patch("src.core.tracing.Langfuse")
    def test_trace_masr_routing_creates_trace(self, mock_langfuse_class: Mock) -> None:
        """trace_masr_routing should create trace with correct payload."""
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
            from src.core.tracing import trace_masr_routing

            import src.core.tracing as tracing_module

            tracing_module._langfuse_enabled = None
            tracing_module._langfuse_client = None

            with trace_masr_routing(
                query_id="test-query-id",
                query="What is AI?",
                metadata={"complexity": "high", "strategy": "balanced"},
            ) as trace:
                assert trace is not None
                mock_client.trace.assert_called_once_with(
                    id="test-query-id",
                    name="masr_routing",
                    input={"query": "What is AI?"},
                    metadata={"complexity": "high", "strategy": "balanced"},
                )
                mock_trace.update.assert_called_once_with(
                    output={"status": "completed"}
                )

    @patch("src.core.tracing.Langfuse")
    def test_trace_provider_call_creates_span(
        self, mock_langfuse_class: Mock
    ) -> None:
        """trace_provider_call should create span with correct metadata."""
        mock_span = MagicMock()
        mock_trace = MagicMock()
        mock_trace.span.return_value = mock_span
        mock_client = MagicMock()
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
            from src.core.tracing import trace_provider_call

            import src.core.tracing as tracing_module

            tracing_module._langfuse_enabled = None
            tracing_module._langfuse_client = None

            with trace_provider_call(
                trace=mock_trace,
                provider="openrouter",
                model="claude-sonnet-4.6",
                metadata={"temperature": 0.7},
            ) as span:
                assert span is not None
                mock_trace.span.assert_called_once_with(
                    name="openrouter_call",
                    metadata={
                        "provider": "openrouter",
                        "model": "claude-sonnet-4.6",
                        "temperature": 0.7,
                    },
                )

    @patch("src.core.tracing.Langfuse")
    def test_record_provider_metrics_updates_span(
        self, mock_langfuse_class: Mock
    ) -> None:
        """record_provider_metrics should update span with usage metrics."""
        mock_span = MagicMock()

        from src.core.tracing import record_provider_metrics

        record_provider_metrics(
            span=mock_span,
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=0.0075,
            latency_ms=250,
        )

        mock_span.update.assert_called_once_with(
            usage={
                "input": 100,
                "output": 50,
                "total": 150,
                "unit": "TOKENS",
            },
            metadata={
                "cost_usd": 0.0075,
                "latency_ms": 250,
            },
        )


class TestTracingErrorHandling:
    """Test error handling and graceful degradation."""

    def test_initialize_langfuse_returns_none_when_keys_missing(self) -> None:
        """_initialize_langfuse should return None when keys are missing."""
        with patch.dict(
            os.environ,
            {"LANGFUSE_ENABLED": "true"},
            clear=True,
        ):
            from src.core.tracing import _initialize_langfuse

            import src.core.tracing as tracing_module

            tracing_module._langfuse_enabled = None
            tracing_module._langfuse_client = None

            client = _initialize_langfuse()
            assert client is None

    def test_initialize_langfuse_handles_import_error(self) -> None:
        """_initialize_langfuse should handle ImportError gracefully."""
        with patch.dict(
            os.environ,
            {
                "LANGFUSE_ENABLED": "true",
                "LANGFUSE_PUBLIC_KEY": "pk-test",
                "LANGFUSE_SECRET_KEY": "sk-test",
            },
            clear=True,
        ):
            with patch("src.core.tracing.Langfuse", side_effect=ImportError):
                from src.core.tracing import _initialize_langfuse

                import src.core.tracing as tracing_module

                tracing_module._langfuse_enabled = None
                tracing_module._langfuse_client = None

                client = _initialize_langfuse()
                assert client is None

    @patch("src.core.tracing.Langfuse")
    def test_initialize_langfuse_handles_initialization_error(
        self, mock_langfuse_class: Mock
    ) -> None:
        """_initialize_langfuse should handle SDK initialization errors."""
        mock_langfuse_class.side_effect = Exception("Connection failed")

        with patch.dict(
            os.environ,
            {
                "LANGFUSE_ENABLED": "true",
                "LANGFUSE_PUBLIC_KEY": "pk-test",
                "LANGFUSE_SECRET_KEY": "sk-test",
            },
            clear=True,
        ):
            from src.core.tracing import _initialize_langfuse

            import src.core.tracing as tracing_module

            tracing_module._langfuse_enabled = None
            tracing_module._langfuse_client = None

            client = _initialize_langfuse()
            assert client is None

    @patch("src.core.tracing.Langfuse")
    def test_trace_masr_routing_handles_trace_error(
        self, mock_langfuse_class: Mock
    ) -> None:
        """trace_masr_routing should handle trace creation errors."""
        mock_client = MagicMock()
        mock_client.trace.side_effect = Exception("Trace failed")
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
            from src.core.tracing import trace_masr_routing

            import src.core.tracing as tracing_module

            tracing_module._langfuse_enabled = None
            tracing_module._langfuse_client = None

            with trace_masr_routing(
                query_id="test-id", query="test", metadata={}
            ) as trace:
                # Should yield None on error
                assert trace is None

    @patch("src.core.tracing.Langfuse")
    def test_flush_langfuse_handles_flush_error(
        self, mock_langfuse_class: Mock
    ) -> None:
        """flush_langfuse should handle flush errors gracefully."""
        mock_client = MagicMock()
        mock_client.flush.side_effect = Exception("Flush failed")
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
            from src.core.tracing import flush_langfuse

            import src.core.tracing as tracing_module

            tracing_module._langfuse_enabled = None
            tracing_module._langfuse_client = None

            # Should not raise
            flush_langfuse()
