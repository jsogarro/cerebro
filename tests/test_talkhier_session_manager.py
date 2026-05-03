"""Tests for TalkHier session manager coordination."""

from typing import Any

import pytest

from src.api.services.talkhier_session_manager import TalkHierSessionManager
from src.models.talkhier_api_models import (
    CoordinationRequest,
    ProtocolType,
    RefinementStrategy,
)


class TestTalkHierSessionManager:
    """Test TalkHier session manager."""

    @pytest.fixture
    def session_manager(self) -> TalkHierSessionManager:
        """Create session manager instance."""
        return TalkHierSessionManager()

    @pytest.mark.asyncio
    async def test_register_session(
        self, session_manager: TalkHierSessionManager
    ) -> None:
        """Test session registration."""
        session_id = "test-session-001"
        config = {
            "protocol_type": ProtocolType.STANDARD,
            "refinement_strategy": RefinementStrategy.QUALITY_FOCUSED,
            "max_rounds": 3,
            "quality_threshold": 0.85,
        }

        await session_manager.register_session(session_id, config)

        assert session_id in session_manager.sessions
        assert (
            session_manager.sessions[session_id].protocol_type == ProtocolType.STANDARD
        )
        assert session_manager.sessions[session_id].started_at is not None

    @pytest.mark.asyncio
    async def test_update_round_metrics(
        self, session_manager: TalkHierSessionManager
    ) -> None:
        """Test round metrics update."""
        session_id = "test-session-002"

        await session_manager.register_session(session_id, {})

        round_data = {
            "round": 1,
            "quality": 0.82,
            "consensus": 0.78,
            "duration_ms": 2500,
        }

        await session_manager.update_round_metrics(session_id, round_data)

        metrics = session_manager.sessions[session_id]
        assert metrics.rounds_completed == 1
        assert 0.82 in metrics.quality_scores
        assert 0.78 in metrics.consensus_scores
        assert 2500 in metrics.round_durations
        assert metrics.final_quality == 0.82
        assert metrics.final_consensus == 0.78

    @pytest.mark.asyncio
    async def test_get_analytics(
        self, session_manager: TalkHierSessionManager
    ) -> None:
        """Test analytics generation."""
        for i in range(5):
            session_id = f"test-session-{i:03d}"
            await session_manager.register_session(
                session_id,
                {
                    "protocol_type": (
                        ProtocolType.STANDARD
                        if i % 2 == 0
                        else ProtocolType.FAST_TRACK
                    )
                },
            )

            await session_manager.update_round_metrics(
                session_id,
                {
                    "quality": 0.75 + i * 0.02,
                    "consensus": 0.70 + i * 0.03,
                    "duration_ms": 2000 + i * 100,
                },
            )

        analytics: dict[str, Any] = await session_manager.get_analytics(
            time_range="24h",
            protocol_type=None,
            min_quality=None,
        )

        assert analytics["total_sessions"] == 5
        assert analytics["active_sessions"] == 5
        assert analytics["average_rounds"] > 0
        assert analytics["average_quality"] > 0
        assert analytics["average_consensus"] > 0
        assert isinstance(analytics["protocol_usage"], dict)
        assert isinstance(analytics["quality_trends"], list)

    @pytest.mark.asyncio
    async def test_coordinate_sessions(
        self, session_manager: TalkHierSessionManager
    ) -> None:
        """Test session coordination."""
        session_ids = ["session-1", "session-2", "session-3"]
        for session_id in session_ids:
            await session_manager.register_session(session_id, {})

        request = CoordinationRequest(
            session_ids=session_ids,
            coordination_type="parallel",
            share_context=True,
            aggregate_results=True,
        )

        status = await session_manager.coordinate_sessions(request)

        assert status.coordination_id
        assert len(status.session_statuses) == 3
        assert 0.0 <= status.overall_progress <= 1.0
        assert 0.0 <= status.aggregated_quality <= 1.0
        assert isinstance(status.coordination_insights, list)
