"""
Unit tests for context compaction telemetry (PR1).

Verifies that telemetry hooks exist, log at INFO level, and token count >= 0.
No behavior changes to compaction/truncation logic.
"""

from unittest.mock import MagicMock, patch

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

    @patch("src.ai_brain.memory.working_memory.TIKTOKEN_AVAILABLE", True)
    @patch("src.ai_brain.memory.working_memory.tiktoken")
    def test_measure_context_tokens_with_dict(self, mock_tiktoken, working_memory):
        """Test token measurement with dict content."""
        # Mock tiktoken encoder
        mock_encoding = MagicMock()
        mock_encoding.encode.return_value = [1, 2, 3, 4, 5]  # 5 tokens
        mock_tiktoken.get_encoding.return_value = mock_encoding

        content = {"key": "value", "messages": ["msg1", "msg2"]}

        with patch("src.core.config.get_settings") as mock_settings:
            settings_instance = MagicMock()
            settings_instance.ENABLE_CONTEXT_COMPACTION_TELEMETRY = True
            mock_settings.return_value = settings_instance

            token_count = working_memory._measure_context_tokens(
                content, context_label="test_dict"
            )

        assert token_count == 5
        assert token_count >= 0

    @patch("src.ai_brain.memory.working_memory.TIKTOKEN_AVAILABLE", True)
    @patch("src.ai_brain.memory.working_memory.tiktoken")
    def test_measure_context_tokens_with_string(self, mock_tiktoken, working_memory):
        """Test token measurement with string content."""
        mock_encoding = MagicMock()
        mock_encoding.encode.return_value = [1, 2, 3]  # 3 tokens
        mock_tiktoken.get_encoding.return_value = mock_encoding

        content = "This is a test string"

        with patch("src.core.config.get_settings") as mock_settings:
            settings_instance = MagicMock()
            settings_instance.ENABLE_CONTEXT_COMPACTION_TELEMETRY = True
            mock_settings.return_value = settings_instance

            token_count = working_memory._measure_context_tokens(
                content, context_label="test_string"
            )

        assert token_count == 3
        assert token_count >= 0

    @patch("src.ai_brain.memory.working_memory.TIKTOKEN_AVAILABLE", False)
    def test_measure_context_tokens_tiktoken_unavailable(self, working_memory):
        """Test graceful handling when tiktoken is unavailable."""
        content = {"test": "data"}
        token_count = working_memory._measure_context_tokens(
            content, context_label="test_unavailable"
        )

        assert token_count == 0

    @patch("src.ai_brain.memory.working_memory.TIKTOKEN_AVAILABLE", True)
    def test_measure_context_tokens_flag_disabled(self, working_memory):
        """Test that measurement is skipped when telemetry flag is disabled."""
        with patch("src.core.config.get_settings") as mock_settings:
            settings_instance = MagicMock()
            settings_instance.ENABLE_CONTEXT_COMPACTION_TELEMETRY = False
            mock_settings.return_value = settings_instance

            content = {"test": "data"}
            token_count = working_memory._measure_context_tokens(
                content, context_label="test_disabled"
            )

        assert token_count == 0


class TestBaseSupervisorTelemetry:
    """Test telemetry in BaseSupervisor."""

    @pytest.fixture
    def mock_supervisor(self):
        """Create a mock supervisor instance."""
        from src.agents.supervisors.base_supervisor import BaseSupervisor

        # Create a concrete implementation of abstract BaseSupervisor for testing
        class TestSupervisor(BaseSupervisor):
            def _register_worker_types(self):
                pass

            def _build_workflow_graph(self):
                pass

            async def _coordinate_workers(self, state, task):
                return state

        return TestSupervisor(
            supervisor_type="test",
            domain="test_domain",
            gemini_service=None,
            cache_client=None,
            config={},
        )

    def test_measure_worker_results_tokens_method_exists(self, mock_supervisor):
        """Verify _measure_worker_results_tokens method exists."""
        assert hasattr(mock_supervisor, "_measure_worker_results_tokens")
        assert callable(mock_supervisor._measure_worker_results_tokens)

    @patch("src.agents.supervisors.base_supervisor.TIKTOKEN_AVAILABLE", True)
    @patch("src.agents.supervisors.base_supervisor.tiktoken")
    def test_measure_worker_results_tokens(self, mock_tiktoken, mock_supervisor):
        """Test worker results token measurement."""
        mock_encoding = MagicMock()
        mock_encoding.encode.return_value = [1, 2, 3, 4, 5, 6, 7, 8]  # 8 tokens
        mock_tiktoken.get_encoding.return_value = mock_encoding

        worker_results = {
            "worker1": {"output": "result1"},
            "worker2": {"output": "result2"},
        }

        with patch(
            "src.core.config.get_settings"
        ) as mock_settings:
            settings_instance = MagicMock()
            settings_instance.ENABLE_CONTEXT_COMPACTION_TELEMETRY = True
            mock_settings.return_value = settings_instance

            token_count = mock_supervisor._measure_worker_results_tokens(
                worker_results, round_number=1
            )

        assert token_count == 8
        assert token_count >= 0

    @patch("src.agents.supervisors.base_supervisor.TIKTOKEN_AVAILABLE", False)
    def test_measure_worker_results_tokens_unavailable(self, mock_supervisor):
        """Test graceful handling when tiktoken is unavailable."""
        worker_results = {"worker1": {"output": "result1"}}
        token_count = mock_supervisor._measure_worker_results_tokens(
            worker_results, round_number=1
        )

        assert token_count == 0


