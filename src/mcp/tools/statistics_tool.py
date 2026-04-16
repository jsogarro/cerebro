"""
Statistics Tool for MCP.

Provides statistical analysis and visualization capabilities.
"""

import logging
from typing import Any

import numpy as np
import plotly.graph_objects as go
from scipy import stats

from src.mcp.base import BaseMCPTool, ToolMetadata, ToolParameter

logger = logging.getLogger(__name__)


class StatisticsTool(BaseMCPTool):
    """
    MCP tool for statistical analysis and visualization.

    Supports descriptive statistics, hypothesis testing, and plotting.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        """Initialize statistics tool."""
        super().__init__(config)

    def _build_metadata(self) -> ToolMetadata:
        """Build tool metadata."""
        return ToolMetadata(
            name="statistics_analyzer",
            description="Perform statistical analysis and generate visualizations",
            version="1.0.0",
            parameters=[
                ToolParameter(
                    name="operation",
                    type="string",
                    description="Operation to perform (descriptive, t_test, correlation, plot)",
                    required=True,
                ),
                ToolParameter(
                    name="data",
                    type="array",
                    description="Data for analysis",
                    required=False,
                ),
                ToolParameter(
                    name="group1",
                    type="array",
                    description="First group for comparison",
                    required=False,
                ),
                ToolParameter(
                    name="group2",
                    type="array",
                    description="Second group for comparison",
                    required=False,
                ),
                ToolParameter(
                    name="x",
                    type="array",
                    description="X values for correlation",
                    required=False,
                ),
                ToolParameter(
                    name="y",
                    type="array",
                    description="Y values for correlation",
                    required=False,
                ),
                ToolParameter(
                    name="plot_type",
                    type="string",
                    description="Type of plot (histogram, box, scatter)",
                    required=False,
                    default="histogram",
                ),
            ],
            tags=["statistics", "analysis", "visualization", "hypothesis"],
        )

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        """
        Execute statistical operation.

        Args:
            **kwargs: Operation parameters

        Returns:
            Analysis results
        """
        try:
            operation = kwargs.get("operation", "")

            if operation == "descriptive":
                return self._descriptive_statistics(kwargs.get("data", []))
            elif operation == "t_test":
                return self._t_test(kwargs.get("group1", []), kwargs.get("group2", []))
            elif operation == "correlation":
                return self._correlation_analysis(
                    kwargs.get("x", []), kwargs.get("y", [])
                )
            elif operation == "plot":
                return self._generate_plot(
                    kwargs.get("data", []), kwargs.get("plot_type", "histogram")
                )
            else:
                return {"success": False, "error": f"Unknown operation: {operation}"}

        except Exception as e:
            logger.error(f"Statistical operation failed: {e!s}")
            return {"success": False, "error": str(e)}

    def _descriptive_statistics(self, data: list[float]) -> dict[str, Any]:
        """Calculate descriptive statistics."""
        if not data:
            return {"success": False, "error": "No data provided"}

        arr = np.array(data)

        return {
            "success": True,
            "mean": float(np.mean(arr)),
            "median": float(np.median(arr)),
            "std": float(np.std(arr)),
            "variance": float(np.var(arr)),
            "min": float(np.min(arr)),
            "max": float(np.max(arr)),
            "q1": float(np.percentile(arr, 25)),
            "q3": float(np.percentile(arr, 75)),
            "iqr": float(np.percentile(arr, 75) - np.percentile(arr, 25)),
            "count": len(data),
        }

    def _t_test(self, group1: list[float], group2: list[float]) -> dict[str, Any]:
        """Perform independent t-test."""
        if not group1 or not group2:
            return {"success": False, "error": "Both groups required for t-test"}

        arr1 = np.array(group1)
        arr2 = np.array(group2)

        t_stat, p_value = stats.ttest_ind(arr1, arr2)

        # Calculate effect size (Cohen's d)
        pooled_std = np.sqrt((np.var(arr1) + np.var(arr2)) / 2)
        cohens_d = (np.mean(arr1) - np.mean(arr2)) / pooled_std if pooled_std > 0 else 0

        return {
            "success": True,
            "t_statistic": float(t_stat),
            "p_value": float(p_value),
            "cohens_d": float(cohens_d),
            "group1_mean": float(np.mean(arr1)),
            "group2_mean": float(np.mean(arr2)),
            "significant": p_value < 0.05,
            "interpretation": (
                "Significant difference"
                if p_value < 0.05
                else "No significant difference"
            ),
        }

    def _correlation_analysis(self, x: list[float], y: list[float]) -> dict[str, Any]:
        """Perform correlation analysis."""
        if not x or not y:
            return {
                "success": False,
                "error": "Both x and y data required for correlation",
            }

        if len(x) != len(y):
            return {"success": False, "error": "x and y must have same length"}

        arr_x = np.array(x)
        arr_y = np.array(y)

        # Pearson correlation
        pearson_r, pearson_p = stats.pearsonr(arr_x, arr_y)

        # Spearman correlation
        spearman_r, spearman_p = stats.spearmanr(arr_x, arr_y)

        # Linear regression
        slope, intercept, r_value, p_value, std_err = stats.linregress(arr_x, arr_y)

        return {
            "success": True,
            "correlation": float(pearson_r),
            "pearson_r": float(pearson_r),
            "pearson_p": float(pearson_p),
            "spearman_r": float(spearman_r),
            "spearman_p": float(spearman_p),
            "regression": {
                "slope": float(slope),
                "intercept": float(intercept),
                "r_squared": float(r_value**2),
                "p_value": float(p_value),
                "std_error": float(std_err),
            },
            "interpretation": self._interpret_correlation(pearson_r),
        }

    def _interpret_correlation(self, r: float) -> str:
        """Interpret correlation coefficient."""
        abs_r = abs(r)
        if abs_r >= 0.9:
            strength = "Very strong"
        elif abs_r >= 0.7:
            strength = "Strong"
        elif abs_r >= 0.5:
            strength = "Moderate"
        elif abs_r >= 0.3:
            strength = "Weak"
        else:
            strength = "Very weak"

        direction = "positive" if r > 0 else "negative"
        return f"{strength} {direction} correlation"

    def _generate_plot(self, data: list[float], plot_type: str) -> dict[str, Any]:
        """Generate statistical plot."""
        if not data:
            return {"success": False, "error": "No data provided for plotting"}

        try:
            if plot_type == "histogram":
                fig = go.Figure(data=[go.Histogram(x=data)])
                fig.update_layout(
                    title="Histogram", xaxis_title="Value", yaxis_title="Frequency"
                )
            elif plot_type == "box":
                fig = go.Figure(data=[go.Box(y=data)])
                fig.update_layout(title="Box Plot", yaxis_title="Value")
            elif plot_type == "scatter":
                fig = go.Figure(
                    data=[go.Scatter(x=list(range(len(data))), y=data, mode="markers")]
                )
                fig.update_layout(
                    title="Scatter Plot", xaxis_title="Index", yaxis_title="Value"
                )
            else:
                # Default to histogram
                fig = go.Figure(data=[go.Histogram(x=data)])

            # Convert to HTML
            plot_html = fig.to_html(include_plotlyjs="cdn")

            return {
                "success": True,
                "plot_html": plot_html,
                "plot_type": plot_type,
                "data_points": len(data),
            }

        except Exception as e:
            logger.error(f"Plot generation failed: {e!s}")
            return {"success": False, "error": str(e)}
