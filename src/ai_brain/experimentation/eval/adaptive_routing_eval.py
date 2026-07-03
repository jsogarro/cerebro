"""
Offline Evaluation Harness for Adaptive Routing

This module provides a deterministic, synthetic evaluation of the adaptive
routing system. It generates a corpus of realistic routing scenarios and
compares static vs. adaptive allocation strategies.

LIMITATIONS:
- Synthetic corpus cannot validate real LLM quality improvements
- Simulated outcomes use a simplified quality function
- Real production validation requires A/B test with live traffic

PURPOSE:
- Demonstrate bandit learning (regret decreases over time)
- Verify allocation bounds are respected
- Validate cold-start grace period
- Ensure no regressions in edge cases
"""

import asyncio
import json
import random
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import numpy as np
from structlog import get_logger

from src.ai_brain.router.query_analyzer import ComplexityLevel
from src.ai_brain.router.routing_types import CollaborationMode

logger = get_logger()


@dataclass
class SyntheticQuery:
    """A synthetic query with known optimal allocation."""

    query_text: str
    complexity_level: ComplexityLevel
    collaboration_mode: CollaborationMode
    analytic_worker_count: int
    optimal_worker_count: int  # Ground truth for evaluation
    base_quality: float  # Base quality score before allocation penalty


@dataclass
class SimulatedOutcome:
    """Simulated outcome for a routing decision."""

    latency_ms: int
    cost: float
    quality_score: float


@dataclass
class EvalMetrics:
    """Evaluation metrics comparing static vs adaptive."""

    static_mae: float  # Mean absolute error from optimal (static)
    adaptive_mae: float  # Mean absolute error from optimal (adaptive)
    static_worker_dist: dict[int, int]  # Histogram of worker counts
    adaptive_worker_dist: dict[int, int]
    adaptation_rate: float  # % of queries where adaptive changed allocation
    bound_hit_rate: float  # % of queries where adaptive hit ±2 cap
    cumulative_regret: float  # Bandit regret (not yet implemented in this stub)
    num_queries: int


