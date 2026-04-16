"""
Visualization generation service using Plotly and NetworkX.

This service generates interactive and static visualizations for research reports,
following functional programming principles with pure data transformation functions.
"""

import base64
import io
import logging
from typing import Any

try:
    import plotly.express as px
    import plotly.graph_objects as go
    import plotly.io as pio
    from plotly.subplots import make_subplots
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False
    go = None
    px = None
    make_subplots = None
    pio = None

try:
    import matplotlib
    import matplotlib.pyplot as plt_module
    import networkx as nx_module
    matplotlib.use('Agg')
    NETWORKX_AVAILABLE = True
    nx: Any = nx_module
    plt: Any = plt_module
except ImportError:
    NETWORKX_AVAILABLE = False
    nx = None
    plt = None

try:
    from wordcloud import WordCloud as WordCloudModule
    WORDCLOUD_AVAILABLE = True
    WordCloud: Any = WordCloudModule
except ImportError:
    WORDCLOUD_AVAILABLE = False
    WordCloud = None


from src.models.report import Report, Visualization, VisualizationType
from src.services.report_config import ReportSettings

logger = logging.getLogger(__name__)


class VisualizationGenerationError(Exception):
    """Exception raised during visualization generation."""
    pass


class VisualizationGenerator:
    """Service for generating visualizations for research reports."""
    
    def __init__(self, settings: ReportSettings | None = None):
        """Initialize visualization generator."""
        self.settings = settings or ReportSettings()
        
        # Check dependencies
        self._validate_dependencies()
        
        # Default visualization settings
        self.default_width = self.settings.default_chart_width
        self.default_height = self.settings.default_chart_height
        
        # Color schemes
        self.color_schemes = {
            'default': px.colors.qualitative.Set1,
            'professional': ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd'],
            'academic': ['#2E86AB', '#A23B72', '#F18F01', '#C73E1D', '#592E83'],
            'monochrome': ['#2c3e50', '#34495e', '#7f8c8d', '#95a5a6', '#bdc3c7'],
        }
    
    def _validate_dependencies(self) -> None:
        """Validate that required dependencies are available."""
        missing_deps = []
        
        if not PLOTLY_AVAILABLE:
            missing_deps.append("plotly")
        if not NETWORKX_AVAILABLE:
            missing_deps.append("networkx")
        if not WORDCLOUD_AVAILABLE:
            missing_deps.append("wordcloud")
        
        if missing_deps:
            logger.warning(f"Some visualization dependencies are missing: {missing_deps}")
    
    def generate_visualization(
        self,
        viz_spec: Visualization,
        format: str = 'html',
        theme: str = 'plotly_white'
    ) -> dict[str, Any]:
        """
        Generate a visualization from specification.
        
        Args:
            viz_spec: Visualization specification
            format: Output format ('html', 'png', 'svg', 'pdf')
            theme: Plotly theme to use
            
        Returns:
            Dictionary with visualization data and metadata
        """
        try:
            logger.info(f"Generating visualization: {viz_spec.id} ({viz_spec.type})")
            
            # Generate visualization based on type
            if viz_spec.type == VisualizationType.BAR_CHART:
                return self._generate_bar_chart(viz_spec, format, theme)
            elif viz_spec.type == VisualizationType.LINE_CHART:
                return self._generate_line_chart(viz_spec, format, theme)
            elif viz_spec.type == VisualizationType.PIE_CHART:
                return self._generate_pie_chart(viz_spec, format, theme)
            elif viz_spec.type == VisualizationType.SCATTER_PLOT:
                return self._generate_scatter_plot(viz_spec, format, theme)
            elif viz_spec.type == VisualizationType.RADAR_CHART:
                return self._generate_radar_chart(viz_spec, format, theme)
            elif viz_spec.type == VisualizationType.HEATMAP:
                return self._generate_heatmap(viz_spec, format, theme)
            elif viz_spec.type == VisualizationType.NETWORK_GRAPH:
                return self._generate_network_graph(viz_spec, format, theme)
            elif viz_spec.type == VisualizationType.WORD_CLOUD:
                return self._generate_word_cloud(viz_spec, format)
            elif viz_spec.type == VisualizationType.HISTOGRAM:
                return self._generate_histogram(viz_spec, format, theme)
            elif viz_spec.type == VisualizationType.BOX_PLOT:
                return self._generate_box_plot(viz_spec, format, theme)
            else:
                raise VisualizationGenerationError(f"Unsupported visualization type: {viz_spec.type}")
                
        except Exception as e:
            logger.error(f"Visualization generation failed: {e}")
            raise VisualizationGenerationError(f"Failed to generate {viz_spec.type}: {e}")
    
    def _generate_bar_chart(
        self,
        viz_spec: Visualization,
        format: str,
        theme: str
    ) -> dict[str, Any]:
        """Generate bar chart visualization."""
        if not PLOTLY_AVAILABLE:
            raise VisualizationGenerationError("Plotly not available for bar chart")
        
        data = viz_spec.data
        config = viz_spec.config
        
        # Extract data
        if 'x' in data and 'y' in data:
            x_values = data['x']
            y_values = data['y']
        elif 'labels' in data and 'values' in data:
            x_values = data['labels']
            y_values = data['values']
        else:
            # Try to infer from data structure
            keys = list(data.keys())
            values = list(data.values())
            x_values = keys
            y_values = values
        
        # Create bar chart
        fig = go.Figure(data=[
            go.Bar(
                x=x_values,
                y=y_values,
                marker_color=self.color_schemes['professional'][0],
                name=viz_spec.title
            )
        ])
        
        # Update layout
        fig.update_layout(
            title=viz_spec.title,
            xaxis_title=config.get('x_label', 'Category'),
            yaxis_title=config.get('y_label', 'Value'),
            template=theme,
            width=viz_spec.width or self.default_width,
            height=viz_spec.height or self.default_height,
        )
        
        return self._finalize_plotly_figure(fig, format, viz_spec)
    
    def _generate_line_chart(
        self,
        viz_spec: Visualization,
        format: str,
        theme: str
    ) -> dict[str, Any]:
        """Generate line chart visualization."""
        if not PLOTLY_AVAILABLE:
            raise VisualizationGenerationError("Plotly not available for line chart")
        
        data = viz_spec.data
        config = viz_spec.config
        
        # Create line chart
        fig = go.Figure()
        
        if 'series' in data:
            # Multiple series
            colors = self.color_schemes['professional']
            for i, series in enumerate(data['series']):
                fig.add_trace(go.Scatter(
                    x=series.get('x', []),
                    y=series.get('y', []),
                    mode='lines+markers',
                    name=series.get('name', f'Series {i+1}'),
                    line=dict(color=colors[i % len(colors)]),
                ))
        else:
            # Single series
            fig.add_trace(go.Scatter(
                x=data.get('x', []),
                y=data.get('y', []),
                mode='lines+markers',
                name=viz_spec.title,
                line=dict(color=self.color_schemes['professional'][0]),
            ))
        
        # Update layout
        fig.update_layout(
            title=viz_spec.title,
            xaxis_title=config.get('x_label', 'X Axis'),
            yaxis_title=config.get('y_label', 'Y Axis'),
            template=theme,
            width=viz_spec.width or self.default_width,
            height=viz_spec.height or self.default_height,
        )
        
        return self._finalize_plotly_figure(fig, format, viz_spec)
    
    def _generate_pie_chart(
        self,
        viz_spec: Visualization,
        format: str,
        theme: str
    ) -> dict[str, Any]:
        """Generate pie chart visualization."""
        if not PLOTLY_AVAILABLE:
            raise VisualizationGenerationError("Plotly not available for pie chart")
        
        data = viz_spec.data
        
        # Extract data
        labels = data.get('labels', [])
        values = data.get('values', [])
        
        # Create pie chart
        fig = go.Figure(data=[
            go.Pie(
                labels=labels,
                values=values,
                hole=0.3 if viz_spec.config.get('donut', False) else 0,
                marker_colors=self.color_schemes['professional'][:len(labels)],
            )
        ])
        
        # Update layout
        fig.update_layout(
            title=viz_spec.title,
            template=theme,
            width=viz_spec.width or self.default_width,
            height=viz_spec.height or self.default_height,
        )
        
        return self._finalize_plotly_figure(fig, format, viz_spec)
    
    def _generate_scatter_plot(
        self,
        viz_spec: Visualization,
        format: str,
        theme: str
    ) -> dict[str, Any]:
        """Generate scatter plot visualization."""
        if not PLOTLY_AVAILABLE:
            raise VisualizationGenerationError("Plotly not available for scatter plot")
        
        data = viz_spec.data
        config = viz_spec.config
        
        # Create scatter plot
        fig = go.Figure(data=go.Scatter(
            x=data.get('x', []),
            y=data.get('y', []),
            mode='markers',
            marker=dict(
                size=data.get('size', 8),
                color=data.get('color', self.color_schemes['professional'][0]),
                colorscale='Viridis' if 'color' in data else None,
                showscale=True if 'color' in data else False,
            ),
            text=data.get('text', None),
            hovertemplate='<b>%{text}</b><br>X: %{x}<br>Y: %{y}<extra></extra>' if 'text' in data else None,
        ))
        
        # Update layout
        fig.update_layout(
            title=viz_spec.title,
            xaxis_title=config.get('x_label', 'X Axis'),
            yaxis_title=config.get('y_label', 'Y Axis'),
            template=theme,
            width=viz_spec.width or self.default_width,
            height=viz_spec.height or self.default_height,
        )
        
        return self._finalize_plotly_figure(fig, format, viz_spec)
    
    def _generate_radar_chart(
        self,
        viz_spec: Visualization,
        format: str,
        theme: str
    ) -> dict[str, Any]:
        """Generate radar chart visualization."""
        if not PLOTLY_AVAILABLE:
            raise VisualizationGenerationError("Plotly not available for radar chart")
        
        data = viz_spec.data
        
        # Extract data
        categories = data.get('categories', [])
        
        fig = go.Figure()
        
        if 'series' in data:
            # Multiple series
            colors = self.color_schemes['professional']
            for i, series in enumerate(data['series']):
                values = series.get('values', [])
                # Close the radar chart
                values_closed = values + [values[0]] if values else []
                categories_closed = categories + [categories[0]] if categories else []
                
                fig.add_trace(go.Scatterpolar(
                    r=values_closed,
                    theta=categories_closed,
                    fill='toself',
                    name=series.get('name', f'Series {i+1}'),
                    line=dict(color=colors[i % len(colors)]),
                ))
        else:
            # Single series
            values = data.get('values', [])
            values_closed = values + [values[0]] if values else []
            categories_closed = categories + [categories[0]] if categories else []
            
            fig.add_trace(go.Scatterpolar(
                r=values_closed,
                theta=categories_closed,
                fill='toself',
                name=viz_spec.title,
                line=dict(color=self.color_schemes['professional'][0]),
            ))
        
        # Update layout
        fig.update_layout(
            polar=dict(
                radialaxis=dict(
                    visible=True,
                    range=[0, max(data.get('values', [1])) * 1.1] if 'values' in data else [0, 1]
                )),
            title=viz_spec.title,
            template=theme,
            width=viz_spec.width or self.default_width,
            height=viz_spec.height or self.default_height,
        )
        
        return self._finalize_plotly_figure(fig, format, viz_spec)
    
    def _generate_heatmap(
        self,
        viz_spec: Visualization,
        format: str,
        theme: str
    ) -> dict[str, Any]:
        """Generate heatmap visualization."""
        if not PLOTLY_AVAILABLE:
            raise VisualizationGenerationError("Plotly not available for heatmap")
        
        data = viz_spec.data
        config = viz_spec.config
        
        # Extract data
        z_values = data.get('z', [[]])
        x_labels = data.get('x', None)
        y_labels = data.get('y', None)
        
        # Create heatmap
        fig = go.Figure(data=go.Heatmap(
            z=z_values,
            x=x_labels,
            y=y_labels,
            colorscale=config.get('colorscale', 'RdYlBu'),
            showscale=True,
            hoverongaps=False,
        ))
        
        # Update layout
        fig.update_layout(
            title=viz_spec.title,
            xaxis_title=config.get('x_label', 'X Axis'),
            yaxis_title=config.get('y_label', 'Y Axis'),
            template=theme,
            width=viz_spec.width or self.default_width,
            height=viz_spec.height or self.default_height,
        )
        
        return self._finalize_plotly_figure(fig, format, viz_spec)
    
    def _generate_network_graph(
        self,
        viz_spec: Visualization,
        format: str,
        theme: str
    ) -> dict[str, Any]:
        """Generate network graph visualization."""
        if not NETWORKX_AVAILABLE:
            raise VisualizationGenerationError("NetworkX not available for network graph")
        
        data = viz_spec.data
        config = viz_spec.config
        
        # Create network graph
        G = nx.Graph()
        
        # Add nodes
        nodes = data.get('nodes', [])
        for node in nodes:
            if isinstance(node, dict):
                G.add_node(node['id'], **{k: v for k, v in node.items() if k != 'id'})
            else:
                G.add_node(node)
        
        # Add edges
        edges = data.get('edges', [])
        for edge in edges:
            if isinstance(edge, dict):
                G.add_edge(edge['source'], edge['target'], weight=edge.get('weight', 1))
            elif isinstance(edge, (list, tuple)) and len(edge) >= 2:
                G.add_edge(edge[0], edge[1])
        
        # Generate layout
        layout_type = config.get('layout', 'spring')
        if layout_type == 'spring':
            pos = nx.spring_layout(G, k=1, iterations=50)
        elif layout_type == 'circular':
            pos = nx.circular_layout(G)
        elif layout_type == 'random':
            pos = nx.random_layout(G)
        else:
            pos = nx.spring_layout(G)
        
        # If Plotly is available, create interactive network
        if PLOTLY_AVAILABLE:
            return self._create_plotly_network(G, pos, viz_spec, format, theme)
        else:
            return self._create_matplotlib_network(G, pos, viz_spec, format)
    
    def _create_plotly_network(
        self,
        G: Any,
        pos: dict[Any, Any],
        viz_spec: Visualization,
        format: str,
        theme: str
    ) -> dict[str, Any]:
        """Create interactive network using Plotly."""
        # Extract node and edge coordinates
        edge_x = []
        edge_y = []
        for edge in G.edges():
            x0, y0 = pos[edge[0]]
            x1, y1 = pos[edge[1]]
            edge_x.extend([x0, x1, None])
            edge_y.extend([y0, y1, None])
        
        # Create edge trace
        edge_trace = go.Scatter(
            x=edge_x, y=edge_y,
            line=dict(width=0.5, color='#888'),
            hoverinfo='none',
            mode='lines'
        )
        
        # Create node trace
        node_x = []
        node_y = []
        node_text = []
        node_info = []
        
        for node in G.nodes():
            x, y = pos[node]
            node_x.append(x)
            node_y.append(y)
            node_text.append(str(node))
            
            # Node info for hover
            adjacencies = list(G.neighbors(node))
            node_info.append(f'Node: {node}<br>Connections: {len(adjacencies)}')
        
        node_trace = go.Scatter(
            x=node_x, y=node_y,
            mode='markers+text',
            hoverinfo='text',
            text=node_text,
            hovertext=node_info,
            textposition="middle center",
            marker=dict(
                showscale=True,
                colorscale='YlGnBu',
                reversescale=True,
                color=[],
                size=10,
                colorbar=dict(
                    thickness=15,
                    len=0.5,
                    x=1.01
                ),
                line=dict(width=2, color='black')
            )
        )
        
        # Color nodes by degree
        node_adjacencies = []
        for node in G.nodes():
            node_adjacencies.append(len(list(G.neighbors(node))))
        
        node_trace.marker.color = node_adjacencies
        
        # Create figure
        fig = go.Figure(data=[edge_trace, node_trace],
                       layout=go.Layout(
                            title=viz_spec.title,
                            titlefont_size=16,
                            showlegend=False,
                            hovermode='closest',
                            margin=dict(b=20,l=5,r=5,t=40),
                            annotations=[ dict(
                                text="Network visualization showing node connections",
                                showarrow=False,
                                xref="paper", yref="paper",
                                x=0.005, y=-0.002,
                                xanchor='left', yanchor='bottom',
                                font=dict(size=12)
                            )],
                            xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                            yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                            width=viz_spec.width or self.default_width,
                            height=viz_spec.height or self.default_height,
                            template=theme
                        ))
        
        return self._finalize_plotly_figure(fig, format, viz_spec)
    
    def _create_matplotlib_network(
        self,
        G: Any,
        pos: dict[Any, Any],
        viz_spec: Visualization,
        format: str
    ) -> dict[str, Any]:
        """Create network using matplotlib (fallback)."""
        plt.figure(figsize=(10, 8))
        
        # Draw network
        nx.draw(G, pos,
                node_color='lightblue',
                node_size=500,
                with_labels=True,
                font_size=10,
                font_weight='bold',
                edge_color='gray',
                alpha=0.7)
        
        plt.title(viz_spec.title)
        plt.axis('off')
        
        # Save to base64
        img_buffer = io.BytesIO()
        plt.savefig(img_buffer, format='png', dpi=150, bbox_inches='tight')
        img_buffer.seek(0)
        img_base64 = base64.b64encode(img_buffer.getvalue()).decode()
        plt.close()
        
        return {
            'type': 'matplotlib',
            'format': 'png',
            'data': img_base64,
            'width': viz_spec.width or self.default_width,
            'height': viz_spec.height or self.default_height,
            'title': viz_spec.title,
        }
    
    def _generate_word_cloud(
        self,
        viz_spec: Visualization,
        format: str
    ) -> dict[str, Any]:
        """Generate word cloud visualization."""
        if not WORDCLOUD_AVAILABLE:
            raise VisualizationGenerationError("WordCloud not available")
        
        data = viz_spec.data
        config = viz_spec.config
        
        # Extract text or word frequencies
        if 'text' in data:
            text = data['text']
            wordcloud = WordCloud(
                width=viz_spec.width or self.default_width,
                height=viz_spec.height or self.default_height,
                background_color=config.get('background_color', 'white'),
                max_words=config.get('max_words', 100),
                colormap=config.get('colormap', 'viridis'),
                relative_scaling=0.5,
                random_state=42
            ).generate(text)
        elif 'frequencies' in data:
            frequencies = data['frequencies']
            wordcloud = WordCloud(
                width=viz_spec.width or self.default_width,
                height=viz_spec.height or self.default_height,
                background_color=config.get('background_color', 'white'),
                max_words=config.get('max_words', 100),
                colormap=config.get('colormap', 'viridis'),
                relative_scaling=0.5,
                random_state=42
            ).generate_from_frequencies(frequencies)
        else:
            raise VisualizationGenerationError("Word cloud requires 'text' or 'frequencies' in data")
        
        # Convert to image
        plt.figure(figsize=(12, 8))
        plt.imshow(wordcloud, interpolation='bilinear')
        plt.axis('off')
        plt.title(viz_spec.title, fontsize=16, pad=20)
        
        # Save to base64
        img_buffer = io.BytesIO()
        plt.savefig(img_buffer, format='png', dpi=150, bbox_inches='tight')
        img_buffer.seek(0)
        img_base64 = base64.b64encode(img_buffer.getvalue()).decode()
        plt.close()
        
        return {
            'type': 'wordcloud',
            'format': 'png',
            'data': img_base64,
            'width': viz_spec.width or self.default_width,
            'height': viz_spec.height or self.default_height,
            'title': viz_spec.title,
        }
    
    def _generate_histogram(
        self,
        viz_spec: Visualization,
        format: str,
        theme: str
    ) -> dict[str, Any]:
        """Generate histogram visualization."""
        if not PLOTLY_AVAILABLE:
            raise VisualizationGenerationError("Plotly not available for histogram")
        
        data = viz_spec.data
        config = viz_spec.config
        
        # Create histogram
        fig = go.Figure(data=[
            go.Histogram(
                x=data.get('values', []),
                nbinsx=config.get('bins', 20),
                name=viz_spec.title,
                marker_color=self.color_schemes['professional'][0],
                opacity=0.7
            )
        ])
        
        # Update layout
        fig.update_layout(
            title=viz_spec.title,
            xaxis_title=config.get('x_label', 'Value'),
            yaxis_title=config.get('y_label', 'Frequency'),
            template=theme,
            width=viz_spec.width or self.default_width,
            height=viz_spec.height or self.default_height,
        )
        
        return self._finalize_plotly_figure(fig, format, viz_spec)
    
    def _generate_box_plot(
        self,
        viz_spec: Visualization,
        format: str,
        theme: str
    ) -> dict[str, Any]:
        """Generate box plot visualization."""
        if not PLOTLY_AVAILABLE:
            raise VisualizationGenerationError("Plotly not available for box plot")
        
        data = viz_spec.data
        config = viz_spec.config
        
        fig = go.Figure()
        
        if 'groups' in data:
            # Multiple groups
            colors = self.color_schemes['professional']
            for i, (group_name, group_values) in enumerate(data['groups'].items()):
                fig.add_trace(go.Box(
                    y=group_values,
                    name=group_name,
                    marker_color=colors[i % len(colors)],
                ))
        else:
            # Single group
            fig.add_trace(go.Box(
                y=data.get('values', []),
                name=viz_spec.title,
                marker_color=self.color_schemes['professional'][0],
            ))
        
        # Update layout
        fig.update_layout(
            title=viz_spec.title,
            yaxis_title=config.get('y_label', 'Value'),
            template=theme,
            width=viz_spec.width or self.default_width,
            height=viz_spec.height or self.default_height,
        )
        
        return self._finalize_plotly_figure(fig, format, viz_spec)
    
    def _finalize_plotly_figure(
        self,
        fig: Any,
        format: str,
        viz_spec: Visualization
    ) -> dict[str, Any]:
        """Finalize Plotly figure and convert to requested format."""
        if format == 'html':
            html_str = pio.to_html(fig, include_plotlyjs='cdn', div_id=viz_spec.id)
            return {
                'type': 'plotly',
                'format': 'html',
                'data': html_str,
                'width': viz_spec.width or self.default_width,
                'height': viz_spec.height or self.default_height,
                'title': viz_spec.title,
                'interactive': True,
            }
        elif format in ['png', 'svg', 'pdf']:
            # Convert to static image
            img_bytes = pio.to_image(
                fig,
                format=format,
                width=viz_spec.width or self.default_width,
                height=viz_spec.height or self.default_height,
                scale=2
            )
            img_base64 = base64.b64encode(img_bytes).decode()
            
            return {
                'type': 'plotly',
                'format': format,
                'data': img_base64,
                'width': viz_spec.width or self.default_width,
                'height': viz_spec.height or self.default_height,
                'title': viz_spec.title,
                'interactive': False,
            }
        else:
            raise VisualizationGenerationError(f"Unsupported format: {format}")
    
    def generate_report_visualizations(
        self,
        report: Report,
        format: str = 'html'
    ) -> dict[str, dict[str, Any]]:
        """
        Generate all visualizations for a report.
        
        Args:
            report: Report object with visualization specifications
            format: Output format for visualizations
            
        Returns:
            Dictionary mapping visualization IDs to generated content
        """
        if not self.settings.enable_visualizations:
            logger.info("Visualization generation is disabled")
            return {}
        
        visualizations = {}
        max_viz = self.settings.max_visualizations_per_report
        
        for i, viz_spec in enumerate(report.visualizations):
            if i >= max_viz:
                logger.warning(f"Reached maximum visualization limit ({max_viz})")
                break
            
            try:
                viz_data = self.generate_visualization(viz_spec, format)
                visualizations[viz_spec.id] = viz_data
            except Exception as e:
                logger.error(f"Failed to generate visualization {viz_spec.id}: {e}")
                # Continue with other visualizations
        
        return visualizations


