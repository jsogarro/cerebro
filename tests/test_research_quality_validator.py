"""Characterization tests for research quality validation extraction."""

from typing import Any

import pytest

from src.agents.communication.talkhier_message import TalkHierContent
from src.agents.supervisors.base_supervisor import SupervisionState
from src.agents.supervisors.research_quality_validator import ResearchQualityValidator
from src.agents.supervisors.research_supervisor import ResearchSupervisor


class ConsensusScore:
    def __init__(self, overall_score: float, evidence_quality: float) -> None:
        self.overall_score = overall_score
        self.evidence_quality = evidence_quality


class ConsensusBuilder:
    def __init__(self) -> None:
        self.messages: list[Any] = []

    async def evaluate_consensus(self, messages: list[Any]) -> ConsensusScore:
        self.messages = messages
        return ConsensusScore(overall_score=0.91, evidence_quality=0.84)


class CommunicationProtocol:
    def __init__(self) -> None:
        self.consensus_builder = ConsensusBuilder()


def build_validator(
    communication_protocol: CommunicationProtocol | None = None,
) -> ResearchQualityValidator:
    return ResearchQualityValidator(
        gemini_service=None,
        communication_protocol=communication_protocol or CommunicationProtocol(),
        get_agent_type=lambda: "research_supervisor",
        quality_threshold=0.85,
    )


def test_quality_validator_accepts_high_scoring_paper() -> None:
    validator = build_validator()
    state = SupervisionState(
        worker_results={
            "draft_paper": TalkHierContent(
                intermediate_outputs={"revision_count": 0}
            ),
            "graduate_review": TalkHierContent(
                intermediate_outputs={"overall_score": 9.1}
            ),
        }
    )

    assert validator.should_revise_paper({"supervision_state": state}) == "accept"


def test_quality_validator_revises_low_scoring_paper_under_revision_limit() -> None:
    validator = build_validator()
    state = SupervisionState(
        worker_results={
            "draft_paper": TalkHierContent(
                intermediate_outputs={"revision_count": 2}
            ),
            "graduate_review": TalkHierContent(
                intermediate_outputs={"overall_score": 8.9}
            ),
        }
    )

    assert validator.should_revise_paper({"supervision_state": state}) == "revise"


@pytest.mark.asyncio
async def test_quality_validator_evaluates_consensus_from_worker_results() -> None:
    communication_protocol = CommunicationProtocol()
    validator = build_validator(communication_protocol)
    state = SupervisionState(
        task_id="task-1",
        worker_results={
            "draft_paper": TalkHierContent(
                intermediate_outputs={"title": "Paper", "revision_count": 0}
            ),
            "synthesis": TalkHierContent(
                content="Synthesis",
                intermediate_outputs={"findings": ["A"]},
            ),
        },
    )

    await validator.evaluate_consensus({"supervision_state": state})

    assert state.current_phase == "consensus_evaluation"
    assert state.context["final_paper"] == {"title": "Paper", "revision_count": 0}
    assert state.consensus_score == 0.91
    assert state.quality_score == 0.84
    assert state.refinement_round == 2
    assert [message.from_agent for message in communication_protocol.consensus_builder.messages] == [
        "draft_paper",
        "synthesis",
    ]


@pytest.mark.asyncio
async def test_quality_validator_builds_research_quality_assessment() -> None:
    validator = build_validator()
    state = SupervisionState(
        quality_score=0.82,
        consensus_score=0.91,
        worker_results={
            "literature_review": TalkHierContent(confidence_score=0.7),
            "methodology": TalkHierContent(confidence_score=0.8),
            "synthesis": TalkHierContent(confidence_score=0.9),
        },
    )

    assessment = await validator.get_research_quality_assessment(state)

    assert assessment["overall_quality"] == 0.82
    assert assessment["consensus_score"] == 0.91
    assert assessment["research_completeness"] == 1.0
    assert assessment["worker_contributions"]["synthesis"] == {
        "confidence": 0.9,
        "contribution_quality": 0.9,
    }


def test_research_supervisor_initializes_quality_validator() -> None:
    supervisor = ResearchSupervisor(config={"quality_threshold": 0.9})

    assert isinstance(supervisor.quality_validator, ResearchQualityValidator)
    assert supervisor.quality_validator.quality_threshold == 0.9
