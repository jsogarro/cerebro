# Statistical Framework for A/B Testing

## Overview

This document details the statistical framework underlying Cerebro's experimentation subsystem. It describes the methods the framework is designed around — Bayesian inference, multi-armed bandits, and classical hypothesis testing — and maps each to the actual implementation.

The implementation lives in `src/ai_brain/experimentation/statistical/`:

- **`enhanced_statistical_engine.py`** — the primary engine. It defines `EnhancedStatisticalEngine` (the orchestrating façade) plus three analyzers: `FrequentistAnalyzer` (t-tests, proportion tests, power analysis), `BayesianAnalyzer` (Bayesian A/B testing), and `MultiBanditOptimizer` (epsilon-greedy, Thompson sampling, UCB, and contextual bandits under a single `BanditAlgorithm` enum).
- **`bayesian_experiment_design.py`** — `BayesianExperimentDesigner` for Bayesian-optimization-based experiment design.
- **`multi_variate_analysis.py`** — `MultiVariateAnalyzer` for multi-metric / Pareto analysis.

> **Class names in older drafts of this doc did not exist in the codebase.** The classes `BayesianABTest`, `ThompsonSampling`, `ContextualBandit`, `UCB`, `MultipleComparisonCorrection`, `PowerAnalysis`, `SequentialTesting`, and `EffectSizeCalculator` are not defined anywhere in `src/`. The corresponding functionality is provided by the classes listed above. The code blocks in this document are **illustrative** — they show the statistical technique, not the exact runtime API. Read the source files above for the real signatures.

### Dependency reality (important)

The framework is written defensively around optional dependencies. Only one of them is an actual project dependency:

- **scipy** — the only statistics library declared in `pyproject.toml` (`scipy>=1.13.0`). Core frequentist tests rely on it.
- **statsmodels** — *optional*. `enhanced_statistical_engine.py` guards the import (`STATSMODELS_AVAILABLE`); when it is missing, power analysis falls back to a hardcoded `power = 0.8` assumption and proportion tests fall back to a manual z-test computation.
- **PyMC / ArviZ** — *not installed* and not in `pyproject.toml`. `BayesianAnalyzer.__init__` raises `ImportError("PyMC required for Bayesian analysis - install with: pip install pymc")` when constructed without them (`enhanced_statistical_engine.py:503`). **Bayesian A/B testing is therefore non-functional out of the box**; `EnhancedStatisticalEngine` sets `self.bayesian = None` and routes Bayesian requests to the frequentist analyzer instead.
- **scikit-learn** — *not a dependency at all*. Any snippet below that imports `sklearn` (e.g. the contextual-bandit example) cannot run as written; the real contextual-bandit path is inside `MultiBanditOptimizer`.
- **mabwiser** — *optional* (`MAB_AVAILABLE` guard); absent by default.

`EnhancedStatisticalEngine.get_engine_stats()` reports which of these capabilities are actually available at runtime.

## Core Statistical Principles

### 1. Statistical Rigor
- All experiments must achieve statistical significance (p < 0.05)
- Multiple comparison corrections applied (Bonferroni, FDR)
- Effect size calculations for practical significance
- Power analysis for sample size determination

### 2. Bayesian vs Frequentist Approaches
We use a hybrid approach leveraging strengths of both paradigms:
- **Frequentist**: Classical hypothesis testing, confidence intervals
- **Bayesian**: Early stopping, posterior distributions, uncertainty quantification

## Bayesian A/B Testing with PyMC

> **Real class: `BayesianAnalyzer` (`enhanced_statistical_engine.py:489`), method `analyze_bayesian_ab_test`.** It requires PyMC + ArviZ, neither of which is installed by default, so this path is **non-functional out of the box** and callers are transparently downgraded to `FrequentistAnalyzer`. The block below is **illustrative** — it shows the PyMC model the analyzer is designed around, not the shipped API.

### Illustrative model architecture

