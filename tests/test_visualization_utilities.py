"""Tests for visualization helper factory functions."""

from src.models.report import VisualizationType
from src.services.visualization_generator import (
    create_confidence_radar_viz,
    create_domain_coverage_viz,
    create_source_distribution_viz,
)


class TestVisualizationUtilities:
    """Test utility functions for creating visualizations."""

    def test_create_source_distribution_viz(self) -> None:
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
        assert len(viz.data["x"]) == 3
        assert viz.data["y"] == [1, 2, 3]
        assert viz.config["x_label"] == "Publication Year"

    def test_create_domain_coverage_viz(self) -> None:
        """Test creating domain coverage visualization."""
        domains = ["AI", "Education", "Technology", "Psychology"]

        viz = create_domain_coverage_viz(domains, "domain-viz")

        assert viz.id == "domain-viz"
        assert viz.type == VisualizationType.PIE_CHART
        assert viz.title == "Research Domain Coverage"
        assert viz.data["labels"] == domains
        assert len(viz.data["values"]) == 4
        assert all(v == 1 for v in viz.data["values"])
        assert viz.config["donut"] is True

    def test_create_confidence_radar_viz(self) -> None:
        """Test creating confidence radar visualization."""
        categories = [
            "Data Quality",
            "Source Reliability",
            "Method Validity",
            "Result Consistency",
        ]
        scores = [0.85, 0.92, 0.78, 0.88]

        viz = create_confidence_radar_viz(categories, scores, "confidence-radar")

        assert viz.id == "confidence-radar"
        assert viz.type == VisualizationType.RADAR_CHART
        assert viz.title == "Confidence Scores by Category"
        assert viz.data["categories"] == categories
        assert viz.data["values"] == scores
