"""Visualization specifications the report builder fills from measured data."""

from typing import Any

from src.models.report import Visualization, VisualizationType


def create_source_distribution_viz(
    sources: list[dict[str, Any]], viz_id: str = "source_dist"
) -> Visualization:
    year_counts: dict[str, int] = {}
    for source in sources:
        year = source.get("year", "Unknown")
        year_counts[str(year)] = year_counts.get(str(year), 0) + 1

    return Visualization(
        id=viz_id,
        type=VisualizationType.BAR_CHART,
        title="Source Distribution by Year",
        data={"x": list(year_counts.keys()), "y": list(year_counts.values())},
        config={"x_label": "Publication Year", "y_label": "Number of Sources"},
        caption=None,
        width=None,
        height=None,
    )


def create_confidence_radar_viz(
    categories: list[str],
    confidence_scores: list[float],
    viz_id: str = "confidence_radar",
) -> Visualization:
    """Create radar chart for confidence scores by category."""
    return Visualization(
        id=viz_id,
        type=VisualizationType.RADAR_CHART,
        title="Confidence Scores by Category",
        data={"categories": categories, "values": confidence_scores},
        config={},
        caption=None,
        width=None,
        height=None,
    )


__all__ = [
    "create_confidence_radar_viz",
    "create_source_distribution_viz",
]
