"""
API Pattern Experiments Integration

This module enables experimentation with different API execution patterns,
including Primary API vs Bypass API usage, execution modes (Chain vs Mixture),
and query handling strategies for the Agent Framework.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import json

from ..core.unified_experiment_manager import UnifiedExperimentManager
from ..core.adaptive_allocation_engine import AdaptiveAllocationEngine

logger = logging.getLogger(__name__)


class APIPattern(Enum):
    """API execution patterns to test."""
    
    PRIMARY_API = "primary_api"  # Standard hierarchical execution
    BYPASS_API = "bypass_api"    # Direct execution bypassing hierarchy
    HYBRID = "hybrid"             # Intelligent switching between patterns
    PARALLEL = "parallel"         # Parallel execution of both patterns


class ExecutionMode(Enum):
    """Execution modes for agent coordination."""
    
    CHAIN = "chain"           # Sequential chaining of agents
    MIXTURE = "mixture"       # Mixed execution with interdependencies
    PARALLEL = "parallel"     # Fully parallel execution
    HIERARCHICAL = "hierarchical"  # Supervisor-worker hierarchy


@dataclass
class APIExperimentConfig:
    """Configuration for API pattern experiments."""
    
    experiment_id: str
    api_patterns: List[APIPattern]
    execution_modes: List[ExecutionMode]
    query_types: List[str]  # Types of queries to test
    metrics: List[str] = field(default_factory=lambda: [
        "latency_ms",
        "total_cost",
        "quality_score",
        "token_usage",
        "api_calls",
        "error_rate"
    ])
    allocation_strategy: str = "thompson_sampling"
    min_samples: int = 50


@dataclass
class APIExecutionResult:
    """Result from API pattern execution."""
    
    request_id: str
    pattern: APIPattern
    execution_mode: ExecutionMode
    query_type: str
    latency_ms: float
    cost: float
    quality_score: float
    token_usage: int
    api_calls: int
    success: bool
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class APIPatternExperimentor:
    """
    Manages experiments for API execution patterns and strategies.
    
    This class integrates with the Agent Framework to test different
    API patterns and execution modes for optimal performance.
    """
    
    def __init__(self):
        """Initialize API pattern experimentor."""
        self.experiment_manager = UnifiedExperimentManager()
        self.allocation_engine = AdaptiveAllocationEngine()
        
        # Active experiments
        self.active_experiments: Dict[str, APIExperimentConfig] = {}
        
        # Execution handlers
        self.pattern_handlers: Dict[APIPattern, Callable] = {}
        self.mode_handlers: Dict[ExecutionMode, Callable] = {}
        
        # Results storage
        self.results: List[APIExecutionResult] = []
        
        # Initialize handlers
        self._initialize_handlers()
    
    def _initialize_handlers(self):
        """Initialize execution handlers for patterns and modes."""
        # Pattern handlers
        self.pattern_handlers = {
            APIPattern.PRIMARY_API: self._execute_primary_api,
            APIPattern.BYPASS_API: self._execute_bypass_api,
            APIPattern.HYBRID: self._execute_hybrid,
            APIPattern.PARALLEL: self._execute_parallel_apis
        }
        
        # Mode handlers
        self.mode_handlers = {
            ExecutionMode.CHAIN: self._execute_chain_mode,
            ExecutionMode.MIXTURE: self._execute_mixture_mode,
            ExecutionMode.PARALLEL: self._execute_parallel_mode,
            ExecutionMode.HIERARCHICAL: self._execute_hierarchical_mode
        }
    
    async def create_experiment(self, config: APIExperimentConfig) -> bool:
        """
        Create a new API pattern experiment.
        
        Args:
            config: Experiment configuration
            
        Returns:
            Success status
        """
        try:
            # Create variants for all pattern-mode combinations
            variants = []
            for pattern in config.api_patterns:
                for mode in config.execution_modes:
                    variant_id = f"{pattern.value}_{mode.value}"
                    variants.append(variant_id)
            
            # Register with experiment manager
            await self.experiment_manager.create_experiment(
                experiment_id=config.experiment_id,
                experiment_type="api_pattern",
                variants=variants,
                allocation_strategy=config.allocation_strategy,
                metrics=config.metrics
            )
            
            # Store configuration
            self.active_experiments[config.experiment_id] = config
            
            logger.info(f"Created API pattern experiment: {config.experiment_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create experiment: {e}")
            return False
    
    async def execute_with_experiment(self,
                                     query: str,
                                     query_type: str,
                                     context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Execute query with experimental API pattern selection.
        
        Args:
            query: Query to execute
            query_type: Type of query (research, analysis, generation, etc.)
            context: Optional execution context
            
        Returns:
            Execution result with experiment metadata
        """
        context = context or {}
        request_id = f"req_{datetime.now().timestamp()}"
        
        # Find applicable experiment
        experiment_config = None
        for exp_id, config in self.active_experiments.items():
            if query_type in config.query_types:
                experiment_config = config
                break
        
        if not experiment_config:
            # No experiment - use default execution
            return await self._execute_default(query, query_type, context)
        
        # Get variant allocation
        allocation = await self.allocation_engine.allocate_variant(
            experiment_id=experiment_config.experiment_id,
            user_context={
                "query_type": query_type,
                "query_length": len(query),
                "has_context": bool(context)
            }
        )
        
        # Parse variant to get pattern and mode
        variant_parts = allocation.variant_id.split("_")
        if len(variant_parts) >= 2:
            pattern = APIPattern(variant_parts[0] + "_" + variant_parts[1] 
                                if variant_parts[0] in ["primary", "bypass"] 
                                else variant_parts[0])
            mode = ExecutionMode("_".join(variant_parts[2:]) 
                               if len(variant_parts) > 2 
                               else ExecutionMode.CHAIN)
        else:
            pattern = APIPattern.PRIMARY_API
            mode = ExecutionMode.CHAIN
        
        # Execute with selected pattern and mode
        start_time = datetime.now()
        
        try:
            result = await self._execute_with_pattern_and_mode(
                query, query_type, context, pattern, mode
            )
            
            execution_time = (datetime.now() - start_time).total_seconds() * 1000
            
            # Record execution result
            exec_result = APIExecutionResult(
                request_id=request_id,
                pattern=pattern,
                execution_mode=mode,
                query_type=query_type,
                latency_ms=execution_time,
                cost=result.get("cost", 0),
                quality_score=result.get("quality_score", 0),
                token_usage=result.get("token_usage", 0),
                api_calls=result.get("api_calls", 0),
                success=True,
                metadata={
                    "experiment_id": experiment_config.experiment_id,
                    "variant": allocation.variant_id
                }
            )
            
            self.results.append(exec_result)
            
            # Report metrics
            await self._report_metrics(exec_result, experiment_config.experiment_id)
            
            # Add experiment metadata to result
            result["experiment_metadata"] = {
                "pattern": pattern.value,
                "mode": mode.value,
                "variant": allocation.variant_id,
                "experiment_id": experiment_config.experiment_id
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Execution failed: {e}")
            
            # Record failure
            exec_result = APIExecutionResult(
                request_id=request_id,
                pattern=pattern,
                execution_mode=mode,
                query_type=query_type,
                latency_ms=(datetime.now() - start_time).total_seconds() * 1000,
                cost=0,
                quality_score=0,
                token_usage=0,
                api_calls=0,
                success=False,
                error_message=str(e)
            )
            
            self.results.append(exec_result)
            await self._report_metrics(exec_result, experiment_config.experiment_id)
            
            raise
    
    async def _execute_with_pattern_and_mode(self,
                                            query: str,
                                            query_type: str,
                                            context: Dict[str, Any],
                                            pattern: APIPattern,
                                            mode: ExecutionMode) -> Dict[str, Any]:
        """Execute with specific pattern and mode combination."""
        # Get handlers
        pattern_handler = self.pattern_handlers.get(pattern)
        mode_handler = self.mode_handlers.get(mode)
        
        if not pattern_handler or not mode_handler:
            raise ValueError(f"No handler for pattern {pattern} or mode {mode}")
        
        # Execute pattern with mode
        pattern_context = {
            **context,
            "execution_mode": mode,
            "query_type": query_type
        }
        
        return await pattern_handler(query, pattern_context, mode_handler)
    
    async def _execute_primary_api(self,
                                  query: str,
                                  context: Dict[str, Any],
                                  mode_handler: Callable) -> Dict[str, Any]:
        """Execute using Primary API pattern."""
        # Primary API uses standard hierarchical execution
        api_context = {
            **context,
            "api_type": "primary",
            "hierarchy_enabled": True
        }
        
        # Execute with specified mode
        result = await mode_handler(query, api_context)
        
        # Simulate Primary API characteristics
        result["api_calls"] = result.get("api_calls", 0) + 3  # More API calls
        result["cost"] = result.get("cost", 0) * 1.2  # Slightly higher cost
        result["quality_score"] = min(1.0, result.get("quality_score", 0.8) * 1.1)  # Better quality
        
        return result
    
    async def _execute_bypass_api(self,
                                 query: str,
                                 context: Dict[str, Any],
                                 mode_handler: Callable) -> Dict[str, Any]:
        """Execute using Bypass API pattern."""
        # Bypass API skips hierarchy for direct execution
        api_context = {
            **context,
            "api_type": "bypass",
            "hierarchy_enabled": False,
            "direct_execution": True
        }
        
        # Execute with specified mode
        result = await mode_handler(query, api_context)
        
        # Simulate Bypass API characteristics
        result["api_calls"] = max(1, result.get("api_calls", 0) - 1)  # Fewer API calls
        result["cost"] = result.get("cost", 0) * 0.8  # Lower cost
        result["latency_ms"] = result.get("latency_ms", 0) * 0.7  # Faster
        
        return result
    
    async def _execute_hybrid(self,
                             query: str,
                             context: Dict[str, Any],
                             mode_handler: Callable) -> Dict[str, Any]:
        """Execute using Hybrid pattern (intelligent switching)."""
        # Determine best pattern based on query characteristics
        query_length = len(query)
        complexity = context.get("complexity", "medium")
        
        if query_length < 100 and complexity == "low":
            # Use Bypass for simple queries
            return await self._execute_bypass_api(query, context, mode_handler)
        elif query_length > 500 or complexity == "high":
            # Use Primary for complex queries
            return await self._execute_primary_api(query, context, mode_handler)
        else:
            # Mixed approach
            primary_result = await self._execute_primary_api(
                query, {**context, "weight": 0.6}, mode_handler
            )
            bypass_result = await self._execute_bypass_api(
                query, {**context, "weight": 0.4}, mode_handler
            )
            
            # Combine results
            return {
                "response": primary_result.get("response", ""),
                "cost": primary_result["cost"] * 0.6 + bypass_result["cost"] * 0.4,
                "quality_score": primary_result["quality_score"] * 0.6 + 
                                bypass_result["quality_score"] * 0.4,
                "api_calls": primary_result["api_calls"] + bypass_result["api_calls"],
                "token_usage": primary_result.get("token_usage", 0) + 
                              bypass_result.get("token_usage", 0)
            }
    
    async def _execute_parallel_apis(self,
                                    query: str,
                                    context: Dict[str, Any],
                                    mode_handler: Callable) -> Dict[str, Any]:
        """Execute both API patterns in parallel."""
        # Run both patterns concurrently
        primary_task = asyncio.create_task(
            self._execute_primary_api(query, context, mode_handler)
        )
        bypass_task = asyncio.create_task(
            self._execute_bypass_api(query, context, mode_handler)
        )
        
        primary_result, bypass_result = await asyncio.gather(
            primary_task, bypass_task
        )
        
        # Select best result based on quality and speed
        if primary_result["quality_score"] > bypass_result["quality_score"] * 1.2:
            return primary_result
        elif bypass_result.get("latency_ms", float('inf')) < primary_result.get("latency_ms", float('inf')) * 0.5:
            return bypass_result
        else:
            # Return primary by default
            return primary_result
    
    async def _execute_chain_mode(self,
                                 query: str,
                                 context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute in chain mode (sequential)."""
        # Simulate sequential agent execution
        stages = ["analysis", "processing", "synthesis", "validation"]
        total_cost = 0
        total_tokens = 0
        
        for stage in stages:
            # Simulate stage execution
            await asyncio.sleep(0.1)  # Simulate processing
            total_cost += 0.001
            total_tokens += 100
        
        return {
            "response": f"Chain execution completed for: {query[:50]}...",
            "cost": total_cost,
            "quality_score": 0.85,
            "api_calls": len(stages),
            "token_usage": total_tokens,
            "latency_ms": len(stages) * 100
        }
    
    async def _execute_mixture_mode(self,
                                   query: str,
                                   context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute in mixture mode (mixed dependencies)."""
        # Simulate mixed execution with interdependencies
        parallel_tasks = 2
        sequential_tasks = 2
        
        # Parallel phase
        parallel_results = await asyncio.gather(*[
            asyncio.sleep(0.05) for _ in range(parallel_tasks)
        ])
        
        # Sequential phase
        for _ in range(sequential_tasks):
            await asyncio.sleep(0.05)
        
        return {
            "response": f"Mixture execution completed for: {query[:50]}...",
            "cost": 0.003,
            "quality_score": 0.88,
            "api_calls": parallel_tasks + sequential_tasks,
            "token_usage": 350,
            "latency_ms": 150
        }
    
    async def _execute_parallel_mode(self,
                                    query: str,
                                    context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute in parallel mode."""
        # Simulate fully parallel execution
        num_agents = 4
        
        # All agents work in parallel
        await asyncio.gather(*[
            asyncio.sleep(0.1) for _ in range(num_agents)
        ])
        
        return {
            "response": f"Parallel execution completed for: {query[:50]}...",
            "cost": 0.004,
            "quality_score": 0.90,
            "api_calls": num_agents,
            "token_usage": 400,
            "latency_ms": 100  # Fastest due to parallelism
        }
    
    async def _execute_hierarchical_mode(self,
                                        query: str,
                                        context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute in hierarchical mode."""
        # Simulate supervisor-worker hierarchy
        supervisor_time = 0.05
        worker_time = 0.1
        num_workers = 3
        
        # Supervisor analysis
        await asyncio.sleep(supervisor_time)
        
        # Workers execute in parallel
        await asyncio.gather(*[
            asyncio.sleep(worker_time) for _ in range(num_workers)
        ])
        
        # Supervisor synthesis
        await asyncio.sleep(supervisor_time)
        
        return {
            "response": f"Hierarchical execution completed for: {query[:50]}...",
            "cost": 0.005,
            "quality_score": 0.92,  # High quality due to supervision
            "api_calls": 1 + num_workers + 1,
            "token_usage": 500,
            "latency_ms": 200
        }
    
    async def _execute_default(self,
                              query: str,
                              query_type: str,
                              context: Dict[str, Any]) -> Dict[str, Any]:
        """Default execution without experimentation."""
        return await self._execute_chain_mode(query, context)
    
    async def _report_metrics(self,
                             result: APIExecutionResult,
                             experiment_id: str):
        """Report execution metrics to experiment manager."""
        metrics = {
            "latency_ms": result.latency_ms,
            "total_cost": result.cost,
            "quality_score": result.quality_score,
            "token_usage": result.token_usage,
            "api_calls": result.api_calls,
            "error_rate": 0.0 if result.success else 1.0
        }
        
        variant_id = f"{result.pattern.value}_{result.execution_mode.value}"
        
        await self.experiment_manager.record_metrics(
            experiment_id=experiment_id,
            variant_id=variant_id,
            metrics=metrics,
            context={
                "request_id": result.request_id,
                "query_type": result.query_type,
                "timestamp": datetime.now().isoformat()
            }
        )
    
    async def analyze_experiment(self,
                                experiment_id: str) -> Dict[str, Any]:
        """
        Analyze results of an API pattern experiment.
        
        Args:
            experiment_id: Experiment to analyze
            
        Returns:
            Analysis results with recommendations
        """
        if experiment_id not in self.active_experiments:
            return {"error": f"Experiment {experiment_id} not found"}
        
        config = self.active_experiments[experiment_id]
        
        # Group results by pattern and mode
        pattern_mode_results = {}
        
        for result in self.results:
            if result.metadata.get("experiment_id") != experiment_id:
                continue
            
            key = f"{result.pattern.value}_{result.execution_mode.value}"
            if key not in pattern_mode_results:
                pattern_mode_results[key] = []
            pattern_mode_results[key].append(result)
        
        # Analyze each combination
        analysis = {}
        for key, results in pattern_mode_results.items():
            if not results:
                continue
            
            analysis[key] = {
                "sample_size": len(results),
                "avg_latency_ms": sum(r.latency_ms for r in results) / len(results),
                "avg_cost": sum(r.cost for r in results) / len(results),
                "avg_quality": sum(r.quality_score for r in results) / len(results),
                "avg_tokens": sum(r.token_usage for r in results) / len(results),
                "avg_api_calls": sum(r.api_calls for r in results) / len(results),
                "success_rate": sum(1 for r in results if r.success) / len(results)
            }
        
        # Find best combination for different objectives
        recommendations = {
            "best_for_speed": min(analysis.items(), 
                                 key=lambda x: x[1]["avg_latency_ms"])[0],
            "best_for_cost": min(analysis.items(), 
                                key=lambda x: x[1]["avg_cost"])[0],
            "best_for_quality": max(analysis.items(), 
                                  key=lambda x: x[1]["avg_quality"])[0],
            "best_balanced": self._find_balanced_best(analysis)
        }
        
        return {
            "experiment_id": experiment_id,
            "query_types": config.query_types,
            "pattern_mode_analysis": analysis,
            "recommendations": recommendations,
            "total_executions": sum(len(r) for r in pattern_mode_results.values())
        }
    
    def _find_balanced_best(self, analysis: Dict[str, Dict]) -> str:
        """Find best balanced configuration."""
        best_score = -float('inf')
        best_key = None
        
        for key, metrics in analysis.items():
            # Balanced scoring function
            score = (
                metrics["avg_quality"] * 0.4 -
                metrics["avg_cost"] * 10 * 0.3 -
                metrics["avg_latency_ms"] / 1000 * 0.3
            )
            
            if score > best_score:
                best_score = score
                best_key = key
        
        return best_key