```python
# ILLUSTRATIVE — requires `pip install pymc arviz` (not project dependencies).
# The shipped implementation is BayesianAnalyzer in enhanced_statistical_engine.py.
import pymc as pm
import arviz as az
from typing import Tuple, Dict, Any

class BayesianABTest:  # illustrative; real class is BayesianAnalyzer
    """
    Bayesian A/B testing with early stopping using PyMC.
    """
    
    def __init__(self, prior_alpha: float = 1.0, prior_beta: float = 1.0):
        self.prior_alpha = prior_alpha
        self.prior_beta = prior_beta
        
    def analyze(self, 
                control_successes: int, 
                control_failures: int,
                treatment_successes: int, 
                treatment_failures: int) -> Dict[str, Any]:
        """
        Perform Bayesian analysis of A/B test results.
        """
        with pm.Model() as model:
            # Beta priors for conversion rates
            p_control = pm.Beta('p_control', 
                               alpha=self.prior_alpha, 
                               beta=self.prior_beta)
            p_treatment = pm.Beta('p_treatment', 
                                alpha=self.prior_alpha, 
                                beta=self.prior_beta)
            
            # Binomial likelihoods
            obs_control = pm.Binomial('obs_control', 
                                     n=control_successes + control_failures,
                                     p=p_control, 
                                     observed=control_successes)
            obs_treatment = pm.Binomial('obs_treatment',
                                       n=treatment_successes + treatment_failures,
                                       p=p_treatment,
                                       observed=treatment_successes)
            
            # Derived quantities
            lift = pm.Deterministic('lift', 
                                   (p_treatment - p_control) / p_control)
            
            # Sample from posterior
            trace = pm.sample(draws=5000, 
                            tune=1000, 
                            return_inferencedata=True)
            
        return self._analyze_trace(trace)
    
    def _analyze_trace(self, trace) -> Dict[str, Any]:
        """Extract key metrics from posterior samples."""
        return {
            'probability_treatment_better': (trace.posterior['lift'] > 0).mean().item(),
            'expected_lift': trace.posterior['lift'].mean().item(),
            'lift_hdi_95': az.hdi(trace, hdi_prob=0.95)['lift'].values,
            'rope_probability': self._calculate_rope(trace),
            'should_stop_early': self._check_early_stopping(trace)
        }
```

### Early Stopping with ROPE (Region of Practical Equivalence)

The ROPE-based early-stopping logic in the shipped engine lives in `EnhancedStatisticalEngine.early_stopping_analysis` (`enhanced_statistical_engine.py:967`). The snippet below is **illustrative** of the ROPE calculation.

```python
# ILLUSTRATIVE
def _calculate_rope(self, trace, rope_width: float = 0.01) -> float:
    """
    Calculate probability that effect is within ROPE.
    If high, we can stop early as treatments are equivalent.
    """
    lift = trace.posterior['lift'].values.flatten()
    return np.mean(np.abs(lift) < rope_width)

def _check_early_stopping(self, trace, threshold: float = 0.95) -> bool:
    """
    Determine if we have enough evidence to stop the experiment.
    """
    prob_better = (trace.posterior['lift'] > 0).mean().item()
    rope_prob = self._calculate_rope(trace)
    
    # Stop if very confident in direction OR equivalence
    return (prob_better > threshold or 
            prob_better < (1 - threshold) or 
            rope_prob > threshold)
```

## Multi-Armed Bandit Algorithms

> **Real class: `MultiBanditOptimizer` (`enhanced_statistical_engine.py:631`).** A single optimizer implements all four strategies selected by the `BanditAlgorithm` enum — `EPSILON_GREEDY`, `THOMPSON_SAMPLING`, `UPPER_CONFIDENCE_BOUND` (UCB), and `CONTEXTUAL_BANDIT` — via `initialize_bandit`, `select_arm`, and `update_bandit`. There are no separate `ThompsonSampling`, `UCB`, or `ContextualBandit` classes. The blocks below are **illustrative** of each algorithm.

### Thompson Sampling (illustrative)

```python
# ILLUSTRATIVE — real path: MultiBanditOptimizer with BanditAlgorithm.THOMPSON_SAMPLING
import numpy as np
from scipy import stats

class ThompsonSampling:  # illustrative
    """
    Thompson Sampling for multi-armed bandit optimization.
    Particularly effective for MASR routing decisions.
    """
    
    def __init__(self, n_arms: int, prior_alpha: float = 1.0, prior_beta: float = 1.0):
        self.n_arms = n_arms
        self.alpha = np.ones(n_arms) * prior_alpha  # Successes + prior
        self.beta = np.ones(n_arms) * prior_beta    # Failures + prior
        
    def select_arm(self) -> int:
        """
        Select an arm based on Thompson Sampling.
        Sample from posterior Beta distributions.
        """
        samples = [np.random.beta(self.alpha[i], self.beta[i]) 
                  for i in range(self.n_arms)]
        return np.argmax(samples)
    
    def update(self, arm: int, reward: float):
        """
        Update posterior based on observed reward.
        Reward should be 0 or 1 for Bernoulli bandits.
        """
        if reward > 0:
            self.alpha[arm] += reward
        else:
            self.beta[arm] += (1 - reward)
    
    def get_arm_probabilities(self) -> np.ndarray:
        """
        Get selection probabilities for each arm.
        Useful for debugging and monitoring.
        """
        samples = np.array([np.random.beta(self.alpha[i], self.beta[i], 10000) 
                           for i in range(self.n_arms)])
        win_counts = np.sum(samples == samples.max(axis=0), axis=1)
        return win_counts / win_counts.sum()
```

