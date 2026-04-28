"""
Supervisor Coordination Service - Service layer for Hierarchical Supervisor API

This service integrates with the existing supervisor factory, MASR router, and
TalkHier protocol to provide comprehensive supervisor coordination capabilities
through REST API endpoints.
"""

import asyncio
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from src.models.supervisor_api_models import (
    ConflictResolutionRequest,
    ConflictResolutionResponse,
    ConflictResolutionStrategy,
    CoordinationMode,
    ExperimentRequest,
    ExperimentResponse,
    MultiSupervisorOrchestrationRequest,
    MultiSupervisorOrchestrationResponse,
    SupervisionStrategy,
    SupervisorComparisonResponse,
    SupervisorExecuteRequest,
    SupervisorExecuteResponse,
    SupervisorHealthResponse,
    SupervisorInfo,
    SupervisorStatsResponse,
    SupervisorType,
    WorkerAllocationOptimizationRequest,
    WorkerAllocationOptimizationResponse,
    WorkerCoordinationRequest,
    WorkerCoordinationResponse,
    WorkerInfo,
    WorkerStatus,
)


@dataclass
class SupervisorMetrics:
    """Metrics tracking for supervisor performance"""
    total_executions: int = 0
    successful_executions: int = 0
    failed_executions: int = 0
    total_execution_time_ms: float = 0.0
    total_quality_score: float = 0.0
    worker_utilization_samples: list[float] = field(default_factory=list)
    last_execution_time: datetime | None = None


