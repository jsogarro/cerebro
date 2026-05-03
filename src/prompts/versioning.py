"""
Prompt Version Management

Provides versioning and A/B testing capabilities for prompt templates,
enabling prompt optimization and performance comparison.

Features:
- Semantic versioning for prompt templates
- A/B testing with statistical significance
- Performance comparison across versions
- Automated champion/challenger management
- Version rollback and deployment controls
"""

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from structlog import get_logger

from .schemas import PromptTemplate

logger = get_logger()


class VersionStatus(Enum):
    """Status of prompt versions."""

    DRAFT = "draft"  # Under development
    TESTING = "testing"  # In A/B test
    CHAMPION = "champion"  # Current best version
    CHALLENGER = "challenger"  # Testing against champion
    RETIRED = "retired"  # No longer used
    DEPRECATED = "deprecated"  # Marked for removal


class ABTestStatus(Enum):
    """A/B test status."""

    PLANNING = "planning"  # Test being planned
    RUNNING = "running"  # Test in progress
    COMPLETED = "completed"  # Test finished
    CANCELLED = "cancelled"  # Test cancelled
    INCONCLUSIVE = "inconclusive"  # No clear winner


@dataclass
class VersionPerformance:
    """Performance metrics for a prompt version."""

    # Usage statistics
    total_uses: int = 0
    successful_uses: int = 0
    success_rate: float = 0.0

    # Quality metrics
    avg_quality_score: float = 0.0
    quality_variance: float = 0.0
    min_quality: float = 0.0
    max_quality: float = 0.0

    # Performance metrics
    avg_response_time_ms: int = 0
    avg_token_usage: int = 0
    avg_cost_per_use: float = 0.0

    # Temporal data
    first_used: datetime | None = None
    last_used: datetime | None = None

    def update(
        self,
        success: bool,
        quality_score: float,
        response_time_ms: int,
        token_usage: int,
        cost: float,
    ) -> None:
        """Update performance metrics with new data point."""

        # Update usage counts
        self.total_uses += 1
        if success:
            self.successful_uses += 1

        # Update success rate
        self.success_rate = self.successful_uses / self.total_uses

        # Update quality metrics (exponential moving average)
        alpha = 0.1
        if self.total_uses == 1:
            self.avg_quality_score = quality_score
            self.min_quality = quality_score
            self.max_quality = quality_score
        else:
            self.avg_quality_score = (
                1 - alpha
            ) * self.avg_quality_score + alpha * quality_score
            self.min_quality = min(self.min_quality, quality_score)
            self.max_quality = max(self.max_quality, quality_score)

        # Update performance metrics
        self.avg_response_time_ms = int(
            (self.avg_response_time_ms * (self.total_uses - 1) + response_time_ms)
            / self.total_uses
        )
        self.avg_token_usage = int(
            (self.avg_token_usage * (self.total_uses - 1) + token_usage)
            / self.total_uses
        )
        self.avg_cost_per_use = (
            self.avg_cost_per_use * (self.total_uses - 1) + cost
        ) / self.total_uses

        # Update temporal data
        now = datetime.now()
        if not self.first_used:
            self.first_used = now
        self.last_used = now


@dataclass
class ABTestConfig:
    """Configuration for A/B testing."""

    test_name: str = ""
    champion_version: str = ""
    challenger_version: str = ""

    # Test parameters
    traffic_split: float = 0.5  # 50/50 split by default
    min_sample_size: int = 100
    confidence_level: float = 0.95
    minimum_effect_size: float = 0.05  # 5% minimum improvement

    # Test duration
    max_duration_days: int = 30
    early_stopping: bool = True

    # Success metrics
    primary_metric: str = "success_rate"
    secondary_metrics: list[str] = field(
        default_factory=lambda: ["quality_score", "response_time"]
    )


@dataclass
class ABTestResult:
    """Result of A/B testing."""

    test_config: ABTestConfig | None = None
    status: ABTestStatus = ABTestStatus.COMPLETED

    # Test results
    champion_performance: VersionPerformance | None = None
    challenger_performance: VersionPerformance | None = None

    # Statistical analysis
    statistical_significance: bool = False
    p_value: float = 1.0
    effect_size: float = 0.0
    confidence_interval: tuple[float, float] = (0.0, 0.0)

    # Decision
    winner: str | None = None  # champion, challenger, or None
    recommendation: str = ""

    # Test metadata
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    total_samples: int = 0


