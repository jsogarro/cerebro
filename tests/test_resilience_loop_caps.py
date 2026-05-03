"""Resilience characterization tests for bounded workflow iteration."""

import pytest

from src.orchestration.graph_builder import GraphConfig, ResearchGraphBuilder
from src.orchestration.state import (
    MaxIterationsExceeded,
    ResearchState,
    WorkflowPhase,
)


def build_state() -> ResearchState:
    return ResearchState(
        project_id="project-1",
        workflow_id="workflow-1",
        query="bounded workflow",
        domains=["general"],
    )


def test_research_state_tracks_iteration_count_and_raises_at_limit() -> None:
    state = build_state()

    state.increment_iteration(max_iterations=2, node_name="initialization")
    state.increment_iteration(max_iterations=2, node_name="query_analysis")

    assert state.iteration_count == 2
    with pytest.raises(MaxIterationsExceeded, match="exceeded max_iterations=2"):
        state.increment_iteration(max_iterations=2, node_name="plan_generation")


def test_graph_builder_enforces_max_iterations_before_node_execution() -> None:
    executed: list[str] = []

    def handler(state: ResearchState) -> ResearchState:
        executed.append("handler")
        return state

    builder = ResearchGraphBuilder(GraphConfig(max_iterations=1))
    wrapped = builder._wrap_handler(
        builder.add_node(
            "initialization",
            handler,
            WorkflowPhase.INITIALIZATION,
        ).nodes["initialization"]
    )

    state = build_state()
    assert wrapped(state) is state
    assert state.iteration_count == 1
    assert executed == ["handler"]

    with pytest.raises(MaxIterationsExceeded):
        wrapped(state)

    assert executed == ["handler"]
