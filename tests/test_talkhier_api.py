"""
Test Suite for TalkHier Protocol API

Comprehensive tests for TalkHier session management, refinement rounds,
consensus building, and WebSocket communication.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.services.talkhier_consensus_evaluator import TalkHierConsensusEvaluator
from src.api.services.talkhier_round_executor import TalkHierRoundExecutor
from src.api.services.talkhier_session_coordinator import TalkHierSessionCoordinator
from src.api.services.talkhier_session_service import (
    TalkHierSession,
    TalkHierSessionService,
)
from src.api.services.talkhier_state_manager import TalkHierStateManager
from src.models.talkhier_api_models import (
    ConsensusCheckRequest,
    ConsensusType,
    MessageRole,
    ParticipantInfo,
    ProtocolType,
    RefinementRoundRequest,
    RefinementStrategy,
    SessionCloseRequest,
    SessionStatus,
    TalkHierSessionRequest,
)


class TestTalkHierStateManager:
    """Test TalkHier state and metrics management."""

    def test_store_and_get_session(self) -> None:
        """Test storing and retrieving a session."""
        manager = TalkHierStateManager()
        session = object()

        manager.store_session("session-1", session)

        assert manager.get_session("session-1") is session

    def test_get_missing_session_raises(self) -> None:
        """Test missing session validation."""
        manager = TalkHierStateManager()

        with pytest.raises(ValueError, match="Session missing not found"):
            manager.get_session("missing")

    def test_record_round_initializes_missing_metrics(self) -> None:
        """Test round metrics are initialized lazily for hand-built sessions."""
        manager = TalkHierStateManager()

        manager.record_round("session-1", quality_score=0.8, consensus_score=0.7)

        metrics = manager.session_metrics["session-1"]
        assert metrics["rounds_completed"] == 1
        assert metrics["quality_progression"] == [0.8]
        assert metrics["consensus_progression"] == [0.7]


class TestTalkHierRoundExecutor:
    """Test TalkHier round execution."""

    @pytest.mark.asyncio
    async def test_quality_focused_aggregation_uses_highest_confidence(self) -> None:
        """Test quality-focused aggregation selects the highest-confidence response."""
        executor = TalkHierRoundExecutor()

        result = await executor.aggregate_refinement_results(
            {
                "agent-low": {"content": "low", "confidence": 0.4},
                "agent-high": {"content": "high", "confidence": 0.9},
            },
            RefinementStrategy.QUALITY_FOCUSED,
        )

        assert result["content"] == "high"

    @pytest.mark.asyncio
    async def test_execute_round_records_metrics(self) -> None:
        """Test executing a round updates session state and metrics."""
        executor = TalkHierRoundExecutor()
        state_manager = TalkHierStateManager()
        session = TalkHierSession(
            session_id="session-1",
            query="Test query",
            domains=["research"],
            status=SessionStatus.ACTIVE,
            created_at=datetime.now(UTC),
            protocol_type=ProtocolType.STANDARD,
            refinement_strategy=RefinementStrategy.QUALITY_FOCUSED,
            max_rounds=3,
            min_rounds=1,
            quality_threshold=0.85,
            consensus_type=ConsensusType.WEIGHTED,
            consensus_threshold=0.8,
            timeout_seconds=300,
            participants=[
                ParticipantInfo(
                    agent_id="agent-1",
                    agent_type="research",
                    role=MessageRole.WORKER,
                    confidence=0.5,
                    rounds_participated=0,
                    quality_scores=[],
                )
            ],
            started_at=datetime.now(UTC),
        )

        response = await executor.execute_refinement_round(
            "session-1",
            session,
            RefinementRoundRequest(round_number=1),
            state_manager,
        )

        assert response.round_status == "completed"
        assert session.current_round == 1
        assert len(session.rounds) == 1
        assert state_manager.session_metrics["session-1"]["rounds_completed"] == 1


class TestTalkHierConsensusEvaluator:
    """Test TalkHier consensus evaluation helpers."""

    @pytest.mark.asyncio
    async def test_agreement_matrix_uses_confidence_distance(self) -> None:
        """Test agreement matrix values are based on confidence distance."""
        evaluator = TalkHierConsensusEvaluator()

        matrix = await evaluator.calculate_agreement_matrix([
            {"agent": "agent-1", "confidence": 0.9},
            {"agent": "agent-2", "confidence": 0.6},
        ])

        assert matrix["agent-1"]["agent-1"] == 1.0
        assert matrix["agent-1"]["agent-2"] == pytest.approx(0.7)

    @pytest.mark.asyncio
    async def test_minority_reports_include_confidence_outliers(self) -> None:
        """Test minority report generation identifies confidence outliers."""
        evaluator = TalkHierConsensusEvaluator()

        reports = await evaluator.generate_minority_reports(
            [
                {"agent": "agent-1", "content": "majority", "confidence": 0.9},
                {"agent": "agent-2", "content": "minority", "confidence": 0.2},
            ],
            consensus_score=0.4,
        )

        assert {report["agent"] for report in reports} == {"agent-1", "agent-2"}


class TestTalkHierSessionCoordinator:
    """Test TalkHier session coordination helpers."""

    @pytest.fixture
    def coordinator(self) -> TalkHierSessionCoordinator:
        """Create coordinator with mocked dependencies."""
        return TalkHierSessionCoordinator(
            supervisor_factory=MagicMock(),
            masr_bridge=MagicMock(),
            masr_router=MagicMock(),
        )

    def test_estimate_duration_uses_protocol_multiplier(
        self, coordinator: TalkHierSessionCoordinator
    ) -> None:
        """Test duration estimate includes participant and protocol factors."""
        duration = coordinator.estimate_duration(
            max_rounds=3,
            participant_count=2,
            protocol_config={"timeout_multiplier": 2.0},
        )

        assert duration == 300

    @pytest.mark.asyncio
    async def test_determine_participants_uses_agent_allocation_agents(
        self, coordinator: TalkHierSessionCoordinator
    ) -> None:
        """Test participant fallback for routing decisions exposing agents."""
        routing_decision = MagicMock()
        routing_decision.agent_allocation.worker_types = MagicMock()
        routing_decision.agent_allocation.agents = [
            MagicMock(agent_type="literature-review"),
            MagicMock(agent_type="synthesis"),
        ]

        participants = await coordinator.determine_participants(
            requested=None,
            routing_decision=routing_decision,
            supervisor=None,
        )

        assert [participant.agent_id for participant in participants] == [
            "literature-review",
            "synthesis",
        ]


class TestTalkHierSessionService:
    """Test TalkHier session service"""
    
    @pytest.fixture
    def session_service(self) -> TalkHierSessionService:
        """Create session service instance"""
        return TalkHierSessionService()
    
    @pytest.mark.asyncio
    async def test_create_session(
        self, session_service: TalkHierSessionService
    ) -> None:
        """Test session creation"""
        request = TalkHierSessionRequest(
            query="Test query for refinement",
            domains=["research"],
            protocol_type=ProtocolType.STANDARD,
            refinement_strategy=RefinementStrategy.QUALITY_FOCUSED,
            max_rounds=3,
            quality_threshold=0.85
        )
        
        with patch.object(session_service, '_get_routing_decision') as mock_routing:
            mock_routing.return_value = AsyncMock(
                supervisor_allocations=[
                    MagicMock(supervisor_type="research")
                ],
                agent_allocation=MagicMock(
                    agents=[
                        MagicMock(agent_type="literature-review"),
                        MagicMock(agent_type="synthesis")
                    ]
                )
            )
            
            response = await session_service.create_session(request)
            
            assert response.session_id
            assert response.status == SessionStatus.ACTIVE
            assert response.protocol_type == ProtocolType.STANDARD
            assert response.refinement_strategy == RefinementStrategy.QUALITY_FOCUSED
            assert response.max_rounds == 3
            assert response.quality_threshold == 0.85
            assert len(response.participants) >= 2
            assert response.websocket_url
            assert response.estimated_duration_seconds > 0
    
    @pytest.mark.asyncio
    async def test_execute_refinement_round(
        self, session_service: TalkHierSessionService
    ) -> None:
        """Test refinement round execution"""
        # Create a session first
        session_id = "test-session-123"
        session = TalkHierSession(
            session_id=session_id,
            query="Test query",
            domains=["research"],
            status=SessionStatus.ACTIVE,
            created_at=datetime.now(UTC),
            protocol_type=ProtocolType.STANDARD,
            refinement_strategy=RefinementStrategy.QUALITY_FOCUSED,
            max_rounds=3,
            min_rounds=1,
            quality_threshold=0.85,
            consensus_type=ConsensusType.WEIGHTED,
            consensus_threshold=0.8,
            timeout_seconds=300,
            participants=[],
            started_at=datetime.now(UTC)
        )
        session_service.sessions[session_id] = session
        
        # Execute refinement round
        request = RefinementRoundRequest(
            round_number=1,
            refinement_focus="Improve evidence quality"
        )
        
        response = await session_service.execute_refinement_round(
            session_id,
            request
        )
        
        assert response.session_id == session_id
        assert response.round_number == 1
        assert response.round_status == "completed"
        assert response.quality_score >= 0.0
        assert response.consensus_score >= 0.0
        assert isinstance(response.participant_responses, dict)
        assert isinstance(response.aggregated_result, dict)
        assert response.duration_ms > 0
    
    @pytest.mark.asyncio
    async def test_check_consensus(
        self, session_service: TalkHierSessionService
    ) -> None:
        """Test consensus checking"""
        # Create session
        session_id = "test-session-456"
        session = TalkHierSession(
            session_id=session_id,
            query="Test query",
            domains=["research"],
            status=SessionStatus.ACTIVE,
            created_at=datetime.now(UTC),
            protocol_type=ProtocolType.STANDARD,
            refinement_strategy=RefinementStrategy.CONSENSUS_DRIVEN,
            max_rounds=3,
            min_rounds=1,
            quality_threshold=0.85,
            consensus_type=ConsensusType.WEIGHTED,
            consensus_threshold=0.8,
            timeout_seconds=300,
            participants=[]
        )
        session_service.sessions[session_id] = session
        
        # Check consensus
        request = ConsensusCheckRequest(
            round_results=[
                {"agent": "agent1", "confidence": 0.85, "content": "Result 1"},
                {"agent": "agent2", "confidence": 0.82, "content": "Result 2"},
                {"agent": "agent3", "confidence": 0.88, "content": "Result 3"}
            ],
            check_quality=True,
            include_minority_report=False
        )
        
        result = await session_service.check_consensus(session_id, request)
        
        assert isinstance(result.has_consensus, bool)
        assert result.consensus_type == ConsensusType.WEIGHTED
        assert 0.0 <= result.consensus_score <= 1.0
        assert isinstance(result.agreement_matrix, dict)
        assert isinstance(result.quality_scores, dict)
        assert result.recommendation
        assert result.reasoning
    
    @pytest.mark.asyncio
    async def test_close_session(
        self, session_service: TalkHierSessionService
    ) -> None:
        """Test session closure"""
        # Create session
        session_id = "test-session-789"
        session = TalkHierSession(
            session_id=session_id,
            query="Test query",
            domains=["research"],
            status=SessionStatus.ACTIVE,
            created_at=datetime.now(UTC),
            protocol_type=ProtocolType.STANDARD,
            refinement_strategy=RefinementStrategy.QUALITY_FOCUSED,
            max_rounds=3,
            min_rounds=1,
            quality_threshold=0.85,
            consensus_type=ConsensusType.WEIGHTED,
            consensus_threshold=0.8,
            timeout_seconds=300,
            participants=[],
            started_at=datetime.now(UTC),
            current_quality=0.87,
            current_consensus=0.85
        )
        session_service.sessions[session_id] = session
        session_service.session_metrics[session_id] = {
            "rounds_completed": 3,
            "quality_progression": [0.75, 0.82, 0.87],
            "consensus_progression": [0.70, 0.78, 0.85],
            "message_count": 15
        }
        
        # Close session
        request = SessionCloseRequest(
            save_transcript=True,
            generate_summary=True
        )
        
        response = await session_service.close_session(session_id, request)
        
        assert response.session_id == session_id
        assert response.final_status in [SessionStatus.COMPLETED, SessionStatus.CANCELLED]
        assert response.total_rounds >= 0
        assert response.total_duration_seconds >= 0
        assert response.final_quality == 0.87
        assert response.final_consensus == 0.85
        assert response.transcript_url or response.summary
        assert isinstance(response.performance_metrics, dict)
    
    @pytest.mark.asyncio
    async def test_validate_protocol(
        self, session_service: TalkHierSessionService
    ) -> None:
        """Test protocol validation"""
        from src.models.talkhier_api_models import ProtocolValidationRequest
        
        request = ProtocolValidationRequest(
            messages=[
                {
                    "role": "supervisor",
                    "content": "Initial task description",
                    "timestamp": "2025-01-08T10:00:00Z"
                },
                {
                    "role": "worker",
                    "content": "Response to task",
                    "timestamp": "2025-01-08T10:01:00Z",
                    "confidence": 0.85
                },
                {
                    "role": "supervisor",
                    "content": "Refinement request",
                    "timestamp": "2025-01-08T10:02:00Z"
                }
            ],
            expected_protocol=ProtocolType.SUPERVISED,
            check_timing=True,
            check_structure=True
        )
        
        response = await session_service.validate_protocol(request)
        
        assert isinstance(response.is_valid, bool)
        assert response.protocol_detected in list(ProtocolType)
        assert isinstance(response.structural_errors, list)
        assert isinstance(response.timing_errors, list)
        assert isinstance(response.role_errors, list)
        assert response.quality_assessment is None or 0.0 <= response.quality_assessment <= 1.0
        assert isinstance(response.recommendations, list)


class TestTalkHierAPIIntegration:
    """Integration tests for TalkHier API endpoints"""

    @pytest.fixture
    def client(self) -> TestClient:
        """Create test client"""
        from src.api.main import app
        return TestClient(app)
    
    def test_list_protocols(self, client: TestClient) -> None:
        """Test protocol listing endpoint"""
        response = client.get("/api/v1/talkhier/protocols")
        
        assert response.status_code == 200
        data = response.json()
        assert "protocols" in data
        assert "default_protocol" in data
        assert "recommended_protocols" in data
        assert len(data["protocols"]) >= 5
    
    def test_health_check(self, client: TestClient) -> None:
        """Test health check endpoint"""
        response = client.get("/api/v1/talkhier/health")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["healthy", "unhealthy"]
        assert data["service"] == "TalkHier Protocol API"
        assert "timestamp" in data


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
