"""
Unit tests for context compaction telemetry (PR1).

Coverage:
- Shared telemetry module (count_tokens, telemetry_enabled, TIKTOKEN_AVAILABLE)
- Flag-off: all four _measure_* methods return 0 and emit no telemetry log
- tiktoken-unavailable path: count_tokens returns 0, no exception
- WorkingMemoryManager._measure_context_tokens and add_message_to_context
  (truncation + correct messages_before count regression test for fix 6a)
- BaseSupervisor._measure_worker_results_tokens with real TalkHierMessage
  content (regression test for fix 7)
- MultiTierMemorySystem._measure_recall_result_tokens with measurement_scope
- DirectExecutionService._measure_domain_output_tokens on an instance
- structlog log-emission assertions for at least one hotspot
"""

import os
from unittest.mock import patch

import pytest
from structlog.testing import capture_logs

# ---------------------------------------------------------------------------
# Shared telemetry module
# ---------------------------------------------------------------------------


class TestTelemetryModule:
    """Tests for src.core.telemetry helpers."""

    def test_tiktoken_available(self):
        from src.core.telemetry import TIKTOKEN_AVAILABLE

        assert TIKTOKEN_AVAILABLE is True

    def test_count_tokens_nonempty(self):
        from src.core.telemetry import count_tokens

        result = count_tokens("hello world")
        assert isinstance(result, int)
        assert result > 0

    def test_count_tokens_empty_string(self):
        from src.core.telemetry import count_tokens

        result = count_tokens("")
        assert result == 0

    def test_count_tokens_when_unavailable(self):
        """Patching TIKTOKEN_AVAILABLE to False must return 0 without raising."""
        import src.core.telemetry as tel_mod

        with patch.object(tel_mod, "TIKTOKEN_AVAILABLE", False):
            result = tel_mod.count_tokens("some text")
        assert result == 0

    def test_telemetry_enabled_default_false(self):
        """Default (False) — settings guard works without env override."""
        from src.core.telemetry import telemetry_enabled

        assert telemetry_enabled() is False

    def test_telemetry_enabled_when_set_true(self):
        with patch.dict(os.environ, {"ENABLE_CONTEXT_COMPACTION_TELEMETRY": "true"}):
            # Force settings reload so the env var is picked up.
            import src.core.config as cfg_mod
            import src.core.telemetry as tel_mod

            original_settings = cfg_mod.settings
            cfg_mod.settings = cfg_mod.Settings()
            try:
                result = tel_mod.telemetry_enabled()
            finally:
                cfg_mod.settings = original_settings
        assert result is True

    def test_telemetry_enabled_exception_returns_false(self):
        """Any exception in settings access must return False safely."""

        with patch("src.core.telemetry.telemetry_enabled", side_effect=RuntimeError):
            pass  # Just verify no import-time error

        # Patch get_settings to raise
        with patch("src.core.config.get_settings", side_effect=RuntimeError("boom")):
            import src.core.telemetry as tel_mod

            result = tel_mod.telemetry_enabled()
        assert result is False


# ---------------------------------------------------------------------------
# Flag-off: all four hotspots return 0 and emit no logs
# ---------------------------------------------------------------------------


