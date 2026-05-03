"""
Cost Optimization Engine for MASR

Optimizes model selection based on cost-performance trade-offs, analyzing:
- Model costs per 1K tokens for different providers
- Expected token usage based on query complexity
- Performance requirements and quality thresholds
- Latency constraints and real-time requirements
- Fallback strategies and error handling costs
- Multi-model ensemble cost optimization

Now supports dynamic model configuration instead of hard-coded specifications.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

from structlog import get_logger

if TYPE_CHECKING:
    from ..config.model_config_manager import ModelConfigManager

logger = get_logger()


class OptimizationStrategy(Enum):
    """Cost optimization strategies."""

    COST_MINIMIZED = "cost_minimized"  # Cheapest option meeting requirements
    BALANCED = "balanced"  # Balance cost and performance
    PERFORMANCE_OPTIMIZED = (
        "performance_optimized"  # Best performance regardless of cost
    )
    LATENCY_OPTIMIZED = "latency_optimized"  # Fastest response time


class ModelTier(Enum):
    """Model performance tiers."""

    BASIC = "basic"  # Simple tasks, lowest cost
    STANDARD = "standard"  # General purpose, balanced
    PREMIUM = "premium"  # Complex reasoning, highest quality
    SPECIALIZED = "specialized"  # Domain-specific models


@dataclass
class ModelSpec:
    """Specification for a foundation model."""

    name: str
    provider: str
    tier: ModelTier
    cost_per_1k_tokens: float
    avg_latency_ms: int
    context_window: int
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    availability: float = 0.99  # SLA availability
    rate_limit: int = 1000  # requests per minute
    supports_streaming: bool = False
    quality_score: float = 0.8  # Overall quality rating 0-1

    def __hash__(self) -> int:
        return hash((self.name, self.provider))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ModelSpec):
            return NotImplemented
        return self.name == other.name and self.provider == other.provider


@dataclass
class CostEstimate:
    """Cost estimate for a specific model configuration."""

    model_name: str
    estimated_tokens: int
    cost_per_request: float
    total_monthly_cost: float  # Based on usage projections
    latency_estimate_ms: int
    quality_score: float
    confidence: float  # Confidence in estimate
    fallback_costs: float = 0.0  # Cost of fallback if primary fails


@dataclass
class OptimizationResult:
    """Result of cost optimization analysis."""

    primary_model: ModelSpec
    fallback_models: list[ModelSpec] = field(default_factory=list)
    estimated_cost: CostEstimate | None = None
    strategy_used: OptimizationStrategy = OptimizationStrategy.BALANCED
    reasoning: str = ""
    alternatives: list[tuple[ModelSpec, CostEstimate]] = field(default_factory=list)
    risk_factors: list[str] = field(default_factory=list)


class CostOptimizer:
    """
    Optimizes model selection for cost-effectiveness while meeting
    performance and quality requirements.

    Integrates with the complexity analyzer to make informed routing
    decisions based on query requirements and cost constraints.

    Now uses dynamic model configuration instead of hard-coded specifications.
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        model_config_manager: Optional["ModelConfigManager"] = None,
    ):
        """Initialize cost optimizer with dynamic model configuration."""
        self.config = config or {}
        self.model_config_manager = model_config_manager

        # Dynamic model specifications
        self.models: dict[str, ModelSpec] = {}
        self._models_loaded = False

        # Optimization parameters
        self.max_cost_per_request = self.config.get("max_cost_per_request", 0.10)
        self.quality_threshold = self.config.get("min_quality_score", 0.7)
        self.latency_threshold_ms = self.config.get("max_latency_ms", 5000)

        # Usage projections for monthly cost calculations
        self.projected_monthly_requests = self.config.get("monthly_requests", 100000)

        # Strategy preferences
        self.default_strategy = OptimizationStrategy(
            self.config.get("default_strategy", "balanced")
        )

    async def load_models(self) -> bool:
        """Load model specifications from configuration manager."""

        if not self.model_config_manager:
            logger.warning(
                "No model configuration manager available, using legacy models"
            )
            self.models = self._initialize_models_legacy()
            self._models_loaded = True
            return True

        try:
            # Load all enabled models
            enabled_models = await self.model_config_manager.get_enabled_models()

            # Convert to the format expected by cost optimizer
            self.models = {}
            for model_name, model_spec in enabled_models.items():
                # Convert ModelSpecification to ModelSpec for compatibility
                model_spec_obj = ModelSpec(
                    name=model_name,
                    provider=model_spec.provider,
                    tier=ModelTier(model_spec.tier.value),
                    cost_per_1k_tokens=model_spec.cost_per_1k_tokens,
                    avg_latency_ms=model_spec.avg_latency_ms,
                    context_window=model_spec.context_window,
                    strengths=model_spec.strengths,
                    weaknesses=model_spec.weaknesses,
                    availability=model_spec.availability,
                    rate_limit=model_spec.rate_limit,
                    supports_streaming=model_spec.supports_streaming,
                    quality_score=model_spec.quality_score,
                )

                self.models[model_name] = model_spec_obj

            self._models_loaded = True
            logger.info(f"Loaded {len(self.models)} models from configuration")
            return True

        except Exception as e:
            logger.error(f"Failed to load models from configuration: {e}")
            # Fallback to legacy models
            self.models = self._initialize_models_legacy()
            self._models_loaded = True
            return False

    async def reload_models(self) -> None:
        """Reload models from configuration (for hot-reload support)."""
        self._models_loaded = False
        await self.load_models()

    def _initialize_models_legacy(self) -> dict[str, ModelSpec]:
        """Initialize available model specifications."""
        models = {}

        # Basic tier models
        models["llama-3.3-70b"] = ModelSpec(
            name="llama-3.3-70b",
            provider="ollama",
            tier=ModelTier.STANDARD,
            cost_per_1k_tokens=0.0008,
            avg_latency_ms=30,
            context_window=128000,
            strengths=["general_purpose", "cost_effective", "fast"],
            weaknesses=["basic_reasoning"],
            availability=0.99,
            rate_limit=10000,
            supports_streaming=True,
            quality_score=0.75,
        )

        models["deepseek-v3"] = ModelSpec(
            name="deepseek-v3",
            provider="deepseek",
            tier=ModelTier.PREMIUM,
            cost_per_1k_tokens=0.002,
            avg_latency_ms=50,
            context_window=200000,
            strengths=["reasoning", "math", "analysis", "code"],
            weaknesses=["creative_writing"],
            availability=0.95,
            rate_limit=1000,
            supports_streaming=True,
            quality_score=0.92,
        )

        models["gemini-pro"] = ModelSpec(
            name="gemini-pro",
            provider="google",
            tier=ModelTier.STANDARD,
            cost_per_1k_tokens=0.001,
            avg_latency_ms=100,
            context_window=100000,
            strengths=["multimodal", "general_purpose", "reliable"],
            weaknesses=["math", "code"],
            availability=0.99,
            rate_limit=5000,
            supports_streaming=False,
            quality_score=0.85,
        )

        models["claude-3-haiku"] = ModelSpec(
            name="claude-3-haiku",
            provider="anthropic",
            tier=ModelTier.BASIC,
            cost_per_1k_tokens=0.0005,
            avg_latency_ms=20,
            context_window=200000,
            strengths=["speed", "cost", "concise"],
            weaknesses=["complex_reasoning"],
            availability=0.99,
            rate_limit=15000,
            supports_streaming=True,
            quality_score=0.70,
        )

        models["qwen3-72b"] = ModelSpec(
            name="qwen3-72b",
            provider="alibaba",
            tier=ModelTier.SPECIALIZED,
            cost_per_1k_tokens=0.0015,
            avg_latency_ms=40,
            context_window=150000,
            strengths=["multilingual", "translation", "international"],
            weaknesses=["english_reasoning"],
            availability=0.97,
            rate_limit=2000,
            supports_streaming=True,
            quality_score=0.80,
        )

        # Load custom models from config if provided
        custom_models = self.config.get("custom_models", {})
        for name, spec in custom_models.items():
            models[name] = ModelSpec(**spec)

        return models

    async def ensure_models_loaded(self) -> None:
        """Ensure model specifications are loaded."""
        if not self._models_loaded:
            await self.load_models()

    async def optimize(
        self,
        complexity_analysis: Any,  # ComplexityAnalysis from query analyzer
        strategy: OptimizationStrategy | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> OptimizationResult:
        """
        Optimize model selection based on complexity analysis and constraints.

        Args:
            complexity_analysis: Result from QueryComplexityAnalyzer
            strategy: Optimization strategy to use
            constraints: Additional constraints (budget, latency, etc.)

        Returns:
            OptimizationResult with recommended model configuration
        """
        strategy = strategy or self.default_strategy
        constraints = constraints or {}

        logger.info(f"Optimizing model selection with {strategy.value} strategy")

        # Ensure model specifications are loaded
        await self.ensure_models_loaded()

        # Filter models based on basic requirements
        candidate_models = await self._filter_candidates(
            complexity_analysis, constraints
        )

        if not candidate_models:
            raise ValueError("No models meet the specified requirements")

        # Calculate costs for each candidate
        cost_estimates = await self._calculate_costs(
            candidate_models, complexity_analysis
        )

        # Select optimal model based on strategy
        primary_model, primary_estimate = await self._select_optimal_model(
            cost_estimates, strategy, constraints
        )

        # Select fallback models
        fallback_models = await self._select_fallback_models(
            candidate_models, primary_model, cost_estimates
        )

        # Generate alternatives for comparison
        alternatives = [
            (model, est)
            for model, est in cost_estimates.items()
            if model != primary_model
        ][:3]

        # Assess risk factors
        risk_factors = self._assess_risk_factors(
            primary_model, complexity_analysis, constraints
        )

        # Generate reasoning
        reasoning = self._generate_reasoning(
            primary_model, primary_estimate, strategy, alternatives
        )

        result = OptimizationResult(
            primary_model=primary_model,
            fallback_models=fallback_models,
            estimated_cost=primary_estimate,
            strategy_used=strategy,
            reasoning=reasoning,
            alternatives=alternatives,
            risk_factors=risk_factors,
        )

        logger.info(
            f"Selected {primary_model.name} with estimated cost "
            f"${primary_estimate.cost_per_request:.4f}/request"
        )

        return result

    async def _filter_candidates(
        self, complexity_analysis: Any, constraints: dict[str, Any]
    ) -> list[ModelSpec]:
        """Filter models based on basic requirements."""
        candidates = []

        max_cost = constraints.get("max_cost_per_request", self.max_cost_per_request)
        max_latency = constraints.get("max_latency_ms", self.latency_threshold_ms)
        min_quality = constraints.get("min_quality_score", self.quality_threshold)
        required_context = constraints.get(
            "min_context_window", complexity_analysis.estimated_tokens * 2
        )

        for model in self.models.values():
            # Check basic constraints
            estimated_cost = (
                model.cost_per_1k_tokens * complexity_analysis.estimated_tokens / 1000
            )

            if (
                estimated_cost <= max_cost
                and model.avg_latency_ms <= max_latency
                and model.quality_score >= min_quality
                and model.context_window >= required_context
                and self._is_domain_compatible(model, complexity_analysis.domains)
            ):
                candidates.append(model)

        return candidates

    def _is_domain_compatible(self, model: ModelSpec, domains: Any) -> bool:
        """Check if model is compatible with query domains."""
        # If it's a general query or model has general strengths, it's compatible
        if not domains or "general_purpose" in model.strengths:
            return True

        # Check specific domain compatibility
        domain_compatibility = {
            "research": ["reasoning", "analysis", "general_purpose"],
            "content": ["creative_writing", "general_purpose"],
            "analytics": ["reasoning", "math", "analysis"],
            "service": ["general_purpose", "concise"],
            "multimodal": ["multimodal"],
        }

        for domain in domains:
            domain_name = domain.value if hasattr(domain, "value") else str(domain)
            required_strengths = domain_compatibility.get(
                domain_name, ["general_purpose"]
            )

            # Check if model has any required strengths for this domain
            if any(strength in model.strengths for strength in required_strengths):
                continue
            else:
                return False

        return True

    async def _calculate_costs(
        self, candidates: list[ModelSpec], complexity_analysis: Any
    ) -> dict[ModelSpec, CostEstimate]:
        """Calculate cost estimates for candidate models."""
        estimates = {}

        for model in candidates:
            # Base cost calculation
            estimated_tokens = complexity_analysis.estimated_tokens
            cost_per_request = (model.cost_per_1k_tokens * estimated_tokens) / 1000

            # Monthly cost projection
            monthly_cost = cost_per_request * self.projected_monthly_requests

            # Adjust for complexity and potential retries
            retry_factor = 1.1  # 10% retry rate assumption
            if complexity_analysis.uncertainty > 0.5:
                retry_factor = 1.2

            cost_per_request *= retry_factor
            monthly_cost *= retry_factor

            # Quality score adjustment based on domain fit
            quality_score = model.quality_score
            if self._is_perfect_domain_fit(model, complexity_analysis):
                quality_score = min(quality_score + 0.1, 1.0)

            estimate = CostEstimate(
                model_name=model.name,
                estimated_tokens=estimated_tokens,
                cost_per_request=cost_per_request,
                total_monthly_cost=monthly_cost,
                latency_estimate_ms=model.avg_latency_ms,
                quality_score=quality_score,
                confidence=1.0 - complexity_analysis.uncertainty,
                fallback_costs=cost_per_request * 0.05,  # 5% fallback assumption
            )

            estimates[model] = estimate

        return estimates

    def _is_perfect_domain_fit(self, model: ModelSpec, complexity_analysis: Any) -> bool:
        """Check if model is a perfect fit for the query domain."""
        # Check if model's strengths perfectly align with query requirements
        reasoning_types = complexity_analysis.reasoning_types

        perfect_fits = {
            "deepseek-v3": ["analytical", "logical", "mathematical"],
            "qwen3-72b": ["multilingual", "translation"],
            "gemini-pro": ["multimodal", "general"],
        }

        model_perfect_types = perfect_fits.get(model.name, [])
        return any(
            reasoning_type in model_perfect_types for reasoning_type in reasoning_types
        )

    async def _select_optimal_model(
        self,
        cost_estimates: dict[ModelSpec, CostEstimate],
        strategy: OptimizationStrategy,
        constraints: dict[str, Any],
    ) -> tuple[ModelSpec, CostEstimate]:
        """Select the optimal model based on strategy."""

        if strategy == OptimizationStrategy.COST_MINIMIZED:
            # Select cheapest model that meets quality threshold
            return min(cost_estimates.items(), key=lambda x: x[1].cost_per_request)

        elif strategy == OptimizationStrategy.PERFORMANCE_OPTIMIZED:
            # Select highest quality model regardless of cost
            return max(cost_estimates.items(), key=lambda x: x[1].quality_score)

        elif strategy == OptimizationStrategy.LATENCY_OPTIMIZED:
            # Select fastest model
            return min(cost_estimates.items(), key=lambda x: x[1].latency_estimate_ms)

        else:  # BALANCED
            # Select model with best cost-quality-latency balance
            def score_model(item: tuple[ModelSpec, CostEstimate]) -> float:
                _model, estimate = item
                # Normalize factors (0-1 scale)
                cost_score = 1 - min(
                    estimate.cost_per_request / self.max_cost_per_request, 1.0
                )
                quality_score = estimate.quality_score
                latency_score = 1 - min(
                    estimate.latency_estimate_ms / self.latency_threshold_ms, 1.0
                )

                # Weighted combination
                weights = constraints.get(
                    "balance_weights", {"cost": 0.4, "quality": 0.4, "latency": 0.2}
                )

                total_score = float(
                    cost_score * float(weights["cost"])
                    + quality_score * float(weights["quality"])
                    + latency_score * float(weights["latency"])
                )

                return total_score

            return max(cost_estimates.items(), key=score_model)

    async def _select_fallback_models(
        self,
        candidates: list[ModelSpec],
        primary_model: ModelSpec,
        cost_estimates: dict[ModelSpec, CostEstimate],
    ) -> list[ModelSpec]:
        """Select appropriate fallback models."""
        fallbacks = []

        # Remove primary from candidates
        remaining = [m for m in candidates if m != primary_model]

        # Sort by reliability and cost
        remaining.sort(
            key=lambda m: (
                -m.availability,  # Higher availability first
                cost_estimates[m].cost_per_request,  # Then lower cost
            )
        )

        # Select up to 2 fallback models
        fallbacks = remaining[:2]

        return fallbacks

    def _assess_risk_factors(
        self, model: ModelSpec, complexity_analysis: Any, constraints: dict[str, Any]
    ) -> list[str]:
        """Assess risk factors for the selected model."""
        risks = []

        # Availability risk
        if model.availability < 0.98:
            risks.append(f"Low availability SLA ({model.availability:.1%})")

        # Rate limiting risk
        if model.rate_limit < 1000:
            risks.append("Restrictive rate limits may cause delays")

        # Quality risk for complex queries
        if complexity_analysis.score > 0.7 and model.quality_score < 0.85:
            risks.append("Model quality may not meet complex query requirements")

        # Cost volatility risk
        if model.cost_per_1k_tokens > 0.0015:
            risks.append("Higher cost model - monitor usage carefully")

        # Context window risk
        if model.context_window < complexity_analysis.estimated_tokens * 3:
            risks.append("Limited context window for complex queries")

        return risks

    def _generate_reasoning(
        self,
        model: ModelSpec,
        estimate: CostEstimate,
        strategy: OptimizationStrategy,
        alternatives: list[tuple[ModelSpec, CostEstimate]],
    ) -> str:
        """Generate human-readable reasoning for the selection."""
        reasoning_parts = []

        # Primary selection reason
        reasoning_parts.append(f"Selected {model.name} for {strategy.value} strategy")

        # Cost justification
        reasoning_parts.append(
            f"Estimated cost: ${estimate.cost_per_request:.4f} per request "
            f"(${estimate.total_monthly_cost:.2f} monthly)"
        )

        # Quality justification
        reasoning_parts.append(f"Quality score: {estimate.quality_score:.2f}")

        # Performance justification
        reasoning_parts.append(f"Expected latency: {estimate.latency_estimate_ms}ms")

        # Comparison with alternatives
        if alternatives:
            alt_model, alt_estimate = alternatives[0]
            cost_diff = alt_estimate.cost_per_request - estimate.cost_per_request
            if cost_diff > 0:
                reasoning_parts.append(
                    f"Saves ${cost_diff:.4f} per request vs {alt_model.name}"
                )
            else:
                reasoning_parts.append(
                    f"Costs ${-cost_diff:.4f} more than {alt_model.name} "
                    f"for better quality/performance"
                )

        return ". ".join(reasoning_parts)


__all__ = [
    "CostEstimate",
    "CostOptimizer",
    "ModelSpec",
    "ModelTier",
    "OptimizationResult",
    "OptimizationStrategy",
]