### Contextual Bandits for Personalization

> **scikit-learn is NOT a project dependency**, so the `sklearn`-based example below cannot run as written — it is purely illustrative. The shipped contextual-bandit path is the `CONTEXTUAL_BANDIT` branch of `MultiBanditOptimizer`, which does not depend on scikit-learn.

```python
# ILLUSTRATIVE — sklearn is not installed; real path: MultiBanditOptimizer (CONTEXTUAL_BANDIT)
from sklearn.linear_model import SGDClassifier
import numpy as np

class ContextualBandit:  # illustrative; sklearn unavailable in this project
    """
    Contextual bandit using online learning.
    Adapts routing based on query features.
    """
    
    def __init__(self, n_actions: int, n_features: int, epsilon: float = 0.1):
        self.n_actions = n_actions
        self.epsilon = epsilon
        # One classifier per action
        self.models = [SGDClassifier(loss='log_loss', learning_rate='constant')
                      for _ in range(n_actions)]
        self.is_fitted = [False] * n_actions
        
    def select_action(self, context: np.ndarray) -> int:
        """
        Select action using epsilon-greedy with context.
        """
        if np.random.random() < self.epsilon:
            # Exploration
            return np.random.randint(self.n_actions)
        else:
            # Exploitation
            predictions = []
            for i, model in enumerate(self.models):
                if self.is_fitted[i]:
                    # Get probability of positive class
                    prob = model.predict_proba(context.reshape(1, -1))[0, 1]
                    predictions.append(prob)
                else:
                    predictions.append(0.5)  # Neutral prior
            return np.argmax(predictions)
    
    def update(self, context: np.ndarray, action: int, reward: float):
        """
        Update the model for the selected action.
        """
        # Convert to binary classification problem
        # Reward > 0.5 is "success", <= 0.5 is "failure"
        label = 1 if reward > 0.5 else 0
        
        if not self.is_fitted[action]:
            # First update for this action
            self.models[action].partial_fit(
                context.reshape(1, -1), 
                [label], 
                classes=[0, 1]
            )
            self.is_fitted[action] = True
        else:
            self.models[action].partial_fit(
                context.reshape(1, -1), 
                [label]
            )
```

### Upper Confidence Bound (UCB) Algorithm (illustrative)

```python
# ILLUSTRATIVE — real path: MultiBanditOptimizer with BanditAlgorithm.UPPER_CONFIDENCE_BOUND
class UCB:  # illustrative
    """
    Upper Confidence Bound algorithm for exploration/exploitation.
    """
    
    def __init__(self, n_arms: int, c: float = 2.0):
        self.n_arms = n_arms
        self.c = c  # Exploration parameter
        self.counts = np.zeros(n_arms)
        self.values = np.zeros(n_arms)
        self.t = 0
        
    def select_arm(self) -> int:
        """
        Select arm based on UCB criterion.
        """
        self.t += 1
        
        # Try each arm once first
        if self.t <= self.n_arms:
            return self.t - 1
        
        # Calculate UCB for each arm
        ucb_values = self.values + self.c * np.sqrt(
            np.log(self.t) / self.counts
        )
        return np.argmax(ucb_values)
    
    def update(self, arm: int, reward: float):
        """
        Update estimates based on observed reward.
        """
        self.counts[arm] += 1
        n = self.counts[arm]
        value = self.values[arm]
        # Running average update
        self.values[arm] = ((n - 1) / n) * value + (1 / n) * reward
```

## Statistical Significance Testing

### Multiple Comparison Corrections

> **Real implementation: `EnhancedStatisticalEngine._bonferroni_correction` (`:1075`) and `._fdr_correction` (`:1080`)**, selected by the `multiple_comparison` argument to `comprehensive_analysis`. The engine ships Bonferroni and FDR (Benjamini–Hochberg); there is no `MultipleComparisonCorrection` class and no dedicated Holm method. Because **statsmodels is optional**, the shipped corrections are computed directly rather than always via `multipletests`. The block below is **illustrative** of the statsmodels-backed variants.

