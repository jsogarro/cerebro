"""
System Experiment Registry

Central registry for all active experiments across system components.
Manages experiment lifecycle, coordination, and integration with existing systems.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Any, Callable
from datetime import datetime, timedelta
import asyncio
import logging
from enum import Enum
from collections import defaultdict

from .unified_experiment_manager import (
    SystemExperiment,
    ExperimentStatus,
    SystemComponent,
    ExperimentVariant
)

logger = logging.getLogger(__name__)


@dataclass
class ComponentRegistration:
    """Registration of a system component for experimentation."""
    component: SystemComponent
    handler: Callable  # Async function to handle experiment updates
    active_experiments: Set[str] = field(default_factory=set)
    capabilities: Dict[str, Any] = field(default_factory=dict)
    health_check: Optional[Callable] = None


@dataclass
class ExperimentEvent:
    """Event in experiment lifecycle."""
    experiment_id: str
    event_type: str
    timestamp: datetime
    data: Dict[str, Any]
    component: Optional[SystemComponent] = None


class ExperimentEventType(Enum):
    """Types of experiment lifecycle events."""
    CREATED = "created"
    STARTED = "started"
    VARIANT_ASSIGNED = "variant_assigned"
    METRIC_RECORDED = "metric_recorded"
    STATUS_CHANGED = "status_changed"
    STOPPED = "stopped"
    WINNER_PROMOTED = "winner_promoted"
    ERROR = "error"
    ROLLBACK = "rollback"


class SystemExperimentRegistry:
    """
    Central registry managing all experiments across Cerebro systems.
    Coordinates between experiment manager and system components.
    """
    
    def __init__(self):
        """Initialize the experiment registry."""
        self.component_registrations: Dict[SystemComponent, ComponentRegistration] = {}
        self.experiment_components: Dict[str, Set[SystemComponent]] = defaultdict(set)
        self.event_history: List[ExperimentEvent] = []
        self.event_handlers: Dict[ExperimentEventType, List[Callable]] = defaultdict(list)
        self.experiment_locks: Dict[str, asyncio.Lock] = {}
        
    async def register_component(self,
                                component: SystemComponent,
                                handler: Callable,
                                capabilities: Optional[Dict[str, Any]] = None,
                                health_check: Optional[Callable] = None) -> None:
        """
        Register a system component for experimentation.
        
        Args:
            component: The system component being registered
            handler: Async function to handle experiment updates for this component
            capabilities: Component capabilities and constraints
            health_check: Optional health check function for the component
        """
        registration = ComponentRegistration(
            component=component,
            handler=handler,
            capabilities=capabilities or {},
            health_check=health_check
        )
        
        self.component_registrations[component] = registration
        
        # Perform initial health check
        if health_check:
            is_healthy = await health_check()
            if not is_healthy:
                raise RuntimeError(f"Component {component.value} failed health check")
    
    async def register_experiment(self, experiment: SystemExperiment) -> None:
        """
        Register a new experiment with affected components.
        
        Args:
            experiment: The experiment to register
        """
        # Create lock for this experiment
        self.experiment_locks[experiment.id] = asyncio.Lock()
        
        # Register with each affected component
        for component in experiment.components:
            if component not in self.component_registrations:
                raise ValueError(f"Component {component.value} not registered")
            
            registration = self.component_registrations[component]
            registration.active_experiments.add(experiment.id)
            self.experiment_components[experiment.id].add(component)
            
            # Notify component of new experiment
            await registration.handler('experiment_registered', {
                'experiment_id': experiment.id,
                'experiment': experiment
            })
        
        # Record event
        await self._record_event(
            ExperimentEvent(
                experiment_id=experiment.id,
                event_type=ExperimentEventType.CREATED.value,
                timestamp=datetime.utcnow(),
                data={'experiment': experiment.__dict__}
            )
        )
    
    async def start_experiment(self, experiment_id: str) -> None:
        """
        Start an experiment across all affected components.
        
        Args:
            experiment_id: ID of the experiment to start
        """
        async with self.experiment_locks[experiment_id]:
            affected_components = self.experiment_components[experiment_id]
            
            # Start experiment in each component
            start_tasks = []
            for component in affected_components:
                registration = self.component_registrations[component]
                task = registration.handler('experiment_started', {
                    'experiment_id': experiment_id
                })
                start_tasks.append(task)
            
            # Wait for all components to start
            results = await asyncio.gather(*start_tasks, return_exceptions=True)
            
            # Check for errors
            errors = [r for r in results if isinstance(r, Exception)]
            if errors:
                # Rollback on error
                await self._rollback_experiment_start(experiment_id, errors)
                raise RuntimeError(f"Failed to start experiment: {errors}")
            
            # Record event
            await self._record_event(
                ExperimentEvent(
                    experiment_id=experiment_id,
                    event_type=ExperimentEventType.STARTED.value,
                    timestamp=datetime.utcnow(),
                    data={'components': [c.value for c in affected_components]}
                )
            )
    
    async def update_experiment_allocation(self,
                                         experiment_id: str,
                                         new_allocations: Dict[str, float]) -> None:
        """
        Update variant allocations (for adaptive experiments).
        
        Args:
            experiment_id: ID of the experiment
            new_allocations: New allocation percentages by variant ID
        """
        async with self.experiment_locks[experiment_id]:
            affected_components = self.experiment_components[experiment_id]
            
            # Update each component
            for component in affected_components:
                registration = self.component_registrations[component]
                await registration.handler('allocation_updated', {
                    'experiment_id': experiment_id,
                    'allocations': new_allocations
                })
    
    async def stop_experiment(self,
                            experiment_id: str,
                            reason: str = "completed") -> None:
        """
        Stop an experiment across all components.
        
        Args:
            experiment_id: ID of the experiment to stop
            reason: Reason for stopping (completed, safety, manual)
        """
        async with self.experiment_locks[experiment_id]:
            affected_components = self.experiment_components[experiment_id]
            
            # Stop experiment in each component
            stop_tasks = []
            for component in affected_components:
                registration = self.component_registrations[component]
                registration.active_experiments.discard(experiment_id)
                task = registration.handler('experiment_stopped', {
                    'experiment_id': experiment_id,
                    'reason': reason
                })
                stop_tasks.append(task)
            
            await asyncio.gather(*stop_tasks, return_exceptions=True)
            
            # Clean up tracking
            del self.experiment_components[experiment_id]
            del self.experiment_locks[experiment_id]
            
            # Record event
            await self._record_event(
                ExperimentEvent(
                    experiment_id=experiment_id,
                    event_type=ExperimentEventType.STOPPED.value,
                    timestamp=datetime.utcnow(),
                    data={'reason': reason}
                )
            )
    
    async def promote_winner(self,
                           experiment_id: str,
                           winning_variant: ExperimentVariant) -> None:
        """
        Promote winning variant configuration to production.
        
        Args:
            experiment_id: ID of the experiment
            winning_variant: The winning variant to promote
        """
        # Get affected components (from history if experiment is stopped)
        affected_components = self._get_experiment_components(experiment_id)
        
        # Apply winning configuration to each component
        promotion_tasks = []
        for component in affected_components:
            if component in self.component_registrations:
                registration = self.component_registrations[component]
                task = registration.handler('promote_variant', {
                    'experiment_id': experiment_id,
                    'variant': winning_variant
                })
                promotion_tasks.append(task)
        
        results = await asyncio.gather(*promotion_tasks, return_exceptions=True)
        
        # Check for errors
        errors = [r for r in results if isinstance(r, Exception)]
        if errors:
            raise RuntimeError(f"Failed to promote winner: {errors}")
        
        # Record event
        await self._record_event(
            ExperimentEvent(
                experiment_id=experiment_id,
                event_type=ExperimentEventType.WINNER_PROMOTED.value,
                timestamp=datetime.utcnow(),
                data={'winning_variant': winning_variant.__dict__}
            )
        )
    
    async def check_component_health(self) -> Dict[SystemComponent, bool]:
        """
        Check health of all registered components.
        
        Returns:
            Dictionary mapping components to health status
        """
        health_status = {}
        
        for component, registration in self.component_registrations.items():
            if registration.health_check:
                try:
                    is_healthy = await registration.health_check()
                    health_status[component] = is_healthy
                except Exception as e:
                    health_status[component] = False
            else:
                # Assume healthy if no health check provided
                health_status[component] = True
        
        return health_status
    
    async def get_active_experiments_for_component(self,
                                                  component: SystemComponent) -> Set[str]:
        """
        Get all active experiments for a specific component.
        
        Args:
            component: The system component
            
        Returns:
            Set of experiment IDs
        """
        if component in self.component_registrations:
            return self.component_registrations[component].active_experiments.copy()
        return set()
    
    async def record_variant_assignment(self,
                                      experiment_id: str,
                                      variant_id: str,
                                      context: Dict[str, Any]) -> None:
        """
        Record a variant assignment event.
        
        Args:
            experiment_id: ID of the experiment
            variant_id: ID of the assigned variant
            context: Context of the assignment
        """
        await self._record_event(
            ExperimentEvent(
                experiment_id=experiment_id,
                event_type=ExperimentEventType.VARIANT_ASSIGNED.value,
                timestamp=datetime.utcnow(),
                data={
                    'variant_id': variant_id,
                    'context': context
                }
            )
        )
    
    async def record_metric(self,
                          experiment_id: str,
                          metric_name: str,
                          value: float,
                          variant_id: Optional[str] = None,
                          component: Optional[SystemComponent] = None) -> None:
        """
        Record a metric value for an experiment.
        
        Args:
            experiment_id: ID of the experiment
            metric_name: Name of the metric
            value: Metric value
            variant_id: Optional variant ID
            component: Optional component that recorded the metric
        """
        await self._record_event(
            ExperimentEvent(
                experiment_id=experiment_id,
                event_type=ExperimentEventType.METRIC_RECORDED.value,
                timestamp=datetime.utcnow(),
                data={
                    'metric_name': metric_name,
                    'value': value,
                    'variant_id': variant_id
                },
                component=component
            )
        )
    
    def register_event_handler(self,
                              event_type: ExperimentEventType,
                              handler: Callable):
        """
        Register a handler for experiment events.
        
        Args:
            event_type: Type of event to handle
            handler: Async function to handle the event
        """
        self.event_handlers[event_type].append(handler)
    
    async def get_experiment_events(self,
                                   experiment_id: str,
                                   event_type: Optional[ExperimentEventType] = None,
                                   start_time: Optional[datetime] = None,
                                   end_time: Optional[datetime] = None) -> List[ExperimentEvent]:
        """
        Get events for an experiment.
        
        Args:
            experiment_id: ID of the experiment
            event_type: Optional filter by event type
            start_time: Optional start time filter
            end_time: Optional end time filter
            
        Returns:
            List of matching events
        """
        events = []
        
        for event in self.event_history:
            if event.experiment_id != experiment_id:
                continue
            
            if event_type and event.event_type != event_type.value:
                continue
            
            if start_time and event.timestamp < start_time:
                continue
            
            if end_time and event.timestamp > end_time:
                continue
            
            events.append(event)
        
        return events
    
    # Private helper methods
    
    async def _record_event(self, event: ExperimentEvent) -> None:
        """Record an event and notify handlers."""
        self.event_history.append(event)
        
        # Notify event handlers
        event_type = ExperimentEventType(event.event_type)
        if event_type in self.event_handlers:
            for handler in self.event_handlers[event_type]:
                try:
                    await handler(event)
                except Exception as e:
                    # Log but don't fail on handler errors
                    logger.error(
                        "event_handler_error",
                        error=str(e),
                        event_type=event.event_type if hasattr(event, 'event_type') else 'unknown',
                        exc_info=True
                    )
    
    async def _rollback_experiment_start(self,
                                       experiment_id: str,
                                       errors: List[Exception]) -> None:
        """Rollback a failed experiment start."""
        affected_components = self.experiment_components[experiment_id]
        
        rollback_tasks = []
        for component in affected_components:
            registration = self.component_registrations[component]
            registration.active_experiments.discard(experiment_id)
            task = registration.handler('experiment_rollback', {
                'experiment_id': experiment_id,
                'errors': [str(e) for e in errors]
            })
            rollback_tasks.append(task)
        
        await asyncio.gather(*rollback_tasks, return_exceptions=True)
        
        # Record rollback event
        await self._record_event(
            ExperimentEvent(
                experiment_id=experiment_id,
                event_type=ExperimentEventType.ROLLBACK.value,
                timestamp=datetime.utcnow(),
                data={'errors': [str(e) for e in errors]}
            )
        )
    
    def _get_experiment_components(self, experiment_id: str) -> Set[SystemComponent]:
        """Get components for an experiment (including stopped ones)."""
        # Check active experiments first
        if experiment_id in self.experiment_components:
            return self.experiment_components[experiment_id]
        
        # Check event history for stopped experiments
        components = set()
        for event in self.event_history:
            if (event.experiment_id == experiment_id and 
                event.event_type == ExperimentEventType.STARTED.value):
                # Extract components from event data
                component_names = event.data.get('components', [])
                for name in component_names:
                    try:
                        components.add(SystemComponent(name))
                    except ValueError:
                        pass
        
        return components