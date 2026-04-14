# Experiment Integration Patterns

## Overview

This document details how the Enhanced A/B Testing System integrates with Cerebro's core intelligence components. Each integration pattern is designed to enable systematic experimentation while maintaining system stability and performance.

## MASR Routing Strategy Experiments

### Integration Architecture

```python
# src/ai_brain/experimentation/integration/masr_experiment_integration.py

from typing import Dict, Any, Optional
from src.ai_brain.router.masr import MASRRouter, RoutingStrategy
from src.ai_brain.experimentation.core.unified_experiment_manager import ExperimentManager

class MASRExperimentIntegration:
    """
    Enables experimentation with MASR routing strategies.
    """
    
    def __init__(self, masr_router: MASRRouter, experiment_manager: ExperimentManager):
        self.masr = masr_router
        self.exp_manager = experiment_manager
        self.active_experiments = {}
        
    async def route_with_experiment(self, 
                                   query: str, 
                                   user_id: str,
                                   context: Dict[str, Any]) -> RoutingDecision:
        """
        Route query with experimental strategy selection.
        """
        # Check for active routing experiments
        experiment = self.exp_manager.get_active_experiment('masr_routing')
        
        if experiment:
            # Assign user to variant
            variant = experiment.get_variant_for_user(user_id)
            
            # Apply experimental routing strategy
            strategy = self._get_experimental_strategy(variant)
            
            # Track assignment
            self.exp_manager.track_assignment(
                experiment_id=experiment.id,
                user_id=user_id,
                variant=variant,
                context={'query': query, 'strategy': strategy}
            )
            
            # Execute routing with experimental strategy
            decision = await self.masr.route(query, strategy=strategy)
            
            # Track metrics
            await self._track_routing_metrics(experiment.id, decision)
            
            return decision
        else:
            # Default routing behavior
            return await self.masr.route(query)
    
    def _get_experimental_strategy(self, variant: str) -> RoutingStrategy:
        """Map experiment variant to routing strategy."""
        strategy_map = {
            'control': RoutingStrategy.BALANCED,
            'cost_efficient': RoutingStrategy.COST_EFFICIENT,
            'quality_focused': RoutingStrategy.QUALITY_FOCUSED,
            'adaptive': RoutingStrategy.ADAPTIVE,
            'ml_optimized': RoutingStrategy.ML_OPTIMIZED
        }
        return strategy_map.get(variant, RoutingStrategy.BALANCED)
```

### Experiment Configuration

```yaml
# experiments/masr_routing_experiment.yaml

experiment:
  id: masr_routing_optimization_v1
  name: "MASR Routing Strategy Optimization"
  description: "Test different routing strategies for cost/quality optimization"
  type: multi_armed_bandit
  
  variants:
    - id: control
      name: "Balanced (Control)"
      allocation: 0.2
      config:
        strategy: balanced
        cost_weight: 0.5
        quality_weight: 0.5
        
    - id: cost_efficient
      name: "Cost Efficient"
      allocation: 0.2
      config:
        strategy: cost_efficient
        cost_weight: 0.8
        quality_weight: 0.2
        
    - id: quality_focused
      name: "Quality Focused"  
      allocation: 0.2
      config:
        strategy: quality_focused
        cost_weight: 0.2
        quality_weight: 0.8
        
    - id: adaptive
      name: "Adaptive"
      allocation: 0.2
      config:
        strategy: adaptive
        learning_rate: 0.1
        exploration_rate: 0.15
        
    - id: ml_optimized
      name: "ML Optimized"
      allocation: 0.2
      config:
        strategy: ml_optimized
        model: thompson_sampling
        prior_alpha: 1.0
        prior_beta: 1.0
  
  metrics:
    primary:
      - name: cost_per_query
        type: continuous
        optimization: minimize
      - name: response_quality_score
        type: continuous
        optimization: maximize
        
    secondary:
      - name: latency_p95
        type: continuous
        optimization: minimize
      - name: user_satisfaction
        type: ordinal
        optimization: maximize
        
  success_criteria:
    - metric: cost_per_query
      improvement: 0.3  # 30% reduction
      confidence: 0.95
    - metric: response_quality_score
      improvement: 0.1  # 10% improvement
      confidence: 0.95
      
  duration:
    min_samples: 10000
    max_duration_days: 14
    early_stopping: true
```