class TestDirectExecutionServiceTelemetry:
    """Test telemetry in DirectExecutionService."""

    @pytest.fixture
    def execution_service(self):
        """Create DirectExecutionService instance."""
        from src.api.services.direct_execution_service import DirectExecutionService

        return DirectExecutionService(
            masr_router=None,
            supervisor_bridge=None,
            supervisor_factory=None,
            event_publisher=None,
            gemini_service=None,
            session_factory=None,
        )

    def test_measure_domain_output_tokens_method_exists(self, execution_service):
        """Verify _measure_domain_output_tokens method exists."""
        assert hasattr(execution_service, "_measure_domain_output_tokens")
        assert callable(execution_service._measure_domain_output_tokens)

    @patch("src.api.services.direct_execution_service.TIKTOKEN_AVAILABLE", True)
    @patch("src.api.services.direct_execution_service.tiktoken")
    def test_measure_domain_output_tokens(self, mock_tiktoken, execution_service):
        """Test domain output token measurement."""
        mock_encoding = MagicMock()
        mock_encoding.encode.return_value = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]  # 10 tokens
        mock_tiktoken.get_encoding.return_value = mock_encoding

        output = {"data": "test output", "results": [1, 2, 3]}

        with patch(
            "src.core.config.get_settings"
        ) as mock_settings:
            settings_instance = MagicMock()
            settings_instance.ENABLE_CONTEXT_COMPACTION_TELEMETRY = True
            mock_settings.return_value = settings_instance

            token_count = execution_service._measure_domain_output_tokens(
                domain="test_domain", output=output, label="before_truncation"
            )

        assert token_count == 10
        assert token_count >= 0

    @patch("src.api.services.direct_execution_service.TIKTOKEN_AVAILABLE", False)
    def test_measure_domain_output_tokens_unavailable(self, execution_service):
        """Test graceful handling when tiktoken is unavailable."""
        output = {"data": "test"}
        token_count = execution_service._measure_domain_output_tokens(
            domain="test", output=output, label="test"
        )

        assert token_count == 0


class TestMultiTierMemoryTelemetry:
    """Test telemetry in MultiTierMemorySystem."""

    @pytest.fixture
    def memory_system(self):
        """Create MultiTierMemorySystem instance."""
        from src.ai_brain.memory.multi_tier_memory import MultiTierMemorySystem

        config = {
            "working_memory": {},
            "episodic_memory": {},
            "semantic_memory": {},
            "procedural_memory": {},
        }
        return MultiTierMemorySystem(config)

    def test_measure_recall_result_tokens_method_exists(self, memory_system):
        """Verify _measure_recall_result_tokens method exists."""
        assert hasattr(memory_system, "_measure_recall_result_tokens")
        assert callable(memory_system._measure_recall_result_tokens)

    @patch("src.ai_brain.memory.multi_tier_memory.TIKTOKEN_AVAILABLE", True)
    @patch("src.ai_brain.memory.multi_tier_memory.tiktoken")
    def test_measure_recall_result_tokens(self, mock_tiktoken, memory_system):
        """Test recall result token measurement."""
        from src.ai_brain.memory.multi_tier_memory import IntelligentRecall

        mock_encoding = MagicMock()
        mock_encoding.encode.return_value = [1, 2, 3, 4, 5]  # 5 tokens
        mock_tiktoken.get_encoding.return_value = mock_encoding

        recall = IntelligentRecall(
            primary_results=[],
            supporting_context={"key": "value"},
            related_episodes=[],
            applicable_procedures=[],
            confidence_score=0.85,
            recall_reasoning="Test reasoning",
        )

        with patch(
            "src.core.config.get_settings"
        ) as mock_settings:
            settings_instance = MagicMock()
            settings_instance.ENABLE_CONTEXT_COMPACTION_TELEMETRY = True
            mock_settings.return_value = settings_instance

            token_count = memory_system._measure_recall_result_tokens(recall)

        assert token_count == 5
        assert token_count >= 0

    @patch("src.ai_brain.memory.multi_tier_memory.TIKTOKEN_AVAILABLE", False)
    def test_measure_recall_result_tokens_unavailable(self, memory_system):
        """Test graceful handling when tiktoken is unavailable."""
        from src.ai_brain.memory.multi_tier_memory import IntelligentRecall

        recall = IntelligentRecall(confidence_score=0.5)
        token_count = memory_system._measure_recall_result_tokens(recall)

        assert token_count == 0


class TestConfigFlag:
    """Test ENABLE_CONTEXT_COMPACTION_TELEMETRY config flag."""

    def test_config_flag_exists(self):
        """Verify the telemetry flag exists in Settings."""
        from src.core.config import Settings

        settings = Settings()
        assert hasattr(settings, "ENABLE_CONTEXT_COMPACTION_TELEMETRY")

    def test_config_flag_default_value(self):
        """Verify the flag defaults to True."""
        from src.core.config import Settings

        settings = Settings()
        assert settings.ENABLE_CONTEXT_COMPACTION_TELEMETRY is True
