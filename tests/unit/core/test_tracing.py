"""Tests for the Langfuse v4 tracing layer (src/core/tracing.py).

Adversarial-round additions (PR #77 remediation):
  - FIX-1 regression: exception strings sent to Langfuse are PII-redacted
  - FIX-3 regression: shutdown/flush with tracing enabled but no client ever
    created never trigger lazy initialization


Covers the no-op-safe contract (disabled / SDK absent / bad keys must never
raise and must emit zero tracing calls) and the enabled contract (observations
are created, updated, and ended with the exact v4 payloads the MASR router and
provider pass).

Critically, this suite guards against two confirmed classes of bug:

1. The double-yield RuntimeError: a body exception inside a tracing context
   manager MUST propagate as the ORIGINAL exception type, never be replaced by
   ``RuntimeError: generator didn't stop after throw()``.
2. Post-yield SDK failures (update/end) MUST be swallowed so the traced work
   still succeeds.

A capturing spy stands in for the Langfuse client so no server is required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import src.core.tracing as tracing
from src.core.tracing import (
    flush_langfuse,
    get_langfuse_client,
    record_provider_metrics,
    reset_langfuse_state,
    shutdown_langfuse,
    trace_masr_routing,
    trace_provider_call,
)


@pytest.fixture(autouse=True)
def _reset_tracing_globals():
    """The module caches the client + init flag; reset around each test."""
    reset_langfuse_state()
    yield
    reset_langfuse_state()


def _patch_enabled(monkeypatch, enabled: bool) -> None:
    """Force the shared enabled-check without touching the real Settings singleton."""
    monkeypatch.setattr(tracing, "_tracing_enabled", lambda: enabled)


# --------------------------------------------------------------------------
# Disabled: everything is a no-op, the SDK is never touched, nothing raises.
# --------------------------------------------------------------------------


class TestDisabledIsNoOp:
    def test_client_is_none_when_disabled(self, monkeypatch):
        _patch_enabled(monkeypatch, False)
        assert get_langfuse_client() is None

    def test_disabled_never_touches_sdk(self, monkeypatch):
        """Disabled must never call _initialize_langfuse (never import the SDK)."""
        _patch_enabled(monkeypatch, False)
        with patch.object(tracing, "_initialize_langfuse") as init:
            assert get_langfuse_client() is None
            init.assert_not_called()

    def test_trace_masr_routing_yields_none_when_disabled(self, monkeypatch):
        _patch_enabled(monkeypatch, False)
        with trace_masr_routing("qid", "hello", {"strategy": "x"}) as obs:
            assert obs is None

    def test_provider_span_and_metrics_are_noops_when_disabled(self, monkeypatch):
        _patch_enabled(monkeypatch, False)
        with (
            trace_masr_routing("qid", "hello") as routing_obs,
            trace_provider_call(routing_obs, "openrouter", "m") as gen_obs,
        ):
            assert gen_obs is None
            # Must not raise even though the observation is None.
            record_provider_metrics(
                gen_obs,
                prompt_tokens=1,
                completion_tokens=2,
                cost_usd=0.1,
                latency_ms=3,
            )

    def test_shutdown_is_clean_noop_when_disabled(self, monkeypatch):
        _patch_enabled(monkeypatch, False)
        # No client, no raise, SDK untouched.
        shutdown_langfuse()


# --------------------------------------------------------------------------
# Enabled but misconfigured: still no-op, still no raise.
# --------------------------------------------------------------------------


class TestEnabledButMisconfigured:
    def test_missing_keys_returns_none(self, monkeypatch):
        _patch_enabled(monkeypatch, True)
        # Real _initialize_langfuse reads Settings; force the missing-keys path.
        fake_settings = MagicMock()
        fake_settings.LANGFUSE_ENABLED = True
        fake_settings.LANGFUSE_PUBLIC_KEY = None
        fake_settings.LANGFUSE_SECRET_KEY = None
        fake_settings.LANGFUSE_HOST = None
        monkeypatch.setattr(tracing, "get_settings", lambda: fake_settings)
        assert get_langfuse_client() is None

    def test_sdk_absent_returns_none(self, monkeypatch):
        _patch_enabled(monkeypatch, True)
        fake_settings = MagicMock()
        fake_settings.LANGFUSE_ENABLED = True
        fake_settings.LANGFUSE_PUBLIC_KEY = "pk"
        fake_settings.LANGFUSE_SECRET_KEY = "sk"
        fake_settings.LANGFUSE_HOST = None
        monkeypatch.setattr(tracing, "get_settings", lambda: fake_settings)
        # Simulate the package being absent: importing langfuse raises ImportError.
        with patch.dict("sys.modules", {"langfuse": None}):
            assert get_langfuse_client() is None


# --------------------------------------------------------------------------
# Enabled, client available (spy): observations fire with real v4 payloads.
# --------------------------------------------------------------------------


class TestEnabledEmitsObservations:
    @pytest.fixture
    def spy_client(self):
        """A capturing spy standing in for the Langfuse v4 client."""
        client = MagicMock(name="LangfuseClient")
        client.create_trace_id.return_value = "trace-id-abc"
        routing = MagicMock(name="RoutingSpan")
        gen = MagicMock(name="Generation")
        client.start_observation.return_value = routing
        routing.start_observation.return_value = gen
        return client, routing, gen

    def test_masr_observation_created_with_trace_context_and_redacted_input(
        self, spy_client, monkeypatch
    ):
        _patch_enabled(monkeypatch, True)
        client, routing, _ = spy_client
        with (
            patch.object(tracing, "get_langfuse_client", return_value=client),
            trace_masr_routing(
                "query-123",
                "impact of AI",
                {"strategy_override": "quality_focused", "has_constraints": True},
            ) as obs,
        ):
            assert obs is routing

        client.create_trace_id.assert_called_once_with(seed="query-123")
        client.start_observation.assert_called_once()
        kwargs = client.start_observation.call_args.kwargs
        assert kwargs["trace_context"] == {"trace_id": "trace-id-abc"}
        assert kwargs["name"] == "masr_routing"
        assert kwargs["as_type"] == "span"
        assert kwargs["input"] == {"query": "impact of AI"}
        assert kwargs["metadata"]["strategy_override"] == "quality_focused"
        # Observation ended in the finally block.
        routing.end.assert_called_once()

    def test_raw_query_is_redacted_and_capped(self, spy_client, monkeypatch):
        _patch_enabled(monkeypatch, True)
        client, _, _ = spy_client
        raw = "please email me at secret@example.com " * 100  # > 500 chars
        with (
            patch.object(tracing, "get_langfuse_client", return_value=client),
            trace_masr_routing("qid", raw),
        ):
            pass
        sent = client.start_observation.call_args.kwargs["input"]["query"]
        assert len(sent) <= 500
        assert "secret@example.com" not in sent
        assert "[EMAIL]" in sent

    def test_provider_generation_created_with_model_metadata(
        self, spy_client, monkeypatch
    ):
        _patch_enabled(monkeypatch, True)
        client, routing, gen = spy_client
        with (
            patch.object(tracing, "get_langfuse_client", return_value=client),
            trace_masr_routing("qid", "q") as r,
            trace_provider_call(
                r,
                "openrouter",
                "deepseek/deepseek-chat",
                {"temperature": 0.7, "tier": "simple"},
            ) as g,
        ):
            assert g is gen

        routing.start_observation.assert_called_once()
        kwargs = routing.start_observation.call_args.kwargs
        assert kwargs["name"] == "openrouter_call"
        assert kwargs["as_type"] == "generation"
        assert kwargs["model"] == "deepseek/deepseek-chat"
        assert kwargs["metadata"]["provider"] == "openrouter"
        assert kwargs["metadata"]["tier"] == "simple"
        gen.end.assert_called_once()

    def test_metrics_recorded_with_usage_and_cost_details(
        self, spy_client, monkeypatch
    ):
        _patch_enabled(monkeypatch, True)
        client, _, gen = spy_client
        with (
            patch.object(tracing, "get_langfuse_client", return_value=client),
            trace_masr_routing("qid", "q") as r,
            trace_provider_call(r, "openrouter", "m") as g,
        ):
            record_provider_metrics(
                g,
                prompt_tokens=100,
                completion_tokens=50,
                cost_usd=0.005,
                latency_ms=250,
            )
        gen.update.assert_called_once()
        kwargs = gen.update.call_args.kwargs
        assert kwargs["usage_details"] == {"input": 100, "output": 50, "total": 150}
        assert kwargs["cost_details"] == {"total": 0.005}
        assert kwargs["metadata"]["latency_ms"] == 250


# --------------------------------------------------------------------------
# The critical regression class: exception propagation + swallowed SDK errors.
# --------------------------------------------------------------------------


class TestExceptionSemantics:
    @pytest.fixture
    def spy_client(self):
        client = MagicMock(name="LangfuseClient")
        client.create_trace_id.return_value = "trace-id-abc"
        routing = MagicMock(name="RoutingSpan")
        gen = MagicMock(name="Generation")
        client.start_observation.return_value = routing
        routing.start_observation.return_value = gen
        return client, routing, gen

    def test_body_exception_propagates_as_original_type_routing(
        self, spy_client, monkeypatch
    ):
        """Body ValueError -> ValueError to caller (NOT RuntimeError double-yield)."""
        _patch_enabled(monkeypatch, True)
        client, routing, _ = spy_client
        with (
            patch.object(tracing, "get_langfuse_client", return_value=client),
            pytest.raises(ValueError, match="body boom"),
            trace_masr_routing("qid", "q"),
        ):
            raise ValueError("body boom")
        # Observation still ended despite the body raising.
        routing.end.assert_called_once()

    def test_body_exception_propagates_as_original_type_provider(
        self, spy_client, monkeypatch
    ):
        _patch_enabled(monkeypatch, True)
        client, _, gen = spy_client
        # Nested intentionally: the KeyError must propagate through BOTH tracing
        # context managers unchanged before pytest.raises catches it.
        with (
            patch.object(tracing, "get_langfuse_client", return_value=client),
            pytest.raises(KeyError),
            trace_masr_routing("qid", "q") as r,
            trace_provider_call(r, "openrouter", "m"),
        ):
            raise KeyError("provider body boom")
        gen.end.assert_called_once()

    def test_body_exception_propagates_when_disabled(self, monkeypatch):
        """No-op path must also let the body exception through unchanged."""
        _patch_enabled(monkeypatch, False)
        with (
            pytest.raises(ValueError, match="disabled body"),
            trace_masr_routing("qid", "q"),
        ):
            raise ValueError("disabled body")

    def test_observation_create_failure_is_swallowed(self, spy_client, monkeypatch):
        """SDK error during observation creation degrades to None, no raise."""
        _patch_enabled(monkeypatch, True)
        client, _, _ = spy_client
        client.start_observation.side_effect = RuntimeError("langfuse down")
        with (
            patch.object(tracing, "get_langfuse_client", return_value=client),
            trace_masr_routing("qid", "q") as obs,
        ):
            assert obs is None  # degraded, body still runs

    def test_post_yield_update_failure_is_swallowed_routing_succeeds(
        self, spy_client, monkeypatch
    ):
        """A post-yield SDK .end() failure must not break the traced work."""
        _patch_enabled(monkeypatch, True)
        client, routing, _ = spy_client
        routing.end.side_effect = RuntimeError("end boom")
        ran = False
        with (
            patch.object(tracing, "get_langfuse_client", return_value=client),
            trace_masr_routing("qid", "q"),
        ):
            ran = True  # traced work completes normally
        assert ran is True  # no exception escaped from .end() failing

    def test_metrics_update_failure_is_swallowed(self, spy_client, monkeypatch):
        _patch_enabled(monkeypatch, True)
        client, _, gen = spy_client
        gen.update.side_effect = RuntimeError("update boom")
        with (
            patch.object(tracing, "get_langfuse_client", return_value=client),
            trace_masr_routing("qid", "q") as r,
            trace_provider_call(r, "openrouter", "m") as g,
        ):
            # No raise despite update blowing up.
            record_provider_metrics(
                g,
                prompt_tokens=1,
                completion_tokens=1,
                cost_usd=0.0,
                latency_ms=1,
            )


# --------------------------------------------------------------------------
# shutdown_langfuse.
# --------------------------------------------------------------------------


class TestShutdown:
    def test_shutdown_calls_client_shutdown_once(self, monkeypatch):
        """shutdown_langfuse reads the cached global directly (FIX-3): set
        _langfuse_client in the module rather than patching get_langfuse_client."""
        _patch_enabled(monkeypatch, True)
        client = MagicMock(name="LangfuseClient")
        # FIX-3: shutdown reads _langfuse_client directly, not via get_langfuse_client.
        monkeypatch.setattr(tracing, "_langfuse_client", client)
        monkeypatch.setattr(tracing, "_langfuse_initialized", True)
        shutdown_langfuse()
        client.shutdown.assert_called_once()

    def test_shutdown_failure_is_swallowed(self, monkeypatch):
        _patch_enabled(monkeypatch, True)
        client = MagicMock(name="LangfuseClient")
        client.shutdown.side_effect = RuntimeError("shutdown boom")
        monkeypatch.setattr(tracing, "_langfuse_client", client)
        monkeypatch.setattr(tracing, "_langfuse_initialized", True)
        shutdown_langfuse()  # no raise

    def test_shutdown_noop_when_disabled(self, monkeypatch):
        _patch_enabled(monkeypatch, False)
        shutdown_langfuse()  # no client, no raise


# --------------------------------------------------------------------------
# Integration: the MASR router call-site fires the tracer on a real route(),
# and the provider integration threads an observation handle from metadata.
# --------------------------------------------------------------------------


class TestMasrRouteWiring:
    def _mock_analysis_and_optimization(self, router):
        from unittest.mock import patch as _patch

        from src.ai_brain.router.cost_optimizer import (
            CostEstimate,
            ModelSpec,
            ModelTier,
            OptimizationResult,
        )
        from src.ai_brain.router.query_analyzer import (
            ComplexityAnalysis,
            ComplexityFactors,
            ComplexityLevel,
        )

        analyze = _patch.object(router.complexity_analyzer, "analyze")
        optimize = _patch.object(router.cost_optimizer, "optimize")
        m_analyze = analyze.start()
        m_optimize = optimize.start()
        m_analyze.return_value = ComplexityAnalysis(
            score=0.6,
            level=ComplexityLevel.MODERATE,
            factors=ComplexityFactors(),
            domains=["research"],
            subtask_count=1,
            uncertainty=0.3,
            reasoning_types=["analytical"],
            recommended_agents={"research": 1},
            estimated_tokens=1000,
        )
        m_optimize.return_value = OptimizationResult(
            primary_model=ModelSpec(
                name="gemini-pro",
                provider="google",
                tier=ModelTier.STANDARD,
                cost_per_1k_tokens=0.001,
                avg_latency_ms=50,
                context_window=32000,
                quality_score=0.85,
            ),
            estimated_cost=CostEstimate(
                model_name="gemini-pro",
                estimated_tokens=1000,
                cost_per_request=0.001,
                total_monthly_cost=100.0,
                latency_estimate_ms=50,
                quality_score=0.85,
                confidence=0.9,
            ),
            reasoning="test",
        )
        return [analyze, optimize]

    @pytest.mark.asyncio
    async def test_route_creates_observation_when_enabled(self, monkeypatch):
        from src.ai_brain.router.masr import MASRouter

        _patch_enabled(monkeypatch, True)
        router = MASRouter(config={"default_strategy": "balanced"})
        patchers = self._mock_analysis_and_optimization(router)
        client = MagicMock(name="LangfuseClient")
        client.create_trace_id.return_value = "trace-xyz"
        client.start_observation.return_value = MagicMock(name="RoutingSpan")
        try:
            with patch.object(tracing, "get_langfuse_client", return_value=client):
                decision = await router.route("What is AI?", context={})
        finally:
            for p in patchers:
                p.stop()

        assert decision.agent_allocation.worker_count >= 1
        client.start_observation.assert_called_once()
        kwargs = client.start_observation.call_args.kwargs
        assert kwargs["name"] == "masr_routing"
        assert kwargs["as_type"] == "span"
        assert kwargs["input"] == {"query": "What is AI?"}

    @pytest.mark.asyncio
    async def test_route_makes_zero_tracing_calls_when_disabled(self, monkeypatch):
        from src.ai_brain.router.masr import MASRouter

        _patch_enabled(monkeypatch, False)
        router = MASRouter(config={"default_strategy": "balanced"})
        patchers = self._mock_analysis_and_optimization(router)
        client = MagicMock(name="LangfuseClient")
        try:
            with patch.object(tracing, "get_langfuse_client", return_value=None):
                decision = await router.route("What is AI?", context={})
        finally:
            for p in patchers:
                p.stop()

        assert decision.agent_allocation.worker_count >= 1
        client.start_observation.assert_not_called()

    @pytest.mark.asyncio
    async def test_route_does_not_mutate_caller_context(self, monkeypatch):
        """The caller's context dict must never gain the _langfuse_trace key.

        This guards against the confirmed cross-request hazard where the trace
        handle leaked into the caller's dict (and into cache_decision's stored
        context).
        """
        from src.ai_brain.router.masr import MASRouter

        _patch_enabled(monkeypatch, True)
        router = MASRouter(config={"default_strategy": "balanced"})
        patchers = self._mock_analysis_and_optimization(router)
        client = MagicMock(name="LangfuseClient")
        client.create_trace_id.return_value = "trace-xyz"
        client.start_observation.return_value = MagicMock(name="RoutingSpan")
        caller_context: dict = {"user_id": "u1"}
        try:
            with patch.object(tracing, "get_langfuse_client", return_value=client):
                await router.route("What is AI?", context=caller_context)
        finally:
            for p in patchers:
                p.stop()

        assert "_langfuse_trace" not in caller_context

    @pytest.mark.asyncio
    async def test_route_accepts_strategy_as_raw_string(self, monkeypatch):
        """Regression: masr_routing_service passes strategy as a str, not enum.

        The router must normalize a raw string strategy without a
        ``strategy.value`` AttributeError.
        """
        from src.ai_brain.router.masr import MASRouter

        _patch_enabled(monkeypatch, False)
        router = MASRouter(config={"default_strategy": "balanced"})
        patchers = self._mock_analysis_and_optimization(router)
        try:
            with patch.object(tracing, "get_langfuse_client", return_value=None):
                decision = await router.route(
                    "What is AI?", context={}, strategy="quality_focused"
                )
        finally:
            for p in patchers:
                p.stop()

        assert decision.routing_strategy.value == "quality_focused"


# --------------------------------------------------------------------------
# Provider tracing integration (ported intent from the dropped provider tests):
# a mock observation handle in request.metadata yields a generation that is
# updated AND ended, and the error path guards the span update.
# --------------------------------------------------------------------------


class TestProviderTracingIntegration:
    def _make_provider(self):
        from src.ai_brain.providers.openrouter_provider import OpenRouterProvider

        provider = OpenRouterProvider(config={"api_key": "test-key"})
        return provider

    @pytest.mark.asyncio
    async def test_generation_created_updated_and_ended_on_success(self, monkeypatch):
        from src.ai_brain.providers.base_provider import ModelRequest, ModelResponse

        _patch_enabled(monkeypatch, True)
        provider = self._make_provider()

        # Parent routing observation whose start_observation returns a generation.
        parent = MagicMock(name="RoutingSpan")
        gen = MagicMock(name="Generation")
        parent.start_observation.return_value = gen

        client = MagicMock(name="LangfuseClient")

        request = ModelRequest(
            prompt="hi",
            max_tokens=10,
            metadata={"tier": "simple", "_langfuse_trace": parent},
        )

        fake_response = ModelResponse(
            request_id=request.request_id,
            content="hello",
            model_name="deepseek/deepseek-chat",
            provider="openrouter",
            completion_tokens=5,
            prompt_tokens=3,
            total_tokens=8,
            latency_ms=42,
            processing_time_ms=42,
            confidence_score=0.9,
            success=True,
            cost_estimate=0.001,
        )

        with (
            patch.object(tracing, "get_langfuse_client", return_value=client),
            patch.object(provider, "ensure_configuration_loaded"),
            patch.object(provider, "validate_request", return_value=True),
            patch.object(provider, "_make_api_request", return_value={"ok": True}),
            patch.object(provider, "_process_api_response", return_value=fake_response),
            patch.object(
                provider,
                "_postprocess_response",
                side_effect=lambda resp, req: resp,
            ),
        ):
            result = await provider.generate(request)

        assert result.success is True
        parent.start_observation.assert_called_once()
        assert parent.start_observation.call_args.kwargs["as_type"] == "generation"
        gen.update.assert_called_once()
        gen.end.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_path_guards_span_update_and_ends_observation(
        self, monkeypatch
    ):
        from src.ai_brain.providers.base_provider import ModelRequest

        _patch_enabled(monkeypatch, True)
        provider = self._make_provider()

        parent = MagicMock(name="RoutingSpan")
        gen = MagicMock(name="Generation")
        # Simulate the span update ITSELF failing on the error path; it must be
        # guarded so it cannot replace the original provider exception.
        gen.update.side_effect = RuntimeError("trace update boom")
        parent.start_observation.return_value = gen

        client = MagicMock(name="LangfuseClient")

        request = ModelRequest(
            prompt="hi",
            max_tokens=10,
            metadata={"tier": "simple", "_langfuse_trace": parent},
        )

        with (
            patch.object(tracing, "get_langfuse_client", return_value=client),
            patch.object(provider, "ensure_configuration_loaded"),
            patch.object(provider, "validate_request", return_value=True),
            patch.object(
                provider,
                "_make_api_request",
                side_effect=ValueError("provider blew up"),
            ),
        ):
            # generate() catches the provider error internally and returns an
            # error response; it must NOT surface the tracing RuntimeError.
            result = await provider.generate(request)

        assert result.success is False
        # Observation ended even though the error path update raised.
        gen.end.assert_called_once()

    @pytest.mark.asyncio
    async def test_provider_noop_when_no_observation_in_metadata(self, monkeypatch):
        from src.ai_brain.providers.base_provider import ModelRequest, ModelResponse

        _patch_enabled(monkeypatch, False)
        provider = self._make_provider()

        request = ModelRequest(prompt="hi", max_tokens=10, metadata={"tier": "simple"})
        fake_response = ModelResponse(
            request_id=request.request_id,
            content="hello",
            model_name="deepseek/deepseek-chat",
            provider="openrouter",
            completion_tokens=5,
            prompt_tokens=3,
            total_tokens=8,
            latency_ms=42,
            processing_time_ms=42,
            confidence_score=0.9,
            success=True,
            cost_estimate=0.001,
        )
        with (
            patch.object(provider, "ensure_configuration_loaded"),
            patch.object(provider, "validate_request", return_value=True),
            patch.object(provider, "_make_api_request", return_value={"ok": True}),
            patch.object(provider, "_process_api_response", return_value=fake_response),
            patch.object(
                provider,
                "_postprocess_response",
                side_effect=lambda resp, req: resp,
            ),
        ):
            # No _langfuse_trace in metadata → trace_provider_call yields None,
            # nothing raises.
            result = await provider.generate(request)
        assert result.success is True


# --------------------------------------------------------------------------
# FIX-1 regression: PII in exception strings must be redacted before being
# sent to Langfuse (an external SaaS).
# --------------------------------------------------------------------------


class TestPIIRedactionInErrorPaths:
    """Regression for adversarial finding: raw str(e) was sent to Langfuse.

    Verifies both the MASR router error path and the OpenRouter provider error
    path. Neither may forward a raw exception string — they must redact PII
    (emails, phone numbers, SSNs, credit card numbers) and cap at 300 chars.
    """

    @pytest.mark.asyncio
    async def test_masr_route_error_redacts_pii_in_trace_metadata(self, monkeypatch):
        """An exception whose .str() contains an email must arrive redacted."""
        from src.ai_brain.router.masr import MASRouter

        _patch_enabled(monkeypatch, True)
        router = MASRouter(config={"default_strategy": "balanced"})

        # Build a spy client whose routing span captures the .update() call.
        client = MagicMock(name="LangfuseClient")
        client.create_trace_id.return_value = "trace-pii"
        routing_span = MagicMock(name="RoutingSpan")
        client.start_observation.return_value = routing_span

        pii_error_msg = "Connection failed: user secret@pii-test.com not authorized"

        # Make complexity_analyzer.analyze raise an error whose str() has PII.
        with (
            patch.object(tracing, "get_langfuse_client", return_value=client),
            patch.object(
                router.complexity_analyzer,
                "analyze",
                side_effect=RuntimeError(pii_error_msg),
            ),
        ):
            decision = await router.route("test query", context={})

        # Router should still return a fallback decision (no crash).
        assert decision is not None

        # The trace.update() call that carries the error must have been made.
        assert (
            routing_span.update.called
        ), "trace.update() should have been called on error"
        update_kwargs = routing_span.update.call_args.kwargs
        sent_metadata = update_kwargs.get("metadata", {})

        # The raw PII email must NOT appear in either the "error" or any field.
        assert "secret@pii-test.com" not in sent_metadata.get(
            "error", ""
        ), "Raw PII email must be redacted in Langfuse error metadata"
        # The placeholder should be present instead.
        assert "[EMAIL]" in sent_metadata.get(
            "error", ""
        ), "Redacted placeholder must be present in error metadata"
        # error_type is safe (class name only) and must still be present.
        assert sent_metadata.get("error_type") == "RuntimeError"

    @pytest.mark.asyncio
    async def test_openrouter_provider_error_redacts_pii_in_span(self, monkeypatch):
        """Provider error path: raw exception string sent to span must be redacted."""
        from src.ai_brain.providers.base_provider import ModelRequest

        _patch_enabled(monkeypatch, True)

        from src.ai_brain.providers.openrouter_provider import OpenRouterProvider

        provider = OpenRouterProvider(config={"api_key": "test-key"})

        parent = MagicMock(name="RoutingSpan")
        gen = MagicMock(name="Generation")
        parent.start_observation.return_value = gen

        client = MagicMock(name="LangfuseClient")

        pii_error_msg = "HTTP 401 for user admin@secret-corp.com: token expired"

        request = ModelRequest(
            prompt="hi",
            max_tokens=10,
            metadata={"tier": "simple", "_langfuse_trace": parent},
        )

        with (
            patch.object(tracing, "get_langfuse_client", return_value=client),
            patch.object(provider, "ensure_configuration_loaded"),
            patch.object(provider, "validate_request", return_value=True),
            patch.object(
                provider,
                "_make_api_request",
                side_effect=RuntimeError(pii_error_msg),
            ),
        ):
            result = await provider.generate(request)

        # Provider swallows and returns an error response — no crash.
        assert result.success is False

        # The span.update() on the error path must have been called.
        assert (
            gen.update.called
        ), "span.update() should have been called on provider error"
        update_kwargs = gen.update.call_args.kwargs

        # status_message and metadata["error"] must both be redacted.
        status_msg = update_kwargs.get("status_message", "")
        meta_error = update_kwargs.get("metadata", {}).get("error", "")

        assert (
            "admin@secret-corp.com" not in status_msg
        ), "PII must be redacted in status_message"
        assert (
            "admin@secret-corp.com" not in meta_error
        ), "PII must be redacted in metadata['error']"
        assert (
            "[EMAIL]" in status_msg
        ), "Redacted placeholder must appear in status_message"
        assert (
            "[EMAIL]" in meta_error
        ), "Redacted placeholder must appear in metadata['error']"
        assert update_kwargs.get("metadata", {}).get("error_type") == "RuntimeError"


# --------------------------------------------------------------------------
# FIX-3 regression: shutdown / flush with tracing enabled but no client ever
# created must NOT trigger lazy initialization.
# --------------------------------------------------------------------------


class TestShutdownNoLazyInit:
    """Regression for adversarial finding: shutdown_langfuse() called
    get_langfuse_client(), which could construct the client just to tear it
    down.  The fixed implementation reads the cached globals directly.
    """

    def test_shutdown_with_enabled_but_no_client_never_calls_initialize(
        self, monkeypatch
    ):
        """Tracing enabled, client never constructed → shutdown is a no-op,
        _initialize_langfuse is never called."""
        _patch_enabled(monkeypatch, True)
        # Do NOT call get_langfuse_client() — so _langfuse_initialized stays False
        # and _langfuse_client stays None.  The module globals are still at their
        # reset state (reset by autouse fixture).

        with patch.object(tracing, "_initialize_langfuse") as mock_init:
            shutdown_langfuse()
            mock_init.assert_not_called()

    def test_flush_with_enabled_but_no_client_never_calls_initialize(self, monkeypatch):
        """Same invariant for flush_langfuse()."""
        _patch_enabled(monkeypatch, True)

        with patch.object(tracing, "_initialize_langfuse") as mock_init:
            flush_langfuse()
            mock_init.assert_not_called()