## Agent API Pattern Experiments

### Primary vs Bypass API Testing

```python
# src/ai_brain/experimentation/integration/api_pattern_experiments.py

class APIPatternExperiment:
    """
    Experiment with Primary (MASR-routed) vs Bypass (direct) API usage.
    """
    
    def __init__(self, experiment_manager: ExperimentManager):
        self.exp_manager = experiment_manager
        self.routing_threshold = 0.9  # Default 90% Primary
        
    async def determine_api_route(self, 
                                 request: APIRequest,
                                 user_context: UserContext) -> APIRoute:
        """
        Determine whether to use Primary or Bypass API.
        """
        experiment = self.exp_manager.get_active_experiment('api_pattern')
        
        if experiment:
            variant = experiment.get_variant_for_context(user_context)
            
            # Get routing threshold for this variant
            threshold = self._get_routing_threshold(variant)
            
            # Make routing decision
            use_primary = random.random() < threshold
            
            # Track the decision
            self.exp_manager.track_event(
                experiment_id=experiment.id,
                event_type='api_route_decision',
                properties={
                    'variant': variant,
                    'route': 'primary' if use_primary else 'bypass',
                    'query_complexity': request.complexity_score,
                    'user_type': user_context.user_type
                }
            )
            
            return APIRoute.PRIMARY if use_primary else APIRoute.BYPASS
        
        # Default behavior
        return APIRoute.PRIMARY if random.random() < 0.9 else APIRoute.BYPASS
    
    def _get_routing_threshold(self, variant: str) -> float:
        """Get Primary API usage threshold for variant."""
        thresholds = {
            'control': 0.90,      # 90% Primary (baseline)
            'high_primary': 0.95, # 95% Primary
            'balanced': 0.85,     # 85% Primary
            'adaptive': None      # Dynamic based on context
        }
        
        if variant == 'adaptive':
            return self._calculate_adaptive_threshold()
        
        return thresholds.get(variant, 0.90)
```

### Chain-of-Agents vs Mixture-of-Agents Experiments

```python
# src/ai_brain/experimentation/integration/execution_pattern_experiments.py

from enum import Enum
from typing import List, Dict, Any

class ExecutionPattern(Enum):
    CHAIN = "chain_of_agents"
    MIXTURE = "mixture_of_agents"
    HYBRID = "hybrid"
    ADAPTIVE = "adaptive"

class ExecutionPatternExperiment:
    """
    Test different agent execution patterns.
    """
    
    def __init__(self, experiment_manager: ExperimentManager):
        self.exp_manager = experiment_manager
        
    async def select_execution_pattern(self,
                                      task: AgentTask,
                                      available_agents: List[Agent]) -> ExecutionPattern:
        """
        Select execution pattern based on experiment.
        """
        experiment = self.exp_manager.get_active_experiment('execution_pattern')
        
        if experiment:
            # Get variant based on task characteristics
            features = self._extract_task_features(task)
            variant = experiment.get_variant_for_features(features)
            
            pattern = self._variant_to_pattern(variant)
            
            # Track selection
            self.exp_manager.track_event(
                experiment_id=experiment.id,
                event_type='pattern_selection',
                properties={
                    'variant': variant,
                    'pattern': pattern.value,
                    'task_type': task.type,
                    'task_complexity': task.complexity,
                    'agent_count': len(available_agents)
                }
            )
            
            return pattern
        
        # Default: Choose based on task complexity
        return self._default_pattern_selection(task)
    
    def _extract_task_features(self, task: AgentTask) -> np.ndarray:
        """Extract features for contextual bandit."""
        return np.array([
            task.complexity,
            len(task.subtasks),
            task.estimated_duration,
            task.priority,
            1 if task.requires_consensus else 0,
            1 if task.is_exploratory else 0
        ])
    
    async def execute_with_pattern(self,
                                  pattern: ExecutionPattern,
                                  agents: List[Agent],
                                  task: AgentTask) -> ExecutionResult:
        """
        Execute task with selected pattern.
        """
        if pattern == ExecutionPattern.CHAIN:
            return await self._execute_chain(agents, task)
        elif pattern == ExecutionPattern.MIXTURE:
            return await self._execute_mixture(agents, task)
        elif pattern == ExecutionPattern.HYBRID:
            return await self._execute_hybrid(agents, task)
        else:  # ADAPTIVE
            return await self._execute_adaptive(agents, task)
```

