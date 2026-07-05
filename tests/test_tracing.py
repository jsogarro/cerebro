"""Tests for the Langfuse tracing layer (src/core/tracing.py).

Covers the no-op-safe contract (flag off / SDK absent / bad keys must never
raise and must emit zero tracing calls) and the flag-on contract (traces and
spans are created with the exact payloads the MASR router and provider pass).

A capturing spy stands in for the Langfuse client so no server is required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import src.core.tracing as tracing
from src.core.tracing import (
    get_langfuse_client,
    record_provider_metrics,
    trace_masr_routing,
    trace_provider_call,
)


@pytest.fixture(autouse=True)
def _reset_tracing_globals():
    """The module caches enabled-state and client in globals; reset per test."""
    tracing._langfuse_client = None
    tracing._langfuse_enabled = None
    yield
    tracing._langfuse_client = None
    tracing._langfuse_enabled = None


# --------------------------------------------------------------------------
# Flag OFF: everything is a no-op, nothing is imported, nothing raises.
# --------------------------------------------------------------------------


class TestDisabledIsNoOp:
    def test_client_is_none_when_flag_unset(self, monkeypatch):
        monkeypatch.delenv("LANGFUSE_ENABLED", raising=False)
        assert get_langfuse_client() is None

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
    def test_client_is_none_for_falsey_flag_values(self, monkeypatch, value):
        monkeypatch.setenv("LANGFUSE_ENABLED", value)
        assert get_langfuse_client() is None

    def test_trace_masr_routing_yields_none_and_makes_no_calls(self, monkeypatch):
        monkeypatch.setenv("LANGFUSE_ENABLED", "false")
        with trace_masr_routing("qid", "hello", {"strategy": "x"}) as trace:
            assert trace is None

    def test_provider_span_and_metrics_are_noops_when_disabled(self, monkeypatch):
        monkeypatch.setenv("LANGFUSE_ENABLED", "false")
        with (
            trace_masr_routing("qid", "hello") as trace,
            trace_provider_call(trace, "openrouter", "m") as span,
        ):
            assert span is None
            # Must not raise even though span is None.
            record_provider_metrics(
                span,
                prompt_tokens=1,
                completion_tokens=2,
                cost_usd=0.1,
                latency_ms=3,
            )

    def test_disabled_path_does_not_import_langfuse(self, monkeypatch):
        """Flag-off must never require the SDK to be importable."""
        monkeypatch.setenv("LANGFUSE_ENABLED", "false")
        with patch.object(
            tracing, "_initialize_langfuse", wraps=tracing._initialize_langfuse
        ):
            # _initialize_langfuse returns early before the `from langfuse import`
            assert get_langfuse_client() is None


# --------------------------------------------------------------------------
# Flag ON but misconfigured: still no-op, still no raise.
# --------------------------------------------------------------------------


class TestEnabledButMisconfigured:
    def test_missing_keys_returns_none(self, monkeypatch):
        monkeypatch.setenv("LANGFUSE_ENABLED", "true")
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
        assert get_langfuse_client() is None

    def test_sdk_absent_returns_none(self, monkeypatch):
        monkeypatch.setenv("LANGFUSE_ENABLED", "true")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
        # Simulate the package being absent: the import inside
        # _initialize_langfuse raises ImportError, which must degrade to None.
        with patch.dict("sys.modules", {"langfuse": None}):
            assert get_langfuse_client() is None


# --------------------------------------------------------------------------
# Flag ON, client available (spy): traces/spans fire with real payloads.
# --------------------------------------------------------------------------


class TestEnabledEmitsSpans:
    @pytest.fixture
    def spy_client(self):
        """A capturing spy standing in for the Langfuse client."""
        client = MagicMock(name="LangfuseClient")
        trace = MagicMock(name="Trace")
        span = MagicMock(name="Span")
        client.trace.return_value = trace
        trace.span.return_value = span
        return client, trace, span

    def test_masr_trace_created_with_query_id_and_metadata(self, spy_client):
        client, trace, _ = spy_client
        with (
            patch.object(tracing, "get_langfuse_client", return_value=client),
            trace_masr_routing(
                "query-123",
                "impact of AI",
                {"strategy_override": "quality_focused", "has_constraints": True},
            ) as t,
        ):
            assert t is trace
        client.trace.assert_called_once()
        kwargs = client.trace.call_args.kwargs
        assert kwargs["id"] == "query-123"
        assert kwargs["name"] == "masr_routing"
        assert kwargs["input"] == {"query": "impact of AI"}
        assert kwargs["metadata"]["strategy_override"] == "quality_focused"
        # Trace is marked completed on context exit.
        trace.update.assert_called_once()

    def test_provider_span_created_under_trace_with_model_metadata(self, spy_client):
        client, trace, span = spy_client
        with (
            patch.object(tracing, "get_langfuse_client", return_value=client),
            trace_masr_routing("qid", "q") as t,
            trace_provider_call(
                t,
                "openrouter",
                "deepseek/deepseek-chat",
                {"temperature": 0.7, "tier": "simple"},
            ) as s,
        ):
            assert s is span
        trace.span.assert_called_once()
        kwargs = trace.span.call_args.kwargs
        assert kwargs["name"] == "openrouter_call"
        assert kwargs["metadata"]["provider"] == "openrouter"
        assert kwargs["metadata"]["model"] == "deepseek/deepseek-chat"
        assert kwargs["metadata"]["tier"] == "simple"

    def test_metrics_recorded_with_tokens_cost_latency(self, spy_client):
        client, _, span = spy_client
        with (
            patch.object(tracing, "get_langfuse_client", return_value=client),
            trace_masr_routing("qid", "q") as t,
            trace_provider_call(t, "openrouter", "m") as s,
        ):
            record_provider_metrics(
                s,
                prompt_tokens=100,
                completion_tokens=50,
                cost_usd=0.005,
                latency_ms=250,
            )
        span.update.assert_called_once()
        kwargs = span.update.call_args.kwargs
        assert kwargs["usage"]["input"] == 100
        assert kwargs["usage"]["output"] == 50
        assert kwargs["usage"]["total"] == 150
        assert kwargs["metadata"]["cost_usd"] == 0.005
        assert kwargs["metadata"]["latency_ms"] == 250

    def test_trace_failure_is_swallowed_and_yields_none(self, spy_client):
        client, _, _ = spy_client
        client.trace.side_effect = RuntimeError("langfuse down")
        with (
            patch.object(tracing, "get_langfuse_client", return_value=client),
            # Must not propagate — tracing can never break the request path.
            trace_masr_routing("qid", "q") as t,
        ):
            assert t is None

    def test_metrics_failure_is_swallowed(self, spy_client):
        client, _, span = spy_client
        span.update.side_effect = RuntimeError("boom")
        with (
            patch.object(tracing, "get_langfuse_client", return_value=client),
            trace_masr_routing("qid", "q") as t,
            trace_provider_call(t, "openrouter", "m") as s,
        ):
            # No raise despite span.update blowing up.
            record_provider_metrics(
                s,
                prompt_tokens=1,
                completion_tokens=1,
                cost_usd=0.0,
                latency_ms=1,
            )
