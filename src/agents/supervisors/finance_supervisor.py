"""
Finance Supervisor Agent

Coordinates a finance analysis team over figures/assumptions/theses supplied in
the query (no market-data feeds or datasets). Implements a LangGraph workflow
with TalkHier worker messaging.

Coordinates:
- Financial Analysis Agent: statement/ratio analysis and health assessment
- Valuation Agent: DCF / comparables valuation from provided assumptions
- Risk Assessment Agent: qualitative risk analysis of a thesis or portfolio
"""

from typing import Any

from langgraph.graph import END, StateGraph
from structlog import get_logger

from ..communication.talkhier_message import MessageType, TalkHierContent
from ..finance_agents import (
    FinancialAnalysisAgent,
    RiskAssessmentAgent,
    ValuationAgent,
)
from ..models import AgentTask
from .base_supervisor import BaseSupervisor, SupervisionState, WorkerDefinition

logger = get_logger()


class FinanceSupervisor(BaseSupervisor):
    """
    Finance team supervisor implementing an analysis → valuation → risk workflow.

    Uses LangGraph for state management and TalkHier for worker coordination.
    """

    def __init__(
        self,
        supervisor_type: str = "finance",
        domain: str = "finance",
        gemini_service: Any | None = None,
        cache_client: Any | None = None,
        config: dict[str, Any] | None = None,
    ):
        """Initialize finance supervisor.

        Accepts ``supervisor_type`` and ``domain`` to match the
        ``BaseSupervisor`` signature used by the MASR supervisor bridge and
        factory. They default to ``"finance"`` so direct construction (and
        tests) need not pass them.
        """
        super().__init__(
            supervisor_type=supervisor_type,
            domain=domain,
            gemini_service=gemini_service,
            cache_client=cache_client,
            config=config,
        )

        self.analysis_depth = (
            config.get("analysis_depth", "comprehensive") if config else "comprehensive"
        )
        self.include_valuation = (
            config.get("include_valuation", True) if config else True
        )

    def _register_worker_types(self) -> None:
        """Register finance worker types."""
        self.worker_definitions.update(
            {
                "financial_analysis": WorkerDefinition(
                    worker_type="financial_analysis",
                    agent_class=FinancialAnalysisAgent,
                    specialization="Financial statement and ratio analysis",
                    capabilities=[
                        "ratio_analysis",
                        "statement_analysis",
                        "health_assessment",
                    ],
                    required_for=["finance", "financial_analysis"],
                    optimal_for=["statement_review", "ratio_analysis"],
                    avg_execution_time_ms=30000,
                    reliability_score=0.92,
                    quality_score=0.88,
                ),
                "valuation": WorkerDefinition(
                    worker_type="valuation",
                    agent_class=ValuationAgent,
                    specialization="DCF and comparables valuation",
                    capabilities=[
                        "dcf_modeling",
                        "comparables",
                        "sensitivity_analysis",
                    ],
                    required_for=["finance", "valuation"],
                    optimal_for=["dcf", "multiples"],
                    avg_execution_time_ms=35000,
                    reliability_score=0.90,
                    quality_score=0.87,
                ),
                "risk_assessment": WorkerDefinition(
                    worker_type="risk_assessment",
                    agent_class=RiskAssessmentAgent,
                    specialization="Qualitative investment risk analysis",
                    capabilities=[
                        "risk_identification",
                        "mitigation_planning",
                        "risk_rating",
                    ],
                    required_for=["finance", "risk_assessment"],
                    optimal_for=["thesis_review", "portfolio_risk"],
                    avg_execution_time_ms=25000,
                    reliability_score=0.93,
                    quality_score=0.89,
                ),
            }
        )

    def _build_workflow_graph(self) -> None:
        """Build LangGraph workflow for finance supervision."""
        self.workflow_graph = StateGraph(dict)

        self.workflow_graph.add_node(
            "analyze_financials",
            self._create_langgraph_node(
                "analyze_financials", self._analyze_financials_phase
            ),
        )
        self.workflow_graph.add_node(
            "coordinate_valuation",
            self._create_langgraph_node(
                "coordinate_valuation", self._coordinate_valuation_phase
            ),
        )
        self.workflow_graph.add_node(
            "coordinate_risk",
            self._create_langgraph_node("coordinate_risk", self._coordinate_risk_phase),
        )
        self.workflow_graph.add_node(
            "evaluate_quality",
            self._create_langgraph_node(
                "evaluate_quality", self._evaluate_quality_phase
            ),
        )

        self.workflow_graph.set_entry_point("analyze_financials")
        self.workflow_graph.add_edge("analyze_financials", "coordinate_valuation")
        self.workflow_graph.add_edge("coordinate_valuation", "coordinate_risk")
        self.workflow_graph.add_edge("coordinate_risk", "evaluate_quality")
        self.workflow_graph.add_edge("evaluate_quality", END)

        self.workflow_graph = self.workflow_graph.compile()  # type: Any

    async def _coordinate_workers(
        self, state: SupervisionState, task: AgentTask
    ) -> SupervisionState:
        """Finance-specific worker coordination."""
        allocated_workers = await self.allocate_workers(task)
        state.allocated_workers = allocated_workers
        state.context.update(
            {
                "analysis_depth": self.analysis_depth,
                "include_valuation": self.include_valuation,
            }
        )
        return state

    async def _analyze_financials_phase(
        self, langgraph_state: dict[str, Any]
    ) -> dict[str, Any]:
        """Run financial statement/ratio analysis."""
        state = langgraph_state["supervision_state"]
        task = langgraph_state["original_task"]

        logger.info("finance_supervisor_phase_started", phase="financial_analysis")
        state.current_phase = "financial_analysis"
        state = await self._coordinate_workers(state, task)

        if "financial_analysis" in self.active_workers:
            message_content = TalkHierContent(
                content=f"Analyze the financial data for: {state.original_query}",
                background="",
                intermediate_outputs=state.context,
            )
            response = await self.send_talkhier_message(
                "financial_analysis", MessageType.SUPERVISOR_ASSIGNMENT, message_content
            )
            if response:
                state.worker_results["financial_analysis"] = response.talkhier_content

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def _coordinate_valuation_phase(
        self, langgraph_state: dict[str, Any]
    ) -> dict[str, Any]:
        """Run valuation informed by the prior analysis."""
        state = langgraph_state["supervision_state"]
        logger.info("finance_supervisor_phase_started", phase="valuation")
        state.current_phase = "valuation"

        if "valuation" in self.active_workers:
            analysis = state.worker_results.get("financial_analysis")
            message_content = TalkHierContent(
                content=f"Produce a valuation for: {state.original_query}",
                background=analysis.content if hasattr(analysis, "content") else "",
                intermediate_outputs=state.context,
            )
            response = await self.send_talkhier_message(
                "valuation", MessageType.SUPERVISOR_ASSIGNMENT, message_content
            )
            if response:
                state.worker_results["valuation"] = response.talkhier_content

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def _coordinate_risk_phase(
        self, langgraph_state: dict[str, Any]
    ) -> dict[str, Any]:
        """Run risk assessment informed by the analysis and valuation."""
        state = langgraph_state["supervision_state"]
        logger.info("finance_supervisor_phase_started", phase="risk_assessment")
        state.current_phase = "risk_assessment"

        if "risk_assessment" in self.active_workers:
            valuation = state.worker_results.get("valuation")
            message_content = TalkHierContent(
                content=f"Assess the risks for: {state.original_query}",
                background=valuation.content if hasattr(valuation, "content") else "",
                intermediate_outputs=state.context,
            )
            response = await self.send_talkhier_message(
                "risk_assessment", MessageType.SUPERVISOR_ASSIGNMENT, message_content
            )
            if response:
                state.worker_results["risk_assessment"] = response.talkhier_content

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def _evaluate_quality_phase(
        self, langgraph_state: dict[str, Any]
    ) -> dict[str, Any]:
        """Evaluate finance analysis completeness."""
        state = langgraph_state["supervision_state"]
        logger.info("finance_supervisor_phase_started", phase="quality_evaluation")
        state.current_phase = "quality_evaluation"

        # Aggregate worker outputs for verification
        aggregated_content = self._aggregate_worker_content(state)

        # Run verification QA gate
        verification_result = await self._run_verification(aggregated_content)
        state.context["verification"] = verification_result
        state.worker_results["verification"] = verification_result

        # Quality scoring based on completion
        completed_phases = len(state.worker_results) - 1  # exclude verification
        base_score = min(0.9, 0.5 + (completed_phases * 0.1))

        # Reduce score if verification found issues
        if verification_result["verdict"] == "revise":
            state.quality_score = base_score * 0.85
            logger.info(
                "finance_supervisor_verification_flagged_issues",
                base_score=base_score,
                adjusted_score=state.quality_score,
            )
        else:
            state.quality_score = base_score

        state.consensus_score = state.quality_score

        langgraph_state["supervision_state"] = state
        return langgraph_state

    def _aggregate_worker_content(self, state: SupervisionState) -> str:
        """Aggregate worker outputs into a single text for verification."""
        parts = []
        for worker_type in ["financial_analysis", "valuation", "risk_assessment"]:
            result = state.worker_results.get(worker_type)
            if result:
                content = result.get("content", "") if isinstance(result, dict) else ""
                if hasattr(result, "content"):
                    content = result.content
                if content:
                    parts.append(f"{worker_type.upper()}: {content}")
        return "\n\n".join(parts) if parts else ""


__all__ = ["FinanceSupervisor"]
