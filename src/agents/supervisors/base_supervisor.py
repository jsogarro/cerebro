"""
Base Supervisor Agent

Abstract base class for all supervisor agents implementing LangGraph orchestration
with TalkHier communication protocols. Provides the foundation for coordinating
teams of specialized worker agents.

Key Features:
- LangGraph state management and workflow orchestration
- TalkHier multi-round refinement protocols
- Dynamic worker allocation and coordination
- Quality assurance and consensus building
- Integration with MASR routing decisions
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from langgraph.graph import StateGraph
from structlog import get_logger

from src.core.types import SupervisionStatsDict

from ...prompts.manager import get_prompt_manager
from ..base import BaseAgent
from ..communication.communication_protocol import (
    CommunicationProtocol,
)
from ..communication.talkhier_message import (
    HierarchyMetadata,
    MessageType,
    TalkHierContent,
    TalkHierMessage,
)
from ..models import AgentResult, AgentTask

logger = get_logger()

# Bounded revision loop configuration
MAX_VERIFICATION_REVISION_ROUNDS = (
    2  # Total verification attempts (initial + 1 revision)
)


class SupervisionMode(Enum):
    """Modes of supervision for different scenarios."""

    SEQUENTIAL = "sequential"  # Workers execute in sequence
    PARALLEL = "parallel"  # Workers execute simultaneously
    HYBRID = "hybrid"  # Mix of sequential and parallel
    ADAPTIVE = "adaptive"  # Dynamically determined based on task


class WorkerAllocation(Enum):
    """Worker allocation strategies."""

    ALL_WORKERS = "all_workers"  # Use all available workers
    OPTIMAL_SET = "optimal_set"  # Use optimal worker subset
    MINIMAL_VIABLE = "minimal_viable"  # Use minimum workers needed
    DYNAMIC = "dynamic"  # Dynamically allocate based on progress


@dataclass
class SupervisionState:
    """State for supervisor agent workflows."""

    # Task information
    task_id: str = ""
    original_query: str = ""
    task_type: str = ""
    domain: str = ""

    # Worker management
    allocated_workers: list[str] = field(default_factory=list)
    worker_tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    worker_results: dict[str, Any] = field(default_factory=dict)

    # Coordination state
    current_phase: str = "initialization"
    supervision_mode: SupervisionMode = SupervisionMode.PARALLEL

    # TalkHier refinement
    refinement_round: int = 1
    consensus_score: float = 0.0
    quality_score: float = 0.0

    # Progress tracking
    started_at: datetime = field(default_factory=datetime.now)
    phase_history: list[dict[str, Any]] = field(default_factory=list)

    # Context and metadata
    context: dict[str, Any] = field(default_factory=dict)
    supervision_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkerDefinition:
    """Definition of a worker agent type."""

    worker_type: str
    agent_class: type[BaseAgent]
    specialization: str
    capabilities: list[str] = field(default_factory=list)

    # Allocation preferences
    required_for: list[str] = field(
        default_factory=list
    )  # Always required for these tasks
    optimal_for: list[str] = field(default_factory=list)  # Optimal for these tasks

    # Performance characteristics
    avg_execution_time_ms: int = 30000
    reliability_score: float = 0.95
    quality_score: float = 0.85


class BaseSupervisor(BaseAgent, ABC):
    """
    Abstract base supervisor agent implementing LangGraph + TalkHier patterns.

    Provides common supervision functionality:
    - Worker team coordination
    - LangGraph workflow management
    - TalkHier refinement protocols
    - Quality assurance and consensus building
    """

    def __init__(
        self,
        supervisor_type: str,
        domain: str,
        gemini_service: Any | None = None,
        cache_client: Any | None = None,
        config: dict[str, Any] | None = None,
    ):
        """Initialize base supervisor."""
        # Set supervisor_type before super().__init__ because
        # BaseAgent.__init__ calls get_agent_type() which needs it.
        self.supervisor_type = supervisor_type
        self.domain = domain

        super().__init__(gemini_service, cache_client, config)

        # Worker management
        self.worker_definitions: dict[str, WorkerDefinition] = {}
        self.active_workers: dict[str, BaseAgent] = {}
        self.worker_allocation_strategy = WorkerAllocation.OPTIMAL_SET

        # LangGraph components
        self.workflow_graph: StateGraph[Any, Any, Any, Any] | None = None
        self.tool_executor: Any | None = None

        # Communication protocol
        self.communication_protocol = CommunicationProtocol(
            config.get("communication_protocol", {}) if config else {}
        )

        # Supervision configuration
        self.max_workers = config.get("max_workers", 10) if config else 10
        self.default_timeout = config.get("default_timeout", 300) if config else 300
        self.quality_threshold = (
            config.get("quality_threshold", 0.85) if config else 0.85
        )
        self.max_parallel_workers = (
            config.get("max_parallel_workers", 5) if config else 5
        )

        # Performance tracking
        self.supervised_tasks = 0
        self.successful_supervisions = 0
        self.average_supervision_time = 0.0

        # MAST (Multi-Agent System failure Taxonomy) labeling
        from src.qa.mast import MASTLabeler

        self.mast_labeler = MASTLabeler(
            max_revision_rounds=MAX_VERIFICATION_REVISION_ROUNDS
        )

        # Initialize supervisor-specific components
        self._register_worker_types()
        self._build_workflow_graph()

    @abstractmethod
    def _register_worker_types(self) -> None:
        """Register worker types for this supervisor."""
        pass

    @abstractmethod
    def _build_workflow_graph(self) -> None:
        """Build LangGraph workflow for this supervisor."""
        pass

    @abstractmethod
    async def _coordinate_workers(
        self, state: SupervisionState, task: AgentTask
    ) -> SupervisionState:
        """Coordinate worker execution (supervisor-specific logic)."""
        pass

    def get_agent_type(self) -> str:
        """Return supervisor agent type."""
        return f"{self.supervisor_type}_supervisor"

    async def execute(self, task: AgentTask) -> AgentResult:
        """
        Execute supervision task using LangGraph + TalkHier protocol.

        Args:
            task: Supervision task to execute

        Returns:
            AgentResult with coordinated team output
        """

        start_time = datetime.now()
        self.supervised_tasks += 1

        try:
            # Instantiate workers from definitions if not already active
            if not self.active_workers:
                for worker_type, worker_def in self.worker_definitions.items():
                    try:
                        worker = worker_def.agent_class(
                            gemini_service=self.gemini_service,
                            cache_client=self.cache_client,
                            config=self.config,
                        )
                        self.active_workers[worker_type] = worker
                        logger.info(
                            "supervisor_worker_instantiated", worker_type=worker_type
                        )
                    except Exception as e:
                        logger.warning(
                            "supervisor_worker_instantiation_failed",
                            worker_type=worker_type,
                            error=str(e),
                        )

            # Initialize supervision state
            state = SupervisionState(
                task_id=task.id,
                original_query=str(task.input_data.get("query", "")),
                task_type=task.agent_type,
                domain=self.domain,
                supervision_mode=self._determine_supervision_mode(task),
                context=task.input_data,
            )

            # Execute LangGraph workflow
            final_state = await self._execute_supervision_workflow(state, task)

            # Build final result
            result = await self._build_supervision_result(final_state, task, start_time)

            if result.status == "completed":
                self.successful_supervisions += 1

            # Update performance metrics
            execution_time = (datetime.now() - start_time).total_seconds()
            self.average_supervision_time = (
                self.average_supervision_time * (self.supervised_tasks - 1)
                + execution_time
            ) / self.supervised_tasks

            return result

        except Exception as e:
            logger.error(
                "supervision_failed",
                task_id=task.id,
                supervisor_type=self.supervisor_type,
                error=str(e),
            )
            return self.handle_error(task, e)

    async def validate_result(self, result: AgentResult) -> bool:
        """Validate supervision result."""

        if result.status != "completed":
            return False

        # Check if quality threshold was met
        quality_score = result.output.get("supervision_quality", {}).get(
            "overall_quality", 0.0
        )

        return bool(quality_score >= self.quality_threshold)

    def register_worker(
        self, worker_def: WorkerDefinition, worker_instance: BaseAgent
    ) -> None:
        """Register a worker agent with this supervisor."""

        self.worker_definitions[worker_def.worker_type] = worker_def
        self.active_workers[worker_def.worker_type] = worker_instance

        logger.info(
            "supervisor_worker_registered",
            worker_type=worker_def.worker_type,
            supervisor_type=self.supervisor_type,
        )

    async def allocate_workers(self, task: AgentTask) -> list[str]:
        """
        Allocate optimal workers for a task.

        Args:
            task: Task to allocate workers for

        Returns:
            List of worker types to use
        """

        task_type = task.agent_type
        complexity = task.input_data.get("complexity_score", 0.5)

        if self.worker_allocation_strategy == WorkerAllocation.ALL_WORKERS:
            return list(self.worker_definitions.keys())

        elif self.worker_allocation_strategy == WorkerAllocation.OPTIMAL_SET:
            # Select workers optimal for this task type
            optimal_workers = []

            for worker_type, worker_def in self.worker_definitions.items():
                if (
                    task_type in worker_def.optimal_for
                    or task_type in worker_def.required_for
                ):
                    optimal_workers.append(worker_type)

            # Ensure minimum viable team
            if not optimal_workers:
                optimal_workers = list(self.worker_definitions.keys())[:3]

            return optimal_workers

        elif self.worker_allocation_strategy == WorkerAllocation.MINIMAL_VIABLE:
            # Select minimum required workers
            required_workers = []

            for worker_type, worker_def in self.worker_definitions.items():
                if task_type in worker_def.required_for:
                    required_workers.append(worker_type)

            # Ensure at least one worker
            if not required_workers and self.worker_definitions:
                required_workers = [next(iter(self.worker_definitions.keys()))]

            return required_workers

        else:  # DYNAMIC
            # Dynamic allocation based on task complexity
            worker_count = min(
                int(complexity * len(self.worker_definitions)) + 1,
                len(self.worker_definitions),
            )

            # Select highest-rated workers for task
            workers_by_score = sorted(
                self.worker_definitions.items(),
                key=lambda x: x[1].quality_score,
                reverse=True,
            )

            return [worker[0] for worker in workers_by_score[:worker_count]]

    def _determine_supervision_mode(self, task: AgentTask) -> SupervisionMode:
        """Determine optimal supervision mode for task."""

        complexity = task.input_data.get("complexity_score", 0.5)
        dependencies = task.input_data.get("dependencies", [])

        if dependencies:
            return SupervisionMode.SEQUENTIAL
        elif complexity > 0.8:
            return SupervisionMode.HYBRID
        else:
            return SupervisionMode.PARALLEL

    async def _execute_supervision_workflow(
        self, state: SupervisionState, task: AgentTask
    ) -> SupervisionState:
        """Execute supervision workflow using LangGraph."""

        if not self.workflow_graph:
            raise ValueError("Workflow graph not initialized")

        # Convert state to LangGraph format
        langgraph_state = {
            "supervision_state": state,
            "original_task": task,
            "supervisor_type": self.supervisor_type,
        }

        # Execute workflow
        if not self.workflow_graph:
            return state

        if hasattr(self.workflow_graph, "ainvoke"):
            final_langgraph_state = await self.workflow_graph.ainvoke(langgraph_state)

            # Extract final state
            final_state: SupervisionState = final_langgraph_state["supervision_state"]

            return final_state

        return state

    async def _build_supervision_result(
        self, state: SupervisionState, task: AgentTask, start_time: datetime
    ) -> AgentResult:
        """Build final supervision result."""

        execution_time = (datetime.now() - start_time).total_seconds()

        # Aggregate worker outputs
        aggregated_output = {
            "supervision_type": self.supervisor_type,
            "domain": self.domain,
            "worker_results": state.worker_results,
            "supervision_quality": {
                "overall_quality": state.quality_score,
                "consensus_achieved": state.consensus_score >= self.quality_threshold,
                "final_consensus_score": state.consensus_score,
                "refinement_rounds": state.refinement_round,
            },
            "coordination_metadata": {
                "supervision_mode": state.supervision_mode.value,
                "workers_used": state.allocated_workers,
                "total_phases": len(state.phase_history),
                "execution_time_seconds": execution_time,
            },
        }

        # Determine result status
        status = (
            "completed"
            if state.consensus_score >= self.quality_threshold
            else "partial"
        )
        confidence = min(state.consensus_score, state.quality_score)

        # Build base metadata
        result_metadata = {
            "agent_type": self.get_agent_type(),
            "supervision_mode": state.supervision_mode.value,
            "workers_coordinated": len(state.allocated_workers),
            "refinement_rounds": state.refinement_round,
        }

        # Wire MAST labels from the verification QA gate into
        # supervision_metadata (Phase S). Supervisors store the
        # _run_verification result under worker_results["verification"].
        verification = state.worker_results.get("verification")
        if isinstance(verification, dict):
            self._store_mast_labels_in_state(state, verification)

        # Include MAST failure metadata if present (Phase S labeling)
        if "mast_failures" in state.supervision_metadata:
            result_metadata["mast_failures"] = state.supervision_metadata[
                "mast_failures"
            ]
            result_metadata["mast_confidence"] = state.supervision_metadata.get(
                "mast_confidence", 0.0
            )
            result_metadata["verification_rounds"] = state.supervision_metadata.get(
                "verification_rounds", 0
            )
            if "revision_history" in state.supervision_metadata:
                result_metadata["revision_history"] = state.supervision_metadata[
                    "revision_history"
                ]

        return AgentResult(
            task_id=task.id,
            status=status,
            output=aggregated_output,
            confidence=confidence,
            execution_time=execution_time,
            metadata=result_metadata,
        )

    async def send_talkhier_message(
        self,
        target_worker: str,
        message_type: MessageType,
        content: TalkHierContent | str,
        context: dict[str, Any] | None = None,
    ) -> TalkHierMessage | None:
        """Send TalkHier message to worker agent."""

        worker = self.active_workers.get(target_worker)
        if not worker:
            logger.error("supervisor_worker_not_found", worker_type=target_worker)
            return None

        # Create TalkHier message
        if isinstance(content, str):
            talkhier_content = TalkHierContent(content=content)
        else:
            talkhier_content = content

        message = TalkHierMessage(
            from_agent=self.get_agent_type(),
            to_agent=target_worker,
            message_type=message_type,
            content=talkhier_content,
            hierarchy_metadata=HierarchyMetadata(
                hierarchy_level=2,  # Supervisor level
                worker_ids=list(self.active_workers.keys()),
            ),
            context=context or {},
        )

        # Send through communication protocol
        try:
            response = await self.communication_protocol._send_message_to_agent(
                worker, message
            )
            return response

        except Exception as e:
            logger.error(
                "supervisor_worker_message_send_failed",
                worker_type=target_worker,
                error=str(e),
            )
            return None

    async def broadcast_to_workers(
        self,
        message_type: MessageType,
        content: TalkHierContent | str,
        worker_subset: list[str] | None = None,
    ) -> list[TalkHierMessage]:
        """Broadcast message to all or subset of workers."""

        target_workers = worker_subset or list(self.active_workers.keys())
        responses = []

        # Send to each worker
        for worker_type in target_workers:
            response = await self.send_talkhier_message(
                worker_type, message_type, content
            )
            if response:
                responses.append(response)

        return responses

    async def coordinate_refinement_round(
        self, state: SupervisionState, round_number: int
    ) -> SupervisionState:
        """Coordinate a single refinement round."""

        logger.info(
            "supervisor_refinement_round_started",
            round_number=round_number,
            supervisor_type=self.supervisor_type,
        )

        # Get refinement prompt
        try:
            prompt_manager = await get_prompt_manager()

            refinement_variables = {
                "round": round_number,
                "previous_outputs": state.worker_results,
                "consensus_score": state.consensus_score,
                "target_threshold": self.quality_threshold,
            }

            refinement_prompt = await prompt_manager.get_prompt(
                f"refinement/round_{round_number}", refinement_variables
            )

            # Create refinement message
            refinement_content = TalkHierContent(
                content=refinement_prompt,
                background=f"Round {round_number} refinement coordination",
                intermediate_outputs={
                    "previous_consensus": state.consensus_score,
                    "target_quality": self.quality_threshold,
                    "worker_count": len(state.allocated_workers),
                },
            )

            # Broadcast refinement request to workers
            responses = await self.broadcast_to_workers(
                MessageType.REFINEMENT_REQUEST,
                refinement_content,
                state.allocated_workers,
            )

            # Update state with refinement round results
            state.refinement_round = round_number

            # Evaluate consensus for this round
            if responses:
                consensus_score = await self.communication_protocol.consensus_builder.evaluate_consensus(
                    responses
                )
                state.consensus_score = consensus_score.overall_score
                state.quality_score = consensus_score.evidence_quality

            # Track phase history
            state.phase_history.append(
                {
                    "round": round_number,
                    "consensus_score": state.consensus_score,
                    "quality_score": state.quality_score,
                    "responses_received": len(responses),
                    "timestamp": datetime.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(
                "supervisor_refinement_round_failed",
                round_number=round_number,
                supervisor_type=self.supervisor_type,
                error=str(e),
            )

        return state

    async def get_supervision_stats(self) -> SupervisionStatsDict:
        """Get supervisor performance statistics."""

        success_rate = self.successful_supervisions / max(self.supervised_tasks, 1)

        return {
            "supervisor": {
                "type": self.supervisor_type,
                "domain": self.domain,
                "supervised_tasks": self.supervised_tasks,
                "successful_supervisions": self.successful_supervisions,
                "success_rate": success_rate,
                "average_supervision_time": self.average_supervision_time,
            },
            "workers": {
                "total_worker_types": len(self.worker_definitions),
                "active_workers": len(self.active_workers),
                "worker_types": list(self.worker_definitions.keys()),
            },
            "communication": dict(
                await self.communication_protocol.get_protocol_stats()
            ),
        }

    def _create_langgraph_node(self, node_name: str, node_func: Any) -> Any:
        """Create LangGraph node with error handling."""

        async def wrapped_node(state: dict[str, Any]) -> dict[str, Any]:
            try:
                result = await node_func(state)
                return dict(result) if result else state
            except Exception as e:
                logger.error(
                    "supervisor_langgraph_node_failed",
                    node_name=node_name,
                    error=str(e),
                )
                # Return state with error information
                state["error"] = str(e)
                state["failed_node"] = node_name
                return state

        return wrapped_node

    async def _run_verification(self, content: str) -> dict[str, Any]:
        """
        Run VerificationAgent on aggregated content as a QA gate.

        Args:
            content: The aggregated worker output to verify

        Returns:
            dict with keys:
                - verdict: "pass" or "revise"
                - report: full verification report text
                - issues: list of specific issues (empty if pass)
                - mast_labels: list of detected MAST failure mode codes (Phase S)
                - mast_confidence: max confidence across detected labels
        """
        # Degrade gracefully if content is empty or missing.
        # Check explicitly before creating the verification task to avoid
        # "Query cannot be empty" errors when VerificationAgent's execute
        # method tries to extract query/content from empty input_data.
        if not content or not content.strip():
            logger.info(
                "supervisor_verification_skipped_empty_content",
                supervisor_type=self.supervisor_type,
                reason="no_aggregated_content_to_verify",
            )
            mast_labels, mast_confidence = self._label_qa_gate("pass", [], "")
            return {
                "verdict": "pass",
                "report": "No content to verify.",
                "issues": [],
                "mast_labels": mast_labels,
                "mast_confidence": mast_confidence,
            }

        try:
            # Import lazily to avoid circular dependency
            from ..factory import AgentFactory

            # Create verification agent with the same gemini_service
            verification_agent = AgentFactory.create_agent(
                "verification",
                {
                    "gemini_service": self.gemini_service,
                    "cache_client": self.cache_client,
                },
            )

            # Build task for verification
            import uuid

            from ..models import AgentTask

            verification_task = AgentTask(
                id=str(uuid.uuid4()),
                agent_type="verification",
                input_data={"content": content},
            )

            # Execute verification
            verification_result = await verification_agent.execute(verification_task)

            # Parse the verdict from the output
            report_text = verification_result.output.get("content", "")
            verdict_line = next(
                (
                    line
                    for line in report_text.split("\n")
                    if line.startswith("VERDICT:")
                ),
                "",
            )
            verdict = "pass"
            if "REVISE" in verdict_line.upper():
                verdict = "revise"
            elif "PASS" in verdict_line.upper():
                verdict = "pass"

            # Extract issues list from report
            issues = self._extract_issues_from_report(report_text)

            logger.info(
                "supervisor_verification_completed",
                supervisor_type=self.supervisor_type,
                verdict=verdict,
                issue_count=len(issues),
            )

            mast_labels, mast_confidence = self._label_qa_gate(verdict, issues, content)
            return {
                "verdict": verdict,
                "report": report_text,
                "issues": issues,
                "mast_labels": mast_labels,
                "mast_confidence": mast_confidence,
            }

        except Exception as e:
            # Degrade gracefully on error — don't break the workflow
            logger.error(
                "supervisor_verification_failed",
                supervisor_type=self.supervisor_type,
                error=str(e),
            )
            mast_labels, mast_confidence = self._label_qa_gate("pass", [], content)
            return {
                "verdict": "pass",  # neutral fallback
                "report": f"Verification error: {e!s}",
                "issues": [],
                "mast_labels": mast_labels,
                "mast_confidence": mast_confidence,
            }

    def _label_qa_gate(
        self, verdict: str, issues: list[str], content: str
    ) -> tuple[list[str], float]:
        """
        Apply MAST labeling to a single-shot QA-gate verification result.

        The QA gate runs once per task (no revision rounds), so labeling uses
        round_num=1 with no previous content and a fresh hash tracker.

        Returns:
            Tuple of (mast_labels, mast_confidence). Never raises — labeling
            failures degrade to no labels so verification is never broken by
            the observability layer.
        """
        try:
            from src.qa.mast import format_mast_labels_for_metadata

            self.mast_labeler.reset_hash_tracker()
            mast_result = self.mast_labeler.label_verification_result(
                verdict=verdict,
                issues=issues,
                round_num=1,
                content=content,
            )
            mast_metadata = format_mast_labels_for_metadata(mast_result)
            return (
                list(mast_metadata["mast_failures"]),
                float(mast_metadata["mast_confidence"]),
            )
        except Exception as e:
            logger.error(
                "mast_qa_gate_labeling_failed",
                supervisor_type=self.supervisor_type,
                error=str(e),
            )
            return [], 0.0

    def _extract_issues_from_report(self, report: str) -> list[str]:
        """Extract structured issues from verification report.

        Args:
            report: The full verification report text

        Returns:
            List of issue strings
        """
        issues = []
        lines = report.split("\n")
        in_issues_section = False

        for line in lines:
            stripped = line.strip()
            # Look for the ISSUES section
            if stripped.upper().startswith("ISSUES:"):
                in_issues_section = True
                # Check if "None" immediately follows
                after_colon = stripped[len("ISSUES:") :].strip()
                if after_colon.upper() == "NONE":
                    break
                continue

            # If we're in the issues section, collect numbered items
            if in_issues_section:
                # Stop at next section or empty line
                if not stripped or stripped.upper().startswith(
                    ("VERDICT:", "SUMMARY:", "RECOMMENDATION:")
                ):
                    break
                # Collect numbered items (e.g., "1. Issue description")
                if stripped and (
                    stripped[0].isdigit() or stripped.startswith(("-", "*", "•"))
                ):
                    issues.append(stripped)

        return issues

    async def _run_worker_with_verification_loop(
        self,
        worker_type: str,
        message_type: MessageType,
        content: TalkHierContent | str,
        context: dict[str, Any] | None = None,
        aggregate_func: Any = None,
    ) -> tuple[TalkHierMessage | None, dict[str, Any]]:
        """
        Execute a worker with bounded verification revision loop.

        Args:
            worker_type: Type of worker to execute
            message_type: Message type for TalkHier communication
            content: Content to send to worker
            context: Optional context dict
            aggregate_func: Optional function to aggregate results for verification
                          If None, uses worker response content directly

        Returns:
            Tuple of (final worker response, verification result dict)

        The verification result dict contains:
            - verdict: "pass" or "revise"
            - report: full verification report
            - issues: list of issues
            - rounds: number of rounds executed
            - mast_labels: list of MAST failure mode codes (Phase S)
            - mast_confidence: overall MAST labeling confidence
            - revision_history: per-round MAST labels and verdicts
        """
        verification_result = {
            "verdict": "pass",
            "report": "",
            "issues": [],
            "rounds": 0,
            "mast_labels": [],
            "mast_confidence": 0.0,
            "revision_history": [],
        }
        worker_response = None
        previous_content: str | None = None

        # Reset hash tracker for this worker execution
        self.mast_labeler.reset_hash_tracker()

        for round_num in range(1, MAX_VERIFICATION_REVISION_ROUNDS + 1):
            logger.info(
                "worker_verification_round_started",
                worker_type=worker_type,
                round=round_num,
                max_rounds=MAX_VERIFICATION_REVISION_ROUNDS,
            )

            # Execute worker
            worker_response = await self.send_talkhier_message(
                worker_type, message_type, content, context
            )

            if not worker_response:
                logger.warning(
                    "worker_verification_no_response",
                    worker_type=worker_type,
                    round=round_num,
                )
                verification_result["verdict"] = "pass"  # Neutral fallback
                verification_result["rounds"] = round_num
                break

            # Aggregate content for verification
            if aggregate_func:
                aggregated_content = aggregate_func(worker_response)
            else:
                aggregated_content = (
                    worker_response.talkhier_content.content
                    if hasattr(worker_response, "talkhier_content")
                    and hasattr(worker_response.talkhier_content, "content")
                    else str(worker_response)
                )

            # Run verification
            verification_result = await self._run_verification(aggregated_content)
            verification_result["rounds"] = round_num

            issues_list = verification_result.get("issues", [])

            # Phase S: Apply MAST labeling (rule-based heuristics, zero LLM cost)
            from src.qa.mast import format_mast_labels_for_metadata

            # Type-safe extraction for mypy
            verdict_str = str(verification_result.get("verdict", "pass"))
            issues_seq = list(issues_list) if isinstance(issues_list, list) else []

            mast_result = self.mast_labeler.label_verification_result(
                verdict=verdict_str,
                issues=issues_seq,
                round_num=round_num,
                content=aggregated_content,
                previous_content=previous_content,
            )

            # Store MAST labels in verification result
            mast_metadata = format_mast_labels_for_metadata(mast_result)
            verification_result["mast_labels"] = mast_metadata["mast_failures"]
            verification_result["mast_confidence"] = mast_metadata["mast_confidence"]

            # Track per-round history for analysis
            if "revision_history" not in verification_result:
                verification_result["revision_history"] = []
            revision_history_list = verification_result["revision_history"]
            assert isinstance(revision_history_list, list)  # Type narrow for mypy
            revision_history_list.append(
                {
                    "round": round_num,
                    "verdict": verdict_str,
                    "mast_labels": mast_metadata["mast_failures"],
                    "mast_confidence": mast_metadata["mast_confidence"],
                    "issues": issues_seq[:],  # shallow copy
                }
            )

            logger.info(
                "worker_verification_round_completed",
                worker_type=worker_type,
                round=round_num,
                verdict=verification_result["verdict"],
                issue_count=len(issues_list) if isinstance(issues_list, list) else 0,
                mast_failures=mast_metadata["mast_failures"],
                mast_confidence=mast_metadata["mast_confidence"],
            )

            # Update previous_content for next round's repetition detection
            previous_content = aggregated_content

            # PASS → break with current response
            if verification_result["verdict"] == "pass":
                logger.info(
                    "worker_verification_passed",
                    worker_type=worker_type,
                    round=round_num,
                )
                break

            # REVISE on final round → apply penalty and break
            if round_num >= MAX_VERIFICATION_REVISION_ROUNDS:
                logger.warning(
                    "worker_verification_max_rounds_reached",
                    worker_type=worker_type,
                    round=round_num,
                    issues=verification_result.get("issues", []),
                )
                break

            # REVISE with rounds remaining → append feedback and retry
            logger.info(
                "worker_verification_revision_needed",
                worker_type=worker_type,
                round=round_num,
                remaining=MAX_VERIFICATION_REVISION_ROUNDS - round_num,
            )

            # Build revision content with feedback
            feedback_text = f"\n\nREVISION FEEDBACK (Round {round_num}):\n" + str(
                verification_result["report"]
            )

            if isinstance(content, str):
                revised_content = TalkHierContent(
                    content=content + feedback_text,
                )
            elif isinstance(content, TalkHierContent):
                current_content = str(
                    content.content if hasattr(content, "content") else ""
                )
                current_background = str(
                    content.background if hasattr(content, "background") else ""
                )
                current_outputs = (
                    content.intermediate_outputs
                    if hasattr(content, "intermediate_outputs")
                    else {}
                )

                revised_content = TalkHierContent(
                    content=current_content + feedback_text,
                    background=current_background,
                    intermediate_outputs=current_outputs,
                )
            else:
                revised_content = content

            content = revised_content

        return worker_response, verification_result

    def _store_mast_labels_in_state(
        self, state: SupervisionState, verification_result: dict[str, Any]
    ) -> None:
        """
        Store MAST labeling results from verification into supervision state.

        Args:
            state: Current supervision state to update
            verification_result: Verification result dict with MAST labels

        This method merges MAST failure metadata into state.supervision_metadata
        for inclusion in the final AgentResult.
        """
        if not verification_result:
            return

        mast_labels = verification_result.get("mast_labels", [])
        if not mast_labels:
            return

        # Merge into supervision_metadata for _build_supervision_result
        state.supervision_metadata["mast_failures"] = mast_labels
        state.supervision_metadata["mast_confidence"] = verification_result.get(
            "mast_confidence", 0.0
        )
        state.supervision_metadata["verification_rounds"] = verification_result.get(
            "rounds", 0
        )
        state.supervision_metadata["revision_history"] = verification_result.get(
            "revision_history", []
        )

        logger.info(
            "mast_labels_stored_in_state",
            supervisor_type=self.supervisor_type,
            mast_failures=mast_labels,
            confidence=verification_result.get("mast_confidence", 0.0),
        )

    async def execute_workers_parallel(
        self,
        worker_specs: list[
            tuple[str, MessageType, TalkHierContent | str, dict[str, Any] | None]
        ],
        supervision_mode: SupervisionMode,
    ) -> dict[str, TalkHierMessage | None]:
        """
        Execute multiple workers based on supervision mode.

        Args:
            worker_specs: List of (worker_type, message_type, content, context) tuples
            supervision_mode: Mode determining execution strategy (PARALLEL vs SEQUENTIAL)

        Returns:
            Dict mapping worker_type to worker response (or None on failure)

        PARALLEL mode behavior:
            - Executes all workers concurrently via asyncio.gather with return_exceptions=True
            - Bounded by asyncio.Semaphore(self.max_parallel_workers)
            - Failed workers are logged and excluded; successful workers proceed
            - If ALL workers fail, returns empty dict (graceful degradation)

        SEQUENTIAL mode behavior:
            - Executes workers one at a time in list order
            - Preserves existing sequential behavior byte-for-byte
        """
        if supervision_mode == SupervisionMode.PARALLEL:
            # Create semaphore to bound parallelism
            semaphore = asyncio.Semaphore(self.max_parallel_workers)

            async def execute_with_semaphore(
                worker_type: str,
                message_type: MessageType,
                content: TalkHierContent | str,
                context: dict[str, Any] | None,
            ) -> tuple[str, TalkHierMessage | None | Exception]:
                """Execute single worker with semaphore bound."""
                async with semaphore:
                    try:
                        response = await self.send_talkhier_message(
                            worker_type, message_type, content, context
                        )
                        return (worker_type, response)
                    except Exception as e:
                        return (worker_type, e)

            # Execute all workers concurrently with return_exceptions via gather
            tasks = [
                execute_with_semaphore(worker_type, msg_type, content, ctx)
                for worker_type, msg_type, content, ctx in worker_specs
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results: separate successes from failures
            worker_results: dict[str, TalkHierMessage | None] = {}
            failed_workers = []

            for result in results:
                if isinstance(result, BaseException):
                    # gather-level exception (shouldn't happen with our wrapper)
                    logger.error(
                        "supervisor_parallel_worker_gather_exception",
                        error=str(result),
                    )
                    continue

                if not isinstance(result, tuple) or len(result) != 2:
                    logger.error(
                        "supervisor_parallel_worker_invalid_result",
                        result_type=type(result).__name__,
                    )
                    continue

                worker_type, response_or_error = result

                if isinstance(response_or_error, Exception):
                    # Worker execution failed
                    logger.error(
                        "supervisor_parallel_worker_failed",
                        worker_type=worker_type,
                        error=str(response_or_error),
                    )
                    failed_workers.append(
                        {"worker_type": worker_type, "error": str(response_or_error)}
                    )
                else:
                    # Worker succeeded
                    worker_results[worker_type] = response_or_error

            # Log partial failure if some workers failed
            if failed_workers:
                logger.warning(
                    "supervisor_parallel_execution_partial_failure",
                    total_workers=len(worker_specs),
                    successful_workers=len(worker_results),
                    failed_workers=failed_workers,
                )

            # ALL workers failed → graceful degradation (empty dict)
            if not worker_results and worker_specs:
                logger.error(
                    "supervisor_parallel_execution_all_workers_failed",
                    total_workers=len(worker_specs),
                    failed_workers=failed_workers,
                )

            return worker_results

        else:
            # SEQUENTIAL mode: preserve existing byte-for-byte behavior
            sequential_results: dict[str, TalkHierMessage | None] = {}

            for worker_type, message_type, content, context in worker_specs:
                try:
                    response = await self.send_talkhier_message(
                        worker_type, message_type, content, context
                    )
                    sequential_results[worker_type] = response
                except Exception as e:
                    logger.error(
                        "supervisor_sequential_worker_failed",
                        worker_type=worker_type,
                        error=str(e),
                    )
                    sequential_results[worker_type] = None

            return sequential_results

    async def close(self) -> None:
        """Close supervisor and cleanup resources."""

        # Close active workers
        for worker in self.active_workers.values():
            if hasattr(worker, "close"):
                try:
                    await worker.close()
                except Exception as e:
                    logger.warning(
                        "supervisor_worker_close_failed",
                        worker_type=getattr(
                            worker, "get_agent_type", lambda: "unknown"
                        )(),
                        error=str(e),
                    )

        self.active_workers.clear()

        logger.info("supervisor_closed", supervisor_type=self.supervisor_type)


__all__ = [
    "BaseSupervisor",
    "SupervisionMode",
    "SupervisionState",
    "WorkerAllocation",
    "WorkerDefinition",
]