```python
# ILLUSTRATIVE — statsmodels is an OPTIONAL dependency (may be absent).
# Real methods: EnhancedStatisticalEngine._bonferroni_correction / ._fdr_correction
from statsmodels.stats.multitest import multipletests
import numpy as np

class MultipleComparisonCorrection:  # illustrative
    """
    Handle multiple comparison problem in experiments.
    """
    
    @staticmethod
    def bonferroni_correction(p_values: np.ndarray, alpha: float = 0.05) -> Tuple[np.ndarray, np.ndarray]:
        """
        Bonferroni correction for multiple comparisons.
        Most conservative approach.
        """
        rejected, p_adjusted, _, _ = multipletests(
            p_values, 
            alpha=alpha, 
            method='bonferroni'
        )
        return rejected, p_adjusted
    
    @staticmethod
    def fdr_correction(p_values: np.ndarray, alpha: float = 0.05) -> Tuple[np.ndarray, np.ndarray]:
        """
        False Discovery Rate (Benjamini-Hochberg) correction.
        Less conservative, controls expected proportion of false discoveries.
        """
        rejected, p_adjusted, _, _ = multipletests(
            p_values, 
            alpha=alpha, 
            method='fdr_bh'
        )
        return rejected, p_adjusted
    
    @staticmethod
    def holm_correction(p_values: np.ndarray, alpha: float = 0.05) -> Tuple[np.ndarray, np.ndarray]:
        """
        Holm-Bonferroni correction.
        Less conservative than Bonferroni but maintains family-wise error rate.
        """
        rejected, p_adjusted, _, _ = multipletests(
            p_values, 
            alpha=alpha, 
            method='holm'
        )
        return rejected, p_adjusted
```

## Power Analysis and Sample Size Determination

> **Real implementation: `FrequentistAnalyzer.power_analysis` (`enhanced_statistical_engine.py:357`).** It uses statsmodels' power solvers **when available**; when statsmodels is absent it falls back to a hardcoded `power = 0.8` assumption and an approximate sample-size calculation (`STATSMODELS_AVAILABLE` guards at `:245`, `:257`, `:383`). There is no standalone `PowerAnalysis` class. The block below is **illustrative** of the statsmodels-backed computation.

```python
# ILLUSTRATIVE — statsmodels optional; real path: FrequentistAnalyzer.power_analysis
# (falls back to power=0.8 when statsmodels is unavailable)
from statsmodels.stats.power import TTestPower, NormalIndPower
import numpy as np

class PowerAnalysis:  # illustrative
    """
    Determine required sample sizes for experiments.
    """
    
    @staticmethod
    def calculate_sample_size_ttest(effect_size: float, 
                                   alpha: float = 0.05, 
                                   power: float = 0.8) -> int:
        """
        Calculate required sample size for t-test.
        """
        analysis = TTestPower()
        sample_size = analysis.solve_power(
            effect_size=effect_size,
            alpha=alpha,
            power=power,
            alternative='two-sided'
        )
        return int(np.ceil(sample_size))
    
    @staticmethod
    def calculate_sample_size_proportion(baseline_rate: float,
                                        minimum_detectable_effect: float,
                                        alpha: float = 0.05,
                                        power: float = 0.8) -> int:
        """
        Calculate sample size for proportion tests (e.g., conversion rates).
        """
        analysis = NormalIndPower()
        
        # Calculate effect size (Cohen's h)
        p1 = baseline_rate
        p2 = baseline_rate * (1 + minimum_detectable_effect)
        effect_size = 2 * (np.arcsin(np.sqrt(p2)) - np.arcsin(np.sqrt(p1)))
        
        sample_size = analysis.solve_power(
            effect_size=effect_size,
            alpha=alpha,
            power=power,
            alternative='two-sided'
        )
        return int(np.ceil(sample_size))
    
    @staticmethod
    def calculate_experiment_duration(daily_traffic: int,
                                     required_sample_size: int,
                                     n_variants: int = 2) -> int:
        """
        Estimate experiment duration based on traffic.
        """
        total_sample_needed = required_sample_size * n_variants
        days_needed = np.ceil(total_sample_needed / daily_traffic)
        return int(days_needed)
```

## Sequential Testing with Alpha Spending

