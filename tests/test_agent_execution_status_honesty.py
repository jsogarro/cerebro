"""Chain/mixture/single-agent execution must not report failures as success."""

from typing import Any

import pytest

from src.agents.models import AgentResult
from src.api.services.agent_execution_service import AgentExecutionService
from src.models.agent_api_models import (
    AgentExecutionRequest,
    AgentType,
    ChainOfAgentsRequest,
    MixtureOfAgentsRequest,
)


class _StubAgent:
    def __init__(self, status: str, seen: list[dict[str, Any]]) -> None:
        self.status = status
        self.seen = seen

    async def execute(self, task: Any) -> AgentResult:
        self.seen.append(task.input_data["context"])
        return AgentResult(
            task_id=task.id,
            status=self.status,
            output={"error": "boom"} if self.status != "success" else {"ok": 1},
            confidence=0.0 if self.status != "success" else 0.9,
            execution_time=0.0,
        )


def _service(
    statuses: dict[AgentType, str], seen: list[dict[str, Any]]
) -> AgentExecutionService:
    service = AgentExecutionService()

    async def get_agent(agent_type: AgentType) -> _StubAgent:
        return _StubAgent(statuses[agent_type], seen)

    service._get_agent_instance = get_agent  # type: ignore[method-assign]
    return service


A, B, C = AgentType.LITERATURE_REVIEW, AgentType.METHODOLOGY, AgentType.SYNTHESIS


def _chain(agents: list[AgentType]) -> ChainOfAgentsRequest:
    return ChainOfAgentsRequest(query="q", agent_chain=agents)


def _mixture(agents: list[AgentType]) -> MixtureOfAgentsRequest:
    return MixtureOfAgentsRequest(
        query="q", agent_types=agents, aggregation_strategy="consensus"
    )


@pytest.mark.asyncio
async def test_single_agent_failure_recorded_as_failure() -> None:
    service = _service({A: "failed"}, [])
    await service.execute_single_agent(A, AgentExecutionRequest(query="q"))
    metrics = service.agent_metrics[A]
    assert metrics["successful_executions"] == 0
    assert metrics["failed_executions"] == 1


@pytest.mark.asyncio
async def test_single_agent_success_recorded_as_success() -> None:
    service = _service({A: "success"}, [])
    await service.execute_single_agent(A, AgentExecutionRequest(query="q"))
    assert service.agent_metrics[A]["successful_executions"] == 1


@pytest.mark.asyncio
async def test_chain_all_failed_is_failed() -> None:
    service = _service({A: "failed", B: "failed"}, [])
    assert (await service.execute_chain_of_agents(_chain([A, B]))).status == "failed"


@pytest.mark.asyncio
async def test_chain_some_failed_is_partial() -> None:
    service = _service({A: "success", B: "failed"}, [])
    assert (await service.execute_chain_of_agents(_chain([A, B]))).status == "partial"


@pytest.mark.asyncio
async def test_chain_all_success_is_completed() -> None:
    service = _service({A: "success", B: "success"}, [])
    assert (await service.execute_chain_of_agents(_chain([A, B]))).status == "completed"


@pytest.mark.asyncio
async def test_chain_does_not_forward_failed_output() -> None:
    seen: list[dict[str, Any]] = []
    service = _service({A: "failed", B: "success"}, seen)
    await service.execute_chain_of_agents(_chain([A, B]))
    assert not any(key.startswith("previous_") for key in seen[1])


@pytest.mark.asyncio
async def test_mixture_all_failed_is_failed() -> None:
    service = _service({A: "failed", B: "failed"}, [])
    assert (
        await service.execute_mixture_of_agents(_mixture([A, B]))
    ).status == "failed"


@pytest.mark.asyncio
async def test_mixture_some_failed_is_partial() -> None:
    service = _service({A: "success", B: "failed"}, [])
    assert (
        await service.execute_mixture_of_agents(_mixture([A, B]))
    ).status == "partial"


@pytest.mark.asyncio
async def test_mixture_all_success_is_completed() -> None:
    service = _service({A: "success", B: "success"}, [])
    assert (
        await service.execute_mixture_of_agents(_mixture([A, B]))
    ).status == "completed"
