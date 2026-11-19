"""REVISE verdicts must trigger bounded revisions on the live supervisor path.

Drives FinanceSupervisor.execute (the real entry point and LangGraph workflow);
only the worker transport and the verification agent are stubbed, so no
provider or network call is made.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, create_autospec

import pytest

from src.agents.communication.talkhier_message import (
    MessageType,
    TalkHierContent,
    TalkHierMessage,
)
from src.agents.models import AgentResult, AgentTask
from src.agents.supervisors.base_supervisor import MAX_VERIFICATION_REVISION_ROUNDS
from src.agents.supervisors.finance_supervisor import FinanceSupervisor
from src.agents.verification_agent import VerificationAgent
from src.api.services.component_catalog import build_application_component_registry
from src.core.kernel import RegistryEntry, TypedRegistry
from src.core.kernel.component_keys import AGENT_KEYS

WORKERS = ["financial_analysis", "valuation", "risk_assessment"]
PASS = "VERDICT: PASS\nISSUES: None"
REVISE = "VERDICT: REVISE\nISSUES:\n1. Unsupported claim"


def _verification_registry(agent: MagicMock) -> TypedRegistry:
    factory = create_autospec(VerificationAgent, spec_set=True, return_value=agent)
    return TypedRegistry(
        RegistryEntry(
            e.key, factory if e.key == AGENT_KEYS["verification"] else e.component
        )
        for e in build_application_component_registry().entries
    )


def _verifier(verdicts: Any) -> MagicMock:
    """Verifier replying per call; an Exception entry is raised; the last repeats."""
    agent = create_autospec(VerificationAgent, instance=True, spec_set=True)
    calls = {"n": 0}

    async def execute(task: AgentTask) -> AgentResult:
        verdict = verdicts[min(calls["n"], len(verdicts) - 1)]
        calls["n"] += 1
        if isinstance(verdict, Exception):
            raise verdict
        return AgentResult(
            task_id=task.id,
            status="completed",
            output={"content": verdict},
            confidence=0.9,
            execution_time=0.0,
        )

    agent.execute.side_effect = execute
    return agent


def _supervisor(verifier: MagicMock) -> tuple[FinanceSupervisor, list[Any]]:
    supervisor = FinanceSupervisor(
        gemini_service=MagicMock(),
        cache_client=MagicMock(),
        config={"component_registry": _verification_registry(verifier)},
    )
    supervisor.active_workers = {name: MagicMock() for name in WORKERS}
    sent: list[Any] = []

    async def send(worker: Any, message: TalkHierMessage) -> TalkHierMessage:
        sent.append(message)
        return TalkHierMessage(
            from_agent=message.to_agent,
            to_agent=message.from_agent,
            message_type=MessageType.WORKER_REPORT,
            content=TalkHierContent(content=f"{message.to_agent} output {len(sent)}"),
        )

    supervisor.communication_protocol = MagicMock()
    supervisor.communication_protocol._send_message_to_agent = AsyncMock(
        side_effect=send
    )
    return supervisor, sent


def _task() -> AgentTask:
    return AgentTask(id="t1", agent_type="finance", input_data={"query": "assess ACME"})


def _calls(sent: list[Any], worker: str) -> list[Any]:
    return [m for m in sent if m.to_agent == worker]


@pytest.mark.asyncio
async def test_revise_triggers_one_revision_with_feedback() -> None:
    supervisor, sent = _supervisor(_verifier([REVISE, PASS]))

    result = await supervisor.execute(_task())

    first = _calls(sent, "financial_analysis")
    assert len(first) == 2
    assert "REVISION_FEEDBACK" not in first[0].talkhier_content.content
    assert "REVISION_FEEDBACK" in first[1].talkhier_content.content
    assert len(_calls(sent, "valuation")) == 1
    assert len(_calls(sent, "risk_assessment")) == 1
    assert result.output["worker_results"]["verification"]["verdict"] == "pass"


@pytest.mark.asyncio
async def test_revision_rounds_are_capped() -> None:
    supervisor, sent = _supervisor(_verifier([REVISE]))

    result = await supervisor.execute(_task())

    assert MAX_VERIFICATION_REVISION_ROUNDS == 2
    for worker in WORKERS:
        assert len(_calls(sent, worker)) == 2
    assert result.status != "completed"


@pytest.mark.asyncio
async def test_erroring_verifier_fails_closed_within_the_cap() -> None:
    supervisor, sent = _supervisor(_verifier([RuntimeError("verifier down")]))

    result = await supervisor.execute(_task())

    for worker in WORKERS:
        assert len(_calls(sent, worker)) <= MAX_VERIFICATION_REVISION_ROUNDS
    assert result.output["worker_results"]["verification"]["verdict"] == "revise"
    assert result.status != "completed"


def test_research_supervisor_workers_go_through_the_verification_loop() -> None:
    from src.agents.supervisors.research_supervisor import ResearchSupervisor

    supervisor = ResearchSupervisor(gemini_service=MagicMock(), cache_client=None)

    sender = supervisor.execution_coordinator.send_talkhier_message
    assert sender == supervisor._send_worker_verified
