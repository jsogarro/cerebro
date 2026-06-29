"""
Real-time dashboard integration tests for the A/B testing system.
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.ai_brain.experimentation.monitoring.real_time_dashboard import (
    DashboardConfig,
    ExperimentSnapshot,
    RealTimeDashboard,
)


class TestRealTimeDashboard:
    """Test the real-time monitoring dashboard."""

    @pytest.fixture
    def dashboard(self) -> RealTimeDashboard:
        """Create a dashboard instance for testing."""
        config = DashboardConfig(
            update_interval_seconds=1,
            history_window_minutes=60,
        )
        with (
            patch(
                "src.ai_brain.experimentation.monitoring.real_time_dashboard.ConnectionManager"
            ),
            patch(
                "src.ai_brain.experimentation.monitoring.real_time_dashboard.EventPublisher"
            ),
            patch(
                "src.ai_brain.experimentation.monitoring.real_time_dashboard.RealTimeDashboard._start_background_tasks"
            ),
        ):
            return RealTimeDashboard(config)

    @pytest.mark.asyncio
    async def test_register_experiment(self, dashboard: RealTimeDashboard) -> None:
        """Test registering an experiment with the dashboard."""
        with patch.object(
            dashboard, "_broadcast_to_dashboard", AsyncMock()
        ) as broadcast:
            await dashboard.register_experiment(
                experiment_id="test_exp_001",
                experiment_config={
                    "type": "routing",
                    "name": "Test Experiment",
                    "strategies": ["a", "b", "c"],
                },
            )

        assert "test_exp_001" in dashboard.active_experiments
        assert "test_exp_001" in dashboard.experiment_history
        broadcast.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_experiment_metrics(
        self, dashboard: RealTimeDashboard
    ) -> None:
        """Test updating experiment metrics."""
        await dashboard.register_experiment("test_exp", {})

        variant_metrics = {
            "control": {
                "quality_score": 0.85,
                "latency_ms": 1000,
                "total_cost": 0.01,
                "success_rate": 0.95,
            },
            "treatment": {
                "quality_score": 0.90,
                "latency_ms": 900,
                "total_cost": 0.008,
                "success_rate": 0.97,
            },
        }
        sample_sizes = {"control": 500, "treatment": 500}
        statistical_analysis = {
            "p_value": 0.02,
            "effect_size": 0.12,
            "winning_variant": "treatment",
            "confidence_level": 0.95,
        }

        with patch.object(dashboard, "_broadcast_to_dashboard", AsyncMock()):
            await dashboard.update_experiment_metrics(
                experiment_id="test_exp",
                variant_metrics=variant_metrics,
                sample_sizes=sample_sizes,
                statistical_analysis=statistical_analysis,
            )

        assert len(dashboard.experiment_history["test_exp"]) == 1
        snapshot = dashboard.experiment_history["test_exp"][0]
        assert snapshot.winning_variant == "treatment"
        assert snapshot.p_value == 0.02

    @pytest.mark.asyncio
    async def test_generate_dashboard_update(
        self, dashboard: RealTimeDashboard
    ) -> None:
        """Test generating dashboard updates with visualizations."""
        snapshot = ExperimentSnapshot(
            experiment_id="test_exp",
            timestamp=datetime.now(UTC),
            variants={
                "control": {"quality_score": 0.8},
                "treatment": {"quality_score": 0.85},
            },
            sample_sizes={"control": 100, "treatment": 100},
            p_value=0.04,
            effect_size=0.1,
            winning_variant="treatment",
        )

        update: dict[str, Any] = await dashboard._generate_dashboard_update(
            "test_exp", snapshot
        )

        assert update["event"] == "experiment_update"
        assert update["experiment_id"] == "test_exp"
        assert "metrics" in update
        assert "charts" in update
        assert "recommendation" in update

    @pytest.mark.asyncio
    async def test_alert_generation(self, dashboard: RealTimeDashboard) -> None:
        """Test alert generation for experiment conditions."""
        snapshot = ExperimentSnapshot(
            experiment_id="test_exp",
            timestamp=datetime.now(UTC),
            variants={},
            sample_sizes={"control": 30, "treatment": 40},
            p_value=0.03,
            effect_size=0.03,
        )

        alerts = dashboard._check_for_alerts(snapshot)

        assert len(alerts) >= 2
        assert any(a["type"] == "warning" for a in alerts)

    @pytest.mark.asyncio
    async def test_export_experiment_data(self, dashboard: RealTimeDashboard) -> None:
        """Test exporting experiment data."""
        await dashboard.register_experiment("test_exp", {})

        for i in range(3):
            snapshot = ExperimentSnapshot(
                experiment_id="test_exp",
                timestamp=datetime.now(UTC),
                variants={"control": {"score": 0.8}, "treatment": {"score": 0.85}},
                sample_sizes={"control": 100 + i * 50, "treatment": 100 + i * 50},
            )
            dashboard.experiment_history["test_exp"].append(snapshot)

        json_data = await dashboard.export_experiment_data("test_exp", "json")
        assert len(json_data) == 3
        assert all("timestamp" in item for item in json_data)
