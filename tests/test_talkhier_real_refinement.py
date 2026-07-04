"""Regression tests for real TalkHier round refinement.

`TalkHierRoundExecutor.execute_direct_refinement` / `execute_supervisor_refinement`
returned hardcoded placeholders ("Direct response from {id}", "Refined response
from {id}") with fabricated evidence — workers never invoked an LLM. These tests
assert the executor now invokes each worker's real agent and returns its output.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.agents.models import AgentResult
from src.api.services.talkhier_round_executor import TalkHierRoundExecutor
from src.models.talkhier_api_models import (
    MessageRole,
    ParticipantInfo,
    RefinementRoundRequest,
)


def _session():
    return SimpleNamespace(
        session_id="s1",
        query="Compare solar and nuclear energy",
        current_result=None,
        participants=[
            ParticipantInfo(
                agent_id="w1", agent_type="literature_review", role=MessageRole.WORKER,
            ),
            ParticipantInfo(
                agent_id="sup", agent_type="research", role=MessageRole.SUPERVISOR,
            ),
        ],
    )


class _FakeAgent:
    def __init__(self, content: str) -> None:
        self._content = content

    async def execute(self, task):
        return AgentResult(
            task_id=task.id,
            status="success",
            output={"content": self._content, "sources": ["s-a", "s-b"]},
            confidence=0.82,
            execution_time=0.1,
        )


async def test_direct_refinement_invokes_real_agent_not_placeholder() -> None:
    executor = TalkHierRoundExecutor()
    request = RefinementRoundRequest(round_number=1, refinement_focus="depth")

    with patch(
        "src.agents.factory.AgentFactory.create_agent",
        return_value=_FakeAgent("real agent analysis of energy tradeoffs"),
    ):
        responses = await executor.execute_direct_refinement(
            _session(), request, SimpleNamespace(),
        )

    # Only the worker participant is invoked, with real agent output.
    assert set(responses) == {"w1"}
    assert responses["w1"]["content"] == "real agent analysis of energy tradeoffs"
    assert "Direct response from" not in responses["w1"]["content"]
    assert responses["w1"]["confidence"] == pytest.approx(0.82)
    assert responses["w1"]["evidence"] == ["s-a", "s-b"]


async def test_supervisor_refinement_invokes_real_agent() -> None:
    executor = TalkHierRoundExecutor()
    request = RefinementRoundRequest(round_number=2)

    with patch(
        "src.agents.factory.AgentFactory.create_agent",
        return_value=_FakeAgent("refined supervisor-guided output"),
    ):
        result = await executor.execute_supervisor_refinement(
            _session(), request, SimpleNamespace(),
        )

    assert result["responses"]["w1"]["content"] == "refined supervisor-guided output"
    assert "Refined response from" not in result["responses"]["w1"]["content"]


async def test_worker_failure_degrades_gracefully() -> None:
    executor = TalkHierRoundExecutor()
    request = RefinementRoundRequest(round_number=1)

    with patch(
        "src.agents.factory.AgentFactory.create_agent",
        side_effect=RuntimeError("agent boom"),
    ):
        responses = await executor.execute_direct_refinement(
            _session(), request, SimpleNamespace(),
        )

    assert responses["w1"]["content"] == ""
    assert responses["w1"]["confidence"] == 0.0
    assert "agent boom" in responses["w1"]["error"]


def test_resolve_agent_type_maps_unknown_to_registry_key() -> None:
    r = TalkHierRoundExecutor._resolve_agent_type
    assert r("literature_review") == "literature_review"
    assert r("analytics_specialist") == "comparative_analysis"
    assert r("citation_bot") == "citation"
    assert r("synthesis_worker") == "synthesis"
    assert r("something_random") == "literature_review"
