"""Characterization tests for research execution coordination extraction."""

from typing import Any

import pytest

from src.agents.communication.talkhier_message import (
    MessageType,
    TalkHierContent,
    TalkHierMessage,
)
from src.agents.supervisors.base_supervisor import SupervisionState
from src.agents.supervisors.research_execution_coordinator import (
    ResearchExecutionCoordinator,
)
from src.agents.supervisors.research_supervisor import ResearchSupervisor


class RecordingSender:
    """Records TalkHier assignments and returns worker-shaped responses."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, MessageType, TalkHierContent | str]] = []

    async def send(
        self,
        target_worker: str,
        message_type: MessageType,
        content: TalkHierContent | str,
        context: dict[str, Any] | None = None,
    ) -> TalkHierMessage:
        self.calls.append((target_worker, message_type, content))
        return TalkHierMessage(
            from_agent=target_worker,
            to_agent="research_supervisor",
            message_type=MessageType.WORKER_REPORT,
            content=TalkHierContent(
                content=f"{target_worker} result",
                intermediate_outputs={"worker": target_worker},
                confidence_score=0.8,
            ),
        )


def build_langgraph_state() -> dict[str, Any]:
    state = SupervisionState(
        task_id="task-1",
        original_query="How does AI affect learning?",
        allocated_workers=[
            "literature_review",
            "methodology",
            "comparative_analysis",
            "synthesis",
            "citation",
        ],
        worker_tasks={
            "literature_review": {"query": "How does AI affect learning?"},
            "methodology": {"research_question": "How does AI affect learning?"},
            "comparative_analysis": {"query": "How does AI affect learning?"},
            "synthesis": {"synthesis_focus": "comprehensive_integration"},
            "citation": {"style": "MLA"},
        },
    )
    return {"supervision_state": state}


@pytest.mark.asyncio
async def test_execution_coordinator_preserves_worker_assignment_payloads() -> None:
    sender = RecordingSender()
    coordinator = ResearchExecutionCoordinator(sender.send, citation_style="MLA")
    langgraph_state = build_langgraph_state()

    await coordinator.coordinate_literature(langgraph_state)
    await coordinator.coordinate_methodology(langgraph_state)
    await coordinator.coordinate_analysis(langgraph_state)
    await coordinator.coordinate_synthesis(langgraph_state)
    await coordinator.coordinate_citation(langgraph_state)

    state = langgraph_state["supervision_state"]
    assert state.current_phase == "citation"
    assert list(state.worker_results) == [
        "literature_review",
        "methodology",
        "comparative_analysis",
        "synthesis",
        "citation",
    ]
    assert [call[0] for call in sender.calls] == [
        "literature_review",
        "methodology",
        "comparative_analysis",
        "synthesis",
        "citation",
    ]

    literature_content = sender.calls[0][2]
    methodology_content = sender.calls[1][2]
    analysis_content = sender.calls[2][2]
    synthesis_content = sender.calls[3][2]
    citation_content = sender.calls[4][2]

    assert isinstance(literature_content, TalkHierContent)
    assert literature_content.content == "Conduct systematic literature review"
    assert literature_content.intermediate_outputs == {
        "query": "How does AI affect learning?"
    }

    assert isinstance(methodology_content, TalkHierContent)
    assert (
        methodology_content.intermediate_outputs["literature_context"]
        is state.worker_results["literature_review"]
    )

    assert isinstance(analysis_content, TalkHierContent)
    assert (
        analysis_content.intermediate_outputs["methodology_framework"]
        is state.worker_results["methodology"]
    )

    assert isinstance(synthesis_content, TalkHierContent)
    assert synthesis_content.intermediate_outputs["agent_outputs"] == {
        "literature_review": {"worker": "literature_review"},
        "methodology": {"worker": "methodology"},
        "comparative_analysis": {"worker": "comparative_analysis"},
    }

    assert isinstance(citation_content, TalkHierContent)
    assert citation_content.intermediate_outputs == {"sources": [], "style": "MLA"}


def test_execution_coordinator_extracts_sources_for_citation() -> None:
    sender = RecordingSender()
    coordinator = ResearchExecutionCoordinator(sender.send, citation_style="APA")
    sources = [{"title": "A", "authors": ["B"], "year": 2024}]

    worker_results = {
        "literature_review": TalkHierContent(
            intermediate_outputs={"sources_found": sources}
        )
    }

    assert coordinator._extract_sources(worker_results) == sources


def test_research_supervisor_initializes_execution_coordinator() -> None:
    supervisor = ResearchSupervisor(config={"citation_style": "Chicago"})

    assert isinstance(supervisor.execution_coordinator, ResearchExecutionCoordinator)
    assert supervisor.execution_coordinator.citation_style == "Chicago"
