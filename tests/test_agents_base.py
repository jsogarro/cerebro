"""
Tests for agent base classes and models.

Following TDD principles - tests written before implementation.
"""

from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, patch

import pytest


class TestAgentModels:
    """Test cases for agent data models."""

    def test_agent_task_creation(self):
        """Test AgentTask dataclass creation."""
        from src.agents.models import AgentTask

        task = AgentTask(
            id="task-001",
            agent_type="literature_review",
            input_data={"query": "AI research"},
            context={"project_id": "proj-001"},
            timeout=300,
            priority=1,
        )

        assert task.id == "task-001"
        assert task.agent_type == "literature_review"
        assert task.input_data == {"query": "AI research"}
        assert task.context == {"project_id": "proj-001"}
        assert task.timeout == 300
        assert task.priority == 1

    def test_agent_task_immutability(self):
        """Test that AgentTask is immutable."""
        from src.agents.models import AgentTask

        task = AgentTask(
            id="task-001",
            agent_type="literature_review",
            input_data={"query": "AI research"},
            context={},
        )

        # Should not be able to modify attributes
        with pytest.raises(FrozenInstanceError):
            task.id = "task-002"

    def test_agent_result_creation(self):
        """Test AgentResult dataclass creation."""
        from src.agents.models import AgentResult

        result = AgentResult(
            task_id="task-001",
            status="success",
            output={"findings": ["finding1", "finding2"]},
            confidence=0.95,
            execution_time=2.5,
            metadata={"agent_version": "1.0"},
        )

        assert result.task_id == "task-001"
        assert result.status == "success"
        assert result.output == {"findings": ["finding1", "finding2"]}
        assert result.confidence == 0.95
        assert result.execution_time == 2.5
        assert result.metadata == {"agent_version": "1.0"}

    def test_agent_message_creation(self):
        """Test AgentMessage dataclass creation."""
        from src.agents.models import AgentMessage

        message = AgentMessage(
            from_agent="literature_review",
            to_agent="synthesis",
            message_type="findings",
            content={"sources": ["source1", "source2"]},
            timestamp=1234567890.0,
        )

        assert message.from_agent == "literature_review"
        assert message.to_agent == "synthesis"
        assert message.message_type == "findings"
        assert message.content == {"sources": ["source1", "source2"]}
        assert message.timestamp == 1234567890.0