def create_visualization_generator(
    settings: ReportSettings | None = None
) -> VisualizationGenerator:
    """Factory function to create a visualization generator."""
    return VisualizationGenerator(settings)


# Utility functions for creating common visualization specifications
def create_source_distribution_viz(sources: list[dict[str, Any]], viz_id: str = "source_dist") -> Visualization:
    year_counts: dict[str, int] = {}
    for source in sources:
        year = source.get('year', 'Unknown')
        year_counts[str(year)] = year_counts.get(str(year), 0) + 1
    
    return Visualization(
        id=viz_id,
        type=VisualizationType.BAR_CHART,
        title="Source Distribution by Year",
        data={
            'x': list(year_counts.keys()),
            'y': list(year_counts.values())
        },
        config={
            'x_label': 'Publication Year',
            'y_label': 'Number of Sources'
        },
        caption=None,
        width=None,
        height=None
    )


def create_domain_coverage_viz(domains: list[str], viz_id: str = "domain_coverage") -> Visualization:
    """Create visualization specification for domain coverage."""
    return Visualization(
        id=viz_id,
        type=VisualizationType.PIE_CHART,
        title="Research Domain Coverage",
        data={
            'labels': domains,
            'values': [1] * len(domains)
        },
        config={'donut': True},
        caption=None,
        width=None,
        height=None
    )


def create_confidence_radar_viz(
    categories: list[str],
    confidence_scores: list[float],
    viz_id: str = "confidence_radar"
) -> Visualization:
    """Create radar chart for confidence scores by category."""
    return Visualization(
        id=viz_id,
        type=VisualizationType.RADAR_CHART,
        title="Confidence Scores by Category",
        data={
            'categories': categories,
            'values': confidence_scores
        },
        config={},
        caption=None,
        width=None,
        height=None
    )


__all__ = [
    "VisualizationGenerationError",
    "VisualizationGenerator",
    "create_confidence_radar_viz",
    "create_domain_coverage_viz",
    "create_source_distribution_viz",
    "create_visualization_generator",
]