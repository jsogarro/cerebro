"""
Tests for visualization generation system.

This module tests the visualization generation functionality including
chart creation, network graphs, and word clouds.
"""

import base64
import json
from unittest.mock import MagicMock, patch

import pytest

from src.models.report import Visualization, VisualizationType
from src.services.report_config import ReportSettings
from src.services.visualization_generator import (
    VisualizationGenerator,
    VisualizationGenerationError,
    create_source_distribution_viz,
    create_domain_coverage_viz,
    create_confidence_radar_viz,
)


class TestVisualizationModels:
    """Test visualization data models."""
    
    def test_visualization_creation(self):
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
    
    def test_visualization_types(self):
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
    def generator(self):
        """Create visualization generator for testing."""
        settings = ReportSettings(
            enable_visualizations=True,
            max_visualizations_per_report=10,
            default_chart_width=800,
            default_chart_height=600
        )
        return VisualizationGenerator(settings)
    
    def test_generator_creation(self, generator):
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
    def test_bar_chart_generation(self, mock_pio, mock_go, generator):
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
    def test_pie_chart_generation(self, mock_pio, mock_go, generator):
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
    def test_line_chart_generation(self, mock_pio, mock_go, generator):
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
    def test_radar_chart_generation(self, mock_pio, mock_go, generator):
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
    def test_network_graph_generation(self, mock_pio, mock_go, mock_nx, generator):
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
    def test_word_cloud_generation(self, mock_plt, mock_wordcloud_class, generator):
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
    
    def test_unsupported_visualization_type(self, generator):
        """Test handling of unsupported visualization types."""
        # Create a visualization with an invalid type (simulate enum extension)
        viz_spec = Visualization(
            id="unsupported-test",
            type="unsupported_type",  # This would normally be caught by Pydantic
            title="Unsupported Chart",
            data={}
        )
        
        # Manually set the type to bypass Pydantic validation
        viz_spec.type = "unsupported_type"
        
        with pytest.raises(VisualizationGenerationError):
            generator.generate_visualization(viz_spec)
    
    @patch('src.services.visualization_generator.PLOTLY_AVAILABLE', False)
    def test_missing_dependencies(self, generator):
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
    def test_static_image_export(self, mock_pio, generator):
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


class TestVisualizationUtilities:
    """Test utility functions for creating visualizations."""
    
    def test_create_source_distribution_viz(self):
        """Test creating source distribution visualization."""
        sources = [
            {"year": 2020, "title": "Paper 1"},
            {"year": 2021, "title": "Paper 2"},
            {"year": 2021, "title": "Paper 3"},
            {"year": 2022, "title": "Paper 4"},
            {"year": 2022, "title": "Paper 5"},
            {"year": 2022, "title": "Paper 6"},
        ]
        
        viz = create_source_distribution_viz(sources, "test-dist")
        
        assert viz.id == "test-dist"
        assert viz.type == VisualizationType.BAR_CHART
        assert viz.title == "Source Distribution by Year"
        assert len(viz.data['x']) == 3  # 2020, 2021, 2022
        assert viz.data['y'] == [1, 2, 3]  # Counts for each year
        assert viz.config['x_label'] == "Publication Year"
    
    def test_create_domain_coverage_viz(self):
        """Test creating domain coverage visualization."""
        domains = ["AI", "Education", "Technology", "Psychology"]
        
        viz = create_domain_coverage_viz(domains, "domain-viz")
        
        assert viz.id == "domain-viz"
        assert viz.type == VisualizationType.PIE_CHART
        assert viz.title == "Research Domain Coverage"
        assert viz.data['labels'] == domains
        assert len(viz.data['values']) == 4
        assert all(v == 1 for v in viz.data['values'])  # Equal weights
        assert viz.config['donut'] is True
    
    def test_create_confidence_radar_viz(self):
        """Test creating confidence radar visualization."""
        categories = ["Data Quality", "Source Reliability", "Method Validity", "Result Consistency"]
        scores = [0.85, 0.92, 0.78, 0.88]
        
        viz = create_confidence_radar_viz(categories, scores, "confidence-radar")
        
        assert viz.id == "confidence-radar"
        assert viz.type == VisualizationType.RADAR_CHART
        assert viz.title == "Confidence Scores by Category"
        assert viz.data['categories'] == categories
        assert viz.data['values'] == scores


