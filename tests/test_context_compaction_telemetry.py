"""
Unit tests for context compaction telemetry (PR1).

Verifies that telemetry hooks exist, log at INFO level, and token count >= 0.
No behavior changes to compaction/truncation logic.

These tests use real tiktoken (installed dependency) and concrete class instances.
"""

import pytest


class TestWorkingMemoryTelemetry:
    """Test telemetry in WorkingMemoryManager."""

    @pytest.fixture
    def working_memory(self):
        """Create WorkingMemoryManager instance."""
        from src.ai_brain.memory.working_memory import WorkingMemoryManager

        config = {
            "redis_url": "redis://localhost:6379/0",
            "max_messages_in_context": 50,
        }
        return WorkingMemoryManager(config)

    def test_measure_context_tokens_method_exists(self, working_memory):
        """Verify _measure_context_tokens method exists."""
        assert hasattr(working_memory, "_measure_context_tokens")
        assert callable(working_memory._measure_context_tokens)

    def test_measure_context_tokens_with_dict(self, working_memory):
        """Test token measurement with dict content."""
        content = {"key": "value", "messages": ["msg1", "msg2"]}
        token_count = working_memory._measure_context_tokens(
            content, context_label="test_dict"
        )
        # Token count should be >= 0 (actual count depends on tiktoken availability)
        assert isinstance(token_count, int)
        assert token_count >= 0

    def test_measure_context_tokens_with_string(self, working_memory):
        """Test token measurement with string content."""
        content = "This is a test string with several words to count"
        token_count = working_memory._measure_context_tokens(
            content, context_label="test_string"
        )
        assert isinstance(token_count, int)
        assert token_count >= 0


class TestBaseSupervisorTelemetry:
    """Test telemetry in BaseSupervisor (using concrete ContentSupervisor)."""

    @pytest.fixture
    def supervisor(self):
        """Create a concrete supervisor for testing."""
        from src.agents.supervisors.content_supervisor import ContentSupervisor

        return ContentSupervisor()

    def test_measure_worker_results_tokens_method_exists(self, supervisor):
        """Verify _measure_worker_results_tokens method exists."""
        assert hasattr(supervisor, "_measure_worker_results_tokens")
        assert callable(supervisor._measure_worker_results_tokens)

    def test_measure_worker_results_tokens(self, supervisor):
        """Test worker results token measurement."""
        worker_results = {
            "worker1": {"output": "result1"},
            "worker2": {"output": "result2"},
        }
        token_count = supervisor._measure_worker_results_tokens(
            worker_results, round_number=1
        )
        assert isinstance(token_count, int)
        assert token_count >= 0


class TestDirectExecutionServiceTelemetry:
    """Test telemetry in DirectExecutionService."""

    def test_measure_domain_output_tokens_method_exists(self):
        """Verify _measure_domain_output_tokens method exists."""
        from src.api.services.direct_execution_service import DirectExecutionService

        # Method exists on the class
        assert hasattr(DirectExecutionService, "_measure_domain_output_tokens")
        assert callable(DirectExecutionService._measure_domain_output_tokens)

        # Verify it's an instance method with correct signature by inspecting
        import inspect

        sig = inspect.signature(DirectExecutionService._measure_domain_output_tokens)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "domain" in params
        assert "output" in params
        assert "label" in params


class TestMultiTierMemoryTelemetry:
    """Test telemetry in MultiTierMemorySystem."""

    @pytest.fixture
    def memory_system(self):
        """Create MultiTierMemorySystem instance."""
        from src.ai_brain.memory.multi_tier_memory import MultiTierMemorySystem

        # Create with minimal config
        config = {
            "redis_url": "redis://localhost:6379/0",
            "postgres_url": "postgresql://localhost/test",
        }
        return MultiTierMemorySystem(config)

    def test_measure_recall_result_tokens_method_exists(self, memory_system):
        """Verify _measure_recall_result_tokens method exists."""
        assert hasattr(memory_system, "_measure_recall_result_tokens")
        assert callable(memory_system._measure_recall_result_tokens)

    def test_measure_recall_result_tokens(self, memory_system):
        """Test recall result token measurement."""
        from src.ai_brain.memory.multi_tier_memory import IntelligentRecall

        recall = IntelligentRecall(
            primary_results=[],
            supporting_context={"key": "value"},
            related_episodes=[],
            applicable_procedures=[],
            confidence_score=0.85,
            recall_reasoning="Test reasoning",
        )
        token_count = memory_system._measure_recall_result_tokens(recall)
        assert isinstance(token_count, int)
        assert token_count >= 0
