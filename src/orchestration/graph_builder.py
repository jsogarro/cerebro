"""
Graph construction utilities for LangGraph workflows.

This module provides utilities for building and configuring workflow graphs
with nodes, edges, and conditional routing.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from langgraph.checkpoint import MemorySaver
from langgraph.graph import END, StateGraph

from src.orchestration.edges import RouterConfig, WorkflowRouter
from src.orchestration.state import ResearchState, WorkflowPhase

logger = logging.getLogger(__name__)


@dataclass
class NodeConfig:
    """Configuration for a workflow node."""

    name: str
    handler: Callable[[ResearchState], ResearchState]
    phase: WorkflowPhase
    description: str = ""
    timeout_seconds: int = 300
    retry_policy: dict[str, Any] | None = None
    dependencies: list[str] = field(default_factory=list)

    def __post_init__(self):
        """Validate node configuration."""
        if not self.name:
            raise ValueError("Node name is required")
        if not callable(self.handler):
            raise ValueError(f"Handler for node {self.name} must be callable")


@dataclass
class EdgeConfig:
    """Configuration for a workflow edge."""

    source: str
    target: str | Callable[[ResearchState], str]
    condition: Callable[[ResearchState], bool] | None = None
    description: str = ""

    def __post_init__(self):
        """Validate edge configuration."""
        if not self.source:
            raise ValueError("Edge source is required")
        if not self.target:
            raise ValueError("Edge target is required")


@dataclass
class GraphConfig:
    """Configuration for the entire workflow graph."""

    name: str = "research_workflow"
    description: str = "Multi-agent research workflow"
    enable_checkpointing: bool = True
    enable_parallel_execution: bool = True
    max_parallel_nodes: int = 3
    enable_visualization: bool = True
    router_config: RouterConfig | None = None

    def __post_init__(self):
        """Initialize router configuration if not provided."""
        if self.router_config is None:
            self.router_config = RouterConfig(
                enable_parallel_execution=self.enable_parallel_execution,
                max_parallel_agents=self.max_parallel_nodes,
            )


class ResearchGraphBuilder:
    """
    Builder for constructing research workflow graphs.

    Provides a fluent interface for defining nodes, edges, and routing logic.
    """

    def __init__(self, config: GraphConfig | None = None):
        """
        Initialize graph builder.

        Args:
            config: Graph configuration
        """
        self.config = config or GraphConfig()
        self.nodes: dict[str, NodeConfig] = {}
        self.edges: list[EdgeConfig] = []
        self.router = WorkflowRouter(self.config.router_config)
        self._graph: StateGraph | None = None
        self._compiled_graph = None

        # Checkpointing
        self.checkpointer = MemorySaver() if self.config.enable_checkpointing else None

    def add_node(
        self,
        name: str,
        handler: Callable[[ResearchState], ResearchState],
        phase: WorkflowPhase,
        **kwargs,
    ) -> "ResearchGraphBuilder":
        """
        Add a node to the graph.

        Args:
            name: Node name
            handler: Node handler function
            phase: Workflow phase for this node
            **kwargs: Additional node configuration

        Returns:
            Self for method chaining
        """
        node_config = NodeConfig(name=name, handler=handler, phase=phase, **kwargs)

        self.nodes[name] = node_config
        logger.debug(f"Added node: {name} for phase {phase}")

        return self

    def add_edge(
        self,
        source: str,
        target: str | Callable[[ResearchState], str],
        condition: Callable[[ResearchState], bool] | None = None,
        description: str = "",
    ) -> "ResearchGraphBuilder":
        """
        Add an edge to the graph.

        Args:
            source: Source node name
            target: Target node name or routing function
            condition: Optional condition for edge traversal
            description: Edge description

        Returns:
            Self for method chaining
        """
        edge_config = EdgeConfig(
            source=source, target=target, condition=condition, description=description
        )

        self.edges.append(edge_config)
        logger.debug(f"Added edge: {source} -> {target}")

        return self

    def add_conditional_edges(
        self,
        source: str,
        router: Callable[[ResearchState], str],
        route_map: dict[str, str],
    ) -> "ResearchGraphBuilder":
        """
        Add conditional edges with routing logic.

        Args:
            source: Source node name
            router: Routing function
            route_map: Mapping of router outputs to target nodes

        Returns:
            Self for method chaining
        """

        # Wrap router to ensure it returns valid targets
        def wrapped_router(state: ResearchState) -> str:
            route = router(state)
            if route not in route_map:
                logger.warning(f"Unknown route: {route}, defaulting to END")
                return END
            return route_map[route]

        self.add_edge(source, wrapped_router)

        return self

    def add_parallel_nodes(
        self, parent: str, nodes: list[NodeConfig], convergence_node: str
    ) -> "ResearchGraphBuilder":
        """
        Add nodes that execute in parallel.

        Args:
            parent: Parent node that triggers parallel execution
            nodes: List of nodes to execute in parallel
            convergence_node: Node where parallel paths converge

        Returns:
            Self for method chaining
        """
        if not self.config.enable_parallel_execution:
            # Fall back to sequential execution
            logger.warning("Parallel execution disabled, adding nodes sequentially")
            prev = parent
            for node in nodes:
                self.add_node(
                    node.name, node.handler, node.phase, description=node.description
                )
                self.add_edge(prev, node.name)
                prev = node.name
            self.add_edge(prev, convergence_node)
        else:
            # Create parallel execution branch
            for node in nodes:
                self.add_node(
                    node.name, node.handler, node.phase, description=node.description
                )
                self.add_edge(parent, node.name)
                self.add_edge(node.name, convergence_node)

        return self

    def build(self) -> StateGraph:
        """
        Build the workflow graph.

        Returns:
            Constructed StateGraph
        """
        if self._graph is not None:
            return self._graph

        # Create the graph
        self._graph = StateGraph(ResearchState)

        # Add all nodes
        for name, node_config in self.nodes.items():
            self._graph.add_node(name, self._wrap_handler(node_config))

        # Add all edges
        for edge in self.edges:
            if callable(edge.target):
                # Conditional edge
                self._graph.add_conditional_edges(edge.source, edge.target)
            else:
                # Direct edge
                if edge.condition:
                    # Add with condition
                    def conditional_target(state: ResearchState) -> str:
                        if edge.condition(state):
                            return edge.target
                        return END

                    self._graph.add_conditional_edges(edge.source, conditional_target)
                else:
                    # Simple edge
                    self._graph.add_edge(edge.source, edge.target)

        # Set entry point
        if "initialization" in self.nodes:
            self._graph.set_entry_point("initialization")
        elif self.nodes:
            # Use first node as entry point
            first_node = list(self.nodes.keys())[0]
            self._graph.set_entry_point(first_node)

        logger.info(
            f"Built graph with {len(self.nodes)} nodes and {len(self.edges)} edges"
        )

        return self._graph

    def compile(self, **kwargs):
        """
        Compile the workflow graph.

        Args:
            **kwargs: Additional compilation arguments

        Returns:
            Compiled workflow
        """
        if self._compiled_graph is not None:
            return self._compiled_graph

        graph = self.build()

        # Add checkpointer if enabled
        if self.checkpointer:
            kwargs["checkpointer"] = self.checkpointer

        self._compiled_graph = graph.compile(**kwargs)

        logger.info(f"Compiled graph: {self.config.name}")

        return self._compiled_graph

    def _wrap_handler(self, node_config: NodeConfig) -> Callable:
        """
        Wrap node handler with error handling and logging.

        Args:
            node_config: Node configuration

        Returns:
            Wrapped handler function
        """

        def wrapped_handler(state: ResearchState) -> ResearchState:
            logger.info(f"Executing node: {node_config.name}")

            try:
                # Update phase
                state.transition_to_phase(node_config.phase)

                # Execute handler
                result = node_config.handler(state)

                # Create checkpoint if needed
                if self.router.should_create_checkpoint(result):
                    result.create_checkpoint()

                logger.info(f"Node {node_config.name} completed successfully")
                return result

            except Exception as e:
                logger.error(f"Error in node {node_config.name}: {e}")
                state.error_count += 1
                state.error_history.append(
                    {
                        "node": node_config.name,
                        "error": str(e),
                        "phase": node_config.phase.value,
                    }
                )

                # Check if we should fail the workflow
                if state.error_count >= state.max_errors:
                    state.transition_to_phase(WorkflowPhase.FAILED)

                return state

        return wrapped_handler

    def visualize(self) -> str:
        """
        Generate a visualization of the graph.

        Returns:
            Graph visualization in DOT format
        """
        if not self.config.enable_visualization:
            return ""

        dot_lines = ["digraph ResearchWorkflow {"]
        dot_lines.append('  rankdir="TB";')
        dot_lines.append("  node [shape=box, style=rounded];")

        # Add nodes
        for name, node_config in self.nodes.items():
            label = f"{name}\\n({node_config.phase.value})"
            dot_lines.append(f'  {name} [label="{label}"];')

        # Add edges
        for edge in self.edges:
            if callable(edge.target):
                # Conditional edge - show as dashed
                dot_lines.append(f"  {edge.source} -> conditional [style=dashed];")
            else:
                # Direct edge
                style = "dashed" if edge.condition else "solid"
                dot_lines.append(f"  {edge.source} -> {edge.target} [style={style}];")

        dot_lines.append("}")

        return "\n".join(dot_lines)

    def get_metrics(self) -> dict[str, Any]:
        """
        Get graph metrics.

        Returns:
            Dictionary of graph metrics
        """
        return {
            "total_nodes": len(self.nodes),
            "total_edges": len(self.edges),
            "parallel_branches": sum(
                1
                for edge in self.edges
                if any(
                    e.source == edge.source and e.target != edge.target
                    for e in self.edges
                )
            ),
            "conditional_edges": sum(
                1 for edge in self.edges if callable(edge.target) or edge.condition
            ),
            "phases_covered": len(set(node.phase for node in self.nodes.values())),
        }


__all__ = [
    "EdgeConfig",
    "GraphConfig",
    "NodeConfig",
    "ResearchGraphBuilder",
]
