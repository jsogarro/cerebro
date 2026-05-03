"""
Tests for visualization generation system.

This module tests the visualization generation functionality including
chart creation, network graphs, and word clouds.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.models.report import Visualization, VisualizationType
from src.services.report_config import ReportSettings
from src.services.visualization_generator import (
    VisualizationGenerationError,
    VisualizationGenerator,
)


class TestVisualizationModels:
    """Test visualization data models."""
    
    def test_visualization_creation(self) -> None:
        """Test basic visualization creation."""
        viz = Visualization(
            id="test-viz-1",
            type=VisualizationType.BAR_CHART,
            title="Test Bar Chart",
            data={"x": ["A", "B", "C"], "y": [10, 20, 30]},
            config={"x_label": "Categories", "y_label": "Values"},
            width=800,
            height=600,
            caption="This is a test chart"
        )
        
        assert viz.id == "test-viz-1"
        assert viz.type == VisualizationType.BAR_CHART
        assert viz.title == "Test Bar Chart"
        assert viz.width == 800
        assert viz.height == 600
        assert viz.caption == "This is a test chart"
        assert viz.data["x"] == ["A", "B", "C"]
        assert viz.config["x_label"] == "Categories"
    
    def test_visualization_types(self) -> None:
        """Test all visualization types are available."""
        types = [
            VisualizationType.BAR_CHART,
            VisualizationType.LINE_CHART,
            VisualizationType.PIE_CHART,
            VisualizationType.SCATTER_PLOT,
            VisualizationType.RADAR_CHART,
            VisualizationType.HEATMAP,
            VisualizationType.NETWORK_GRAPH,
            VisualizationType.WORD_CLOUD,
            VisualizationType.HISTOGRAM,
            VisualizationType.BOX_PLOT,
        ]
        
        for viz_type in types:
            assert isinstance(viz_type.value, str)
            assert len(viz_type.value) > 0


class TestVisualizationGenerator:
    """Test visualization generator service."""
    
    @pytest.fixture
    def generator(self) -> VisualizationGenerator:
        """Create visualization generator for testing."""
        settings = ReportSettings(
            enable_visualizations=True,
            max_visualizations_per_report=10,
            default_chart_width=800,
            default_chart_height=600
        )
        return VisualizationGenerator(settings)
    
    def test_generator_creation(self, generator: VisualizationGenerator) -> None:
        """Test generator initialization."""
        assert generator.settings.enable_visualizations is True
        assert generator.default_width == 800
        assert generator.default_height == 600
        assert len(generator.color_schemes) > 0
        assert 'default' in generator.color_schemes
        assert 'professional' in generator.color_schemes
    
    @patch('src.services.visualization_generator.PLOTLY_AVAILABLE', True)
    @patch('src.services.visualization_generator.go')
    @patch('src.services.visualization_generator.pio')
    def test_bar_chart_generation(
        self, mock_pio: MagicMock, mock_go: MagicMock, generator: VisualizationGenerator
    ) -> None:
        """Test bar chart generation."""
        # Mock Plotly components
        mock_figure = MagicMock()
        mock_go.Figure.return_value = mock_figure
        mock_go.Bar = MagicMock()
        mock_pio.to_html.return_value = "<div>Mock chart HTML</div>"
        
        viz_spec = Visualization(
            id="bar-test",
            type=VisualizationType.BAR_CHART,
            title="Test Bar Chart",
            data={"x": ["A", "B", "C"], "y": [10, 20, 30]},
            config={"x_label": "Categories", "y_label": "Values"}
        )
        
        result = generator.generate_visualization(viz_spec)
        
        assert result['type'] == 'plotly'
        assert result['format'] == 'html'
        assert result['title'] == "Test Bar Chart"
        assert result['interactive'] is True
        
        # Verify Plotly was called correctly
        mock_go.Figure.assert_called_once()
        mock_go.Bar.assert_called_once()
        mock_figure.update_layout.assert_called_once()
    
    @patch('src.services.visualization_generator.PLOTLY_AVAILABLE', True)
    @patch('src.services.visualization_generator.go')
    @patch('src.services.visualization_generator.pio')
    def test_pie_chart_generation(
        self, mock_pio: MagicMock, mock_go: MagicMock, generator: VisualizationGenerator
    ) -> None:
        """Test pie chart generation."""
        mock_figure = MagicMock()
        mock_go.Figure.return_value = mock_figure
        mock_go.Pie = MagicMock()
        mock_pio.to_html.return_value = "<div>Mock pie chart</div>"
        
        viz_spec = Visualization(
            id="pie-test",
            type=VisualizationType.PIE_CHART,
            title="Test Pie Chart",
            data={"labels": ["A", "B", "C"], "values": [30, 40, 30]},
            config={"donut": True}
        )
        
        result = generator.generate_visualization(viz_spec)
        
        assert result['type'] == 'plotly'
        assert result['title'] == "Test Pie Chart"
        
        # Verify pie chart with hole (donut)
        mock_go.Pie.assert_called_once()
        pie_call_args = mock_go.Pie.call_args[1]
        assert pie_call_args['hole'] == 0.3  # Donut hole
    
    @patch('src.services.visualization_generator.PLOTLY_AVAILABLE', True)
    @patch('src.services.visualization_generator.go')
    @patch('src.services.visualization_generator.pio')
    def test_line_chart_generation(
        self, mock_pio: MagicMock, mock_go: MagicMock, generator: VisualizationGenerator
    ) -> None:
        """Test line chart generation."""
        mock_figure = MagicMock()
        mock_go.Figure.return_value = mock_figure
        mock_go.Scatter = MagicMock()
        mock_pio.to_html.return_value = "<div>Mock line chart</div>"
        
        viz_spec = Visualization(
            id="line-test",
            type=VisualizationType.LINE_CHART,
            title="Test Line Chart",
            data={"x": [1, 2, 3, 4], "y": [10, 15, 12, 18]},
            config={"x_label": "Time", "y_label": "Value"}
        )
        
        result = generator.generate_visualization(viz_spec)
        
        assert result['type'] == 'plotly'
        assert result['title'] == "Test Line Chart"
        
        # Verify scatter plot with line mode
        mock_figure.add_trace.assert_called_once()
        mock_go.Scatter.assert_called_once()
        scatter_args = mock_go.Scatter.call_args[1]
        assert scatter_args['mode'] == 'lines+markers'
    
    @patch('src.services.visualization_generator.PLOTLY_AVAILABLE', True)
    @patch('src.services.visualization_generator.go')
    @patch('src.services.visualization_generator.pio')
    def test_radar_chart_generation(
        self, mock_pio: MagicMock, mock_go: MagicMock, generator: VisualizationGenerator
    ) -> None:
        """Test radar chart generation."""
        mock_figure = MagicMock()
        mock_go.Figure.return_value = mock_figure
        mock_go.Scatterpolar = MagicMock()
        mock_pio.to_html.return_value = "<div>Mock radar chart</div>"
        
        viz_spec = Visualization(
            id="radar-test",
            type=VisualizationType.RADAR_CHART,
            title="Test Radar Chart",
            data={
                "categories": ["A", "B", "C", "D"],
                "values": [0.8, 0.6, 0.9, 0.7]
            }
        )
        
        result = generator.generate_visualization(viz_spec)
        
        assert result['type'] == 'plotly'
        assert result['title'] == "Test Radar Chart"
        
        # Verify scatterpolar was called
        mock_figure.add_trace.assert_called_once()
        mock_go.Scatterpolar.assert_called_once()
        scatter_args = mock_go.Scatterpolar.call_args[1]
        assert scatter_args['fill'] == 'toself'
    
    @patch('src.services.visualization_generator.NETWORKX_AVAILABLE', True)
    @patch('src.services.visualization_generator.PLOTLY_AVAILABLE', True)
    @patch('src.services.visualization_generator.nx')
    @patch('src.services.visualization_generator.go')
    @patch('src.services.visualization_generator.pio')
    def test_network_graph_generation(
        self,
        mock_pio: MagicMock,
        mock_go: MagicMock,
        mock_nx: MagicMock,
        generator: VisualizationGenerator,
    ) -> None:
        """Test network graph generation."""
        # Mock NetworkX
        mock_graph = MagicMock()
        mock_nx.Graph.return_value = mock_graph
        mock_nx.spring_layout.return_value = {
            'node1': (0.1, 0.2),
            'node2': (0.8, 0.7),
            'node3': (0.5, 0.9)
        }
        mock_graph.edges.return_value = [('node1', 'node2'), ('node2', 'node3')]
        mock_graph.nodes.return_value = ['node1', 'node2', 'node3']
        mock_graph.neighbors.return_value = ['node2']
        
        # Mock Plotly
        mock_figure = MagicMock()
        mock_go.Figure.return_value = mock_figure
        mock_go.Scatter = MagicMock()
        mock_pio.to_html.return_value = "<div>Mock network graph</div>"
        
        viz_spec = Visualization(
            id="network-test",
            type=VisualizationType.NETWORK_GRAPH,
            title="Test Network Graph",
            data={
                "nodes": [
                    {"id": "node1", "label": "Node 1"},
                    {"id": "node2", "label": "Node 2"},
                    {"id": "node3", "label": "Node 3"}
                ],
                "edges": [
                    {"source": "node1", "target": "node2"},
                    {"source": "node2", "target": "node3"}
                ]
            },
            config={"layout": "spring"}
        )
        
        result = generator.generate_visualization(viz_spec)
        
        assert result['type'] == 'plotly'
        assert result['title'] == "Test Network Graph"
        
        # Verify NetworkX graph was created
        mock_nx.Graph.assert_called_once()
        mock_graph.add_node.assert_called()
        mock_graph.add_edge.assert_called()
    
    @patch('src.services.visualization_generator.WORDCLOUD_AVAILABLE', True)
    @patch('src.services.visualization_generator.WordCloud')
    @patch('src.services.visualization_generator.plt')
    def test_word_cloud_generation(
        self,
        mock_plt: MagicMock,
        mock_wordcloud_class: MagicMock,
        generator: VisualizationGenerator,
    ) -> None:
        """Test word cloud generation."""
        # Mock WordCloud
        mock_wordcloud = MagicMock()
        mock_wordcloud_class.return_value = mock_wordcloud
        mock_wordcloud.generate.return_value = mock_wordcloud
        
        # Mock matplotlib
        mock_plt.figure.return_value = MagicMock()
        mock_plt.imshow.return_value = MagicMock()
        
        # Mock image saving
        with patch('io.BytesIO') as mock_bytesio, \
             patch('base64.b64encode') as mock_b64encode:
            
            mock_buffer = MagicMock()
            mock_bytesio.return_value = mock_buffer
            mock_buffer.getvalue.return_value = b'fake_image_data'
            mock_b64encode.return_value = b'ZmFrZV9pbWFnZV9kYXRh'
            
            viz_spec = Visualization(
                id="wordcloud-test",
                type=VisualizationType.WORD_CLOUD,
                title="Test Word Cloud",
                data={"text": "artificial intelligence machine learning education technology"},
                config={"background_color": "white", "max_words": 50}
            )
            
            result = generator.generate_visualization(viz_spec)
            
            assert result['type'] == 'wordcloud'
            assert result['format'] == 'png'
            assert result['title'] == "Test Word Cloud"
            assert 'data' in result
            
            # Verify WordCloud was configured correctly
            mock_wordcloud_class.assert_called_once()
            wordcloud_kwargs = mock_wordcloud_class.call_args[1]
            assert wordcloud_kwargs['background_color'] == 'white'
            assert wordcloud_kwargs['max_words'] == 50
    
    def test_unsupported_visualization_type(
        self, generator: VisualizationGenerator
    ) -> None:
        """Test handling of unsupported visualization types."""
        viz_spec = Visualization.model_construct(
            id="unsupported-test",
            type="unsupported_type",
            title="Unsupported Chart",
            data={},
            config={},
        )
        
        with pytest.raises(VisualizationGenerationError):
            generator.generate_visualization(viz_spec)
    
    @patch('src.services.visualization_generator.PLOTLY_AVAILABLE', False)
    def test_missing_dependencies(self, generator: VisualizationGenerator) -> None:
        """Test handling when dependencies are missing."""
        viz_spec = Visualization(
            id="no-plotly-test",
            type=VisualizationType.BAR_CHART,
            title="Test Chart",
            data={"x": [1, 2, 3], "y": [10, 20, 30]}
        )
        
        with pytest.raises(VisualizationGenerationError, match="Plotly not available"):
            generator.generate_visualization(viz_spec)
    
    @patch('src.services.visualization_generator.PLOTLY_AVAILABLE', True)
    @patch('src.services.visualization_generator.pio')
    def test_static_image_export(
        self, mock_pio: MagicMock, generator: VisualizationGenerator
    ) -> None:
        """Test exporting visualization as static image."""
        # Mock image export
        mock_pio.to_image.return_value = b'fake_png_data'
        mock_pio.to_html.return_value = "<div>Mock chart</div>"
        
        with patch('base64.b64encode') as mock_b64encode:
            mock_b64encode.return_value = b'ZmFrZV9wbmdfZGF0YQ=='
            
            viz_spec = Visualization(
                id="static-test",
                type=VisualizationType.BAR_CHART,
                title="Static Chart",
                data={"x": [1, 2, 3], "y": [10, 20, 30]}
            )
            
            result = generator.generate_visualization(viz_spec, format='png')
            
            assert result['type'] == 'plotly'
            assert result['format'] == 'png'
            assert result['interactive'] is False
            assert 'data' in result
            
            # Verify static export was called
            mock_pio.to_image.assert_called_once()
