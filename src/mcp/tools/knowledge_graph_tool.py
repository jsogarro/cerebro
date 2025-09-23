"""
Knowledge Graph Tool for MCP.

Provides entity extraction, graph building, and analysis capabilities.
"""

import logging
from typing import Any

import networkx as nx
import plotly.graph_objects as go

from src.mcp.base import BaseMCPTool, ToolMetadata, ToolParameter

logger = logging.getLogger(__name__)


class KnowledgeGraphTool(BaseMCPTool):
    """
    MCP tool for knowledge graph operations.

    Supports entity extraction, graph building, analysis, and visualization.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        """Initialize knowledge graph tool."""
        super().__init__(config)
        self.graph = nx.Graph()

    def _build_metadata(self) -> ToolMetadata:
        """Build tool metadata."""
        return ToolMetadata(
            name="knowledge_graph",
            description="Build and analyze knowledge graphs",
            version="1.0.0",
            parameters=[
                ToolParameter(
                    name="operation",
                    type="string",
                    description="Operation (extract_entities, build_graph, analyze_graph, visualize)",
                    required=True,
                ),
                ToolParameter(
                    name="text",
                    type="string",
                    description="Text for entity extraction",
                    required=False,
                ),
                ToolParameter(
                    name="entities",
                    type="array",
                    description="List of entities",
                    required=False,
                ),
                ToolParameter(
                    name="relationships",
                    type="array",
                    description="List of relationships",
                    required=False,
                ),
            ],
            tags=["knowledge", "graph", "entity", "network", "visualization"],
        )

    async def execute(self, **kwargs) -> dict[str, Any]:
        """
        Execute knowledge graph operation.

        Args:
            **kwargs: Operation parameters

        Returns:
            Operation results
        """
        try:
            operation = kwargs.get("operation", "")

            if operation == "extract_entities":
                return self._extract_entities(kwargs.get("text", ""))
            elif operation == "build_graph":
                return self._build_graph(
                    kwargs.get("entities", []), kwargs.get("relationships", [])
                )
            elif operation == "analyze_graph":
                return self._analyze_graph()
            elif operation == "visualize":
                return self._visualize_graph()
            else:
                return {"success": False, "error": f"Unknown operation: {operation}"}

        except Exception as e:
            logger.error(f"Knowledge graph operation failed: {e!s}")
            return {"success": False, "error": str(e)}

    def _extract_entities(self, text: str) -> dict[str, Any]:
        """
        Extract entities from text.

        Simple implementation - in production would use NER models.
        """
        if not text:
            return {"success": False, "error": "No text provided"}

        # Simple entity extraction based on capitalization and patterns
        entities = []
        words = text.split()

        # Look for capitalized words (potential entities)
        for i, word in enumerate(words):
            # Skip first word of sentences (but allow common abbreviations)
            common_abbrevs = [
                "Inc.",
                "Corp.",
                "Ltd.",
                "LLC",
                "Dr.",
                "Mr.",
                "Mrs.",
                "Ms.",
            ]
            prev_is_sentence_end = (
                i > 0
                and words[i - 1].endswith(".")
                and words[i - 1] not in common_abbrevs
            )

            if word[0].isupper() and (i == 0 or not prev_is_sentence_end):
                # Check if it's part of a multi-word entity
                entity_text = word
                j = i + 1
                while j < len(words) and (
                    words[j][0].isupper()
                    or words[j] in ["Inc.", "Corp.", "Ltd.", "LLC"]
                ):
                    entity_text += " " + words[j]
                    j += 1

                # Classify entity type
                entity_type = self._classify_entity(entity_text)

                entities.append(
                    {"text": entity_text, "type": entity_type, "position": i}
                )

        # Remove duplicates
        unique_entities = []
        seen = set()
        for entity in entities:
            if entity["text"] not in seen:
                unique_entities.append(entity)
                seen.add(entity["text"])

        return {
            "success": True,
            "entities": unique_entities,
            "count": len(unique_entities),
        }

    def _classify_entity(self, text: str) -> str:
        """Classify entity type based on patterns."""
        # Simple classification rules
        if any(suffix in text.lower() for suffix in ["inc", "corp", "llc", "ltd"]):
            return "organization"
        elif any(
            word in text.lower() for word in ["university", "college", "institute"]
        ):
            return "institution"
        elif text.count(" ") >= 1 and text[0].isupper():
            # Multi-word capitalized - likely a person name
            return "person"
        elif text[0].isupper():
            return "concept"
        else:
            return "unknown"

    def _build_graph(
        self, entities: list[dict[str, Any]], relationships: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Build knowledge graph from entities and relationships."""
        if not entities:
            return {"success": False, "error": "No entities provided"}

        # Clear existing graph
        self.graph.clear()

        # Add nodes (entities)
        for entity in entities:
            self.graph.add_node(
                entity["id"],
                label=entity.get("text", entity["id"]),
                type=entity.get("type", "unknown"),
            )

        # Add edges (relationships)
        for rel in relationships:
            self.graph.add_edge(
                rel["source"], rel["target"], type=rel.get("type", "related")
            )

        return {
            "success": True,
            "graph": {
                "nodes": self.graph.number_of_nodes(),
                "edges": self.graph.number_of_edges(),
                "density": (
                    nx.density(self.graph) if self.graph.number_of_nodes() > 0 else 0
                ),
            },
        }

    def _analyze_graph(self) -> dict[str, Any]:
        """Analyze the current graph."""
        if self.graph.number_of_nodes() == 0:
            return {"success": False, "error": "No graph built yet"}

        # Calculate various metrics
        metrics = {}

        # Centrality measures
        if self.graph.number_of_nodes() > 0:
            degree_centrality = nx.degree_centrality(self.graph)
            top_nodes = sorted(
                degree_centrality.items(), key=lambda x: x[1], reverse=True
            )[:5]

            metrics["centrality"] = {
                "top_nodes": [
                    {"node": node, "score": score} for node, score in top_nodes
                ]
            }

        # Community detection (for connected graphs)
        if nx.is_connected(self.graph):
            communities = list(nx.community.greedy_modularity_communities(self.graph))
            metrics["communities"] = {
                "count": len(communities),
                "sizes": [len(c) for c in communities],
            }
        else:
            components = list(nx.connected_components(self.graph))
            metrics["communities"] = {
                "connected_components": len(components),
                "component_sizes": [len(c) for c in components],
            }

        # Graph statistics
        metrics["statistics"] = {
            "nodes": self.graph.number_of_nodes(),
            "edges": self.graph.number_of_edges(),
            "density": nx.density(self.graph),
            "average_degree": (
                sum(dict(self.graph.degree()).values()) / self.graph.number_of_nodes()
                if self.graph.number_of_nodes() > 0
                else 0
            ),
        }

        # Path analysis
        if self.graph.number_of_nodes() > 1:
            if nx.is_connected(self.graph):
                metrics["paths"] = {
                    "diameter": nx.diameter(self.graph),
                    "average_path_length": nx.average_shortest_path_length(self.graph),
                }

        return {"success": True, "metrics": metrics}

    def _visualize_graph(self) -> dict[str, Any]:
        """Generate graph visualization."""
        if self.graph.number_of_nodes() == 0:
            return {"success": False, "error": "No graph built yet"}

        # Use spring layout for positioning
        pos = nx.spring_layout(self.graph, k=1, iterations=50)

        # Extract node positions
        node_x = []
        node_y = []
        node_text = []

        for node in self.graph.nodes():
            x, y = pos[node]
            node_x.append(x)
            node_y.append(y)
            node_data = self.graph.nodes[node]
            node_text.append(node_data.get("label", str(node)))

        # Extract edge positions
        edge_x = []
        edge_y = []

        for edge in self.graph.edges():
            x0, y0 = pos[edge[0]]
            x1, y1 = pos[edge[1]]
            edge_x.extend([x0, x1, None])
            edge_y.extend([y0, y1, None])

        # Create Plotly figure
        edge_trace = go.Scatter(
            x=edge_x,
            y=edge_y,
            line=dict(width=0.5, color="#888"),
            hoverinfo="none",
            mode="lines",
        )

        node_trace = go.Scatter(
            x=node_x,
            y=node_y,
            mode="markers+text",
            text=node_text,
            textposition="top center",
            hoverinfo="text",
            marker=dict(
                showscale=True,
                colorscale="YlGnBu",
                size=10,
                colorbar=dict(
                    thickness=15,
                    title="Node Connections",
                    xanchor="left",
                    titleside="right",
                ),
            ),
        )

        # Color nodes by degree
        node_adjacencies = []
        for node in self.graph.nodes():
            node_adjacencies.append(len(list(self.graph.neighbors(node))))

        node_trace.marker.color = node_adjacencies

        # Create figure
        fig = go.Figure(
            data=[edge_trace, node_trace],
            layout=go.Layout(
                title="Knowledge Graph Visualization",
                showlegend=False,
                hovermode="closest",
                margin=dict(b=20, l=5, r=5, t=40),
                xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            ),
        )

        # Convert to HTML
        html = fig.to_html(include_plotlyjs="cdn")

        return {
            "success": True,
            "visualization": html,
            "nodes": self.graph.number_of_nodes(),
            "edges": self.graph.number_of_edges(),
        }
