"""
Procedural Memory Manager

Manages procedural memory for storing learned workflows, patterns, and
optimization data. This enables agents to learn and improve their
performance over time.

Procedural memory stores:
- Learned workflow patterns and sequences
- Optimization parameters and model configurations
- Successful task completion strategies
- Error patterns and recovery procedures
- Performance improvement suggestions
- Agent behavior adaptations
"""

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ProcedureType(Enum):
    """Types of procedural knowledge."""

    WORKFLOW = "workflow"
    OPTIMIZATION = "optimization"
    ERROR_RECOVERY = "error_recovery"
    PATTERN = "pattern"
    STRATEGY = "strategy"
    ADAPTATION = "adaptation"


@dataclass
class Procedure:
    """Individual procedure stored in procedural memory."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    procedure_type: ProcedureType = ProcedureType.WORKFLOW

    # Procedure definition
    steps: list[dict[str, Any]] = field(default_factory=list)
    conditions: dict[str, Any] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)

    # Context and applicability
    domain: str | None = None
    agent_type: str | None = None
    task_types: list[str] = field(default_factory=list)

    # Performance data
    usage_count: int = 0
    success_count: int = 0
    avg_performance_score: float = 0.0
    last_used: datetime | None = None

    # Learning metadata
    learned_from: list[str] = field(default_factory=list)  # Source episodes/experiences
    confidence: float = 0.5  # Confidence in this procedure
    version: int = 1

    # Temporal information
    created_at: datetime = field(default_factory=datetime.now)
    last_updated: datetime = field(default_factory=datetime.now)

    # Additional metadata
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProcedureQuery:
    """Query parameters for procedure retrieval."""

    procedure_type: ProcedureType | None = None
    domain: str | None = None
    agent_type: str | None = None
    task_types: list[str] | None = None
    min_confidence: float = 0.0
    min_success_rate: float = 0.0
    min_usage_count: int = 0
    tags: list[str] | None = None
    limit: int = 10


class ProceduralMemoryManager:
    """
    Manages procedural memory for learned workflows and optimizations.

    Procedural memory provides:
    - Storage of learned behavioral patterns
    - Workflow optimization and adaptation
    - Error recovery procedures
    - Performance improvement strategies
    """

    def __init__(self, config: dict[str, Any]):
        """Initialize procedural memory manager."""
        self.config = config

        # Storage configuration
        self.storage_backend = config.get("storage_backend", "json_file")
        self.storage_path = config.get("storage_path", "data/procedural_memory.json")

        # Learning parameters
        self.min_confidence_threshold = config.get("min_confidence", 0.7)
        self.min_usage_for_promotion = config.get("min_usage_promotion", 5)
        self.performance_decay_factor = config.get("performance_decay", 0.95)

        # In-memory storage
        self.procedures: dict[str, Procedure] = {}

        # Performance tracking
        self.store_count = 0
        self.retrieve_count = 0
        self.learn_count = 0

    async def initialize(self) -> None:
        """Initialize the procedural memory system."""

        try:
            # Load existing procedures
            await self._load_procedures()
            logger.info(f"Loaded {len(self.procedures)} procedures from storage")

        except Exception as e:
            logger.error(f"Failed to load procedures: {e}")
            # Start with empty memory
            self.procedures = {}

    async def store_procedure(self, procedure: Procedure) -> bool:
        """
        Store a procedure in procedural memory.

        Args:
            procedure: Procedure to store

        Returns:
            True if stored successfully
        """

        try:
            # Update timestamp
            procedure.last_updated = datetime.now()

            # Store in memory
            self.procedures[procedure.id] = procedure

            # Persist to storage
            await self._persist_procedures()

            self.store_count += 1
            logger.debug(f"Stored procedure: {procedure.name}")
            return True

        except Exception as e:
            logger.error(f"Failed to store procedure {procedure.id}: {e}")
            return False

    async def retrieve_procedures(self, query: ProcedureQuery) -> list[Procedure]:
        """
        Retrieve procedures based on query parameters.

        Args:
            query: Query parameters

        Returns:
            List of matching procedures
        """

        try:
            matching_procedures = []

            for procedure in self.procedures.values():
                # Apply filters
                if (
                    query.procedure_type
                    and procedure.procedure_type != query.procedure_type
                ):
                    continue

                if query.domain and procedure.domain != query.domain:
                    continue

                if query.agent_type and procedure.agent_type != query.agent_type:
                    continue

                if query.task_types and not any(
                    t in procedure.task_types for t in query.task_types
                ):
                    continue

                if procedure.confidence < query.min_confidence:
                    continue

                success_rate = procedure.success_count / max(procedure.usage_count, 1)
                if success_rate < query.min_success_rate:
                    continue

                if procedure.usage_count < query.min_usage_count:
                    continue

                if query.tags and not any(t in procedure.tags for t in query.tags):
                    continue

                matching_procedures.append(procedure)

            # Sort by confidence and performance
            matching_procedures.sort(
                key=lambda p: (p.confidence, p.avg_performance_score), reverse=True
            )

            self.retrieve_count += 1
            return matching_procedures[: query.limit]

        except Exception as e:
            logger.error(f"Failed to retrieve procedures: {e}")
            return []

    async def delete_by_user_id(self, user_id: str) -> int:
        """Delete procedures associated with a user."""

        procedure_ids = [
            procedure_id
            for procedure_id, procedure in self.procedures.items()
            if procedure.metadata.get("user_id") == user_id
        ]
        for procedure_id in procedure_ids:
            del self.procedures[procedure_id]
        if procedure_ids:
            await self._persist_procedures()
        return len(procedure_ids)

    async def learn_from_episode(
        self, episode_data: dict[str, Any], performance_score: float, success: bool
    ) -> str | None:
        """
        Learn a new procedure from an episode.

        Args:
            episode_data: Episode data containing steps and context
            performance_score: Performance score of the episode
            success: Whether the episode was successful

        Returns:
            ID of learned procedure or None if learning failed
        """

        try:
            # Extract procedural knowledge from episode
            steps = episode_data.get("steps", [])
            context = episode_data.get("context", {})

            if not steps:
                return None

            # Create new procedure
            procedure = Procedure(
                name=f"Learned procedure {datetime.now().strftime('%Y%m%d_%H%M%S')}",
                procedure_type=ProcedureType.WORKFLOW,
                steps=steps,
                conditions=context.get("conditions", {}),
                parameters=context.get("parameters", {}),
                domain=episode_data.get("domain"),
                agent_type=episode_data.get("agent_type"),
                task_types=[episode_data.get("task_type", "general")],
                usage_count=1,
                success_count=1 if success else 0,
                avg_performance_score=performance_score,
                last_used=datetime.now(),
                learned_from=[episode_data.get("episode_id", "unknown")],
                confidence=self._calculate_initial_confidence(
                    performance_score, success
                ),
            )

            # Store the procedure
            success = await self.store_procedure(procedure)

            if success:
                self.learn_count += 1
                logger.info(f"Learned new procedure: {procedure.name}")
                return procedure.id

            return None

        except Exception as e:
            logger.error(f"Failed to learn procedure from episode: {e}")
            return None

    async def update_procedure_performance(
        self, procedure_id: str, performance_score: float, success: bool
    ) -> bool:
        """
        Update procedure performance based on usage.

        Args:
            procedure_id: ID of procedure to update
            performance_score: New performance score
            success: Whether the usage was successful

        Returns:
            True if updated successfully
        """

        try:
            procedure = self.procedures.get(procedure_id)
            if not procedure:
                return False

            # Update performance statistics
            procedure.usage_count += 1
            if success:
                procedure.success_count += 1

            # Update average performance (exponential moving average)
            alpha = 0.2  # Learning rate
            procedure.avg_performance_score = (
                1 - alpha
            ) * procedure.avg_performance_score + alpha * performance_score

            # Update confidence based on performance
            success_rate = procedure.success_count / procedure.usage_count
            procedure.confidence = min(
                success_rate
                * (procedure.avg_performance_score / 100.0)
                * min(procedure.usage_count / self.min_usage_for_promotion, 1.0),
                1.0,
            )

            # Update timestamps
            procedure.last_used = datetime.now()
            procedure.last_updated = datetime.now()

            # Persist changes
            await self._persist_procedures()

            return True

        except Exception as e:
            logger.error(f"Failed to update procedure performance {procedure_id}: {e}")
            return False

    async def optimize_procedure(
        self, procedure_id: str, optimization_data: dict[str, Any]
    ) -> bool:
        """
        Optimize an existing procedure based on new insights.

        Args:
            procedure_id: ID of procedure to optimize
            optimization_data: Data for optimization

        Returns:
            True if optimized successfully
        """

        try:
            procedure = self.procedures.get(procedure_id)
            if not procedure:
                return False

            # Create optimized version
            optimized_procedure = Procedure(**asdict(procedure))
            optimized_procedure.id = str(uuid.uuid4())
            optimized_procedure.version = procedure.version + 1
            optimized_procedure.last_updated = datetime.now()

            # Apply optimizations
            if "parameters" in optimization_data:
                optimized_procedure.parameters.update(optimization_data["parameters"])

            if "steps" in optimization_data:
                optimized_procedure.steps = optimization_data["steps"]

            if "conditions" in optimization_data:
                optimized_procedure.conditions.update(optimization_data["conditions"])

            # Reset performance stats for new version
            optimized_procedure.usage_count = 0
            optimized_procedure.success_count = 0
            optimized_procedure.confidence = max(
                procedure.confidence * 0.8, 0.5
            )  # Slight confidence decrease for unproven optimization

            # Store optimized version
            success = await self.store_procedure(optimized_procedure)

            if success:
                logger.info(f"Created optimized version of procedure: {procedure.name}")

            return success

        except Exception as e:
            logger.error(f"Failed to optimize procedure {procedure_id}: {e}")
            return False

    async def get_best_procedure_for_task(
        self,
        task_type: str,
        domain: str | None = None,
        agent_type: str | None = None,
    ) -> Procedure | None:
        """Get the best procedure for a specific task."""

        query = ProcedureQuery(
            procedure_type=ProcedureType.WORKFLOW,
            domain=domain,
            agent_type=agent_type,
            task_types=[task_type],
            min_confidence=self.min_confidence_threshold,
            limit=1,
        )

        procedures = await self.retrieve_procedures(query)
        return procedures[0] if procedures else None

    async def cleanup_old_procedures(self, retention_days: int = 30) -> int:
        """Remove old, unused procedures."""

        cutoff_date = datetime.now() - timedelta(days=retention_days)
        removed_count = 0

        procedures_to_remove = []

        for procedure_id, procedure in self.procedures.items():
            # Remove if old, low confidence, and rarely used
            if (
                procedure.created_at < cutoff_date
                and procedure.confidence < 0.3
                and procedure.usage_count < 2
            ):
                procedures_to_remove.append(procedure_id)

        for procedure_id in procedures_to_remove:
            del self.procedures[procedure_id]
            removed_count += 1

        if removed_count > 0:
            await self._persist_procedures()
            logger.info(f"Cleaned up {removed_count} old procedures")

        return removed_count

    async def get_memory_stats(self) -> dict[str, Any]:
        """Get procedural memory statistics."""

        stats: dict[str, Any] = {
            "store_count": self.store_count,
            "retrieve_count": self.retrieve_count,
            "learn_count": self.learn_count,
            "total_procedures": len(self.procedures),
        }

        if self.procedures:
            # Calculate aggregate statistics
            total_confidence = sum(p.confidence for p in self.procedures.values())
            total_usage = sum(p.usage_count for p in self.procedures.values())
            total_success = sum(p.success_count for p in self.procedures.values())

            stats.update(
                {
                    "avg_confidence": float(total_confidence / len(self.procedures)),
                    "total_usage": int(total_usage),
                    "overall_success_rate": float(total_success / max(total_usage, 1)),
                    "high_confidence_procedures": sum(
                        1 for p in self.procedures.values() if p.confidence > 0.8
                    ),
                }
            )

            # Type distribution
            type_counts: dict[str, int] = {}
            for procedure in self.procedures.values():
                ptype = procedure.procedure_type.value
                type_counts[ptype] = type_counts.get(ptype, 0) + 1

            stats["type_distribution"] = type_counts

        return stats

    def _calculate_initial_confidence(
        self, performance_score: float, success: bool
    ) -> float:
        """Calculate initial confidence for a learned procedure."""

        base_confidence = 0.5

        # Adjust based on performance
        performance_factor = performance_score / 100.0  # Assuming score is 0-100

        # Adjust based on success
        success_factor = 1.0 if success else 0.5

        confidence = base_confidence * performance_factor * success_factor
        return min(max(confidence, 0.1), 0.9)  # Clamp between 0.1 and 0.9

    async def _load_procedures(self) -> None:
        """Load procedures from persistent storage."""

        if self.storage_backend == "json_file":
            try:
                import os

                if os.path.exists(self.storage_path):
                    with open(self.storage_path) as f:
                        data = json.load(f)

                    # Convert back to Procedure objects
                    for proc_id, proc_data in data.items():
                        # Handle enum conversion
                        proc_data["procedure_type"] = ProcedureType(
                            proc_data["procedure_type"]
                        )

                        # Handle datetime conversion
                        for date_field in ["created_at", "last_updated", "last_used"]:
                            if proc_data.get(date_field):
                                proc_data[date_field] = datetime.fromisoformat(
                                    proc_data[date_field]
                                )

                        procedure = Procedure(**proc_data)
                        self.procedures[proc_id] = procedure

            except Exception as e:
                logger.error(f"Failed to load procedures from {self.storage_path}: {e}")

    async def _persist_procedures(self) -> None:
        """Persist procedures to storage."""

        if self.storage_backend == "json_file":
            try:
                import os

                os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)

                # Convert to JSON-serializable format
                data = {}
                for proc_id, procedure in self.procedures.items():
                    proc_dict = asdict(procedure)

                    # Handle enum conversion
                    proc_dict["procedure_type"] = procedure.procedure_type.value

                    # Handle datetime conversion
                    for date_field in ["created_at", "last_updated", "last_used"]:
                        if proc_dict.get(date_field):
                            proc_dict[date_field] = proc_dict[date_field].isoformat()

                    data[proc_id] = proc_dict

                with open(self.storage_path, "w") as f:
                    json.dump(data, f, indent=2)

            except Exception as e:
                logger.error(
                    f"Failed to persist procedures to {self.storage_path}: {e}"
                )


__all__ = ["ProceduralMemoryManager", "Procedure", "ProcedureQuery", "ProcedureType"]