## Memory System Optimization Experiments

### Memory Tier Access Patterns

```python
# src/ai_brain/experimentation/integration/memory_experiments.py

class MemoryOptimizationExperiment:
    """
    Optimize memory tier usage and retrieval patterns.
    """
    
    def __init__(self, 
                 memory_system: MultiTierMemory,
                 experiment_manager: ExperimentManager):
        self.memory = memory_system
        self.exp_manager = experiment_manager
        
    async def retrieve_with_experiment(self,
                                      query: str,
                                      context: QueryContext) -> MemoryResult:
        """
        Retrieve from memory with experimental tier strategy.
        """
        experiment = self.exp_manager.get_active_experiment('memory_optimization')
        
        if experiment:
            variant = experiment.get_variant_for_query(query)
            strategy = self._get_memory_strategy(variant)
            
            # Track retrieval attempt
            start_time = time.time()
            
            # Apply experimental strategy
            result = await self._retrieve_with_strategy(query, strategy)
            
            # Track metrics
            self.exp_manager.track_metrics(
                experiment_id=experiment.id,
                metrics={
                    'retrieval_latency': time.time() - start_time,
                    'cache_hit': result.cache_hit,
                    'tiers_accessed': len(result.tiers_accessed),
                    'relevance_score': result.relevance_score,
                    'variant': variant
                }
            )
            
            return result
        
        # Default retrieval
        return await self.memory.retrieve(query)
    
    def _get_memory_strategy(self, variant: str) -> MemoryStrategy:
        """Map variant to memory retrieval strategy."""
        strategies = {
            'control': MemoryStrategy.HIERARCHICAL,
            'aggressive_cache': MemoryStrategy.CACHE_FIRST,
            'semantic_priority': MemoryStrategy.SEMANTIC_FIRST,
            'parallel_retrieval': MemoryStrategy.PARALLEL_ALL,
            'adaptive': MemoryStrategy.ML_OPTIMIZED
        }
        return strategies.get(variant, MemoryStrategy.HIERARCHICAL)
```

### Cross-Tier Integration Testing

```python
class CrossTierIntegrationExperiment:
    """
    Test different cross-tier memory integration strategies.
    """
    
    async def integrate_memories_with_experiment(self,
                                                memories: List[Memory],
                                                integration_context: IntegrationContext):
        """
        Integrate memories across tiers with experimental strategies.
        """
        experiment = self.exp_manager.get_active_experiment('cross_tier_integration')
        
        if experiment:
            variant = experiment.get_variant()
            
            if variant == 'weighted_merge':
                return await self._weighted_merge_integration(memories)
            elif variant == 'semantic_clustering':
                return await self._semantic_clustering_integration(memories)
            elif variant == 'temporal_priority':
                return await self._temporal_priority_integration(memories)
            elif variant == 'ml_fusion':
                return await self._ml_fusion_integration(memories)
            else:  # control
                return await self._default_integration(memories)
```

## TalkHier Protocol Parameter Tuning

### Multi-Round Refinement Experiments

