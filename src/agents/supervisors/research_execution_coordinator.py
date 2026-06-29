"""Worker execution coordination for the research supervisor."""

from collections.abc import Awaitable, Callable
from typing import Any

from structlog import get_logger

from ..communication.talkhier_message import (
    MessageType,
    TalkHierContent,
    TalkHierMessage,
)

logger = get_logger()

SendTalkHierMessage = Callable[
    [str, MessageType, TalkHierContent | str, dict[str, Any] | None],
    Awaitable[TalkHierMessage | None],
]


class ResearchExecutionCoordinator:
    """Coordinates research worker execution phases."""

    def __init__(
        self,
        send_talkhier_message: SendTalkHierMessage,
        citation_style: str,
    ) -> None:
        self.send_talkhier_message = send_talkhier_message
        self.citation_style = citation_style

    async def coordinate_literature(
        self,
        langgraph_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Coordinate literature review worker."""
        state = langgraph_state["supervision_state"]

        logger.info("research_phase_started", phase="literature_review")
        state.current_phase = "literature_review"

        if "literature_review" in state.allocated_workers:
            literature_task = state.worker_tasks["literature_review"]

            response = await self.send_talkhier_message(
                "literature_review",
                MessageType.SUPERVISOR_ASSIGNMENT,
                TalkHierContent(
                    content="Conduct systematic literature review",
                    background=f"Research supervision for: {state.original_query}",
                    intermediate_outputs=literature_task,
                ),
                None,
            )

            if response:
                state.worker_results["literature_review"] = response.talkhier_content

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def coordinate_methodology(
        self,
        langgraph_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Coordinate methodology worker."""
        state = langgraph_state["supervision_state"]

        logger.info("research_phase_started", phase="methodology")
        state.current_phase = "methodology"

        if "methodology" in state.allocated_workers:
            methodology_task = state.worker_tasks["methodology"]

            if "literature_review" in state.worker_results:
                methodology_task["literature_context"] = state.worker_results[
                    "literature_review"
                ]

            response = await self.send_talkhier_message(
                "methodology",
                MessageType.SUPERVISOR_ASSIGNMENT,
                TalkHierContent(
                    content="Design appropriate research methodology",
                    background=f"Methodology design for: {state.original_query}",
                    intermediate_outputs=methodology_task,
                ),
                None,
            )

            if response:
                state.worker_results["methodology"] = response.talkhier_content

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def coordinate_analysis(
        self,
        langgraph_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Coordinate comparative analysis worker."""
        state = langgraph_state["supervision_state"]

        logger.info("research_phase_started", phase="comparative_analysis")
        state.current_phase = "comparative_analysis"

        if "comparative_analysis" in state.allocated_workers:
            analysis_task = state.worker_tasks["comparative_analysis"]

            analysis_task["literature_findings"] = state.worker_results.get(
                "literature_review"
            )
            analysis_task["methodology_framework"] = state.worker_results.get(
                "methodology"
            )

            response = await self.send_talkhier_message(
                "comparative_analysis",
                MessageType.SUPERVISOR_ASSIGNMENT,
                TalkHierContent(
                    content="Conduct comparative analysis of approaches",
                    background=f"Analysis coordination for: {state.original_query}",
                    intermediate_outputs=analysis_task,
                ),
                None,
            )

            if response:
                state.worker_results["comparative_analysis"] = response.talkhier_content

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def coordinate_synthesis(
        self,
        langgraph_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Coordinate synthesis worker."""
        state = langgraph_state["supervision_state"]

        logger.info("research_phase_started", phase="synthesis")
        state.current_phase = "synthesis"

        if "synthesis" in state.allocated_workers:
            agent_outputs = self._build_agent_outputs(state.worker_results)

            logger.info(
                "research_synthesis_inputs_prepared",
                agent_count=len(agent_outputs),
                agent_types=list(agent_outputs.keys()),
            )

            response = await self.send_talkhier_message(
                "synthesis",
                MessageType.SUPERVISOR_ASSIGNMENT,
                TalkHierContent(
                    content="Synthesize all research findings",
                    background=f"Synthesis coordination for: {state.original_query}",
                    intermediate_outputs={
                        "agent_outputs": agent_outputs,
                        "synthesis_focus": "comprehensive",
                    },
                ),
                None,
            )

            if response:
                state.worker_results["synthesis"] = response.talkhier_content

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def coordinate_citation(
        self,
        langgraph_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Coordinate citation worker."""
        state = langgraph_state["supervision_state"]

        logger.info("research_phase_started", phase="citation")
        state.current_phase = "citation"

        if "citation" in state.allocated_workers:
            sources = self._extract_sources(state.worker_results)

            response = await self.send_talkhier_message(
                "citation",
                MessageType.SUPERVISOR_ASSIGNMENT,
                TalkHierContent(
                    content="Format citations and verify sources",
                    background=f"Citation coordination for: {state.original_query}",
                    intermediate_outputs={
                        "sources": sources,
                        "style": self.citation_style,
                    },
                ),
                None,
            )

            if response:
                state.worker_results["citation"] = response.talkhier_content

        langgraph_state["supervision_state"] = state
        return langgraph_state

    def _build_agent_outputs(
        self,
        worker_results: dict[str, Any],
    ) -> dict[str, Any]:
        """Build synthesis input from available worker results."""
        agent_outputs = {}
        for worker_type, result in worker_results.items():
            if result and hasattr(result, "intermediate_outputs"):
                agent_outputs[worker_type] = (
                    result.intermediate_outputs
                    if isinstance(result.intermediate_outputs, dict)
                    else {"findings": result.content}
                )

        return agent_outputs

    def _extract_sources(self, worker_results: dict[str, Any]) -> list[Any]:
        """Extract literature sources for citation coordination."""
        if "literature_review" not in worker_results:
            return []

        lit_result = worker_results["literature_review"]
        if not hasattr(lit_result, "intermediate_outputs") or not isinstance(
            lit_result.intermediate_outputs, dict
        ):
            return []

        return (
            lit_result.intermediate_outputs.get("sources_found")
            or lit_result.intermediate_outputs.get("sources")
            or []
        )