class TestFlagOff:
    """With telemetry disabled (default), all _measure_* return 0 and log nothing."""

    @pytest.fixture
    def working_memory(self):
        from src.ai_brain.memory.working_memory import WorkingMemoryManager

        return WorkingMemoryManager({"max_messages_in_context": 5})

    @pytest.fixture
    def supervisor(self):
        from src.agents.supervisors.content_supervisor import ContentSupervisor

        return ContentSupervisor()

    @pytest.fixture
    def memory_system(self):
        from src.ai_brain.memory.multi_tier_memory import MultiTierMemorySystem

        return MultiTierMemorySystem({})

    @pytest.fixture
    def des(self):
        from src.api.services.direct_execution_service import DirectExecutionService

        return DirectExecutionService.__new__(DirectExecutionService)

    def test_working_memory_no_telemetry(self, working_memory):
        with capture_logs() as logs:
            result = working_memory._measure_context_tokens(
                {"key": "value"}, context_label="test"
            )
        assert result == 0
        telemetry_logs = [e for e in logs if "token" in e.get("event", "").lower()]
        assert telemetry_logs == []

    def test_supervisor_no_telemetry(self, supervisor):
        with capture_logs() as logs:
            result = supervisor._measure_worker_results_tokens(
                {"w1": {"output": "x"}}, round_number=1
            )
        assert result == 0
        telemetry_logs = [e for e in logs if "token" in e.get("event", "").lower()]
        assert telemetry_logs == []

    def test_memory_system_no_telemetry(self, memory_system):
        from src.ai_brain.memory.multi_tier_memory import IntelligentRecall

        recall = IntelligentRecall(
            supporting_context={"k": "v"}, recall_reasoning="test"
        )
        with capture_logs() as logs:
            result = memory_system._measure_recall_result_tokens(recall)
        assert result == 0
        telemetry_logs = [e for e in logs if "token" in e.get("event", "").lower()]
        assert telemetry_logs == []

    def test_des_no_telemetry(self, des):
        with capture_logs() as logs:
            result = des._measure_domain_output_tokens(
                "research", "some output text", label="before_truncation"
            )
        assert result == 0
        telemetry_logs = [e for e in logs if "token" in e.get("event", "").lower()]
        assert telemetry_logs == []


# ---------------------------------------------------------------------------
# tiktoken-unavailable path
# ---------------------------------------------------------------------------


class TestTiktokenUnavailable:
    """When TIKTOKEN_AVAILABLE is False count_tokens returns 0, no exception."""

    def test_count_tokens_unavailable(self):
        import src.core.telemetry as tel_mod

        with patch.object(tel_mod, "TIKTOKEN_AVAILABLE", False):
            result = tel_mod.count_tokens("lots of text here")
        assert result == 0

    def test_measure_context_tokens_unavailable(self):
        """When count_tokens returns 0 (tiktoken unavailable), _measure_context_tokens
        returns 0 without raising. Patch count_tokens in the consuming module."""
        from src.ai_brain.memory.working_memory import WorkingMemoryManager

        wm = WorkingMemoryManager({})
        with (
            patch(
                "src.ai_brain.memory.working_memory.telemetry_enabled",
                return_value=True,
            ),
            patch("src.ai_brain.memory.working_memory.count_tokens", return_value=0),
        ):
            result = wm._measure_context_tokens("text", context_label="t")
        assert result == 0


# ---------------------------------------------------------------------------
# WorkingMemoryManager: add_message_to_context
# ---------------------------------------------------------------------------


class TestWorkingMemoryAddMessage:
    """Regression tests for fix 6a: messages_before and branch-only measurement."""

    @pytest.fixture
    def wm(self):
        from src.ai_brain.memory.working_memory import WorkingMemoryManager

        return WorkingMemoryManager({"max_messages_in_context": 3})

    @pytest.mark.asyncio
    async def test_truncation_fires_telemetry_with_correct_messages_before(self, wm):
        """Overflow past max_messages must log messages_before equal to the
        true pre-truncation count (regression for fix 6a)."""
        from src.ai_brain.memory.working_memory import ConversationContext

        # Pre-fill context with max_messages messages then add one more so
        # truncation triggers.  The context is stored in the in-memory fallback
        # (no Redis) via store_conversation_context.
        ctx = ConversationContext(session_id="sess-trunc")
        ctx.messages = [{"role": "user", "content": f"msg{i}"} for i in range(3)]
        await wm.store_conversation_context(ctx)

        # Patch telemetry_enabled in the consuming module (not in src.core.telemetry)
        # so the module-local binding sees it as True.
        with (
            patch(
                "src.ai_brain.memory.working_memory.telemetry_enabled",
                return_value=True,
            ),
            capture_logs() as logs,
        ):
            await wm.add_message_to_context(
                "sess-trunc", {"role": "user", "content": "overflow message"}
            )

        # After adding the 4th message (max=3) truncation must have fired.
        trunc_logs = [e for e in logs if "truncated" in e.get("event", "").lower()]
        assert trunc_logs, "Expected a truncation log event"
        assert trunc_logs[0]["messages_before"] == 4  # 3 existing + 1 added
        assert trunc_logs[0]["messages_after"] == 3

    @pytest.mark.asyncio
    async def test_no_truncation_no_telemetry_log(self, wm):
        """When context stays under max_messages, no telemetry log is emitted."""
        from src.ai_brain.memory.working_memory import ConversationContext

        ctx = ConversationContext(session_id="sess-small")
        await wm.store_conversation_context(ctx)

        with (
            patch(
                "src.ai_brain.memory.working_memory.telemetry_enabled",
                return_value=True,
            ),
            capture_logs() as logs,
        ):
            await wm.add_message_to_context(
                "sess-small", {"role": "user", "content": "just one message"}
            )

        # No truncation should have occurred — no token measurement logs
        token_logs = [e for e in logs if "token" in e.get("event", "").lower()]
        assert token_logs == []