> **Illustrative.** There is no `SequentialTesting` class in `src/`. The engine exposes early-stopping via `EnhancedStatisticalEngine.early_stopping_analysis` (`:967`) and a `SEQUENTIAL_PROBABILITY_RATIO` value in the `StatisticalMethod` enum; the alpha-spending functions below document the intended approach rather than shipped code.

```python
# ILLUSTRATIVE — no SequentialTesting class exists; see early_stopping_analysis.
class SequentialTesting:  # illustrative
    """
    Sequential testing with alpha spending functions.
    Allows for continuous monitoring without inflating Type I error.
    """
    
    def __init__(self, total_alpha: float = 0.05, n_looks: int = 10):
        self.total_alpha = total_alpha
        self.n_looks = n_looks
        self.current_look = 0
        self.alpha_spent = 0
        
    def obrien_fleming_bounds(self) -> float:
        """
        O'Brien-Fleming spending function.
        Conservative early, aggressive late.
        """
        self.current_look += 1
        t = self.current_look / self.n_looks
        
        # O'Brien-Fleming spending function
        if t <= 0:
            return 0
        elif t >= 1:
            return self.total_alpha
        else:
            # Approximate spending function
            z_alpha = stats.norm.ppf(1 - self.total_alpha / 2)
            z_t = z_alpha / np.sqrt(t)
            alpha_t = 2 * (1 - stats.norm.cdf(z_t))
            
            alpha_to_spend = alpha_t - self.alpha_spent
            self.alpha_spent = alpha_t
            
            return alpha_to_spend
    
    def pocock_bounds(self) -> float:
        """
        Pocock spending function.
        Equal alpha spending at each look.
        """
        self.current_look += 1
        alpha_per_look = self.total_alpha / self.n_looks
        return alpha_per_look
```

## Effect Size Calculations

> **Illustrative.** There is no `EffectSizeCalculator` class. In the shipped engine, effect sizes (e.g. Cohen's d / Cohen's h) are computed inline within `FrequentistAnalyzer`'s metric analyzers and reported on `StatisticalTestResult`. The standalone helpers below are reference formulas.

```python
# ILLUSTRATIVE — no EffectSizeCalculator class; effect sizes are computed inside FrequentistAnalyzer.
import numpy as np
from scipy import stats

class EffectSizeCalculator:  # illustrative
    """
    Calculate various effect size metrics.
    """
    
    @staticmethod
    def cohens_d(group1: np.ndarray, group2: np.ndarray) -> float:
        """
        Cohen's d for continuous outcomes.
        """
        n1, n2 = len(group1), len(group2)
        var1, var2 = np.var(group1, ddof=1), np.var(group2, ddof=1)
        
        # Pooled standard deviation
        pooled_std = np.sqrt(((n1 - 1) * var1 + (n2 - 1) * var2) / (n1 + n2 - 2))
        
        # Cohen's d
        d = (np.mean(group1) - np.mean(group2)) / pooled_std
        return d
    
    @staticmethod
    def cohens_h(p1: float, p2: float) -> float:
        """
        Cohen's h for proportions.
        """
        return 2 * (np.arcsin(np.sqrt(p2)) - np.arcsin(np.sqrt(p1)))
    
    @staticmethod
    def risk_ratio(p_treatment: float, p_control: float) -> float:
        """
        Risk ratio (relative risk).
        """
        return p_treatment / p_control if p_control > 0 else np.inf
    
    @staticmethod
    def odds_ratio(p_treatment: float, p_control: float) -> float:
        """
        Odds ratio for binary outcomes.
        """
        odds_treatment = p_treatment / (1 - p_treatment) if p_treatment < 1 else np.inf
        odds_control = p_control / (1 - p_control) if p_control < 1 else np.inf
        return odds_treatment / odds_control if odds_control > 0 else np.inf
    
    @staticmethod
    def number_needed_to_treat(p_treatment: float, p_control: float) -> float:
        """
        NNT: How many users need treatment to see one additional success.
        """
        absolute_risk_reduction = p_treatment - p_control
        return 1 / absolute_risk_reduction if absolute_risk_reduction > 0 else np.inf
```

## Integration with Cerebro Systems

### MASR Router Integration

> **Real class: `MASRExperimentalRouter(MASRouter)` in `integration/masr_experiment_integration.py:89`.** It subclasses the in-process `MASRouter` (MASR = Multi-Agent System Router) to add A/B-testing over routing strategies, driven by an `AdaptiveAllocationEngine` (`core/adaptive_allocation_engine.py`). There is no `MASRExperimentIntegration` class and it does not construct standalone `ThompsonSampling` / `ContextualBandit` objects.