class TestVisualizationIntegration:
    """Test visualization integration with report generation."""
    
    def test_report_visualization_generation(self):
        """Test generating multiple visualizations for a report."""
        from src.models.report import Report, ReportConfiguration
        
        settings = ReportSettings(
            enable_visualizations=True,
            max_visualizations_per_report=5
        )
        generator = VisualizationGenerator(settings)
        
        # Create report with visualizations
        report = Report(
            id="test-report",
            title="Test Report", 
            query="Test query",
            configuration=ReportConfiguration()
        )
        
        # Add various visualization types
        visualizations = [
            Visualization(
                id="viz-1",
                type=VisualizationType.BAR_CHART,
                title="Chart 1",
                data={"x": [1, 2, 3], "y": [10, 20, 30]}
            ),
            Visualization(
                id="viz-2",
                type=VisualizationType.PIE_CHART,
                title="Chart 2",
                data={"labels": ["A", "B"], "values": [60, 40]}
            ),
        ]
        
        for viz in visualizations:
            report.add_visualization(viz)
        
        assert len(report.visualizations) == 2
        
        # Mock the generation for testing
        with patch.object(generator, 'generate_visualization') as mock_generate:
            mock_generate.return_value = {
                'type': 'plotly',
                'format': 'html',
                'data': '<div>Mock chart</div>',
                'title': 'Mock Chart'
            }
            
            results = generator.generate_report_visualizations(report)
            
            assert len(results) == 2
            assert 'viz-1' in results
            assert 'viz-2' in results
            assert mock_generate.call_count == 2
    
    def test_visualization_limit_enforcement(self):
        """Test that visualization limits are enforced."""
        settings = ReportSettings(
            enable_visualizations=True,
            max_visualizations_per_report=2  # Low limit for testing
        )
        generator = VisualizationGenerator(settings)
        
        from src.models.report import Report, ReportConfiguration
        
        report = Report(
            id="test-report",
            title="Test Report",
            query="Test query", 
            configuration=ReportConfiguration()
        )
        
        # Add more visualizations than the limit
        for i in range(5):
            viz = Visualization(
                id=f"viz-{i}",
                type=VisualizationType.BAR_CHART,
                title=f"Chart {i}",
                data={"x": [1, 2], "y": [10, 20]}
            )
            report.add_visualization(viz)
        
        assert len(report.visualizations) == 5
        
        # Mock the generation
        with patch.object(generator, 'generate_visualization') as mock_generate:
            mock_generate.return_value = {'type': 'plotly', 'data': 'mock'}
            
            results = generator.generate_report_visualizations(report)
            
            # Should only generate up to the limit
            assert len(results) == 2
            assert mock_generate.call_count == 2
    
    def test_visualization_error_handling(self):
        """Test error handling during visualization generation."""
        generator = VisualizationGenerator()
        
        from src.models.report import Report, ReportConfiguration
        
        report = Report(
            id="test-report",
            title="Test Report",
            query="Test query",
            configuration=ReportConfiguration()
        )
        
        # Add visualizations
        viz1 = Visualization(
            id="viz-good",
            type=VisualizationType.BAR_CHART,
            title="Good Chart",
            data={"x": [1, 2], "y": [10, 20]}
        )
        
        viz2 = Visualization(
            id="viz-bad",
            type=VisualizationType.PIE_CHART,
            title="Bad Chart",
            data={}  # Empty data that should cause error
        )
        
        report.add_visualization(viz1)
        report.add_visualization(viz2)
        
        # Mock generation with one success and one failure
        def mock_generate_side_effect(viz_spec, format='html'):
            if viz_spec.id == "viz-good":
                return {'type': 'plotly', 'data': 'good_chart'}
            else:
                raise VisualizationGenerationError("Mock error")
        
        with patch.object(generator, 'generate_visualization', side_effect=mock_generate_side_effect):
            results = generator.generate_report_visualizations(report)
            
            # Should return results for successful visualization only
            assert len(results) == 1
            assert 'viz-good' in results
            assert 'viz-bad' not in results


if __name__ == "__main__":
    pytest.main([__file__])