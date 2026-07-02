"""Resilience characterization tests for bounded workflow iteration.

NOTE: This file previously contained tests for the removed LangGraph orchestration
subsystem (ResearchGraphBuilder, ResearchState). Those tests have been removed.
Only tests for active systems (TalkHier, MASR, GeminiService) remain.
"""

import asyncio
from datetime import UTC, datetime

import pytest

from src.ai_brain.router.masr import MASRouter
from src.api.services.talkhier_round_executor import TalkHierRoundExecutor
from src.api.services.talkhier_session_service import TalkHierSession
from src.api.services.talkhier_state_manager import TalkHierStateManager
from src.models.talkhier_api_models import (
    ConsensusType,
    ProtocolType,
    RefinementRoundRequest,
    RefinementStrategy,
    SessionStatus,
)
from src.reliability.retry_strategies import CircuitBreaker
from src.services.gemini_service import GeminiService


def test_talkhier_refinement_round_rejects_rounds_past_max_rounds() -> None:
    """TalkHier refinement rounds respect max_rounds cap."""

    async def execute_past_cap() -> None:
        await TalkHierRoundExecutor().execute_refinement_round(
            "session-1",
            session,
            RefinementRoundRequest(round_number=2),
            TalkHierStateManager(),
        )

    session = TalkHierSession(
        session_id="session-1",
        query="bounded TalkHier",
        domains=["general"],
        status=SessionStatus.ACTIVE,
        created_at=datetime.now(UTC),
        protocol_type=ProtocolType.STANDARD,
        refinement_strategy=RefinementStrategy.QUALITY_FOCUSED,
        max_rounds=1,
        min_rounds=1,
        quality_threshold=0.9,
        consensus_type=ConsensusType.WEIGHTED,
        consensus_threshold=0.9,
        timeout_seconds=300,
        participants=[],
        current_round=1,
    )

    with pytest.raises(ValueError, match="exceeds max_rounds=1"):
        asyncio.run(execute_past_cap())


def test_gemini_api_call_uses_tenacity_retry_policy() -> None:
    """GeminiService uses tenacity retry decorator."""
    assert hasattr(GeminiService._generate_content, "retry")


def test_masr_router_exposes_routing_circuit_breaker() -> None:
    """MASR router has circuit breaker for routing decisions."""
    router = MASRouter(config={"enable_learning": False})

    assert isinstance(router.routing_circuit_breaker, CircuitBreaker)