```python
# src/ai_brain/experimentation/integration/talkhier_experiments.py

class TalkHierExperiment:
    """
    Optimize TalkHier protocol parameters.
    """
    
    def __init__(self,
                 talkhier_protocol: TalkHierProtocol,
                 experiment_manager: ExperimentManager):
        self.protocol = talkhier_protocol
        self.exp_manager = experiment_manager
        
    async def configure_refinement_with_experiment(self,
                                                  task: SupervisorTask) -> RefinementConfig:
        """
        Configure refinement rounds based on experiment.
        """
        experiment = self.exp_manager.get_active_experiment('talkhier_refinement')
        
        if experiment:
            variant = experiment.get_variant()
            
            config = RefinementConfig()
            
            if variant == 'minimal':
                config.max_rounds = 2
                config.consensus_threshold = 0.7
                config.early_stopping = True
                
            elif variant == 'balanced':
                config.max_rounds = 3
                config.consensus_threshold = 0.8
                config.early_stopping = True
                
            elif variant == 'quality_focused':
                config.max_rounds = 5
                config.consensus_threshold = 0.9
                config.early_stopping = False
                
            elif variant == 'adaptive':
                # Dynamically determine based on task complexity
                config = self._adaptive_refinement_config(task)
            
            else:  # control
                config.max_rounds = 3
                config.consensus_threshold = 0.85
                config.early_stopping = True
            
            # Track configuration
            self.exp_manager.track_event(
                experiment_id=experiment.id,
                event_type='refinement_config',
                properties={
                    'variant': variant,
                    'max_rounds': config.max_rounds,
                    'consensus_threshold': config.consensus_threshold,
                    'task_complexity': task.complexity
                }
            )
            
            return config
        
        # Default configuration
        return self.protocol.default_config()
```

### Consensus Building Strategies

```python
class ConsensusExperiment:
    """
    Test different consensus building strategies.
    """
    
    async def build_consensus_with_experiment(self,
                                             worker_responses: List[WorkerResponse]) -> Consensus:
        """
        Build consensus using experimental strategies.
        """
        experiment = self.exp_manager.get_active_experiment('consensus_strategy')
        
        if experiment:
            variant = experiment.get_variant()
            
            strategies = {
                'majority_vote': self._majority_vote_consensus,
                'weighted_vote': self._weighted_vote_consensus,
                'quality_weighted': self._quality_weighted_consensus,
                'debate_resolution': self._debate_resolution_consensus,
                'ml_aggregation': self._ml_aggregation_consensus
            }
            
            strategy_func = strategies.get(variant, self._majority_vote_consensus)
            
            # Execute strategy
            start_time = time.time()
            consensus = await strategy_func(worker_responses)
            duration = time.time() - start_time
            
            # Track metrics
            self.exp_manager.track_metrics(
                experiment_id=experiment.id,
                metrics={
                    'consensus_quality': consensus.quality_score,
                    'agreement_level': consensus.agreement_level,
                    'rounds_to_consensus': consensus.rounds_taken,
                    'consensus_duration': duration,
                    'variant': variant
                }
            )
            
            return consensus
```

## Experiment Lifecycle Integration

### Experiment Creation and Deployment

```python
class ExperimentLifecycleManager:
    """
    Manage experiment lifecycle from creation to conclusion.
    """
    
    async def create_system_experiment(self,
                                      experiment_config: ExperimentConfig) -> Experiment:
        """
        Create and deploy a system-wide experiment.
        """
        # Validate configuration
        self._validate_config(experiment_config)
        
        # Create experiment
        experiment = Experiment(
            id=str(uuid.uuid4()),
            name=experiment_config.name,
            type=experiment_config.type,
            status=ExperimentStatus.CREATED
        )
        
        # Register with appropriate systems
        if experiment_config.affects_masr:
            await self._register_masr_experiment(experiment)
        
        if experiment_config.affects_memory:
            await self._register_memory_experiment(experiment)
            
        if experiment_config.affects_agents:
            await self._register_agent_experiment(experiment)
            
        if experiment_config.affects_talkhier:
            await self._register_talkhier_experiment(experiment)
        
        # Initialize tracking
        await self._initialize_tracking(experiment)
        
        # Start experiment
        experiment.status = ExperimentStatus.RUNNING
        await self._persist_experiment(experiment)
        
        return experiment
```

### Automated Analysis and Decision Making

