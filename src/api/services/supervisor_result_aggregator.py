"""Result aggregation helpers for supervisor coordination."""

import asyncio
from typing import Any


class ResultAggregator:
    """Aggregates supervisor outputs and derives quality metrics."""

    def calculate_quality_score(self, result: Any, threshold: float) -> float | None:
        """Quality reported by the executor, or None when it reported none."""
        _ = threshold
        if not isinstance(result, dict):
            return None
        metadata = result.get("_execution_metadata")
        score = metadata.get("quality_score") if isinstance(metadata, dict) else None
        if isinstance(score, bool) or not isinstance(score, int | float):
            return None
        return min(1.0, max(0.0, float(score)))

    async def synthesize_results(
        self,
        results: dict[str, Any],
        priority_weights: dict[str, float],
    ) -> tuple[Any, bool]:
        """Synthesize results from multiple supervisors."""
        await asyncio.sleep(0.3)

        synthesis_parts = []
        for supervisor_type, result_data in results.items():
            weight = priority_weights.get(supervisor_type, 1.0)
            synthesis_parts.append(
                f"{supervisor_type} (weight={weight}): {result_data['result']}"
            )

        synthesized = f"Synthesized result combining: {'; '.join(synthesis_parts)}"

        quality_scores = [result["quality_score"] for result in results.values()]
        consensus = bool(
            all(abs(score - quality_scores[0]) < 0.1 for score in quality_scores)
        )

        return synthesized, consensus

    def calculate_consistency(self, results: dict[str, Any]) -> float:
        """Calculate consistency across supervisor results."""
        if len(results) < 2:
            return 1.0

        quality_scores = [result["quality_score"] for result in results.values()]
        mean_score = sum(quality_scores) / len(quality_scores)
        variance = sum(
            (quality_score - mean_score) ** 2 for quality_score in quality_scores
        ) / len(quality_scores)

        consistency = max(0.0, 1.0 - (variance * 10))
        return float(consistency)
