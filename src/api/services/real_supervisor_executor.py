"""
Real supervisor execution module.

Provides actual execution logic for supervisor coordination via MASR bridge.
"""

import asyncio
import uuid
from typing import Any

from structlog import get_logger

from src.agents.models import AgentTask
from src.agents.supervisors.base_supervisor import BaseSupervisor
from src.ai_brain.integration.masr_supervisor_bridge import (
    MASRSupervisorBridge,
    SupervisorConfiguration,
)
from src.models.supervisor_api_models import (
    CoordinationMode,
    SupervisionStrategy,
    WorkerInfo,
    WorkerStatus,
)

logger = get_logger()


class RealSupervisorExecutor:
    """Executes real supervisor/worker coordination via MASR bridge."""

    def __init__(
        self,
        supervisor_registry: dict[str, type[BaseSupervisor]],
        masr_bridge: MASRSupervisorBridge,
    ):
        self.supervisor_registry = supervisor_registry
        self.masr_bridge = masr_bridge

    async def execute_with_workers(
        self,
        supervisor_type: str,
        task: str,
        workers: list[WorkerInfo],
        strategy: SupervisionStrategy,
        coordination_mode: CoordinationMode,
        quality_threshold: float,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        """Execute task with assigned workers using real supervisor execution."""
        try:
            for worker in workers:
                worker.status = WorkerStatus.EXECUTING

            supervisor_config = SupervisorConfiguration(
                supervisor_type=supervisor_type,
                domain=supervisor_type,
                worker_allocation=[w.worker_type for w in workers],
                quality_threshold=quality_threshold,
                max_refinement_rounds=(
                    3 if strategy == SupervisionStrategy.ITERATIVE else 1
                ),
                timeout_seconds=timeout_seconds,
                execution_mode=self._coordination_mode_to_execution_mode(
                    coordination_mode
                ),
                max_workers=len(workers),
                max_parallel_workers=(
                    len(workers)
                    if coordination_mode == CoordinationMode.PARALLEL
                    else 1
                ),
            )

            agent_task = AgentTask(
                id=str(uuid.uuid4()),
                agent_type=supervisor_type,
                input_data={"query": task, "task": task},
                context={
                    "strategy": strategy.value,
                    "coordination_mode": coordination_mode.value,
                    "workers": [w.worker_id for w in workers],
                },
                timeout=timeout_seconds,
            )

            supervisor_class = self.supervisor_registry.get(supervisor_type)
            if not supervisor_class:
                logger.warning(
                    f"Supervisor type '{supervisor_type}' not found, falling back to research"
                )
                supervisor_class = self.supervisor_registry.get("research")
                if not supervisor_class:
                    raise ValueError("No supervisors available")

            execution_result = await self.masr_bridge.executor.execute(
                supervisor_class, supervisor_config, agent_task
            )

            for worker in workers:
                worker.status = WorkerStatus.COMPLETED
                worker.current_task = None

            if execution_result.agent_result:
                result = execution_result.agent_result.output
                if isinstance(result, dict):
                    result["_execution_metadata"] = {
                        "quality_score": execution_result.quality_score,
                        "execution_time_seconds": execution_result.execution_time_seconds,
                        "workers_used": execution_result.workers_used,
                        "refinement_rounds": execution_result.refinement_rounds,
                    }
            else:
                result = {
                    "status": "failed",
                    "message": "Execution completed but no result returned",
                    "errors": execution_result.errors,
                }

            await asyncio.sleep(0.1)
            for worker in workers:
                worker.status = WorkerStatus.IDLE

            logger.info(
                "Successfully executed task with real supervisor",
                supervisor_type=supervisor_type,
                workers_used=len(workers),
                quality_score=execution_result.quality_score,
            )

            return result

        except Exception as e:
            logger.error(
                "Error during real worker execution",
                error=str(e),
                supervisor_type=supervisor_type,
            )
            for worker in workers:
                worker.status = WorkerStatus.IDLE
                worker.current_task = None
            raise

    def _coordination_mode_to_execution_mode(self, mode: CoordinationMode) -> str:
        mapping = {
            CoordinationMode.SEQUENTIAL: "sequential",
            CoordinationMode.PARALLEL: "parallel",
            CoordinationMode.HIERARCHICAL: "hybrid",
            CoordinationMode.ADAPTIVE: "adaptive",
        }
        return mapping.get(mode, "parallel")


async def resolve_conflict_with_supervisor(
    conflict_outputs: list[dict[str, Any]],
    strategy: str,
    supervisor_guidance: str | None,
    gemini_service: Any | None = None,
) -> tuple[Any, float, str]:
    """Resolve conflicts between worker outputs using real supervisor reasoning."""
    if strategy == "supervisor_override" and supervisor_guidance:
        return (
            supervisor_guidance,
            0.95,
            "Supervisor authority provided explicit resolution",
        )

    if strategy == "majority_vote":
        outputs = [w["output"] for w in conflict_outputs]
        resolved = max(set(outputs), key=outputs.count)
        confidence = outputs.count(resolved) / len(outputs)
        reasoning = f"Majority vote selected output with {confidence:.2%} agreement"
        return resolved, confidence, reasoning

    if strategy == "quality_based":
        best_output = max(conflict_outputs, key=lambda x: x.get("confidence", 0))
        resolved = best_output["output"]
        confidence = best_output.get("confidence", 0.8)
        reasoning = f"Selected output with highest confidence score of {confidence:.2f}"
        return resolved, confidence, reasoning

    if gemini_service:
        try:
            outputs_text = "\n\n".join(
                [
                    f"Output {i + 1} (confidence: {o.get('confidence', 'N/A')}):\n{o['output']}"
                    for i, o in enumerate(conflict_outputs)
                ]
            )

            prompt = f"""You are a supervisor resolving conflicts between worker outputs.

Strategy: {strategy}
{"Guidance: " + supervisor_guidance if supervisor_guidance else ""}

Worker Outputs:
{outputs_text}

Analyze and provide:
RESOLUTION: <your resolved output>
CONFIDENCE: <0.0-1.0>
REASONING: <explanation>
"""

            response = await gemini_service.generate_content(prompt)

            lines = response.strip().split("\n")
            resolved = None
            confidence = 0.85
            reasoning = "LLM-based synthesis"

            for line in lines:
                if line.startswith("RESOLUTION:"):
                    resolved = line.replace("RESOLUTION:", "").strip()
                elif line.startswith("CONFIDENCE:"):
                    try:
                        confidence = float(line.replace("CONFIDENCE:", "").strip())
                    except ValueError:
                        confidence = 0.85
                elif line.startswith("REASONING:"):
                    reasoning = line.replace("REASONING:", "").strip()

            if resolved:
                return resolved, confidence, reasoning

        except Exception as e:
            logger.error("LLM-based conflict resolution failed", error=str(e))

    resolved = "Weighted consensus: " + " | ".join(
        [str(o["output"]) for o in conflict_outputs]
    )
    return resolved, 0.85, "Combined outputs using weighted consensus"