```python
class ExperimentAnalyzer:
    """
    Automated analysis and decision making for experiments.
    """
    
    async def analyze_and_decide(self, experiment_id: str) -> ExperimentDecision:
        """
        Analyze experiment results and make deployment decision.
        """
        experiment = await self._load_experiment(experiment_id)
        metrics = await self._load_metrics(experiment_id)
        
        # Statistical analysis
        analysis_results = await self._perform_statistical_analysis(metrics)
        
        # Check success criteria
        criteria_met = self._check_success_criteria(
            experiment.success_criteria,
            analysis_results
        )
        
        # Make decision
        if criteria_met:
            decision = ExperimentDecision.PROMOTE_WINNER
            winning_variant = self._identify_winner(analysis_results)
            
            # Prepare for production deployment
            deployment_config = await self._prepare_deployment(
                experiment,
                winning_variant
            )
            
            # Schedule gradual rollout
            await self._schedule_rollout(deployment_config)
            
        elif self._should_continue(analysis_results):
            decision = ExperimentDecision.CONTINUE
            
            # Adjust allocation if using bandits
            if experiment.type == ExperimentType.MULTI_ARMED_BANDIT:
                await self._update_bandit_allocation(experiment, metrics)
                
        else:
            decision = ExperimentDecision.STOP
            
            # Revert to control
            await self._revert_to_control(experiment)
        
        # Record decision
        await self._record_decision(experiment_id, decision, analysis_results)
        
        return decision
```

## Monitoring and Observability

### Real-Time Experiment Dashboard

```python
class ExperimentDashboard:
    """
    Real-time monitoring of all active experiments.
    """
    
    async def get_dashboard_data(self) -> DashboardData:
        """
        Aggregate data for experiment dashboard.
        """
        active_experiments = await self._get_active_experiments()
        
        dashboard_data = DashboardData()
        
        for experiment in active_experiments:
            # Get current metrics
            metrics = await self._get_current_metrics(experiment.id)
            
            # Calculate statistical significance
            significance = await self._calculate_significance(metrics)
            
            # Get variant performance
            variant_performance = await self._get_variant_performance(
                experiment.id,
                metrics
            )
            
            # Add to dashboard
            dashboard_data.add_experiment({
                'id': experiment.id,
                'name': experiment.name,
                'status': experiment.status,
                'duration': experiment.duration_days,
                'sample_size': metrics.total_samples,
                'significance': significance,
                'variant_performance': variant_performance,
                'projected_winner': self._project_winner(variant_performance),
                'estimated_improvement': self._estimate_improvement(variant_performance)
            })
        
        return dashboard_data
```

## Safety and Rollback Mechanisms

### Automatic Rollback on Degradation

```python
class ExperimentSafetyMonitor:
    """
    Monitor experiments for safety and trigger rollbacks.
    """
    
    async def monitor_experiment_safety(self, experiment_id: str):
        """
        Continuously monitor experiment for degradation.
        """
        experiment = await self._load_experiment(experiment_id)
        baseline_metrics = await self._get_baseline_metrics()
        
        while experiment.status == ExperimentStatus.RUNNING:
            current_metrics = await self._get_current_metrics(experiment_id)
            
            # Check for significant degradation
            degradation = self._check_degradation(baseline_metrics, current_metrics)
            
            if degradation.is_significant:
                # Trigger immediate rollback
                await self._trigger_rollback(experiment_id, degradation)
                
                # Alert relevant teams
                await self._send_alerts(experiment_id, degradation)
                
                # Stop experiment
                experiment.status = ExperimentStatus.STOPPED_SAFETY
                await self._persist_experiment(experiment)
                
                break
            
            # Wait before next check
            await asyncio.sleep(60)  # Check every minute
```

## Best Practices

### 1. Integration Design
- Keep experiments isolated from core business logic
- Use feature flags for easy enable/disable
- Implement gradual rollout capabilities
- Maintain backward compatibility

### 2. Metric Collection
- Instrument all relevant touch points
- Use consistent metric naming conventions
- Collect both business and technical metrics
- Ensure data quality and validation

### 3. Experiment Isolation
- Prevent experiment interference
- Use proper randomization
- Control for confounding variables
- Implement clean experiment boundaries

### 4. Production Safety
- Always have rollback mechanisms
- Monitor for degradation continuously
- Set conservative safety thresholds
- Maintain audit logs of all changes

### 5. Performance Impact
- Minimize overhead of experimentation code
- Use async operations where possible
- Cache experiment configurations
- Profile and optimize hot paths

## Conclusion

These integration patterns enable Cerebro to experiment with every aspect of its intelligence system while maintaining stability and performance. By deeply integrating experimentation into core components, we create a platform that continuously evolves and improves based on empirical evidence.