class TestBaseAgent:
    """Test cases for BaseAgent abstract class."""

    def test_base_agent_cannot_be_instantiated(self):
        """Test that BaseAgent is abstract and cannot be instantiated directly."""
        from src.agents.base import BaseAgent

        with pytest.raises(TypeError) as exc_info:
            BaseAgent()

        assert "Can't instantiate abstract class" in str(exc_info.value)

    def test_base_agent_requires_execute_implementation(self):
        """Test that subclasses must implement execute method."""
        from src.agents.base import BaseAgent
        from src.agents.models import AgentResult

        class IncompleteAgent(BaseAgent):
            async def validate_result(self, result: AgentResult) -> bool:
                return True

        with pytest.raises(TypeError) as exc_info:
            IncompleteAgent()

        assert "Can't instantiate abstract class" in str(exc_info.value)

    def test_base_agent_requires_validate_implementation(self):
        """Test that subclasses must implement validate_result method."""
        from src.agents.base import BaseAgent
        from src.agents.models import AgentResult, AgentTask

        class IncompleteAgent(BaseAgent):
            async def execute(self, task: AgentTask) -> AgentResult:
                return AgentResult(
                    task_id=task.id,
                    status="success",
                    output={},
                    confidence=1.0,
                    execution_time=0.0,
                    metadata={},
                )

        with pytest.raises(TypeError) as exc_info:
            IncompleteAgent()

        assert "Can't instantiate abstract class" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_base_agent_provides_logging(self):
        """Test that base agent provides logging capabilities."""
        from src.agents.base import BaseAgent
        from src.agents.models import AgentResult, AgentTask

        class TestAgent(BaseAgent):
            async def execute(self, task: AgentTask) -> AgentResult:
                self.log_info(f"Executing task {task.id}")
                return AgentResult(
                    task_id=task.id,
                    status="success",
                    output={"test": "result"},
                    confidence=1.0,
                    execution_time=0.1,
                    metadata={},
                )

            async def validate_result(self, result: AgentResult) -> bool:
                return result.status == "success"

        agent = TestAgent()
        task = AgentTask(id="test-001", agent_type="test", input_data={}, context={})

        with patch.object(agent, "log_info") as mock_log:
            result = await agent.execute(task)
            mock_log.assert_called_once_with("Executing task test-001")

        assert result.status == "success"

    @pytest.mark.asyncio
    async def test_base_agent_provides_metrics(self):
        """Test that base agent provides metrics collection."""
        from src.agents.base import BaseAgent
        from src.agents.models import AgentResult, AgentTask

        class TestAgent(BaseAgent):
            async def execute(self, task: AgentTask) -> AgentResult:
                self.record_metric("tasks_processed", 1)
                return AgentResult(
                    task_id=task.id,
                    status="success",
                    output={},
                    confidence=1.0,
                    execution_time=0.1,
                    metadata={},
                )

            async def validate_result(self, result: AgentResult) -> bool:
                return True

        agent = TestAgent()
        task = AgentTask(id="test-001", agent_type="test", input_data={}, context={})

        with patch.object(agent, "record_metric") as mock_metric:
            await agent.execute(task)
            mock_metric.assert_called_once_with("tasks_processed", 1)

    @pytest.mark.asyncio
    async def test_base_agent_error_handling(self):
        """Test that base agent provides error handling."""
        from src.agents.base import BaseAgent
        from src.agents.models import AgentResult, AgentTask

        class ErrorAgent(BaseAgent):
            async def execute(self, task: AgentTask) -> AgentResult:
                try:
                    raise ValueError("Simulated error")
                except Exception as e:
                    return self.handle_error(task, e)

            async def validate_result(self, result: AgentResult) -> bool:
                return result.status != "failed"

        agent = ErrorAgent()
        task = AgentTask(id="test-001", agent_type="test", input_data={}, context={})

        result = await agent.execute(task)

        assert result.status == "failed"
        assert "Simulated error" in result.output.get("error", "")

    @pytest.mark.asyncio
    async def test_base_agent_communication(self):
        """Test inter-agent communication."""
        from src.agents.base import BaseAgent
        from src.agents.models import AgentMessage, AgentResult, AgentTask

        class CommunicatingAgent(BaseAgent):
            def __init__(self):
                super().__init__()
                self.received_messages = []

            async def execute(self, task: AgentTask) -> AgentResult:
                return AgentResult(
                    task_id=task.id,
                    status="success",
                    output={},
                    confidence=1.0,
                    execution_time=0.1,
                    metadata={},
                )

            async def validate_result(self, result: AgentResult) -> bool:
                return True

            async def receive_message(self, message: AgentMessage):
                self.received_messages.append(message)

        agent1 = CommunicatingAgent()
        agent2 = CommunicatingAgent()

        message = AgentMessage(
            from_agent="agent1",
            to_agent="agent2",
            message_type="test",
            content={"data": "test"},
            timestamp=1234567890.0,
        )

        await agent1.communicate(agent2, message)

        assert len(agent2.received_messages) == 1
        assert agent2.received_messages[0] == message

    @pytest.mark.asyncio
    async def test_base_agent_with_gemini_service(self):
        """Test that base agent can integrate with Gemini service."""
        from src.agents.base import BaseAgent
        from src.agents.models import AgentResult, AgentTask

        class GeminiAgent(BaseAgent):
            async def execute(self, task: AgentTask) -> AgentResult:
                # Simulate Gemini call
                if self.gemini_service:
                    response = await self.gemini_service.generate_content("test prompt")
                    output = {"gemini_response": response}
                else:
                    output = {"message": "No Gemini service"}

                return AgentResult(
                    task_id=task.id,
                    status="success",
                    output=output,
                    confidence=1.0,
                    execution_time=0.1,
                    metadata={},
                )

            async def validate_result(self, result: AgentResult) -> bool:
                return True

        # Test without Gemini service
        agent = GeminiAgent()
        task = AgentTask(id="test-001", agent_type="test", input_data={}, context={})

        result = await agent.execute(task)
        assert result.output == {"message": "No Gemini service"}

        # Test with mocked Gemini service
        mock_gemini = AsyncMock()
        mock_gemini.generate_content = AsyncMock(return_value="Generated content")

        agent_with_gemini = GeminiAgent(gemini_service=mock_gemini)
        result = await agent_with_gemini.execute(task)

        assert result.output == {"gemini_response": "Generated content"}
        mock_gemini.generate_content.assert_called_once_with("test prompt")

    def test_base_agent_get_agent_type(self):
        """Test that agents can report their type."""
        from src.agents.base import BaseAgent
        from src.agents.models import AgentResult, AgentTask

        class TypedAgent(BaseAgent):
            async def execute(self, task: AgentTask) -> AgentResult:
                return AgentResult(
                    task_id=task.id,
                    status="success",
                    output={},
                    confidence=1.0,
                    execution_time=0.1,
                    metadata={},
                )

            async def validate_result(self, result: AgentResult) -> bool:
                return True

            def get_agent_type(self) -> str:
                return "typed_agent"

        agent = TypedAgent()
        assert agent.get_agent_type() == "typed_agent"

    @pytest.mark.asyncio
    async def test_base_agent_retry_logic(self):
        """Test that base agent provides retry logic for failed operations."""
        from src.agents.base import BaseAgent
        from src.agents.models import AgentResult, AgentTask

        class RetryAgent(BaseAgent):
            def __init__(self):
                super().__init__()
                self.attempt_count = 0

            async def execute(self, task: AgentTask) -> AgentResult:
                self.attempt_count += 1

                if self.attempt_count < 3:
                    raise ConnectionError("Simulated connection error")

                return AgentResult(
                    task_id=task.id,
                    status="success",
                    output={"attempts": self.attempt_count},
                    confidence=1.0,
                    execution_time=0.1,
                    metadata={},
                )

            async def validate_result(self, result: AgentResult) -> bool:
                return True

        agent = RetryAgent()
        task = AgentTask(id="test-001", agent_type="test", input_data={}, context={})

        # The execute_with_retry method should retry on connection errors
        result = await agent.execute_with_retry(task)

        assert result.status == "success"
        assert result.output["attempts"] == 3