Key facts about the real integration:

- **Variants** are `control`, `cost_efficient`, `quality_focused`, `adaptive` — mapped to `RoutingStrategy` values (`masr_experiment_integration.py:118-121`).
- **Default allocation strategy is `epsilon_greedy`**, not Thompson sampling (`MASRExperimentConfig.allocation_strategy = "epsilon_greedy"`, `:69`). Allocation runs through `AdaptiveAllocationEngine`.
- **Adaptive Thompson-sampling routing on the production MASR path is flag-gated OFF**: `ADAPTIVE_ROUTING_ENABLED = False` (`src/core/config.py:236`). The bandit-driven allocation in this experimental router is not active on the default query path.

The block below is **illustrative** of the strategy-selection concept; see the source for the real signatures.

```python
# ILLUSTRATIVE — real class is MASRExperimentalRouter(MASRouter) using AdaptiveAllocationEngine
# (default allocation_strategy="epsilon_greedy"; adaptive/Thompson routing is flag-gated OFF).
class MASRExperimentIntegration:  # illustrative
    """
    Statistical framework integration with MASR routing.
    """

    def __init__(self):
        # Real router delegates allocation to AdaptiveAllocationEngine over
        # variants: control / cost_efficient / quality_focused / adaptive.
        self.optimizer = MultiBanditOptimizer()  # single optimizer, algorithm chosen via BanditAlgorithm

    def select_routing_strategy(self, query_features: np.ndarray = None) -> str:
        """
        Select routing strategy using bandit allocation.
        """
        strategies = ['control', 'cost_efficient', 'quality_focused', 'adaptive']
        # Real path: AdaptiveAllocationEngine.allocate(...) with epsilon-greedy by default.
        ...

    def update_routing_performance(self,
                                  strategy: str,
                                  performance_score: float,
                                  query_features: np.ndarray = None):
        """
        Update allocation based on observed performance.
        """
        # Real path: MultiBanditOptimizer.update_bandit(arm, reward)
        ...
```

## Best Practices

### 1. Experiment Design
- Always perform power analysis before starting
- Use stratification for heterogeneous populations
- Consider sequential testing for continuous monitoring
- Apply appropriate multiple comparison corrections

### 2. Statistical Validity
- Check assumptions (normality, independence, etc.)
- Use appropriate tests for data types
- Report effect sizes alongside p-values
- Consider practical significance, not just statistical

### 3. Bayesian Methods
- Choose informative priors when available
- Use ROPE for practical equivalence testing
- Implement early stopping to save resources
- Report full posterior distributions

### 4. Multi-Armed Bandits
- Start with Thompson Sampling for simplicity
- Use contextual bandits for personalization
- Monitor exploration/exploitation balance
- Implement safety constraints when needed

### 5. Deployment status (not yet a live surface)

The experimentation subsystem is a **library**, not a running service. The REST routers that would expose it — `src/api/routes/experiments.py` and `src/api/routes/experiment_agent_api.py` — are **never mounted** (`main.py` does not `include_router` them), so there are **no live HTTP experiment endpoints** and no `/ws/experiments` WebSocket. The statistical engine and `MASRExperimentalRouter` are importable and unit-testable in-process, but the following operational guidance is **aspirational** until those routers are wired up:

- gradual rollout with statistical monitoring
- automatic rollback on degradation
- decision logging for offline analysis
- experiment-history retention for learning

## Conclusion

This statistical framework provides mathematically sound building blocks — frequentist hypothesis testing (fully functional on scipy), multi-armed bandit optimization, and a Bayesian analysis path (dependent on PyMC, which is not installed by default) — for evaluating routing and agent-configuration variants.

A note on scope: these are analysis primitives, not a closed-loop optimizer. Cerebro does **not** currently self-improve from them. The `src/improvement` subsystem is a stub — `RewardModel.predict_quality` returns a constant `0.5` (`src/improvement/__init__.py:53`), and `RLTrainer`, `PromptOptimizer`, and `MetaLearner` are no-ops. Adaptive/bandit routing is flag-gated OFF (`ADAPTIVE_ROUTING_ENABLED=False`), and the experiment REST surface is unmounted. The framework is the statistical foundation an offline or future closed-loop optimization process could build on; today it is used for analysis, not automated decision-making.