# ---------------------------------------------------------------------------
# BaseSupervisor: TalkHierMessage content measurement (fix 7)
# ---------------------------------------------------------------------------


class TestBaseSupervisorTalkhierMeasurement:
    """Regression test: token count reflects actual TalkHierMessage content."""

    @pytest.fixture
    def supervisor(self):
        from src.agents.supervisors.content_supervisor import ContentSupervisor

        return ContentSupervisor()

    def test_talkhier_message_content_measured(self, supervisor):
        """A TalkHierMessage with ~1000-char content must yield token_count > 100
        (regression for fix 7: previously measured short repr, not content)."""
        from src.agents.communication.talkhier_message import (
            MessageType,
            TalkHierContent,
            TalkHierMessage,
        )

        long_content = (
            "The quick brown fox jumps over the lazy dog. " * 25
        )  # ~1100 chars
        msg = TalkHierMessage(
            from_agent="worker1",
            to_agent="supervisor",
            message_type=MessageType.FINDINGS,
            content=TalkHierContent(content=long_content),
        )

        # Patch the module-local binding in base_supervisor, not src.core.telemetry.
        with patch(
            "src.agents.supervisors.base_supervisor.telemetry_enabled",
            return_value=True,
        ):
            result = supervisor._measure_worker_results_tokens(
                {"worker1": msg}, round_number=1
            )

        assert result > 100, (
            f"Expected token_count > 100 for ~1000-char content, got {result}"
        )

    def test_plain_dict_still_works(self, supervisor):
        """Non-TalkHierMessage values fall back to json.dumps(default=str)."""
        worker_results = {"w1": {"output": "result text " * 20}}
        with patch(
            "src.agents.supervisors.base_supervisor.telemetry_enabled",
            return_value=True,
        ):
            result = supervisor._measure_worker_results_tokens(
                worker_results, round_number=0
            )
        assert result > 0


# ---------------------------------------------------------------------------
# MultiTierMemorySystem: measurement_scope field (fix 8)
# ---------------------------------------------------------------------------


class TestMultiTierMemoryTelemetry:
    """_measure_recall_result_tokens must log measurement_scope='metadata_summary'."""

    @pytest.fixture
    def memory_system(self):
        from src.ai_brain.memory.multi_tier_memory import MultiTierMemorySystem

        return MultiTierMemorySystem({})

    def test_recall_measurement_scope_in_log(self, memory_system):
        from src.ai_brain.memory.multi_tier_memory import IntelligentRecall

        recall = IntelligentRecall(
            supporting_context={"key": "value"},
            confidence_score=0.9,
            recall_reasoning="test recall",
        )

        with (
            patch(
                "src.ai_brain.memory.multi_tier_memory.telemetry_enabled",
                return_value=True,
            ),
            capture_logs() as logs,
        ):
            result = memory_system._measure_recall_result_tokens(recall)

        assert result > 0
        assert logs, "Expected at least one log event"
        assert logs[0].get("measurement_scope") == "metadata_summary", (
            f"Expected measurement_scope='metadata_summary', got: {logs[0]}"
        )


# ---------------------------------------------------------------------------
# DirectExecutionService._measure_domain_output_tokens (fix 9)
# ---------------------------------------------------------------------------