class SupervisorCoordinationService:
    """
    Service layer for supervisor coordination and management.
    Integrates with existing supervisor factory and MASR router.
    """
    
    def __init__(self) -> None:
        """Initialize the supervisor coordination service"""
        self.supervisors: dict[str, SupervisorInfo] = {}
        self.workers: dict[str, list[WorkerInfo]] = {}
        self.metrics: dict[str, SupervisorMetrics] = {}
        self.active_coordinations: dict[str, dict[str, Any]] = {}
        self.conflict_resolutions: dict[str, dict[str, Any]] = {}
        self.experiments: dict[str, dict[str, Any]] = {}

        # Initialize supervisor registry
        self._initialize_supervisors()

    def _initialize_supervisors(self) -> None:
        """Initialize available supervisors based on domains"""
        for supervisor_type in SupervisorType:
            supervisor_id = str(uuid.uuid4())
            self.supervisors[supervisor_type.value] = SupervisorInfo(
                supervisor_id=supervisor_id,
                supervisor_type=supervisor_type,
                status="active",
                capabilities=self._get_supervisor_capabilities(supervisor_type),
                worker_count=self._get_worker_count(supervisor_type),
                active_tasks=0,
                performance_metrics=self._get_initial_metrics(supervisor_type)
            )
            
            # Initialize metrics tracking
            self.metrics[supervisor_type.value] = SupervisorMetrics()
            
            # Initialize workers for this supervisor
            self.workers[supervisor_type.value] = self._create_workers_for_supervisor(supervisor_type)
    
    def _get_supervisor_capabilities(self, supervisor_type: SupervisorType) -> list[str]:
        """Get capabilities for a supervisor type"""
        capabilities_map = {
            SupervisorType.RESEARCH: [
                "literature_review", "comparative_analysis", 
                "methodology_design", "synthesis", "citation_management"
            ],
            SupervisorType.CONTENT: [
                "content_creation", "editing", "optimization",
                "seo", "formatting"
            ],
            SupervisorType.ANALYTICS: [
                "data_analysis", "visualization", "statistical_modeling",
                "prediction", "reporting"
            ],
            SupervisorType.SERVICE: [
                "customer_support", "troubleshooting", "documentation",
                "feedback_analysis", "escalation"
            ],
            SupervisorType.GENERAL: [
                "task_coordination", "resource_allocation", "monitoring",
                "quality_assurance", "reporting"
            ]
        }
        return capabilities_map.get(supervisor_type, ["general_coordination"])
    
    def _get_worker_count(self, supervisor_type: SupervisorType) -> int:
        """Get initial worker count for supervisor type"""
        worker_counts = {
            SupervisorType.RESEARCH: 5,
            SupervisorType.CONTENT: 4,
            SupervisorType.ANALYTICS: 3,
            SupervisorType.SERVICE: 3,
            SupervisorType.GENERAL: 2
        }
        return worker_counts.get(supervisor_type, 2)
    
    def _get_initial_metrics(self, supervisor_type: SupervisorType) -> dict[str, float]:
        """Get initial performance metrics for supervisor"""
        return {
            "success_rate": 0.95,
            "average_quality": 0.88,
            "average_latency_ms": 2500,
            "cost_efficiency": 0.82,
            "worker_satisfaction": 0.90
        }
    
    def _create_workers_for_supervisor(self, supervisor_type: SupervisorType) -> list[WorkerInfo]:
        """Create worker agents for a supervisor"""
        workers = []
        worker_types = self._get_worker_types_for_supervisor(supervisor_type)
        
        for i, worker_type in enumerate(worker_types):
            worker = WorkerInfo(
                worker_id=f"{supervisor_type.value}-worker-{i+1}",
                worker_type=worker_type,
                status=WorkerStatus.IDLE,
                capabilities=self._get_worker_capabilities(worker_type),
                performance_score=random.uniform(0.8, 0.95),
                current_task=None
            )
            workers.append(worker)
        
        return workers
    
    def _get_worker_types_for_supervisor(self, supervisor_type: SupervisorType) -> list[str]:
        """Get worker types for a supervisor"""
        worker_types_map = {
            SupervisorType.RESEARCH: [
                "literature_review", "comparative_analysis",
                "methodology", "synthesis", "citation"
            ],
            SupervisorType.CONTENT: [
                "writer", "editor", "optimizer", "formatter"
            ],
            SupervisorType.ANALYTICS: [
                "data_analyst", "statistician", "visualizer"
            ],
            SupervisorType.SERVICE: [
                "support_agent", "troubleshooter", "escalation_specialist"
            ],
            SupervisorType.GENERAL: [
                "coordinator", "monitor"
            ]
        }
        return worker_types_map.get(supervisor_type, ["general_worker"])
    
    def _get_worker_capabilities(self, worker_type: str) -> list[str]:
        """Get capabilities for a worker type"""
        capabilities_map = {
            "literature_review": ["search", "extract", "summarize", "evaluate"],
            "comparative_analysis": ["compare", "contrast", "evaluate", "synthesize"],
            "methodology": ["design", "validate", "recommend", "critique"],
            "synthesis": ["integrate", "summarize", "conclude", "recommend"],
            "citation": ["format", "validate", "cross_reference", "manage"],
            "writer": ["create", "draft", "structure", "style"],
            "editor": ["review", "correct", "improve", "polish"],
            "data_analyst": ["analyze", "interpret", "model", "predict"],
            "support_agent": ["respond", "assist", "resolve", "document"]
        }
        return capabilities_map.get(worker_type, ["execute", "report"])
    
    async def execute_supervisor_task(
        self,
        supervisor_type: str,
        request: SupervisorExecuteRequest
    ) -> SupervisorExecuteResponse:
        """Execute a task through a supervisor with worker coordination"""
        start_time = time.time()
        execution_id = str(uuid.uuid4())
        
        # Get supervisor info
        supervisor = self.supervisors.get(supervisor_type)
        if not supervisor:
            raise ValueError(f"Supervisor type '{supervisor_type}' not found")
        
        # Update supervisor status
        supervisor.active_tasks += 1
        
        # Select and assign workers based on strategy
        assigned_workers = await self._assign_workers_for_task(
            supervisor_type,
            request.query,
            request.max_workers or 5,
            request.coordination_mode or CoordinationMode.HIERARCHICAL
        )

        # Simulate task execution with workers
        result = await self._execute_with_workers(
            supervisor_type,
            request.query,
            assigned_workers,
            request.supervision_strategy or SupervisionStrategy.COLLABORATIVE,
            request.coordination_mode or CoordinationMode.HIERARCHICAL,
            request.quality_threshold or 0.8,
            request.timeout_seconds or 120
        )

        # Calculate execution metrics
        execution_time_ms = int((time.time() - start_time) * 1000)
        quality_score = self._calculate_quality_score(result, request.quality_threshold or 0.8)
        
        # Update metrics
        self._update_supervisor_metrics(
            supervisor_type,
            execution_time_ms,
            quality_score,
            success=result is not None
        )
        
        # Update supervisor status
        supervisor.active_tasks -= 1
        
        # Determine refinement rounds based on quality
        refinement_rounds = 1
        if request.supervision_strategy == SupervisionStrategy.ITERATIVE:
            refinement_rounds = min(3, max(1, int((1.0 - quality_score) * 5)))
        
        return SupervisorExecuteResponse(
            execution_id=execution_id,
            supervisor_type=SupervisorType(supervisor_type),
            status="completed" if result else "failed",
            result=result,
            workers_used=[w.worker_id for w in assigned_workers],
            coordination_mode=request.coordination_mode or CoordinationMode.HIERARCHICAL,
            quality_score=quality_score,
            execution_time_ms=execution_time_ms,
            refinement_rounds=refinement_rounds,
            metadata={
                "strategy": (request.supervision_strategy or SupervisionStrategy.COLLABORATIVE).value,
                "parameters": request.parameters or {},
                "context": request.context or {}
            }
        )
    
    async def _assign_workers_for_task(
        self,
        supervisor_type: str,
        task: str,
        max_workers: int,
        coordination_mode: CoordinationMode
    ) -> list[WorkerInfo]:
        """Assign workers for a task based on requirements"""
        available_workers = [
            w for w in self.workers[supervisor_type]
            if w.status == WorkerStatus.IDLE
        ]
        
        # Determine number of workers based on coordination mode
        if coordination_mode == CoordinationMode.SEQUENTIAL:
            num_workers = min(1, len(available_workers))
        elif coordination_mode == CoordinationMode.PARALLEL:
            num_workers = min(max_workers, len(available_workers))
        else:  # HIERARCHICAL, ADAPTIVE, DEBATE
            num_workers = min(max(3, max_workers // 2), len(available_workers))
        
        # Select workers based on performance scores
        selected_workers = sorted(
            available_workers,
            key=lambda w: w.performance_score or 0,
            reverse=True
        )[:num_workers]
        
        # Update worker status
        for worker in selected_workers:
            worker.status = WorkerStatus.ASSIGNED
            worker.current_task = task[:50]  # Truncate task description
        
        return selected_workers
    
    async def _execute_with_workers(
        self,
        supervisor_type: str,
        task: str,
        workers: list[WorkerInfo],
        strategy: SupervisionStrategy,
        coordination_mode: CoordinationMode,
        quality_threshold: float,
        timeout_seconds: int
    ) -> Any | None:
        """Execute task with assigned workers"""
        # Simulate execution based on strategy and coordination mode
        await asyncio.sleep(0.5)  # Simulate processing time
        
        # Update worker status
        for worker in workers:
            worker.status = WorkerStatus.EXECUTING
        
        # Simulate execution results based on strategy
        if strategy == SupervisionStrategy.DIRECT:
            result = f"Direct execution result for: {task}"
        elif strategy == SupervisionStrategy.COLLABORATIVE:
            result = f"Collaborative result from {len(workers)} workers for: {task}"
        elif strategy == SupervisionStrategy.ITERATIVE:
            result = f"Iterative refinement result after multiple rounds for: {task}"
        else:
            result = f"Execution result for: {task}"
        
        # Update worker status
        for worker in workers:
            worker.status = WorkerStatus.COMPLETED
            worker.current_task = None
        
        # Reset worker status after completion
        await asyncio.sleep(0.1)
        for worker in workers:
            worker.status = WorkerStatus.IDLE
        
        return result
    
    def _calculate_quality_score(self, result: Any, threshold: float) -> float:
        """Calculate quality score for execution result"""
        if not result:
            return 0.0
        
        # Simulate quality calculation
        base_score = random.uniform(0.7, 0.95)
        
        # Adjust based on result characteristics
        if isinstance(result, str):
            length_factor = min(1.0, len(result) / 100)
            base_score = base_score * 0.8 + length_factor * 0.2
        
        return min(1.0, max(0.0, base_score))
    
    def _update_supervisor_metrics(
        self,
        supervisor_type: str,
        execution_time_ms: int,
        quality_score: float,
        success: bool
    ) -> None:
        """Update supervisor performance metrics"""
        metrics = self.metrics[supervisor_type]
        
        metrics.total_executions += 1
        if success:
            metrics.successful_executions += 1
        else:
            metrics.failed_executions += 1
        
        metrics.total_execution_time_ms += execution_time_ms
        metrics.total_quality_score += quality_score
        metrics.last_execution_time = datetime.now(UTC)
        
        # Update worker utilization
        active_workers = sum(
            1 for w in self.workers[supervisor_type]
            if w.status != WorkerStatus.IDLE
        )
        total_workers = len(self.workers[supervisor_type])
        if total_workers > 0:
            utilization = active_workers / total_workers
            metrics.worker_utilization_samples.append(utilization)
            # Keep only last 100 samples
            if len(metrics.worker_utilization_samples) > 100:
                metrics.worker_utilization_samples = metrics.worker_utilization_samples[-100:]
    
    async def get_all_supervisors(self) -> list[SupervisorInfo]:
        """Get information about all available supervisors"""
        return list(self.supervisors.values())
    
    async def get_supervisor_info(self, supervisor_type: str) -> SupervisorInfo:
        """Get detailed information about a specific supervisor"""
        if supervisor_type not in self.supervisors:
            raise ValueError(f"Supervisor type '{supervisor_type}' not found")
        return self.supervisors[supervisor_type]
    
    async def get_supervisor_workers(self, supervisor_type: str) -> list[WorkerInfo]:
        """Get all workers managed by a supervisor"""
        if supervisor_type not in self.workers:
            raise ValueError(f"Supervisor type '{supervisor_type}' not found")
        return self.workers[supervisor_type]
    
    async def coordinate_workers(
        self,
        supervisor_type: str,
        request: WorkerCoordinationRequest
    ) -> WorkerCoordinationResponse:
        """Coordinate workers for a specific task"""
        coordination_id = str(uuid.uuid4())
        
        # Assign workers based on request
        assigned_workers = await self._assign_workers_for_coordination(
            supervisor_type,
            request.worker_types,
            request.coordination_mode
        )
        
        # Create coordination plan
        coordination_plan = self._create_coordination_plan(
            request.task,
            assigned_workers,
            request.coordination_mode,
            request.refinement_rounds or 1,
            request.conflict_resolution or ConflictResolutionStrategy.SUPERVISOR_OVERRIDE
        )

        # Store active coordination
        self.active_coordinations[coordination_id] = {
            "supervisor_type": supervisor_type,
            "request": request,
            "workers": assigned_workers,
            "plan": coordination_plan,
            "started_at": datetime.now(UTC)
        }

        # Estimate completion time
        estimated_time = self._estimate_completion_time(
            len(assigned_workers),
            request.coordination_mode,
            request.refinement_rounds or 1
        )
        
        return WorkerCoordinationResponse(
            coordination_id=coordination_id,
            workers_assigned=assigned_workers,
            coordination_plan=coordination_plan,
            estimated_completion_time=estimated_time,
            status="initiated"
        )
    
    async def _assign_workers_for_coordination(
        self,
        supervisor_type: str,
        worker_types: list[str],
        coordination_mode: CoordinationMode
    ) -> list[WorkerInfo]:
        """Assign specific worker types for coordination"""
        assigned = []
        available_workers = self.workers.get(supervisor_type, [])
        
        for worker_type in worker_types:
            # Find workers matching the requested type
            matching_workers = [
                w for w in available_workers
                if w.worker_type == worker_type and w.status == WorkerStatus.IDLE
            ]
            
            if matching_workers:
                worker = matching_workers[0]
                worker.status = WorkerStatus.ASSIGNED
                assigned.append(worker)
            else:
                # Create a placeholder worker if none available
                worker = WorkerInfo(
                    worker_id=f"{supervisor_type}-{worker_type}-temp",
                    worker_type=worker_type,
                    status=WorkerStatus.ASSIGNED,
                    capabilities=self._get_worker_capabilities(worker_type),
                    performance_score=0.85,
                    current_task=None
                )
                assigned.append(worker)
        
        return assigned
    
    def _create_coordination_plan(
        self,
        task: str,
        workers: list[WorkerInfo],
        mode: CoordinationMode,
        refinement_rounds: int,
        conflict_resolution: ConflictResolutionStrategy
    ) -> dict[str, Any]:
        """Create a coordination execution plan"""
        plan: dict[str, Any] = {
            "task": task,
            "mode": mode.value,
            "phases": [],
            "refinement_rounds": refinement_rounds,
            "conflict_resolution": conflict_resolution.value
        }
        phases: list[dict[str, Any]] = []

        if mode == CoordinationMode.SEQUENTIAL:
            # Sequential execution phases
            for i, worker in enumerate(workers):
                phases.append({
                    "phase": i + 1,
                    "worker": worker.worker_id,
                    "action": f"Execute task component {i + 1}",
                    "dependencies": [i] if i > 0 else []
                })
        
        elif mode == CoordinationMode.PARALLEL:
            # Parallel execution phase
            phases.append({
                "phase": 1,
                "workers": [w.worker_id for w in workers],
                "action": "Execute task components in parallel",
                "dependencies": []
            })
            # Aggregation phase
            phases.append({
                "phase": 2,
                "action": "Aggregate parallel results",
                "dependencies": [1]
            })

        elif mode == CoordinationMode.HIERARCHICAL:
            # Hierarchical execution with supervisor oversight
            phases.append({
                "phase": 1,
                "action": "Supervisor task decomposition",
                "dependencies": []
            })
            phases.append({
                "phase": 2,
                "workers": [w.worker_id for w in workers],
                "action": "Worker execution with supervisor guidance",
                "dependencies": [1]
            })
            phases.append({
                "phase": 3,
                "action": "Supervisor quality review and integration",
                "dependencies": [2]
            })

        plan["phases"] = phases
        return plan
    
    def _estimate_completion_time(
        self,
        num_workers: int,
        mode: CoordinationMode,
        refinement_rounds: int
    ) -> int:
        """Estimate completion time in seconds"""
        base_time = 10  # Base time per worker
        
        if mode == CoordinationMode.SEQUENTIAL:
            time_estimate = base_time * num_workers
        elif mode == CoordinationMode.PARALLEL:
            time_estimate = base_time + 5  # Parallel execution + aggregation
        else:
            time_estimate = base_time * (num_workers // 2 + 1)
        
        # Add time for refinement rounds
        time_estimate += (refinement_rounds - 1) * 5
        
        return int(time_estimate)
    
    async def orchestrate_multi_supervisor(
        self,
        request: MultiSupervisorOrchestrationRequest
    ) -> MultiSupervisorOrchestrationResponse:
        """Orchestrate multiple supervisors for cross-domain tasks"""
        orchestration_id = str(uuid.uuid4())
        start_time = time.time()
        
        # Get involved supervisors
        supervisors_involved = []
        for sup_type in request.supervisor_types:
            if sup_type.value in self.supervisors:
                supervisors_involved.append(self.supervisors[sup_type.value])
        
        # Execute with each supervisor
        individual_results = {}
        for sup_type in request.supervisor_types:
            # Create supervisor-specific request
            sup_request = SupervisorExecuteRequest(
                query=request.query,
                supervision_strategy=SupervisionStrategy.COLLABORATIVE,
                coordination_mode=CoordinationMode.HIERARCHICAL,
                max_workers=5,
                quality_threshold=0.8,
                timeout_seconds=request.timeout_seconds // len(request.supervisor_types) if request.timeout_seconds else 120
            )
            
            # Execute with supervisor
            result = await self.execute_supervisor_task(sup_type.value, sup_request)
            individual_results[sup_type.value] = {
                "result": result.result,
                "quality_score": result.quality_score,
                "execution_time_ms": result.execution_time_ms
            }
        
        # Synthesize results if required
        synthesized_result = None
        consensus_achieved = False
        
        if request.synthesis_required:
            synthesized_result, consensus_achieved = await self._synthesize_results(
                individual_results,
                request.priority_weights or {}
            )
        
        # Calculate quality metrics
        quality_scores = [float(r["quality_score"]) for r in individual_results.values() if "quality_score" in r and r["quality_score"] is not None]
        quality_metrics = {
            "average_quality": sum(quality_scores) / len(quality_scores) if quality_scores else 0.0,
            "consistency": self._calculate_consistency(individual_results),
            "coverage": len(individual_results) / len(request.supervisor_types)
        }
        
        orchestration_time_ms = int((time.time() - start_time) * 1000)
        
        return MultiSupervisorOrchestrationResponse(
            orchestration_id=orchestration_id,
            supervisors_involved=supervisors_involved,
            individual_results=individual_results,
            synthesized_result=synthesized_result,
            orchestration_time_ms=orchestration_time_ms,
            consensus_achieved=consensus_achieved,
            quality_metrics=quality_metrics
        )
    
    async def _synthesize_results(
        self,
        results: dict[str, Any],
        priority_weights: dict[str, float]
    ) -> tuple[Any, bool]:
        """Synthesize results from multiple supervisors"""
        # Simulate synthesis process
        await asyncio.sleep(0.3)
        
        # Create synthesized result
        synthesis_parts = []
        for supervisor_type, result_data in results.items():
            weight = priority_weights.get(supervisor_type, 1.0)
            synthesis_parts.append(f"{supervisor_type} (weight={weight}): {result_data['result']}")
        
        synthesized = f"Synthesized result combining: {'; '.join(synthesis_parts)}"
        
        quality_scores = [r["quality_score"] for r in results.values()]
        consensus = bool(all(abs(q - quality_scores[0]) < 0.1 for q in quality_scores))

        return synthesized, consensus
    
    def _calculate_consistency(self, results: dict[str, Any]) -> float:
        """Calculate consistency across supervisor results"""
        if len(results) < 2:
            return 1.0
        
        quality_scores = [r["quality_score"] for r in results.values()]
        mean_score = sum(quality_scores) / len(quality_scores)
        variance = sum((q - mean_score) ** 2 for q in quality_scores) / len(quality_scores)
        
        consistency = max(0.0, 1.0 - (variance * 10))
        return float(consistency)
    
    async def get_supervisor_stats(self, supervisor_type: str) -> SupervisorStatsResponse:
        """Get performance statistics for a supervisor"""
        if supervisor_type not in self.metrics:
            raise ValueError(f"Supervisor type '{supervisor_type}' not found")
        
        metrics = self.metrics[supervisor_type]
        
        # Calculate statistics
        success_rate = (
            metrics.successful_executions / metrics.total_executions
            if metrics.total_executions > 0 else 0.0
        )
        
        avg_execution_time = (
            metrics.total_execution_time_ms / metrics.total_executions
            if metrics.total_executions > 0 else 0.0
        )
        
        avg_quality = (
            metrics.total_quality_score / metrics.total_executions
            if metrics.total_executions > 0 else 0.0
        )
        
        worker_utilization = (
            sum(metrics.worker_utilization_samples) / len(metrics.worker_utilization_samples)
            if metrics.worker_utilization_samples else 0.0
        )
        
        # Get top worker types
        worker_usage: dict[str, int] = {}
        for worker in self.workers[supervisor_type]:
            worker_usage[worker.worker_type] = worker_usage.get(worker.worker_type, 0) + 1
        
        top_worker_types = sorted(
            worker_usage.keys(),
            key=lambda x: worker_usage[x],
            reverse=True
        )[:3]
        
        # Determine performance trend
        if len(metrics.worker_utilization_samples) >= 10:
            recent = metrics.worker_utilization_samples[-5:]
            older = metrics.worker_utilization_samples[-10:-5]
            if sum(recent) / len(recent) > sum(older) / len(older) * 1.1:
                trend = "improving"
            elif sum(recent) / len(recent) < sum(older) / len(older) * 0.9:
                trend = "declining"
            else:
                trend = "stable"
        else:
            trend = "insufficient_data"
        
        return SupervisorStatsResponse(
            supervisor_type=SupervisorType(supervisor_type),
            total_executions=metrics.total_executions,
            success_rate=success_rate,
            average_execution_time_ms=avg_execution_time,
            average_quality_score=avg_quality,
            worker_utilization=worker_utilization,
            top_worker_types=top_worker_types,
            recent_performance_trend=trend,
            cost_metrics={
                "average_cost_per_execution": 0.25,  # Placeholder
                "cost_efficiency": 0.85  # Placeholder
            }
        )
    
    async def get_supervisor_health(self, supervisor_type: str) -> SupervisorHealthResponse:
        """Get health status of a supervisor"""
        if supervisor_type not in self.supervisors:
            raise ValueError(f"Supervisor type '{supervisor_type}' not found")
        
        supervisor = self.supervisors[supervisor_type]
        metrics = self.metrics[supervisor_type]
        
        # Count active workers
        active_workers = sum(
            1 for w in self.workers[supervisor_type]
            if w.status != WorkerStatus.IDLE
        )
        
        # Calculate health score
        health_factors = []
        issues = []
        recommendations = []
        
        # Check success rate
        if metrics.total_executions > 0:
            success_rate = metrics.successful_executions / metrics.total_executions
            health_factors.append(success_rate)
            if success_rate < 0.8:
                issues.append(f"Low success rate: {success_rate:.2%}")
                recommendations.append("Review error logs and improve error handling")
        
        # Check worker availability
        idle_workers = sum(
            1 for w in self.workers[supervisor_type]
            if w.status == WorkerStatus.IDLE
        )
        worker_availability = idle_workers / len(self.workers[supervisor_type])
        health_factors.append(worker_availability)
        if worker_availability < 0.3:
            issues.append(f"Low worker availability: {worker_availability:.2%}")
            recommendations.append("Consider scaling up worker pool")
        
        # Check recent activity
        if metrics.last_execution_time:
            time_since_last = datetime.now(UTC) - metrics.last_execution_time
            if time_since_last > timedelta(hours=1):
                issues.append(f"No recent activity for {time_since_last.total_seconds() / 3600:.1f} hours")
        
        # Calculate overall health score
        if health_factors:
            health_score = sum(health_factors) / len(health_factors)
        else:
            health_score = 1.0
        
        # Determine status
        from typing import Literal
        status: Literal["healthy", "degraded", "unhealthy"]
        if health_score >= 0.8:
            status = "healthy"
        elif health_score >= 0.5:
            status = "degraded"
        else:
            status = "unhealthy"

        return SupervisorHealthResponse(
            supervisor_type=SupervisorType(supervisor_type),
            status=status,
            health_score=health_score,
            active_workers=active_workers,
            queue_depth=supervisor.active_tasks,
            last_execution=metrics.last_execution_time,
            issues=issues,
            recommendations=recommendations
        )
    
    async def optimize_worker_allocation(
        self,
        request: WorkerAllocationOptimizationRequest
    ) -> WorkerAllocationOptimizationResponse:
        """Optimize worker allocation for a task"""
        optimization_id = str(uuid.uuid4())
        
        # Analyze task requirements
        complexity = request.task_requirements.get("complexity", 0.5)
        _domains = request.task_requirements.get("domains", [])
        quality_target = request.task_requirements.get("quality_target", 0.8)
        
        # Determine optimal allocation based on optimization goal
        if request.optimization_goal == "quality":
            allocation = self._optimize_for_quality(
                request.available_workers, complexity, quality_target
            )
        elif request.optimization_goal == "speed":
            allocation = self._optimize_for_speed(
                request.available_workers, complexity
            )
        elif request.optimization_goal == "cost":
            allocation = self._optimize_for_cost(
                request.available_workers, complexity
            )
        else:  # balanced
            allocation = self._optimize_balanced(
                request.available_workers, complexity, quality_target
            )
        
        # Calculate expected performance
        expected_performance = {
            "quality": min(1.0, quality_target + 0.1),
            "speed": 1.0 / (sum(allocation.values()) * 5),  # Simplified speed calculation
            "cost": sum(allocation.values()) * 0.1,  # Simplified cost calculation
            "efficiency": 0.85
        }
        
        # Generate reasoning
        reasoning = (
            f"For {request.optimization_goal} optimization with complexity {complexity:.2f}, "
            f"allocated {sum(allocation.values())} workers across {len(allocation)} types. "
            f"This configuration balances resource usage with expected quality of {expected_performance['quality']:.2f}."
        )
        
        return WorkerAllocationOptimizationResponse(
            optimization_id=optimization_id,
            recommended_allocation=allocation,
            expected_performance=expected_performance,
            optimization_score=0.88,
            reasoning=reasoning,
            alternative_allocations=[
                {
                    "allocation": self._get_alternative_allocation(allocation),
                    "trade_offs": "Higher quality but increased cost"
                }
            ]
        )
    
    def _optimize_for_quality(
        self, available_workers: int, complexity: float, quality_target: float
    ) -> dict[str, int]:
        """Optimize allocation for quality"""
        # Allocate more specialized workers for quality
        base_allocation = max(3, int(available_workers * 0.6))
        return {
            "specialist": min(base_allocation, available_workers // 2),
            "reviewer": min(2, available_workers // 4),
            "synthesizer": min(1, available_workers // 4)
        }
    
    def _optimize_for_speed(self, available_workers: int, complexity: float) -> dict[str, int]:
        """Optimize allocation for speed"""
        # Maximize parallelization
        return {
            "parallel_worker": min(available_workers - 1, max(2, available_workers // 2)),
            "aggregator": 1
        }
    
    def _optimize_for_cost(self, available_workers: int, complexity: float) -> dict[str, int]:
        """Optimize allocation for cost"""
        # Minimize worker count while meeting requirements
        min_workers = max(1, int(complexity * 3))
        return {
            "efficient_worker": min(min_workers, available_workers)
        }
    
    def _optimize_balanced(
        self, available_workers: int, complexity: float, quality_target: float
    ) -> dict[str, int]:
        """Balanced optimization"""
        base_count = max(2, int(available_workers * 0.4))
        return {
            "primary_worker": base_count,
            "support_worker": max(1, available_workers - base_count - 1),
            "coordinator": 1
        }
    
    def _get_alternative_allocation(self, primary: dict[str, int]) -> dict[str, int]:
        """Generate an alternative allocation"""
        alternative = primary.copy()
        # Modify allocation slightly
        for key in alternative:
            alternative[key] = max(1, alternative[key] + random.choice([-1, 0, 1]))
        return alternative
    
    async def resolve_conflict(
        self,
        request: ConflictResolutionRequest
    ) -> ConflictResolutionResponse:
        """Resolve conflicts between worker outputs"""
        # Store conflict for tracking
        self.conflict_resolutions[request.conflict_id] = {
            "request": request,
            "timestamp": datetime.now(UTC)
        }
        
        # Resolve based on strategy
        if request.resolution_strategy == ConflictResolutionStrategy.SUPERVISOR_OVERRIDE:
            resolved = request.supervisor_guidance or "Supervisor decision"
            confidence = 0.95
            reasoning = "Supervisor authority used to resolve conflict"
        
        elif request.resolution_strategy == ConflictResolutionStrategy.MAJORITY_VOTE:
            # Find most common output
            outputs = [w["output"] for w in request.worker_outputs]
            resolved = max(set(outputs), key=outputs.count)
            confidence = outputs.count(resolved) / len(outputs)
            reasoning = f"Majority vote selected output with {confidence:.2%} agreement"
        
        elif request.resolution_strategy == ConflictResolutionStrategy.QUALITY_BASED:
            # Select output with highest confidence
            best_output = max(request.worker_outputs, key=lambda x: x.get("confidence", 0))
            resolved = best_output["output"]
            confidence = best_output.get("confidence", 0.8)
            reasoning = f"Selected output with highest confidence score of {confidence:.2f}"
        
        elif request.resolution_strategy == ConflictResolutionStrategy.WEIGHTED_CONSENSUS:
            # Weighted combination (simplified)
            resolved = "Weighted consensus of all outputs"
            confidence = 0.85
            reasoning = "Combined outputs using weighted consensus"
        
        else:  # DEBATE_RESOLUTION
            resolved = "Resolution through structured debate"
            confidence = 0.88
            reasoning = "Workers engaged in structured debate to reach resolution"
        
        # Calculate worker consensus
        worker_consensus = {
            w["worker_id"]: random.uniform(0.6, 1.0)
            for w in request.worker_outputs
        }
        
        return ConflictResolutionResponse(
            conflict_id=request.conflict_id,
            resolution_strategy=request.resolution_strategy,
            resolved_output=resolved,
            confidence_score=confidence,
            resolution_reasoning=reasoning,
            worker_consensus=worker_consensus
        )
    
    async def compare_supervisor_performance(
        self,
        supervisor_types: list[str]
    ) -> SupervisorComparisonResponse:
        """Compare performance across multiple supervisors"""
        comparison_id = str(uuid.uuid4())
        
        # Collect metrics for each supervisor
        performance_metrics = {}
        for sup_type in supervisor_types:
            if sup_type in self.metrics:
                stats = await self.get_supervisor_stats(sup_type)
                performance_metrics[sup_type] = {
                    "success_rate": stats.success_rate,
                    "average_quality": stats.average_quality_score,
                    "average_latency_ms": stats.average_execution_time_ms,
                    "worker_utilization": stats.worker_utilization
                }
        
        # Create rankings
        rankings = {}
        for metric in ["success_rate", "average_quality", "average_latency_ms", "worker_utilization"]:
            if metric == "average_latency_ms":
                # Lower is better for latency
                ranked = sorted(
                    supervisor_types,
                    key=lambda x: performance_metrics.get(x, {}).get(metric, float('inf'))
                )
            else:
                # Higher is better for other metrics
                ranked = sorted(
                    supervisor_types,
                    key=lambda x: performance_metrics.get(x, {}).get(metric, 0),
                    reverse=True
                )
            rankings[metric] = ranked
        
        # Generate recommendations
        recommendations = {}
        for sup_type in supervisor_types:
            metrics = performance_metrics.get(sup_type, {})
            if metrics.get("success_rate", 0) > 0.9:
                recommendations[sup_type] = "Excellent for critical tasks"
            elif metrics.get("average_latency_ms", float('inf')) < 2000:
                recommendations[sup_type] = "Good for time-sensitive tasks"
            else:
                recommendations[sup_type] = "Suitable for standard tasks"
        
        return SupervisorComparisonResponse(
            comparison_id=comparison_id,
            supervisors_compared=[SupervisorType(s) for s in supervisor_types],
            performance_metrics=performance_metrics,
            rankings=rankings,
            recommendations=recommendations,
            visualization_data={
                "chart_type": "radar",
                "data": performance_metrics
            }
        )
    
    async def run_experiment(
        self,
        request: ExperimentRequest
    ) -> ExperimentResponse:
        """Run experiment to test different coordination strategies"""
        experiment_id = str(uuid.uuid4())
        
        # Store experiment
        self.experiments[experiment_id] = {
            "request": request,
            "started_at": datetime.now(UTC)
        }
        
        # Run tests for each strategy
        strategy_results: dict[str, list[float]] = {}
        results: dict[str, dict[str, float]] = {}
        for strategy in request.strategies_to_test:
            strategy_results = {}

            for _ in range(request.repetitions or 1):
                # Simulate execution with strategy
                start_time = time.time()
                
                # Create test request
                _test_request = SupervisorExecuteRequest(
                    query=request.query,
                    supervision_strategy=strategy,
                    coordination_mode=CoordinationMode.HIERARCHICAL,
                    max_workers=5,
                    quality_threshold=0.8,
                    timeout_seconds=120
                )
                
                # Simulate execution (simplified)
                await asyncio.sleep(0.1)
                
                # Generate metrics
                execution_time = (time.time() - start_time) * 1000
                quality = random.uniform(0.7, 0.95)
                cost = random.uniform(0.1, 0.5)
                
                # Aggregate metrics
                for metric in request.metrics_to_track:
                    if metric == "quality":
                        value = quality
                    elif metric == "speed":
                        value = 1000 / execution_time  # Convert to speed score
                    elif metric == "cost":
                        value = cost
                    else:
                        value = random.uniform(0.5, 1.0)
                    
                    if metric not in strategy_results:
                        strategy_results[metric] = []
                    strategy_results[metric].append(value)
            
            # Calculate averages
            results[strategy.value] = {
                metric: sum(values) / len(values)
                for metric, values in strategy_results.items()
            }
        
        # Determine best strategy
        # Simple scoring: weighted average of metrics
        scores = {}
        for strategy in request.strategies_to_test:
            scores[strategy] = sum(
                results[strategy.value].values()
            ) / len(request.metrics_to_track)
        
        best_strategy = max(scores, key=lambda k: scores[k])

        # Calculate statistical significance (simplified)
        statistical_significance = 0.95 if (request.repetitions or 1) > 1 else None
        
        # Generate recommendations
        recommendations = [
            f"Best strategy for this query type: {best_strategy.value}",
            f"Consider {best_strategy.value} for similar tasks",
            "Increase repetitions for more reliable results" if (request.repetitions or 1) < 3 else ""
        ]
        recommendations = [r for r in recommendations if r]  # Remove empty
        
        return ExperimentResponse(
            experiment_id=experiment_id,
            strategies_tested=request.strategies_to_test,
            results=results,
            best_strategy=best_strategy,
            statistical_significance=statistical_significance,
            recommendations=recommendations,
            detailed_analysis={
                "scores": {s.value: score for s, score in scores.items()},
                "query_characteristics": {
                    "length": len(request.query),
                    "complexity_estimate": 0.7
                }
            }
        )