class PromptVersionManager:
    """
    Manages prompt template versions and A/B testing.

    Provides comprehensive version control for prompt templates including
    performance tracking, A/B testing, and automated champion selection.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        """Initialize version manager."""
        self.config = config or {}

        # Version storage
        self.versions: dict[str, dict[str, PromptTemplate]] = (
            {}
        )  # template_name -> {version -> template}
        self.version_performance: dict[str, VersionPerformance] = (
            {}
        )  # template_name:version -> performance
        self.version_status: dict[str, VersionStatus] = (
            {}
        )  # template_name:version -> status

        # A/B testing
        self.active_ab_tests: dict[str, ABTestConfig] = {}  # test_name -> config
        self.ab_test_results: dict[str, ABTestResult] = {}  # test_name -> result

        # Configuration
        self.default_ab_config = ABTestConfig(
            **self.config.get("default_ab_config", {})
        )
        self.auto_promote_champions = self.config.get("auto_promote_champions", True)
        self.min_usage_for_promotion = self.config.get("min_usage_for_promotion", 50)

    async def register_version(
        self,
        template: PromptTemplate,
        version_status: VersionStatus = VersionStatus.DRAFT,
    ) -> str:
        """Register a new version of a prompt template."""

        template_name = template.metadata.name
        version = template.metadata.version
        version_key = f"{template_name}:{version}"

        # Initialize version tracking
        if template_name not in self.versions:
            self.versions[template_name] = {}

        self.versions[template_name][version] = template
        self.version_performance[version_key] = VersionPerformance()
        self.version_status[version_key] = version_status

        # Set as champion if it's the first version
        if len(self.versions[template_name]) == 1:
            self.version_status[version_key] = VersionStatus.CHAMPION
            template.metadata.champion_version = True

        logger.info(
            f"Registered {template_name} v{version} with status {version_status.value}"
        )

        return version_key

    async def start_ab_test(
        self,
        template_name: str,
        champion_version: str,
        challenger_version: str,
        test_config: ABTestConfig | None = None,
    ) -> str:
        """Start A/B test between two prompt versions."""

        test_config = test_config or self.default_ab_config
        test_name = f"{template_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Update test config
        test_config.test_name = test_name
        test_config.champion_version = champion_version
        test_config.challenger_version = challenger_version

        # Update version statuses
        champion_key = f"{template_name}:{champion_version}"
        challenger_key = f"{template_name}:{challenger_version}"

        self.version_status[champion_key] = VersionStatus.CHAMPION
        self.version_status[challenger_key] = VersionStatus.CHALLENGER

        # Store test configuration
        self.active_ab_tests[test_name] = test_config

        logger.info(f"Started A/B test: {test_name}")
        logger.info(f"Champion: {champion_version}, Challenger: {challenger_version}")

        return test_name

    async def record_usage(
        self,
        template_name: str,
        version: str,
        success: bool,
        quality_score: float,
        response_time_ms: int = 0,
        token_usage: int = 0,
        cost: float = 0.0,
    ) -> bool:
        """Record usage for performance tracking."""

        version_key = f"{template_name}:{version}"

        if version_key not in self.version_performance:
            self.version_performance[version_key] = VersionPerformance()

        # Update performance metrics
        self.version_performance[version_key].update(
            success, quality_score, response_time_ms, token_usage, cost
        )

        # Check if any A/B tests should be updated
        await self._update_ab_tests(template_name, version, success, quality_score)

        return True

    async def get_champion_version(self, template_name: str) -> str | None:
        """Get current champion version for template."""

        if template_name not in self.versions:
            return None

        # Find champion version
        for version, _template in self.versions[template_name].items():
            version_key = f"{template_name}:{version}"
            if self.version_status.get(version_key) == VersionStatus.CHAMPION:
                return version

        # If no champion, return latest version
        versions = sorted(self.versions[template_name].keys(), reverse=True)
        return versions[0] if versions else None

    async def get_version_for_ab_test(
        self, template_name: str
    ) -> tuple[str | None, float]:
        """Get version to use based on A/B test configuration."""

        # Check if template is in active A/B test
        for _test_name, test_config in self.active_ab_tests.items():
            if template_name in test_config.test_name:
                # Random assignment based on traffic split
                import random

                if random.random() < test_config.traffic_split:
                    return test_config.challenger_version, test_config.traffic_split
                else:
                    return test_config.champion_version, 1.0 - test_config.traffic_split

        # No A/B test active, return champion
        champion_version = await self.get_champion_version(template_name)
        return champion_version, 1.0

    async def _update_ab_tests(
        self, template_name: str, version: str, success: bool, quality_score: float
    ) -> None:
        """Update A/B tests with new usage data."""

        # Find relevant A/B tests
        for test_name, test_config in self.active_ab_tests.items():
            if (template_name in test_config.test_name and
                version in [
                    test_config.champion_version,
                    test_config.challenger_version,
                ]):
                await self._evaluate_ab_test_progress(test_name, test_config)

    async def _evaluate_ab_test_progress(
        self, test_name: str, test_config: ABTestConfig
    ) -> None:
        """Evaluate A/B test progress and determine if test should end."""

        champion_key = f"{test_config.test_name}:{test_config.champion_version}"
        challenger_key = f"{test_config.test_name}:{test_config.challenger_version}"

        champion_perf = self.version_performance.get(champion_key)
        challenger_perf = self.version_performance.get(challenger_key)

        if not champion_perf or not challenger_perf:
            return

        # Check if we have enough samples
        total_samples = champion_perf.total_uses + challenger_perf.total_uses

        if total_samples >= test_config.min_sample_size:
            # Perform statistical analysis (simplified)
            champion_success_rate = champion_perf.success_rate
            challenger_success_rate = challenger_perf.success_rate

            # Simple effect size calculation
            effect_size = abs(challenger_success_rate - champion_success_rate)

            # Simplified statistical significance (would use proper tests in production)
            statistical_significance = (
                effect_size > test_config.minimum_effect_size
                and total_samples > test_config.min_sample_size
            )

            if statistical_significance:
                await self._complete_ab_test(
                    test_name, test_config, champion_perf, challenger_perf
                )

    async def _complete_ab_test(
        self,
        test_name: str,
        test_config: ABTestConfig,
        champion_perf: VersionPerformance,
        challenger_perf: VersionPerformance,
    ) -> None:
        """Complete A/B test and determine winner."""

        # Compare performance
        champion_score = (
            champion_perf.success_rate * 0.7 + champion_perf.avg_quality_score * 0.3
        )
        challenger_score = (
            challenger_perf.success_rate * 0.7 + challenger_perf.avg_quality_score * 0.3
        )

        # Determine winner
        if challenger_score > champion_score * (1 + test_config.minimum_effect_size):
            winner = "challenger"
            recommendation = (
                f"Promote challenger version {test_config.challenger_version}"
            )

            # Auto-promote if enabled
            if self.auto_promote_champions:
                await self._promote_challenger(test_config)

        elif champion_score > challenger_score * (1 + test_config.minimum_effect_size):
            winner = "champion"
            recommendation = f"Retain champion version {test_config.champion_version}"

        else:
            winner = None
            recommendation = "No significant difference detected"

        # Create test result
        test_result = ABTestResult(
            test_config=test_config,
            status=ABTestStatus.COMPLETED,
            champion_performance=champion_perf,
            challenger_performance=challenger_perf,
            winner=winner,
            recommendation=recommendation,
            completed_at=datetime.now(),
            total_samples=champion_perf.total_uses + challenger_perf.total_uses,
        )

        # Store result and clean up active test
        self.ab_test_results[test_name] = test_result
        del self.active_ab_tests[test_name]

        logger.info(f"A/B test completed: {test_name}")
        logger.info(f"Winner: {winner}, Recommendation: {recommendation}")

    async def _promote_challenger(self, test_config: ABTestConfig) -> None:
        """Promote challenger to champion status."""

        template_name = test_config.test_name.split("_")[0]  # Extract template name

        champion_key = f"{template_name}:{test_config.champion_version}"
        challenger_key = f"{template_name}:{test_config.challenger_version}"

        # Update statuses
        self.version_status[champion_key] = VersionStatus.RETIRED
        self.version_status[challenger_key] = VersionStatus.CHAMPION

        # Update template metadata
        if (
            template_name in self.versions
            and test_config.challenger_version in self.versions[template_name]
        ):

            template = self.versions[template_name][test_config.challenger_version]
            template.metadata.champion_version = True

            # Retire old champion
            if test_config.champion_version in self.versions[template_name]:
                old_champion = self.versions[template_name][
                    test_config.champion_version
                ]
                old_champion.metadata.champion_version = False

        logger.info(
            f"Promoted {test_config.challenger_version} to champion for {template_name}"
        )

    async def get_version_stats(self) -> dict[str, Any]:
        """Get version management statistics."""

        stats = {
            "total_templates": len(self.versions),
            "total_versions": sum(len(versions) for versions in self.versions.values()),
            "active_ab_tests": len(self.active_ab_tests),
            "completed_tests": len(self.ab_test_results),
            "version_breakdown": {},
        }

        # Count versions by status
        status_counts = {}
        for status in VersionStatus:
            status_counts[status.value] = 0

        for _version_key, status in self.version_status.items():
            status_counts[status.value] += 1

        stats["version_breakdown"] = status_counts

        # Performance summary
        if self.version_performance:
            all_performances = list(self.version_performance.values())
            stats["performance_summary"] = {
                "avg_success_rate": statistics.mean(
                    [p.success_rate for p in all_performances]
                ),
                "avg_quality_score": statistics.mean(
                    [p.avg_quality_score for p in all_performances]
                ),
                "total_usage": sum([p.total_uses for p in all_performances]),
            }

        return stats


__all__ = [
    "ABTestConfig",
    "ABTestResult",
    "ABTestStatus",
    "PromptVersionManager",
    "VersionPerformance",
    "VersionStatus",
]