class TestDirectExecutionServiceTelemetry:
    """_measure_domain_output_tokens accepts a pre-stringified str and counts > 0."""

    @pytest.fixture
    def des(self):
        from src.api.services.direct_execution_service import DirectExecutionService

        # Instantiate without __init__ to avoid needing MASRouter etc.
        return DirectExecutionService.__new__(DirectExecutionService)

    def test_method_signature_accepts_str(self):
        import inspect

        from src.api.services.direct_execution_service import DirectExecutionService

        sig = inspect.signature(DirectExecutionService._measure_domain_output_tokens)
        params = sig.parameters
        assert "output" in params
        # Annotation should be str (fix 9)
        annotation = params["output"].annotation
        assert annotation is str or annotation == "str", (
            f"Expected output: str, got {annotation}"
        )

    def test_returns_positive_count_for_real_text(self, des):
        text = "This is a domain output with plenty of tokens. " * 10
        with (
            patch(
                "src.api.services.direct_execution_service.telemetry_enabled",
                return_value=True,
            ),
            capture_logs() as logs,
        ):
            result = des._measure_domain_output_tokens(
                "research", text, label="before_truncation"
            )

        assert result > 0
        assert logs, "Expected a log event from measurement"
        assert logs[0].get("event") == "domain_output_token_measurement"

    def test_returns_zero_when_flag_off(self, des):
        result = des._measure_domain_output_tokens("research", "some text")
        assert result == 0


# ---------------------------------------------------------------------------
# Log emission verification for working_memory hotspot
# ---------------------------------------------------------------------------


class TestLogEmission:
    """Verify structlog events are emitted at INFO level from _measure_context_tokens."""

    def test_context_token_log_emitted(self):
        from src.ai_brain.memory.working_memory import WorkingMemoryManager

        wm = WorkingMemoryManager({})
        text = "log emission test " * 10

        with (
            patch(
                "src.ai_brain.memory.working_memory.telemetry_enabled",
                return_value=True,
            ),
            capture_logs() as logs,
        ):
            result = wm._measure_context_tokens(text, context_label="emission_test")

        assert result > 0
        assert logs, "Expected at least one structlog event"
        event_names = [e.get("event") for e in logs]
        assert "context_token_measurement" in event_names
        measurement = next(
            e for e in logs if e.get("event") == "context_token_measurement"
        )
        assert measurement.get("context_label") == "emission_test"
        assert measurement.get("token_count", 0) > 0


# ---------------------------------------------------------------------------
# FIX 1: Event-loop DoS guard — count_tokens_capped on large inputs
# ---------------------------------------------------------------------------


class TestCountTokensCapped:
    """count_tokens_capped must return quickly and produce a bounded count
    even when fed a >100KB payload (FIX 1: event-loop DoS guard)."""

    def test_large_input_returns_bounded_count(self):
        """Input larger than _MAX_MEASURE_CHARS is capped before encoding."""
        import time

        from src.core.telemetry import _MAX_MEASURE_CHARS, count_tokens_capped

        large_text = "a" * (_MAX_MEASURE_CHARS + 50_000)  # 150 KB+
        start = time.monotonic()
        token_count, truncated = count_tokens_capped(large_text)
        elapsed = time.monotonic() - start

        assert truncated is True, "Expected truncated=True for oversized input"
        assert isinstance(token_count, int)
        assert token_count > 0, "Capped token count must be > 0"
        # The capped text is _MAX_MEASURE_CHARS chars; tokens ~= chars/4 at
        # minimum, so the count should be well under 2x the cap in tokens.
        assert token_count < _MAX_MEASURE_CHARS, (
            f"Token count {token_count} should be less than char cap {_MAX_MEASURE_CHARS}"
        )
        # Must complete well under 1 s (typical: <100 ms even on a slow machine)
        assert elapsed < 1.0, f"count_tokens_capped took {elapsed:.3f}s on large input"

    def test_small_input_not_truncated(self):
        """Input within the cap must return truncated=False and a correct count."""
        from src.core.telemetry import count_tokens, count_tokens_capped

        text = "hello world this is a test"
        token_count, truncated = count_tokens_capped(text)

        assert truncated is False
        assert token_count == count_tokens(text)

    def test_large_worker_results_measurement_is_fast_and_bounded(self):
        """_measure_worker_results_tokens on a >100KB blob must return
        a bounded count without raising (FIX 1 probe for base_supervisor)."""
        import time

        from src.agents.supervisors.content_supervisor import ContentSupervisor
        from src.core.telemetry import _MAX_MEASURE_CHARS

        supervisor = ContentSupervisor()

        # Build a worker_results dict whose serialized form exceeds _MAX_MEASURE_CHARS
        big_value = "x" * (_MAX_MEASURE_CHARS + 50_000)
        worker_results = {"worker1": {"output": big_value}}

        start = time.monotonic()
        with patch(
            "src.agents.supervisors.base_supervisor.telemetry_enabled",
            return_value=True,
        ):
            token_count = supervisor._measure_worker_results_tokens(
                worker_results, round_number=1
            )
        elapsed = time.monotonic() - start

        assert isinstance(token_count, int)
        assert token_count > 0, "Token count must be > 0 for non-empty payload"
        # After capping at _MAX_MEASURE_CHARS chars the token count must be
        # less than the char cap (tokens are always <= chars for ASCII).
        assert token_count < _MAX_MEASURE_CHARS, (
            f"Token count {token_count} must be < char cap {_MAX_MEASURE_CHARS}"
        )
        assert elapsed < 1.0, (
            f"_measure_worker_results_tokens took {elapsed:.3f}s on large blob"
        )