class AdaptiveRoutingEvaluator:
    """Offline evaluator for adaptive routing."""

    def __init__(self, seed: int = 42):
        """Initialize evaluator with deterministic seed."""
        self.seed = seed
        random.seed(seed)
        np.random.seed(seed)

    def generate_corpus(self, num_queries: int = 200) -> list[SyntheticQuery]:
        """Generate synthetic query corpus with realistic complexity distribution."""
        corpus = []

        # Distribution: 25% SIMPLE, 40% MODERATE, 35% COMPLEX
        distributions = [
            (ComplexityLevel.SIMPLE, CollaborationMode.DIRECT, 50, 1, 0.85),
            (ComplexityLevel.MODERATE, CollaborationMode.PARALLEL, 80, 3, 0.80),
            (ComplexityLevel.COMPLEX, CollaborationMode.HIERARCHICAL, 70, 5, 0.75),
        ]

        query_id = 0
        for level, mode, count, optimal_workers, base_quality in distributions:
            for _ in range(count):
                # Analytic baseline is optimal ± 1 (with some noise)
                analytic = max(1, optimal_workers + random.randint(-1, 1))
                corpus.append(
                    SyntheticQuery(
                        query_text=f"Synthetic query {query_id} ({level.value})",
                        complexity_level=level,
                        collaboration_mode=mode,
                        analytic_worker_count=analytic,
                        optimal_worker_count=optimal_workers,
                        base_quality=base_quality,
                    )
                )
                query_id += 1

        # Shuffle to avoid ordering bias
        random.shuffle(corpus)
        return corpus

    def simulate_outcome(
        self, query: SyntheticQuery, allocated_workers: int
    ) -> SimulatedOutcome:
        """Simulate outcome based on allocation quality."""
        # Quality penalty: quadratic distance from optimal
        worker_delta = abs(allocated_workers - query.optimal_worker_count)
        quality_penalty = 0.05 * (worker_delta**2)
        quality = max(
            0.1, query.base_quality - quality_penalty + np.random.normal(0, 0.02)
        )
        quality = min(1.0, quality)  # Cap at 1.0

        # Latency: roughly linear in worker_count (more workers = slower coordination)
        latency = int(500 + 200 * allocated_workers + np.random.normal(0, 50))

        # Cost: linear in worker_count
        cost = 0.01 * allocated_workers + np.random.normal(0, 0.002)

        return SimulatedOutcome(
            latency_ms=max(100, latency), cost=max(0.001, cost), quality_score=quality
        )

    def run_static_baseline(self, corpus: list[SyntheticQuery]) -> dict[str, Any]:
        """Run static (non-adaptive) allocation on corpus."""
        allocations = []
        worker_dist: dict[int, int] = {}

        for query in corpus:
            # Static: use analytic baseline (no adaptation)
            allocated = query.analytic_worker_count
            allocations.append(allocated)

            worker_dist[allocated] = worker_dist.get(allocated, 0) + 1

        # Compute MAE from optimal
        mae = np.mean(
            [
                abs(alloc - q.optimal_worker_count)
                for alloc, q in zip(allocations, corpus, strict=True)
            ]
        )

        return {"allocations": allocations, "mae": mae, "worker_dist": worker_dist}

    async def run_adaptive(self, corpus: list[SyntheticQuery]) -> dict[str, Any]:
        """Run adaptive allocation on corpus (STUB: not yet implemented)."""
        # STUB: For now, adaptive == static (no real bandit learning)
        # Full implementation requires wiring AdaptiveAllocationEngine with
        # reward feedback after each query
        logger.warning(
            "Adaptive allocation not yet implemented; returning static baseline"
        )
        return self.run_static_baseline(corpus)

    async def evaluate(self, num_queries: int = 200) -> EvalMetrics:
        """Run full evaluation: static vs adaptive."""
        logger.info(f"Generating corpus of {num_queries} queries (seed={self.seed})")
        corpus = self.generate_corpus(num_queries)

        logger.info("Running static baseline...")
        static_results = self.run_static_baseline(corpus)

        logger.info("Running adaptive allocation...")
        adaptive_results = await self.run_adaptive(corpus)

        # Compute metrics
        adaptation_rate = 0.0  # STUB: would compare allocations
        bound_hit_rate = 0.0  # STUB: would track ±2 cap hits

        metrics = EvalMetrics(
            static_mae=static_results["mae"],
            adaptive_mae=adaptive_results["mae"],
            static_worker_dist=static_results["worker_dist"],
            adaptive_worker_dist=adaptive_results["worker_dist"],
            adaptation_rate=adaptation_rate,
            bound_hit_rate=bound_hit_rate,
            cumulative_regret=0.0,  # STUB
            num_queries=num_queries,
        )

        logger.info("Evaluation complete", metrics=asdict(metrics))
        return metrics

    def generate_report(self, metrics: EvalMetrics) -> str:
        """Generate markdown report."""
        report = f"""# Adaptive Routing Offline Evaluation Report

Generated: {datetime.now().isoformat()}

## Summary

This is a **synthetic evaluation** with the following limitations:
- Simulated quality function (real quality requires live LLM execution + human eval)
- Deterministic seeded corpus (real diversity requires production traffic)
- Adaptive allocation **not yet fully implemented** (this report shows static baseline only)

## Metrics

- **Queries evaluated**: {metrics.num_queries}
- **Static MAE from optimal**: {metrics.static_mae:.3f}
- **Adaptive MAE from optimal**: {metrics.adaptive_mae:.3f}
- **Adaptation rate**: {metrics.adaptation_rate:.1%} (% of queries where adaptive changed allocation)
- **Bound hit rate**: {metrics.bound_hit_rate:.1%} (% of queries where ±2 cap was hit)
- **Cumulative regret**: {metrics.cumulative_regret:.3f}

## Worker Count Distribution

### Static Baseline
{self._format_distribution(metrics.static_worker_dist)}

### Adaptive Allocation
{self._format_distribution(metrics.adaptive_worker_dist)}

## Conclusion

**Status**: Adaptive routing infrastructure is **wired but not yet learning** in this eval.
The evaluation framework is in place; full bandit integration deferred to follow-up work.

**Next steps**:
1. Wire `AdaptiveAllocationEngine` to receive reward feedback after each query
2. Implement real bandit arm selection in `_get_adaptive_allocation_adjustment()`
3. Re-run this eval to measure learning (expect adaptive_mae < static_mae after warmup)
4. Promote to production A/B test with real traffic

"""
        return report

    def _format_distribution(self, dist: dict[int, int]) -> str:
        """Format worker distribution as markdown table."""
        if not dist:
            return "_Empty distribution_"

        lines = ["| Workers | Count |", "|---------|-------|"]
        for workers in sorted(dist.keys()):
            lines.append(f"| {workers} | {dist[workers]} |")
        return "\n".join(lines)


async def main() -> None:
    """CLI entry point for offline eval."""
    evaluator = AdaptiveRoutingEvaluator(seed=42)
    metrics = await evaluator.evaluate(num_queries=200)

    # Generate markdown report
    report = evaluator.generate_report(metrics)
    print(report)

    # Save JSON summary
    output_path = "/tmp/adaptive_routing_eval_results.json"
    with open(output_path, "w") as f:
        json.dump(asdict(metrics), f, indent=2)

    print(f"\nJSON summary saved to {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
