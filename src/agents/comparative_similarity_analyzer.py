"""Similarity and confidence scoring helpers for comparative analysis."""

from typing import Any


class SimilarityAnalyzer:
    """Analyzes comparative similarity signals and confidence scores."""

    def calculate_confidence(
        self,
        items_count: int,
        criteria_count: int,
        matrix_completeness: float,
        trade_offs: list[str],
        recommendations: list[str],
    ) -> float:
        """Calculate confidence score based on analysis completeness."""
        confidence = 0.4

        if items_count >= 4:
            confidence += 0.15
        elif items_count >= 3:
            confidence += 0.10
        elif items_count >= 2:
            confidence += 0.05

        if criteria_count >= 4:
            confidence += 0.15
        elif criteria_count >= 3:
            confidence += 0.10
        elif criteria_count >= 2:
            confidence += 0.05
        elif criteria_count >= 1:
            confidence += 0.02

        confidence += matrix_completeness * 0.2

        if len(trade_offs) >= 3:
            confidence += 0.1
        elif len(trade_offs) >= 2:
            confidence += 0.07
        elif len(trade_offs) >= 1:
            confidence += 0.04

        if len(recommendations) >= 3:
            confidence += 0.1
        elif len(recommendations) >= 2:
            confidence += 0.07
        elif len(recommendations) >= 1:
            confidence += 0.04

        return min(max(confidence, 0.0), 1.0)

    def calculate_confidence_with_mcp(
        self,
        items_count: int,
        criteria_count: int,
        matrix_completeness: float,
        trade_offs: list[str],
        recommendations: list[str],
        mcp_data_quality: dict[str, bool],
    ) -> float:
        """Calculate confidence score with MCP data quality factors."""
        confidence = self.calculate_confidence(
            items_count,
            criteria_count,
            matrix_completeness,
            trade_offs,
            recommendations,
        )

        mcp_bonus = 0.0
        if mcp_data_quality.get("research_quality"):
            mcp_bonus += 0.05
        if mcp_data_quality.get("statistical_quality"):
            mcp_bonus += 0.05
        if mcp_data_quality.get("graph_quality"):
            mcp_bonus += 0.05

        return min(confidence + mcp_bonus, 1.0)

    def calculate_text_similarity(self, first: str, second: str) -> float:
        """Calculate deterministic token-overlap similarity for two text values."""
        first_words = set(first.lower().split())
        second_words = set(second.lower().split())
        if not first_words and not second_words:
            return 1.0
        if not first_words or not second_words:
            return 0.0

        return len(first_words & second_words) / len(first_words | second_words)

    def calculate_matrix_similarity(
        self,
        matrix: dict[str, dict[str, float]],
    ) -> dict[str, Any]:
        """Calculate pairwise similarity between compared items from score vectors."""
        items = list(matrix)
        similarities: list[dict[str, Any]] = []

        for left_index, left_item in enumerate(items):
            for right_item in items[left_index + 1 :]:
                shared_criteria = set(matrix[left_item]) & set(matrix[right_item])
                if not shared_criteria:
                    similarity = 0.0
                else:
                    distance = sum(
                        abs(matrix[left_item][criterion] - matrix[right_item][criterion])
                        for criterion in shared_criteria
                    ) / len(shared_criteria)
                    similarity = max(0.0, 1.0 - distance)

                similarities.append(
                    {
                        "items": [left_item, right_item],
                        "similarity": similarity,
                        "shared_criteria": len(shared_criteria),
                    }
                )

        average_similarity = (
            sum(float(pair["similarity"]) for pair in similarities) / len(similarities)
            if similarities
            else 0.0
        )

        return {
            "pairwise": similarities,
            "average_similarity": average_similarity,
            "method": "score_vector_overlap",
        }
