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
from types import SimpleNamespace
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
    cumulative_regret: float  # Sum of (optimal-quality - achieved-quality) over queries
    num_queries: int


class AdaptiveRoutingEvaluator:
    """Offline evaluator for adaptive routing."""

    def __init__(self, seed: int = 42):
        """Initialize evaluator with deterministic seed."""
        self.seed = seed
        random.seed(seed)
        np.random.seed(seed)

    def generate_corpus(
        self, num_queries: int = 200, bias_mode: str = "calibrated"
    ) -> list[SyntheticQuery]:
        """Generate synthetic query corpus with realistic complexity distribution.

        Args:
            num_queries: Total queries to generate (proportionally distributed)
            bias_mode: "calibrated" (analytic ≈ optimal) or "biased" (analytic systematically wrong)

        In "biased" mode, the analytic baseline is systematically misaligned:
        - HIERARCHICAL: analytic = optimal + 2 (over-allocation by 2 workers)
        - PARALLEL: analytic = optimal - 1 (under-allocation by 1 worker)
        - DIRECT: analytic ≈ optimal (unchanged, as baseline is already clamped to 1)

        This tests whether adaptive routing can recover from systematic baseline errors.
        """
        corpus = []

        # Distribution: 25% SIMPLE, 40% MODERATE, 35% COMPLEX — scaled to
        # num_queries (counts were previously hardcoded to a 200-query corpus,
        # silently ignoring num_queries and invalidating horizon comparisons).
        n_direct = max(1, round(num_queries * 0.25))
        n_parallel = max(1, round(num_queries * 0.40))
        n_hier = max(1, num_queries - n_direct - n_parallel)
        distributions = [
            (ComplexityLevel.SIMPLE, CollaborationMode.DIRECT, n_direct, 1, 0.85),
            (ComplexityLevel.MODERATE, CollaborationMode.PARALLEL, n_parallel, 3, 0.80),
            (ComplexityLevel.COMPLEX, CollaborationMode.HIERARCHICAL, n_hier, 5, 0.75),
        ]

        query_id = 0
        for level, mode, count, optimal_workers, base_quality in distributions:
            for _ in range(count):
                if bias_mode == "biased":
                    # Inject systematic baseline bias per mode
                    if mode == CollaborationMode.HIERARCHICAL:
                        # Over-allocate: analytic = optimal + 2
                        analytic = optimal_workers + 2
                    elif mode == CollaborationMode.PARALLEL:
                        # Under-allocate: analytic = optimal - 1
                        analytic = max(1, optimal_workers - 1)
                    else:  # DIRECT
                        # Unchanged (already near optimal, clamped to 1)
                        analytic = max(1, optimal_workers + random.randint(-1, 1))
                else:  # calibrated (original)
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
        """Run adaptive allocation on corpus with REAL bandit learning."""
        from src.ai_brain.router.masr import MASRouter
        from src.ai_brain.router.routing_outcome import (
            ADAPTIVE_POLICY_VERSION,
            EvaluatorEligibilityPolicy,
            MetricAvailability,
            OutcomeSource,
            RoutingOutcome,
        )

        # Create MASR instance with adaptive routing enabled
        config = {
            "adaptive_routing_enabled": True,
            "adaptive_routing_min_history": 0,
            "adaptive_routing_min_samples_per_arm": 0,
            "adaptive_routing_performance_threshold": 0.0,
            "adaptive_routing_max_worker_adjust": 2,
            "adaptive_routing_rng": np.random.default_rng(self.seed),
            "max_parallel": 5,
            "max_agents": 10,
        }
        eligibility_policy = EvaluatorEligibilityPolicy(
            allowed_evaluators={"adaptive-routing-eval": frozenset({"1"})}
        )
        router = MASRouter(
            config=config,
            outcome_eligibility_policy=eligibility_policy,
        )

        allocations: list[int] = []
        worker_dist: dict[int, int] = {}
        adaptation_count = 0
        bound_hit_count = 0
        reward_failures = 0
        cumulative_regret = 0.0

        def _analysis_for(query: SyntheticQuery) -> SimpleNamespace:
            # Lightweight stand-in exposing the two fields the router's
            # baseline inference reads (domains, subtask_count).
            n = max(1, query.analytic_worker_count)
            return SimpleNamespace(
                domains=[f"domain_{i}" for i in range(max(1, n - 1))],
                subtask_count=n,
                reasoning_types=[],
            )

        for outcome_index, query in enumerate(corpus):
            # Get baseline (what static would use)
            baseline = query.analytic_worker_count
            analysis = _analysis_for(query)

            # Get adaptive recommendation through the REAL router hook
            recommended = await router._get_adaptive_allocation_adjustment(
                complexity_analysis=analysis,  # type: ignore[arg-type]
                collaboration_mode=query.collaboration_mode,
                episodic_prior=None,
            )

            if recommended is not None:
                allocation, attribution = router._allocate_agents_with_attribution(
                    complexity_analysis=analysis,  # type: ignore[arg-type]
                    collaboration_mode=query.collaboration_mode,
                    adaptive_recommendation=recommended,
                )
                allocated = allocation.worker_count
                if attribution.applied_arm != 0:
                    adaptation_count += 1
                if abs(attribution.applied_arm) == 2:
                    bound_hit_count += 1
            else:
                allocated = baseline
                attribution = None

            allocations.append(allocated)
            worker_dist[allocated] = worker_dist.get(allocated, 0) + 1

            # Simulate outcome and feed the reward through the REAL
            # record_routing_outcome path (delta -> variant -> bandit reward).
            outcome = self.simulate_outcome(query, allocated)
            typed_outcome = RoutingOutcome(
                outcome_id=f"synthetic-eval-{outcome_index}",
                routing_id=f"synthetic-route-{outcome_index}",
                policy_version=ADAPTIVE_POLICY_VERSION,
                source=OutcomeSource.EVALUATOR,
                collaboration_mode=query.collaboration_mode,
                proposed_arm=attribution.proposed_arm if attribution else 0,
                applied_arm=attribution.applied_arm if attribution else 0,
                final_worker_count=allocated,
                execution_status="completed",
                latency_ms=outcome.latency_ms,
                measured_cost=outcome.cost,
                cost_availability=MetricAvailability.MEASURED,
                quality_score=outcome.quality_score,
                quality_availability=MetricAvailability.MEASURED,
                evaluator_name="adaptive-routing-eval",
                evaluator_version="1",
            )
            try:
                application = await router.record_routing_outcome(typed_outcome)
                if not application.learning_updated:
                    reward_failures += 1
                    logger.warning(
                        "reward recording was not applied",
                        status=application.status.value,
                        reason=application.reason,
                    )
            except Exception as e:
                reward_failures += 1
                logger.warning(f"reward recording failed: {e}")

            # Bandit regret: quality at the known-optimal allocation minus achieved
            optimal_outcome = self.simulate_outcome(query, query.optimal_worker_count)
            cumulative_regret += max(
                0.0, optimal_outcome.quality_score - outcome.quality_score
            )

        # Validity guards: an eval whose learning loop never engaged is
        # meaningless — fail loudly instead of reporting a vacuous 0.0%.
        if reward_failures == len(corpus):
            raise RuntimeError(
                "adaptive eval invalid: every reward recording failed - "
                "the bandit never received feedback"
            )
        if adaptation_count == 0:
            raise RuntimeError(
                "adaptive eval invalid: adaptation rate is 0.0% after warm-up - "
                "the adaptive hook never engaged (check guards/wiring)"
            )

        # Compute MAE from optimal
        mae = np.mean(
            [
                abs(alloc - q.optimal_worker_count)
                for alloc, q in zip(allocations, corpus, strict=True)
            ]
        )

        adaptation_rate = adaptation_count / len(corpus) if corpus else 0.0
        bound_hit_rate = bound_hit_count / len(corpus) if corpus else 0.0

        return {
            "allocations": allocations,
            "mae": mae,
            "worker_dist": worker_dist,
            "adaptation_rate": adaptation_rate,
            "bound_hit_rate": bound_hit_rate,
            "cumulative_regret": cumulative_regret,
        }

    async def evaluate(self, num_queries: int = 200) -> EvalMetrics:
        """Run full evaluation: static vs adaptive."""
        logger.info(f"Generating corpus of {num_queries} queries (seed={self.seed})")
        corpus = self.generate_corpus(num_queries)

        logger.info("Running static baseline...")
        static_results = self.run_static_baseline(corpus)

        logger.info("Running adaptive allocation...")
        adaptive_results = await self.run_adaptive(corpus)

        # Extract metrics from adaptive results
        adaptation_rate = adaptive_results.get("adaptation_rate", 0.0)
        bound_hit_rate = adaptive_results.get("bound_hit_rate", 0.0)

        metrics = EvalMetrics(
            static_mae=static_results["mae"],
            adaptive_mae=adaptive_results["mae"],
            static_worker_dist=static_results["worker_dist"],
            adaptive_worker_dist=adaptive_results["worker_dist"],
            adaptation_rate=adaptation_rate,
            bound_hit_rate=bound_hit_rate,
            cumulative_regret=adaptive_results.get("cumulative_regret", 0.0),
            num_queries=num_queries,
        )

        logger.info("Evaluation complete", metrics=asdict(metrics))
        return metrics

    def generate_report(self, metrics: EvalMetrics) -> str:
        delta = metrics.adaptive_mae - metrics.static_mae
        if delta <= -0.01:
            verdict = (
                "Adaptive allocation OUTPERFORMS static on this synthetic corpus - "
                "candidate for live A/B validation."
            )
        elif delta < 0.01:
            verdict = (
                "Adaptive allocation matches static on this synthetic corpus - "
                "no evidence of harm, no evidence of benefit yet."
            )
        else:
            verdict = (
                "Adaptive allocation UNDERPERFORMS static on this synthetic corpus - "
                "DO NOT PROMOTE. Exploration cost exceeds learned gains at this "
                "horizon; revisit reward shaping or exploration schedule first."
            )
        """Generate markdown report."""
        report = f"""# Adaptive Routing Offline Evaluation Report

Generated: {datetime.now().isoformat()}

## Summary

This is a **synthetic evaluation** with the following limitations:
- Simulated quality function (real quality requires live LLM execution + human eval)
- Deterministic seeded corpus (real diversity requires production traffic)
- In-memory bandit state (resets per process; production needs persistence)

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

**Verdict**: {verdict}

**Observations**:
- Adaptation rate {metrics.adaptation_rate:.1%} confirms the bandit loop engaged
  (arm selection + reward feedback are live end-to-end)
- Bound hit rate {metrics.bound_hit_rate:.1%} shows the ±2 cap functioning
- MAE delta (adaptive - static): {metrics.adaptive_mae - metrics.static_mae:+.3f}
  (negative = adaptive better)

**Limitations**:
- Synthetic quality function cannot validate real LLM improvements
- In-memory state (no persistence across restarts)
- Corpus may not show statistical significance

**Next steps**: keep `ADAPTIVE_ROUTING_ENABLED=False`; do not promote until an
eval (or live A/B in the experimentation harness) shows adaptive matching or
beating static allocation.
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
