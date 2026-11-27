"""The TalkHier ``background`` part must reach the worker's task input."""

from typing import Any

import pytest

from src.agents.communication.communication_protocol import CommunicationProtocol
from src.agents.communication.talkhier_message import (
    MessageType,
    TalkHierContent,
    TalkHierMessage,
)
from src.agents.models import AgentResult, AgentTask

SENTINEL = "BACKGROUND-SENTINEL-7f3a"


class _CapturingAgent:
    def __init__(self) -> None:
        self.tasks: list[AgentTask] = []

    def get_agent_type(self) -> str:
        return "worker"

    async def execute(self, task: AgentTask) -> AgentResult:
        self.tasks.append(task)
        return AgentResult(
            task_id=task.id,
            status="success",
            output={"summary": "ok"},
            confidence=0.9,
            execution_time=0.0,
        )


@pytest.mark.asyncio
async def test_background_reaches_the_worker_task_input() -> None:
    agent = _CapturingAgent()
    message = TalkHierMessage(
        from_agent="supervisor",
        to_agent="worker",
        message_type=MessageType.REQUEST,
        content=TalkHierContent(content="the query", background=SENTINEL),
        conversation_id="c1",
    )

    await CommunicationProtocol()._send_message_to_agent(agent, message)  # type: ignore[arg-type]

    sent: dict[str, Any] = agent.tasks[0].input_data
    assert sent["background"] == SENTINEL
