"""Tests for visualization integration with report generation."""

from typing import Any
from unittest.mock import patch

from src.models.report import (
    Report,
    ReportConfiguration,
    Visualization,
    VisualizationType,
)
from src.services.report_config import ReportSettings
from src.services.visualization_generator import (
    VisualizationGenerationError,
    VisualizationGenerator,
)


class TestVisualizationIntegration:
    """Test visualization integration with report generation."""

    def test_report_visualization_generation(self) -> None:
        """Test generating multiple visualizations for a report."""
        settings = ReportSettings(
            enable_visualizations=True, max_visualizations_per_report=5
        )
        generator = VisualizationGenerator(settings)

        report = Report(
            id="test-report",
            title="Test Report",
            query="Test query",
            configuration=ReportConfiguration(),
        )

        visualizations = [
            Visualization(
                id="viz-1",
                type=VisualizationType.BAR_CHART,
                title="Chart 1",
                data={"x": [1, 2, 3], "y": [10, 20, 30]},
            ),
            Visualization(
                id="viz-2",
                type=VisualizationType.PIE_CHART,
                title="Chart 2",
                data={"labels": ["A", "B"], "values": [60, 40]},
            ),
        ]

        for viz in visualizations:
            report.add_visualization(viz)

        assert len(report.visualizations) == 2

        with patch.object(generator, "generate_visualization") as mock_generate:
            mock_generate.return_value = {
                "type": "plotly",
                "format": "html",
                "data": "<div>Mock chart</div>",
                "title": "Mock Chart",
            }

            results = generator.generate_report_visualizations(report)

            assert len(results) == 2
            assert "viz-1" in results
            assert "viz-2" in results
            assert mock_generate.call_count == 2

    def test_visualization_limit_enforcement(self) -> None:
        """Test that visualization limits are enforced."""
        settings = ReportSettings(
            enable_visualizations=True,
            max_visualizations_per_report=2,
        )
        generator = VisualizationGenerator(settings)

        report = Report(
            id="test-report",
            title="Test Report",
            query="Test query",
            configuration=ReportConfiguration(),
        )

        for i in range(5):
            viz = Visualization(
                id=f"viz-{i}",
                type=VisualizationType.BAR_CHART,
                title=f"Chart {i}",
                data={"x": [1, 2], "y": [10, 20]},
            )
            report.add_visualization(viz)

        assert len(report.visualizations) == 5

        with patch.object(generator, "generate_visualization") as mock_generate:
            mock_generate.return_value = {"type": "plotly", "data": "mock"}

            results = generator.generate_report_visualizations(report)

            assert len(results) == 2
            assert mock_generate.call_count == 2

    def test_visualization_error_handling(self) -> None:
        """Test error handling during visualization generation."""
        generator = VisualizationGenerator()

        report = Report(
            id="test-report",
            title="Test Report",
            query="Test query",
            configuration=ReportConfiguration(),
        )

        viz1 = Visualization(
            id="viz-good",
            type=VisualizationType.BAR_CHART,
            title="Good Chart",
            data={"x": [1, 2], "y": [10, 20]},
        )

        viz2 = Visualization(
            id="viz-bad",
            type=VisualizationType.PIE_CHART,
            title="Bad Chart",
            data={},
        )

        report.add_visualization(viz1)
        report.add_visualization(viz2)

        def mock_generate_side_effect(
            viz_spec: Visualization, _format: str = "html"
        ) -> dict[str, Any]:
            if viz_spec.id == "viz-good":
                return {"type": "plotly", "data": "good_chart"}
            raise VisualizationGenerationError("Mock error")

        with patch.object(
            generator,
            "generate_visualization",
            side_effect=mock_generate_side_effect,
        ):
            results = generator.generate_report_visualizations(report)

            assert len(results) == 1
            assert "viz-good" in results
            assert "viz-bad" not in results
