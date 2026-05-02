"""Comparison matrix helpers for the comparative analysis agent."""

from typing import Any


class ComparisonMatrixBuilder:
    """Builds, normalizes, ranks, and visualizes comparison matrices."""

    def normalize_comparison_matrix(
        self,
        matrix: dict[str, dict[str, float]],
    ) -> dict[str, dict[str, float]]:
        """Normalize comparison matrix values to 0-1 range."""
        if not matrix:
            return {}

        criteria_ranges = {}
        for item_scores in matrix.values():
            for criterion, score in item_scores.items():
                if criterion not in criteria_ranges:
                    criteria_ranges[criterion] = {"min": score, "max": score}
                else:
                    criteria_ranges[criterion]["min"] = min(
                        criteria_ranges[criterion]["min"], score
                    )
                    criteria_ranges[criterion]["max"] = max(
                        criteria_ranges[criterion]["max"], score
                    )

        normalized: dict[str, dict[str, float]] = {}
        for item, scores in matrix.items():
            normalized[item] = {}
            for criterion, score in scores.items():
                range_val = (
                    criteria_ranges[criterion]["max"]
                    - criteria_ranges[criterion]["min"]
                )
                if range_val > 0:
                    normalized[item][criterion] = (
                        score - criteria_ranges[criterion]["min"]
                    ) / range_val
                else:
                    normalized[item][criterion] = score

        return normalized

    def calculate_rankings(
        self,
        matrix: dict[str, dict[str, float]],
        criteria: list[str],
    ) -> dict[str, list[str]]:
        """Calculate rankings for each criterion and overall."""
        if not matrix:
            return {}

        rankings = {}

        for criterion in criteria:
            criterion_scores = [
                (item, item_scores.get(criterion, 0.0))
                for item, item_scores in matrix.items()
            ]
            criterion_scores.sort(key=lambda x: x[1], reverse=True)
            rankings[criterion] = [item for item, _ in criterion_scores]

        overall_scores: dict[str, float] = {}
        for item, item_scores in matrix.items():
            overall_scores[item] = (
                sum(item_scores.values()) / len(item_scores) if item_scores else 0.0
            )

        overall_ranking = sorted(
            overall_scores.items(), key=lambda x: x[1], reverse=True
        )
        rankings["overall"] = [item for item, _ in overall_ranking]

        return rankings

    def assess_matrix_completeness(
        self,
        matrix: dict[str, dict[str, float]],
    ) -> float:
        """Assess how complete the comparison matrix is."""
        if not matrix:
            return 0.0

        all_criteria: set[str] = set()
        for scores in matrix.values():
            all_criteria.update(scores.keys())

        if not all_criteria:
            return 0.0

        completeness_scores = []
        for scores in matrix.values():
            item_completeness = len(scores) / len(all_criteria)
            completeness_scores.append(item_completeness)

        return sum(completeness_scores) / len(completeness_scores)

    def generate_visual_data(
        self,
        matrix: dict[str, dict[str, float]],
        criteria: list[str],
    ) -> dict[str, Any]:
        """Generate data for visual representation."""
        data_points: dict[str, list[float]] = {}
        for item, scores in matrix.items():
            data_points[item] = [
                scores.get(criterion, 0.0) for criterion in criteria
            ]

        return {
            "chart_type": "radar",
            "labels": criteria,
            "data_points": data_points,
        }

    async def enhance_matrix_with_statistics(
        self,
        matrix: dict[str, dict[str, float]],
        statistical_data: dict[str, Any],
    ) -> dict[str, dict[str, float]]:
        """Enhance comparison matrix with statistical insights."""
        if not matrix or not statistical_data.get("success"):
            return matrix

        enhanced_matrix = matrix.copy()
        descriptive_stats = statistical_data.get("descriptive_stats", {})
        if descriptive_stats:
            for item in enhanced_matrix:
                for criterion in enhanced_matrix[item]:
                    original_score = enhanced_matrix[item][criterion]
                    std_dev = descriptive_stats.get("std_dev", 1.0)
                    mean_val = descriptive_stats.get("mean", 0.5)

                    if std_dev > 0:
                        z_score = (original_score - mean_val) / std_dev
                        enhanced_matrix[item][criterion] = max(
                            0, min(1, 0.5 + z_score * 0.2)
                        )

        return enhanced_matrix

    async def calculate_statistical_rankings(
        self,
        matrix: dict[str, dict[str, float]],
        statistical_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Calculate statistically-informed rankings."""
        if not statistical_data.get("success"):
            return {"method": "basic", "note": "No statistical data available"}

        item_scores = {}
        for item, scores in matrix.items():
            mean_score = sum(scores.values()) / len(scores) if scores else 0.0
            item_scores[item] = {
                "score": mean_score,
                "confidence": self.calculate_ranking_confidence(
                    scores, statistical_data
                ),
            }

        sorted_items = sorted(
            item_scores.items(), key=lambda x: x[1]["score"], reverse=True
        )

        rankings_list: list[dict[str, str | float | int]] = [
            {"item": item, "score": data["score"], "confidence": data["confidence"]}
            for item, data in sorted_items
        ]

        return {
            "overall_with_confidence": rankings_list,
            "method": "statistical",
            "tests_used": statistical_data.get("tests_performed", []),
        }

    def calculate_ranking_confidence(
        self,
        scores: dict[str, float],
        statistical_data: dict[str, Any],
    ) -> float:
        """Calculate confidence for individual item ranking."""
        if not scores:
            return 0.0

        score_values = list(scores.values())
        variance = sum((s - 0.5) ** 2 for s in score_values) / len(score_values)
        base_confidence = max(0.5, 1.0 - variance * 2)

        if statistical_data.get("data_quality") == "high":
            base_confidence = min(1.0, base_confidence + 0.2)

        return base_confidence
