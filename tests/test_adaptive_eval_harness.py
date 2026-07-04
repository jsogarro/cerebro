"""Tests for the adaptive-routing offline evaluation harness.

Guards the eval infrastructure itself, after a corpus-generation bug
(hardcoded 200-query corpus silently ignoring ``num_queries``) invalidated
an entire round of horizon-scaling conclusions.
"""

import numpy as np
import pytest

from src.ai_brain.experimentation.eval.adaptive_routing_eval import (
    AdaptiveRoutingEvaluator,
)
from src.ai_brain.experimentation.statistical.enhanced_statistical_engine import (
    BanditAlgorithm,
    MultiBanditOptimizer,
)


class TestCorpusGeneration:
    """The corpus must honor the requested size and bias injection."""

    @pytest.mark.parametrize("n", [200, 500, 1000, 2000])
    def test_corpus_length_honors_num_queries(self, n: int) -> None:
        """Regression: counts were hardcoded to 200, ignoring num_queries."""
        ev = AdaptiveRoutingEvaluator(seed=42)
        assert len(ev.generate_corpus(n)) == n

    def test_corpus_keeps_mode_proportions(self) -> None:
        ev = AdaptiveRoutingEvaluator(seed=42)
        corpus = ev.generate_corpus(1000)
        modes = [q.collaboration_mode.value for q in corpus]
        assert 200 <= modes.count("direct") <= 300  # ~25%
        assert 350 <= modes.count("parallel") <= 450  # ~40%
        assert 300 <= modes.count("hierarchical") <= 400  # ~35%

    def test_biased_corpus_shifts_best_arm_off_zero(self) -> None:
        """Bias injection must make a nonzero delta optimal where injected."""
        ev = AdaptiveRoutingEvaluator(seed=42)
        corpus = ev.generate_corpus(600, bias_mode="biased")
        hier = [q for q in corpus if q.collaboration_mode.value == "hierarchical"]
        assert hier, "biased corpus must include hierarchical queries"
        means = {}
        for delta in (-2, -1, 0, 1, 2):
            qualities = [
                ev.simulate_outcome(
                    q, max(1, q.analytic_worker_count + delta),
                ).quality_score
                for q in hier
            ]
            means[delta] = sum(qualities) / len(qualities)
        assert max(means, key=lambda d: means[d]) != 0

    def test_calibrated_corpus_keeps_zero_optimal_for_hierarchical(self) -> None:
        ev = AdaptiveRoutingEvaluator(seed=42)
        corpus = ev.generate_corpus(600, bias_mode="calibrated")
        hier = [q for q in corpus if q.collaboration_mode.value == "hierarchical"]
        means = {}
        for delta in (-2, -1, 0, 1, 2):
            qualities = [
                ev.simulate_outcome(
                    q, max(1, q.analytic_worker_count + delta),
                ).quality_score
                for q in hier
            ]
            means[delta] = sum(qualities) / len(qualities)
        assert max(means, key=lambda d: means[d]) == 0


class TestPosteriorTemperatureLever:
    """The convergence lever must sharpen Thompson draws once warm."""

    @pytest.mark.asyncio
    async def test_sharpening_activates_after_threshold(self) -> None:
        np.random.seed(0)
        bandit = MultiBanditOptimizer(
            {
                "posterior_temp_enabled": True,
                "posterior_temp_threshold": 10,
                "posterior_temp_factor": 3.0,
            },
        )
        await bandit.initialize_bandit(
            num_arms=2, algorithm=BanditAlgorithm.THOMPSON_SAMPLING,
        )
        # Feed a clearly better arm 0
        for _ in range(20):
            await bandit.update_bandit(0, reward=0.9)
            await bandit.update_bandit(1, reward=0.2)
        # Post-threshold, sharpened posteriors should pick arm 0 near-always
        picks = []
        for _ in range(200):
            result = await bandit.select_arm()
            picks.append(result.selected_arm)
        assert picks.count(0) >= 190

    @pytest.mark.asyncio
    async def test_sharpening_exploits_at_least_as_much(self) -> None:
        """Comparative: with identical evidence and RNG, the sharpened bandit
        picks the best arm at least as often as the unsharpened one.
        """

        async def run(enabled: bool) -> int:
            np.random.seed(0)
            bandit = MultiBanditOptimizer(
                {
                    "posterior_temp_enabled": enabled,
                    "posterior_temp_threshold": 10,
                    "posterior_temp_factor": 3.0,
                },
            )
            await bandit.initialize_bandit(
                num_arms=2, algorithm=BanditAlgorithm.THOMPSON_SAMPLING,
            )
            for _ in range(20):
                await bandit.update_bandit(0, reward=0.9)
                await bandit.update_bandit(1, reward=0.2)
            picks = [(await bandit.select_arm()).selected_arm for _ in range(200)]
            return picks.count(0)

        assert await run(True) >= await run(False)

    @pytest.mark.asyncio
    async def test_continuous_update_separates_posterior_means(self) -> None:
        """Regression for the binary reward>0.5 threshold: rewards in a narrow
        high band must still separate arm posteriors.
        """
        bandit = MultiBanditOptimizer({})
        await bandit.initialize_bandit(
            num_arms=2, algorithm=BanditAlgorithm.THOMPSON_SAMPLING,
        )
        for _ in range(50):
            await bandit.update_bandit(0, reward=0.80)
            await bandit.update_bandit(1, reward=0.65)
        mean0 = bandit.alpha_params[0] / (
            bandit.alpha_params[0] + bandit.beta_params[0]
        )
        mean1 = bandit.alpha_params[1] / (
            bandit.alpha_params[1] + bandit.beta_params[1]
        )
        assert mean0 - mean1 > 0.10