# ---------------------------------------------------------------------------
# FIX 2: Refinement round measures fresh responses, not stale worker_results
# ---------------------------------------------------------------------------


class TestRefinementRoundMeasuresFreshResponses:
    """coordinate_refinement_round must measure the fresh broadcast responses,
    not the stale state.worker_results (FIX 2)."""

    def test_measure_called_with_fresh_responses(self):
        """_measure_worker_results_tokens is called with data derived from the
        broadcast responses dict, not from state.worker_results."""
        import asyncio

        from src.agents.supervisors.base_supervisor import SupervisionState
        from src.agents.supervisors.content_supervisor import ContentSupervisor

        supervisor = ContentSupervisor()
        state = SupervisionState(
            task_id="t1",
            original_query="q",
            refinement_round=0,
            # Stale worker_results that should NOT be what gets measured
            worker_results={"stale_worker": {"output": "old stale data"}},
        )

        captured_args: list[dict] = []

        original_measure = supervisor._measure_worker_results_tokens

        def capture_measure(worker_results_arg, round_number=0):
            captured_args.append(
                {"worker_results": worker_results_arg, "round": round_number}
            )
            return original_measure(worker_results_arg, round_number)

        # broadcast_to_workers returns a list of TalkHierMessage objects.
        # We stub it to return one fake response.
        from src.agents.communication.talkhier_message import (
            MessageType,
            TalkHierContent,
            TalkHierMessage,
        )

        fresh_response = TalkHierMessage(
            from_agent="worker1",
            to_agent="supervisor",
            message_type=MessageType.FINDINGS,
            content=TalkHierContent(content="fresh new response content"),
        )

        async def fake_broadcast(*args, **kwargs):
            return [fresh_response]

        async def fake_consensus(responses):
            class FakeScore:
                overall_score = 0.9
                evidence_quality = 0.85

            return FakeScore()

        # Patch out prompt manager (raises on missing config in test env)
        from unittest.mock import AsyncMock

        fake_pm = AsyncMock()
        fake_pm.get_prompt = AsyncMock(return_value="refinement prompt text")

        async def fake_get_pm():
            return fake_pm

        with (
            patch(
                "src.agents.supervisors.base_supervisor.get_prompt_manager", fake_get_pm
            ),
            patch.object(supervisor, "broadcast_to_workers", fake_broadcast),
            patch.object(
                supervisor.communication_protocol.consensus_builder,
                "evaluate_consensus",
                fake_consensus,
            ),
            patch.object(supervisor, "_measure_worker_results_tokens", capture_measure),
        ):
            asyncio.get_event_loop().run_until_complete(
                supervisor.coordinate_refinement_round(state, round_number=1)
            )

        assert len(captured_args) == 1, (
            "Expected exactly one call to _measure_worker_results_tokens"
        )
        measured = captured_args[0]["worker_results"]

        # The measured dict must NOT be the stale state.worker_results keys
        assert "stale_worker" not in measured, (
            "Stale worker_results key found in measurement — fix 2 not applied correctly"
        )
        # The measured dict must be keyed by integer indices (the responses_as_dict pattern)
        assert len(measured) == 1, "Expected one entry (one fresh response)"
        assert "0" in measured, "Expected string index key '0' from responses_as_dict"
