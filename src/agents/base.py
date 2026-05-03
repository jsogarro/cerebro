"""
Base agent class providing common functionality for all agents.

This module defines the abstract base class that all specialized agents
must inherit from, following functional programming principles.
"""

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Any

from structlog import get_logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.agents.models import (
    AgentMessage,
    AgentMetrics,
    AgentResult,
    AgentState,
    AgentTask,
)
from src.services.prompts.agent_prompts import get_agent_prompt_version

logger = get_logger()


class BaseAgent(ABC):
    """
    Abstract base class for all research agents.

    This class provides common functionality while enforcing the agent
    interface. All agents should be designed as pure functions that
    transform input data to output data.
    """

    def __init__(
        self,
        gemini_service: Any | None = None,
        cache_client: Any | None = None,
        config: dict[str, Any] | None = None,
    ):
        """
        Initialize the base agent.

        Args:
            gemini_service: Optional Gemini service for AI capabilities
            cache_client: Optional Redis client for caching
            config: Optional configuration dictionary
        """
        self.gemini_service = gemini_service
        self.cache_client = cache_client
        self.config = config or {}

        # Initialize MCP integration if provided
        self.mcp_integration = config.get("mcp_integration") if config else None

        # Initialize metrics
        self._metrics = AgentMetrics(agent_type=self.get_agent_type())

        # Initialize state
        self._state = AgentState(agent_type=self.get_agent_type(), status="idle")

        # Message queue for inter-agent communication
        self._message_queue: list[AgentMessage] = []

        # Logger specific to this agent
        self._logger = get_logger(f"{__name__}.{self.get_agent_type()}")

    @abstractmethod
    async def execute(self, task: AgentTask) -> AgentResult:
        """
        Execute the agent's main task.

        This should be implemented as a pure function that transforms
        the task input into a result output.

        Args:
            task: The task to execute

        Returns:
            The execution result
        """
        pass

    @abstractmethod
    async def validate_result(self, result: AgentResult) -> bool:
        """
        Validate the result of an execution.

        This ensures the result meets quality standards before being
        returned.

        Args:
            result: The result to validate

        Returns:
            True if valid, False otherwise
        """
        pass

    def get_agent_type(self) -> str:
        """
        Get the type of this agent.

        By default, returns the class name in lowercase.
        Can be overridden by subclasses.

        Returns:
            The agent type identifier
        """
        return self.__class__.__name__.lower().replace("agent", "")

    def log_info(self, message: str, **kwargs: Any) -> None:
        """Log an info message with agent context."""
        fields = {"agent_type": self.get_agent_type(), "detail": message}
        fields.update(kwargs)
        self._logger.info("agent_info", **fields)

    def log_error(self, message: str, **kwargs: Any) -> None:
        """Log an error message with agent context."""
        fields = {"agent_type": self.get_agent_type(), "detail": message}
        fields.update(kwargs)
        self._logger.error("agent_error", **fields)

    def log_warning(self, message: str, **kwargs: Any) -> None:
        """Log a warning message with agent context."""
        fields = {"agent_type": self.get_agent_type(), "detail": message}
        fields.update(kwargs)
        self._logger.warning("agent_warning", **fields)

    def record_metric(self, name: str, value: float) -> None:
        """
        Record a metric for monitoring.

        Args:
            name: Metric name
            value: Metric value
        """
        self.log_info(f"Metric: {name}={value}")

    def build_execution_metadata(self, **metadata: Any) -> dict[str, Any]:
        """Build agent execution metadata with prompt version tracking."""

        agent_type = self.get_agent_type()
        return {
            "agent_type": agent_type,
            "prompt_template": agent_type,
            "prompt_version": get_agent_prompt_version(agent_type),
            **metadata,
        }

    def handle_error(self, task: AgentTask, error: Exception) -> AgentResult:
        """
        Handle an error during execution.

        Creates a failed result with error information.

        Args:
            task: The task that failed
            error: The exception that occurred

        Returns:
            A failed AgentResult
        """
        self.log_error(f"Task {task.id} failed: {error!s}")

        return AgentResult(
            task_id=task.id,
            status="failed",
            output={"error": str(error), "error_type": type(error).__name__},
            confidence=0.0,
            execution_time=0.0,
            metadata=self.build_execution_metadata(
                task_type=task.agent_type,
            ),
        )

    async def communicate(self, other_agent: "BaseAgent", message: AgentMessage) -> None:
        """
        Send a message to another agent.

        Args:
            other_agent: The agent to send to
            message: The message to send
        """
        await other_agent.receive_message(message)
        self.log_info(
            f"Sent message to {other_agent.get_agent_type()}: {message.message_type}"
        )

    async def receive_message(self, message: AgentMessage) -> None:
        """
        Receive a message from another agent.

        Args:
            message: The received message
        """
        self._message_queue.append(message)
        self.log_info(
            f"Received message from {message.from_agent}: {message.message_type}"
        )

    def get_messages(self) -> list[AgentMessage]:
        """
        Get all received messages.

        Returns a copy to maintain immutability.

        Returns:
            List of received messages
        """
        return self._message_queue.copy()

    def clear_messages(self) -> None:
        """Clear the message queue."""
        self._message_queue.clear()

    @retry(
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    async def execute_with_retry(self, task: AgentTask) -> AgentResult:
        """
        Execute a task with retry logic.

        Retries on connection errors and timeouts.

        Args:
            task: The task to execute

        Returns:
            The execution result
        """
        start_time = time.time()

        # Update state
        self._state = self._state.with_new_status("processing", task.id)

        try:
            # Execute with timeout
            result = await asyncio.wait_for(self.execute(task), timeout=task.timeout)

            # Validate result
            if not await self.validate_result(result):
                self.log_warning(f"Task {task.id} produced invalid result")
                result = AgentResult(
                    task_id=task.id,
                    status="failed",
                    output={"error": "Result validation failed"},
                    confidence=0.0,
                    execution_time=time.time() - start_time,
                    metadata={"agent_type": self.get_agent_type()},
                )

            # Update metrics
            self._metrics = self._metrics.with_new_task(result)

            return result

        except TimeoutError:
            self.log_error(f"Task {task.id} timed out after {task.timeout}s")
            result = AgentResult(
                task_id=task.id,
                status="timeout",
                output={"error": f"Task timed out after {task.timeout} seconds"},
                confidence=0.0,
                execution_time=task.timeout,
                metadata={"agent_type": self.get_agent_type()},
            )
            self._metrics = self._metrics.with_new_task(result)
            return result

        except Exception as e:
            result = self.handle_error(task, e)
            self._metrics = self._metrics.with_new_task(result)
            raise  # Re-raise for retry logic

        finally:
            # Update state back to idle
            self._state = self._state.with_new_status("idle")

    def get_metrics(self) -> AgentMetrics:
        """
        Get current agent metrics.

        Returns:
            Current metrics
        """
        return self._metrics

    def get_state(self) -> AgentState:
        """
        Get current agent state.

        Returns:
            Current state
        """
        return self._state

    async def generate_prompt(self, task: AgentTask) -> str:
        """
        Generate a prompt for the Gemini service.

        Can be overridden by subclasses for specialized prompts.

        Args:
            task: The task to generate a prompt for

        Returns:
            The generated prompt
        """
        return f"Process this task: {task.input_data}"

    async def parse_response(self, response: str, task: AgentTask) -> dict[str, Any]:
        """
        Parse a response from the Gemini service.

        Can be overridden by subclasses for specialized parsing.

        Args:
            response: The response to parse
            task: The original task

        Returns:
            Parsed response data
        """
        return {"raw_response": response}

    async def cache_result(self, key: str, result: AgentResult, ttl: int = 3600) -> None:
        """
        Cache a result if cache client is available.

        Args:
            key: Cache key
            result: Result to cache
            ttl: Time to live in seconds
        """
        if self.cache_client:
            try:
                import json

                await self.cache_client.setex(
                    key,
                    ttl,
                    json.dumps(
                        {
                            "task_id": result.task_id,
                            "status": result.status,
                            "output": result.output,
                            "confidence": result.confidence,
                            "execution_time": result.execution_time,
                            "metadata": result.metadata,
                        }
                    ),
                )
                self.log_info(f"Cached result for key: {key}")
            except Exception as e:
                self.log_warning(f"Failed to cache result: {e}")

    async def get_cached_result(self, key: str) -> AgentResult | None:
        """
        Get a cached result if available.

        Args:
            key: Cache key

        Returns:
            Cached result or None
        """
        if self.cache_client:
            try:
                import json

                data = await self.cache_client.get(key)
                if data:
                    result_data = json.loads(data)
                    return AgentResult(**result_data)
            except Exception as e:
                self.log_warning(f"Failed to get cached result: {e}")

        return None
