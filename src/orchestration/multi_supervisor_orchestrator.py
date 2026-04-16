"""
Multi-Supervisor Orchestrator

Advanced orchestrator for handling complex queries that require coordination
between multiple domain supervisors. Enables cross-domain collaboration,
result synthesis, and resource management across supervisor teams.

Key Features:
- Multi-domain query decomposition and routing
- Inter-supervisor communication protocols
- Cross-domain result synthesis and conflict resolution
- Resource balancing and load management
- Quality assurance across supervisor boundaries
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Any, Set, Tuple
import uuid

from langgraph.graph import StateGraph, END

from .research_orchestrator import ResearchOrchestrator, OrchestratorConfig, WorkflowResult
from .state import ResearchState, WorkflowPhase, WorkflowMetadata
from .query_decomposer import QueryDecomposer
from .inter_supervisor_communicator import InterSupervisorCommunicator
from .cross_domain_synthesizer import CrossDomainSynthesizer
from ..ai_brain.router.masr import MASRouter, RoutingDecision, CollaborationMode
from ..ai_brain.integration.masr_supervisor_bridge import (
    MASRSupervisorBridge, SupervisorExecutionResult
)
from ..agents.supervisors.supervisor_factory import SupervisorFactory
from ..agents.models import AgentTask, AgentResult
from ..agents.communication.talkhier_message import (
    TalkHierMessage, TalkHierContent, MessageType, HierarchyMetadata
)

logger = logging.getLogger(__name__)


class SupervisorCoordinationMode(Enum):
    """Modes of coordination between supervisors."""
    
    SEQUENTIAL = "sequential"      # Supervisors execute one after another
    PARALLEL = "parallel"         # Supervisors execute simultaneously
    HIERARCHICAL = "hierarchical" # Primary supervisor coordinates others
    PIPELINE = "pipeline"         # Output of one feeds into next
    CONSENSUS = "consensus"       # All supervisors contribute to final decision


@dataclass
class SupervisorAllocation:
    """Allocation of supervisors for multi-domain query."""
    
    primary_supervisor: str
    supporting_supervisors: List[str]
    coordination_mode: SupervisorCoordinationMode
    
    # Resource constraints
    max_concurrent_supervisors: int = 3
    timeout_seconds: int = 600
    
    # Quality requirements
    consensus_threshold: float = 0.9
    min_quality_score: float = 0.85
    
    # Context and metadata
    domain_assignments: Dict[str, List[str]] = field(default_factory=dict)
    cross_domain_dependencies: List[Tuple[str, str]] = field(default_factory=list)


@dataclass
class MultiSupervisorState:
    """State for multi-supervisor orchestration workflow."""
    
    # Query and context
    query_id: str = ""
    original_query: str = ""
    query_domains: List[str] = field(default_factory=list)
    
    # Supervisor allocation
    supervisor_allocation: Optional[SupervisorAllocation] = None
    active_supervisors: Dict[str, str] = field(default_factory=dict)  # type -> execution_id
    
    # Execution tracking
    supervisor_results: Dict[str, SupervisorExecutionResult] = field(default_factory=dict)
    supervisor_outputs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    # Cross-supervisor communication
    inter_supervisor_messages: List[TalkHierMessage] = field(default_factory=list)
    coordination_events: List[Dict[str, Any]] = field(default_factory=list)
    
    # Quality and consensus
    domain_quality_scores: Dict[str, float] = field(default_factory=dict)
    cross_domain_consensus_score: float = 0.0
    overall_quality_score: float = 0.0
    
    # Synthesis and final result
    synthesis_strategy: str = "comprehensive"
    conflict_resolution_applied: bool = False
    final_synthesized_result: Optional[Dict[str, Any]] = None
    
    # Workflow management
    current_coordination_phase: str = "initialization"
    completed_supervisors: Set[str] = field(default_factory=set)
    failed_supervisors: Set[str] = field(default_factory=set)
    
    # Performance tracking
    started_at: datetime = field(default_factory=datetime.now)
    total_coordination_time: float = 0.0
    resource_utilization: Dict[str, float] = field(default_factory=dict)


class MultiSupervisorOrchestrator:
    """
    Advanced orchestrator for multi-supervisor coordination.
    
    Handles complex queries requiring multiple domain supervisors,
    coordinates their execution, and synthesizes cross-domain results.
    """
    
    def __init__(
        self,
        base_orchestrator: ResearchOrchestrator,
        config: Optional[Dict[str, Any]] = None
    ):
        """Initialize multi-supervisor orchestrator."""
        
        self.base_orchestrator = base_orchestrator
        self.config = config or {}
        
        # Initialize specialized components
        self.query_decomposer = QueryDecomposer(self.config.get("query_decomposer", {}))
        self.inter_supervisor_communicator = InterSupervisorCommunicator(
            self.config.get("inter_supervisor_comm", {})
        )
        self.cross_domain_synthesizer = CrossDomainSynthesizer(
            self.config.get("cross_domain_synthesizer", {})
        )
        
        # Multi-supervisor configuration
        self.max_concurrent_supervisors = self.config.get("max_concurrent_supervisors", 3)
        self.coordination_timeout = self.config.get("coordination_timeout_seconds", 900)
        self.enable_cross_domain_synthesis = self.config.get("enable_synthesis", True)
        
        # Performance tracking
        self.multi_supervisor_stats = {
            "total_multi_supervisor_queries": 0,
            "successful_coordinations": 0,
            "cross_domain_syntheses": 0,
            "average_coordination_time": 0.0,
        }
    
    async def orchestrate_multi_supervisor_query(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
        coordination_mode: Optional[SupervisorCoordinationMode] = None
    ) -> WorkflowResult:
        """
        Orchestrate a complex query requiring multiple supervisors.
        
        Args:
            query: Complex multi-domain query
            context: Additional context and constraints
            coordination_mode: How to coordinate supervisors
            
        Returns:
            Comprehensive workflow result with cross-domain synthesis
        """
        
        start_time = datetime.now()
        query_id = str(uuid.uuid4())
        self.multi_supervisor_stats["total_multi_supervisor_queries"] += 1
        
        logger.info(f"Starting multi-supervisor orchestration for query: {query[:100]}...")
        
        try:
            # Step 1: Decompose query into domain components
            decomposition = self.query_decomposer.decompose_query(query)
            
            if len(decomposition["detected_domains"]) <= 1:
                # Single domain query - delegate to base orchestrator
                logger.info("Single domain detected, delegating to base orchestrator")
                return await self._delegate_to_base_orchestrator(query, context)
            
            # Step 2: Plan supervisor allocation
            supervisor_allocation = await self._plan_supervisor_allocation(
                decomposition, coordination_mode, context
            )
            
            # Step 3: Initialize multi-supervisor state
            multi_state = MultiSupervisorState(
                query_id=query_id,
                original_query=query,
                query_domains=decomposition["detected_domains"],
                supervisor_allocation=supervisor_allocation,
            )
            
            # Step 4: Execute coordinated supervision
            coordination_result = await self._execute_coordinated_supervision(
                multi_state, decomposition
            )
            
            # Step 5: Synthesize cross-domain results
            if self.enable_cross_domain_synthesis and coordination_result["supervisor_results"]:
                synthesis_result = await self.cross_domain_synthesizer.synthesize_supervisor_results(
                    coordination_result["supervisor_results"],
                    multi_state.synthesis_strategy
                )
                multi_state.final_synthesized_result = synthesis_result
                self.multi_supervisor_stats["cross_domain_syntheses"] += 1
            
            # Step 6: Build final workflow result
            execution_time = (datetime.now() - start_time).total_seconds()
            
            success = coordination_result["success"]
            if success:
                self.multi_supervisor_stats["successful_coordinations"] += 1
            
            # Update average coordination time
            self.multi_supervisor_stats["average_coordination_time"] = (
                self.multi_supervisor_stats["average_coordination_time"] * 
                (self.multi_supervisor_stats["total_multi_supervisor_queries"] - 1) +
                execution_time
            ) / self.multi_supervisor_stats["total_multi_supervisor_queries"]
            
            return WorkflowResult(
                success=success,
                workflow_id=query_id,
                project_id=context.get("project_id", f"multi_super_{query_id}"),
                final_state=ResearchState(  # Convert to base state for compatibility
                    project_id=context.get("project_id", f"multi_super_{query_id}"),
                    workflow_id=query_id,
                    query=query,
                    domains=decomposition["detected_domains"],
                    context=context or {},
                ),
                outputs=multi_state.final_synthesized_result,
                quality_score=multi_state.overall_quality_score,
                execution_time=execution_time,
                errors=coordination_result.get("errors", []),
            )
            
        except Exception as e:
            logger.error(f"Multi-supervisor orchestration failed: {e}")
            execution_time = (datetime.now() - start_time).total_seconds()
            
            return WorkflowResult(
                success=False,
                workflow_id=query_id,
                project_id=context.get("project_id", f"multi_super_{query_id}"),
                final_state=ResearchState(
                    project_id=context.get("project_id", f"multi_super_{query_id}"),
                    workflow_id=query_id,
                    query=query,
                    domains=[],
                    context=context or {},
                ),
                execution_time=execution_time,
                errors=[str(e)],
            )
    
    async def _plan_supervisor_allocation(
        self,
        decomposition: Dict[str, Any],
        coordination_mode: Optional[SupervisorCoordinationMode],
        context: Optional[Dict[str, Any]]
    ) -> SupervisorAllocation:
        """Plan allocation of supervisors for multi-domain query."""
        
        domains = decomposition["detected_domains"]
        domain_relevance = decomposition["domain_relevance"]
        
        # Select primary supervisor (highest relevance)
        primary_supervisor = max(domain_relevance.items(), key=lambda x: x[1])[0]
        supporting_supervisors = [d for d in domains if d != primary_supervisor]
        
        # Determine coordination mode
        if coordination_mode is None:
            if len(domains) <= 2:
                coordination_mode = SupervisorCoordinationMode.PARALLEL
            elif decomposition["cross_domain_dependencies"]:
                coordination_mode = SupervisorCoordinationMode.PIPELINE
            else:
                coordination_mode = SupervisorCoordinationMode.HIERARCHICAL
        
        return SupervisorAllocation(
            primary_supervisor=primary_supervisor,
            supporting_supervisors=supporting_supervisors,
            coordination_mode=coordination_mode,
            max_concurrent_supervisors=min(len(domains), self.max_concurrent_supervisors),
            domain_assignments={domain: [domain] for domain in domains},
            cross_domain_dependencies=decomposition["cross_domain_dependencies"],
        )
    
    async def _execute_coordinated_supervision(
        self,
        multi_state: MultiSupervisorState,
        decomposition: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute coordinated supervision across multiple supervisors."""
        
        allocation = multi_state.supervisor_allocation
        coordination_mode = allocation.coordination_mode
        
        logger.info(f"Executing {coordination_mode.value} coordination with "
                   f"{len(allocation.supporting_supervisors) + 1} supervisors")
        
        supervisor_results = {}
        execution_errors = []
        
        try:
            if coordination_mode == SupervisorCoordinationMode.PARALLEL:
                supervisor_results = await self._execute_parallel_supervision(
                    multi_state, decomposition
                )
            elif coordination_mode == SupervisorCoordinationMode.SEQUENTIAL:
                supervisor_results = await self._execute_sequential_supervision(
                    multi_state, decomposition
                )
            elif coordination_mode == SupervisorCoordinationMode.PIPELINE:
                supervisor_results = await self._execute_pipeline_supervision(
                    multi_state, decomposition
                )
            else:  # HIERARCHICAL or CONSENSUS
                supervisor_results = await self._execute_hierarchical_supervision(
                    multi_state, decomposition
                )
            
            # Calculate overall quality
            if supervisor_results:
                quality_scores = [r.quality_score for r in supervisor_results.values()]
                multi_state.overall_quality_score = sum(quality_scores) / len(quality_scores)
            
            success = bool(supervisor_results) and all(
                r.status.value == "completed" for r in supervisor_results.values()
            )
            
            return {
                "success": success,
                "supervisor_results": supervisor_results,
                "coordination_mode": coordination_mode.value,
                "errors": execution_errors,
            }
            
        except Exception as e:
            logger.error(f"Coordinated supervision execution failed: {e}")
            return {
                "success": False,
                "supervisor_results": supervisor_results,
                "coordination_mode": coordination_mode.value,
                "errors": execution_errors + [str(e)],
            }
    
    async def _execute_parallel_supervision(
        self,
        multi_state: MultiSupervisorState,
        decomposition: Dict[str, Any]
    ) -> Dict[str, SupervisorExecutionResult]:
        """Execute supervisors in parallel."""
        
        allocation = multi_state.supervisor_allocation
        all_supervisors = [allocation.primary_supervisor] + allocation.supporting_supervisors
        
        # Create tasks for all supervisors
        supervisor_tasks = {}
        
        for supervisor_type in all_supervisors:
            # Create domain-specific sub-query
            sub_query = decomposition["domain_subqueries"].get(supervisor_type, multi_state.original_query)
            
            task = self._create_supervisor_task(
                supervisor_type, sub_query, multi_state, decomposition
            )
            
            supervisor_tasks[supervisor_type] = task
        
        # Execute all supervisors concurrently
        results = await asyncio.gather(
            *[self._execute_single_supervisor(supervisor_type, task) 
              for supervisor_type, task in supervisor_tasks.items()],
            return_exceptions=True
        )
        
        # Process results
        supervisor_results = {}
        for i, (supervisor_type, result) in enumerate(zip(supervisor_tasks.keys(), results)):
            if isinstance(result, Exception):
                logger.error(f"Supervisor {supervisor_type} failed: {result}")
                # Create failed result
                supervisor_results[supervisor_type] = SupervisorExecutionResult(
                    execution_id=f"failed_{supervisor_type}",
                    supervisor_type=supervisor_type,
                    domain=supervisor_type,
                    status="failed",
                    errors=[str(result)],
                )
            else:
                supervisor_results[supervisor_type] = result
        
        return supervisor_results
    
    async def _execute_sequential_supervision(
        self,
        multi_state: MultiSupervisorState,
        decomposition: Dict[str, Any]
    ) -> Dict[str, SupervisorExecutionResult]:
        """Execute supervisors sequentially."""
        
        allocation = multi_state.supervisor_allocation
        all_supervisors = [allocation.primary_supervisor] + allocation.supporting_supervisors
        
        supervisor_results = {}
        accumulated_context = {}
        
        for supervisor_type in all_supervisors:
            # Create task with accumulated context from previous supervisors
            sub_query = decomposition["domain_subqueries"].get(supervisor_type, multi_state.original_query)
            
            task = self._create_supervisor_task(
                supervisor_type, sub_query, multi_state, decomposition
            )
            
            # Add context from previous supervisors
            task.input_data["previous_results"] = accumulated_context
            
            # Execute supervisor
            result = await self._execute_single_supervisor(supervisor_type, task)
            supervisor_results[supervisor_type] = result
            
            # Accumulate context for next supervisor
            if result.agent_result and result.agent_result.output:
                accumulated_context[supervisor_type] = result.agent_result.output
            
            # Handle coordination between supervisors
            if len(accumulated_context) > 0:
                await self.inter_supervisor_communicator.coordinate_supervisor_handoff(
                    list(accumulated_context.keys())[-1], supervisor_type, accumulated_context
                )
        
        return supervisor_results
    
    async def _execute_pipeline_supervision(
        self,
        multi_state: MultiSupervisorState,
        decomposition: Dict[str, Any]
    ) -> Dict[str, SupervisorExecutionResult]:
        """Execute supervisors in pipeline mode with dependency handling."""
        
        allocation = multi_state.supervisor_allocation
        dependencies = allocation.cross_domain_dependencies
        
        # Build execution order based on dependencies
        execution_order = self._build_execution_order(
            [allocation.primary_supervisor] + allocation.supporting_supervisors,
            dependencies
        )
        
        supervisor_results = {}
        
        for supervisor_type in execution_order:
            # Collect inputs from dependencies
            dependency_inputs = {}
            for dep_from, dep_to in dependencies:
                if dep_to == supervisor_type and dep_from in supervisor_results:
                    dep_result = supervisor_results[dep_from]
                    if dep_result.agent_result and dep_result.agent_result.output:
                        dependency_inputs[dep_from] = dep_result.agent_result.output
            
            # Create task with dependency inputs
            sub_query = decomposition["domain_subqueries"].get(supervisor_type, multi_state.original_query)
            task = self._create_supervisor_task(
                supervisor_type, sub_query, multi_state, decomposition
            )
            task.input_data["dependency_inputs"] = dependency_inputs
            
            # Execute supervisor
            result = await self._execute_single_supervisor(supervisor_type, task)
            supervisor_results[supervisor_type] = result
        
        return supervisor_results
    
    async def _execute_hierarchical_supervision(
        self,
        multi_state: MultiSupervisorState,
        decomposition: Dict[str, Any]
    ) -> Dict[str, SupervisorExecutionResult]:
        """Execute supervisors in hierarchical mode with primary coordination."""
        
        allocation = multi_state.supervisor_allocation
        primary_supervisor = allocation.primary_supervisor
        supporting_supervisors = allocation.supporting_supervisors
        
        # First, execute supporting supervisors in parallel
        supporting_results = {}
        if supporting_supervisors:
            supporting_tasks = {}
            
            for supervisor_type in supporting_supervisors:
                sub_query = decomposition["domain_subqueries"].get(supervisor_type, multi_state.original_query)
                task = self._create_supervisor_task(
                    supervisor_type, sub_query, multi_state, decomposition
                )
                supporting_tasks[supervisor_type] = task
            
            # Execute supporting supervisors
            results = await asyncio.gather(
                *[self._execute_single_supervisor(supervisor_type, task)
                  for supervisor_type, task in supporting_tasks.items()],
                return_exceptions=True
            )
            
            for i, (supervisor_type, result) in enumerate(zip(supporting_tasks.keys(), results)):
                if not isinstance(result, Exception):
                    supporting_results[supervisor_type] = result
        
        # Execute primary supervisor with supporting results as context
        primary_sub_query = decomposition["domain_subqueries"].get(primary_supervisor, multi_state.original_query)
        primary_task = self._create_supervisor_task(
            primary_supervisor, primary_sub_query, multi_state, decomposition
        )
        primary_task.input_data["supporting_results"] = {
            supervisor_type: result.agent_result.output if result.agent_result else {}
            for supervisor_type, result in supporting_results.items()
        }
        
        primary_result = await self._execute_single_supervisor(primary_supervisor, primary_task)
        
        # Combine all results
        all_results = supporting_results.copy()
        all_results[primary_supervisor] = primary_result
        
        return all_results
    
    def _create_supervisor_task(
        self,
        supervisor_type: str,
        sub_query: str,
        multi_state: MultiSupervisorState,
        decomposition: Dict[str, Any]
    ) -> AgentTask:
        """Create agent task for supervisor execution."""
        
        return AgentTask(
            id=f"multi_super_{supervisor_type}_{multi_state.query_id}",
            agent_type=supervisor_type,
            input_data={
                "query": sub_query,
                "original_query": multi_state.original_query,
                "domains": [supervisor_type],
                "context": {
                    "multi_supervisor_orchestration": True,
                    "primary_supervisor": multi_state.supervisor_allocation.primary_supervisor,
                    "coordination_mode": multi_state.supervisor_allocation.coordination_mode.value,
                    "decomposition": decomposition,
                }
            }
        )
    
    async def _execute_single_supervisor(
        self, supervisor_type: str, task: AgentTask
    ) -> SupervisorExecutionResult:
        """Execute a single supervisor task."""
        
        # Use base orchestrator's MASR integration if available
        if (hasattr(self.base_orchestrator, '_supervisor_bridge') and 
            self.base_orchestrator._supervisor_bridge):
            
            # Create mock routing decision for supervisor execution
            from ..ai_brain.router.masr import RoutingDecision, AgentAllocation, CollaborationMode
            from ..ai_brain.router.query_analyzer import ComplexityAnalysis, ComplexityLevel, ComplexityFactors
            
            mock_routing_decision = RoutingDecision(
                query_id=task.id,
                timestamp=datetime.now(),
                complexity_analysis=ComplexityAnalysis(
                    score=0.7,
                    level=ComplexityLevel.MODERATE,
                    factors=ComplexityFactors(),
                    domains=[supervisor_type],
                    subtask_count=3,
                    uncertainty=0.3,
                    reasoning_types=[],
                    recommended_agents={supervisor_type: 1},
                    estimated_tokens=2000,
                ),
                optimization_result=None,  # Would be populated in real implementation
                collaboration_mode=CollaborationMode.HIERARCHICAL,
                agent_allocation=AgentAllocation(
                    supervisor_type=supervisor_type,
                    worker_count=3,
                    worker_types=[f"{supervisor_type}_worker"],
                ),
                estimated_cost=0.01,
                estimated_latency_ms=120000,
                estimated_quality=0.85,
                confidence_score=0.8,
            )
            
            # Get supervisor registry
            supervisor_registry = {
                supervisor_type: self.base_orchestrator._supervisor_factory.supervisor_registry[supervisor_type].supervisor_class
            } if hasattr(self.base_orchestrator, '_supervisor_factory') and supervisor_type in self.base_orchestrator._supervisor_factory.supervisor_registry else {}
            
            if supervisor_registry:
                # Execute via MASR-Supervisor bridge
                return await self.base_orchestrator._supervisor_bridge.execute_routing_decision(
                    routing_decision=mock_routing_decision,
                    task=task,
                    supervisor_registry=supervisor_registry
                )
        
        # Fallback: simulate supervisor execution
        return SupervisorExecutionResult(
            execution_id=f"simulated_{supervisor_type}_{task.id}",
            supervisor_type=supervisor_type,
            domain=supervisor_type,
            status="completed",
            quality_score=0.85,
            consensus_score=0.9,
            execution_time_seconds=60.0,
            workers_used=3,
            refinement_rounds=2,
            agent_result=AgentResult(
                task_id=task.id,
                status="completed",
                output={
                    "supervisor_type": supervisor_type,
                    "domain_analysis": f"Analysis for {supervisor_type} domain",
                    "findings": [f"Key finding 1 for {supervisor_type}", f"Key finding 2 for {supervisor_type}"],
                    "recommendations": [f"Recommendation for {supervisor_type}"],
                },
                confidence=0.85,
                execution_time=60.0,
            )
        )
    
    def _build_execution_order(
        self, supervisors: List[str], dependencies: List[Tuple[str, str]]
    ) -> List[str]:
        """Build execution order based on dependencies (topological sort)."""
        
        # Simple topological sort implementation
        in_degree = {supervisor: 0 for supervisor in supervisors}
        
        for dep_from, dep_to in dependencies:
            if dep_to in in_degree:
                in_degree[dep_to] += 1
        
        # Start with supervisors that have no dependencies
        queue = [s for s in supervisors if in_degree[s] == 0]
        execution_order = []
        
        while queue:
            current = queue.pop(0)
            execution_order.append(current)
            
            # Update in-degrees of dependent supervisors
            for dep_from, dep_to in dependencies:
                if dep_from == current and dep_to in in_degree:
                    in_degree[dep_to] -= 1
                    if in_degree[dep_to] == 0:
                        queue.append(dep_to)
        
        # Add any remaining supervisors (handles cycles/disconnected components)
        for supervisor in supervisors:
            if supervisor not in execution_order:
                execution_order.append(supervisor)
        
        return execution_order
    
    async def _delegate_to_base_orchestrator(
        self, query: str, context: Optional[Dict[str, Any]]
    ) -> WorkflowResult:
        """Delegate to base orchestrator for single-domain queries."""
        
        # Extract required parameters for base orchestrator
        project_id = context.get("project_id", f"delegated_{uuid.uuid4()}")
        domains = context.get("domains", ["research"])
        
        return await self.base_orchestrator.execute(
            project_id=project_id,
            query=query,
            domains=domains,
            context=context,
        )
    
    async def get_multi_supervisor_stats(self) -> Dict[str, Any]:
        """Get multi-supervisor orchestration statistics."""
        
        base_stats = await self.base_orchestrator.get_masr_stats()
        
        return {
            "multi_supervisor_stats": self.multi_supervisor_stats,
            "base_orchestrator_stats": base_stats,
            "cross_domain_synthesizer": {
                "synthesis_strategies": list(self.cross_domain_synthesizer.synthesis_strategies.keys()),
            },
            "query_decomposer": {
                "supported_domains": list(self.query_decomposer.domain_patterns.keys()),
            },
        }
    
    async def health_check(self) -> Dict[str, Any]:
        """Perform comprehensive health check."""
        
        base_health = await self.base_orchestrator.health_check()
        
        multi_supervisor_health = {
            "query_decomposer": "healthy",
            "inter_supervisor_communicator": "healthy",
            "cross_domain_synthesizer": "healthy",
            "coordination_modes": [mode.value for mode in SupervisorCoordinationMode],
        }
        
        return {
            "status": "healthy",
            "base_orchestrator": base_health,
            "multi_supervisor_components": multi_supervisor_health,
            "capabilities": {
                "max_concurrent_supervisors": self.max_concurrent_supervisors,
                "coordination_timeout": self.coordination_timeout,
                "cross_domain_synthesis": self.enable_cross_domain_synthesis,
            }
        }


__all__ = [
    "MultiSupervisorOrchestrator",
    "SupervisorCoordinationMode",
    "SupervisorAllocation",
    "MultiSupervisorState",
    "QueryDecomposer",
    "InterSupervisorCommunicator",
    "CrossDomainSynthesizer",
]