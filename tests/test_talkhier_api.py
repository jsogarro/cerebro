"""
Test Suite for TalkHier Protocol API

Comprehensive tests for TalkHier session management, refinement rounds,
consensus building, and WebSocket communication.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocket
from fastapi.testclient import TestClient

from src.api.services.talkhier_consensus_evaluator import TalkHierConsensusEvaluator
from src.api.services.talkhier_round_executor import TalkHierRoundExecutor
from src.api.services.talkhier_session_manager import TalkHierSessionManager
from src.api.services.talkhier_session_service import (
    TalkHierSession,
    TalkHierSessionService,
)
from src.api.services.talkhier_state_manager import TalkHierStateManager
from src.api.websocket.talkhier_websocket_events import TalkHierWebSocketHandler
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

    def test_store_and_get_session(self):
        """Test storing and retrieving a session."""
        manager = TalkHierStateManager()
        session = object()

        manager.store_session("session-1", session)

        assert manager.get_session("session-1") is session

    def test_get_missing_session_raises(self):
        """Test missing session validation."""
        manager = TalkHierStateManager()

        with pytest.raises(ValueError, match="Session missing not found"):
            manager.get_session("missing")

    def test_record_round_initializes_missing_metrics(self):
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
    async def test_quality_focused_aggregation_uses_highest_confidence(self):
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
    async def test_execute_round_records_metrics(self):
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
    async def test_agreement_matrix_uses_confidence_distance(self):
        """Test agreement matrix values are based on confidence distance."""
        evaluator = TalkHierConsensusEvaluator()

        matrix = await evaluator.calculate_agreement_matrix([
            {"agent": "agent-1", "confidence": 0.9},
            {"agent": "agent-2", "confidence": 0.6},
        ])

        assert matrix["agent-1"]["agent-1"] == 1.0
        assert matrix["agent-1"]["agent-2"] == pytest.approx(0.7)

    @pytest.mark.asyncio
    async def test_minority_reports_include_confidence_outliers(self):
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


class TestTalkHierSessionService:
    """Test TalkHier session service"""
    
    @pytest.fixture
    def session_service(self):
        """Create session service instance"""
        return TalkHierSessionService()
    
    @pytest.mark.asyncio
    async def test_create_session(self, session_service):
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
    async def test_execute_refinement_round(self, session_service):
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
    async def test_check_consensus(self, session_service):
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
    async def test_close_session(self, session_service):
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
    async def test_validate_protocol(self, session_service):
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


class TestTalkHierSessionManager:
    """Test TalkHier session manager"""
    
    @pytest.fixture
    def session_manager(self):
        """Create session manager instance"""
        return TalkHierSessionManager()
    
    @pytest.mark.asyncio
    async def test_register_session(self, session_manager):
        """Test session registration"""
        session_id = "test-session-001"
        config = {
            "protocol_type": ProtocolType.STANDARD,
            "refinement_strategy": RefinementStrategy.QUALITY_FOCUSED,
            "max_rounds": 3,
            "quality_threshold": 0.85
        }
        
        await session_manager.register_session(session_id, config)
        
        assert session_id in session_manager.sessions
        assert session_manager.sessions[session_id].protocol_type == ProtocolType.STANDARD
        assert session_manager.sessions[session_id].started_at is not None
    
    @pytest.mark.asyncio
    async def test_update_round_metrics(self, session_manager):
        """Test round metrics update"""
        session_id = "test-session-002"
        
        # Register session first
        await session_manager.register_session(session_id, {})
        
        # Update metrics
        round_data = {
            "round": 1,
            "quality": 0.82,
            "consensus": 0.78,
            "duration_ms": 2500
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
    async def test_get_analytics(self, session_manager):
        """Test analytics generation"""
        # Register some test sessions
        for i in range(5):
            session_id = f"test-session-{i:03d}"
            await session_manager.register_session(session_id, {
                "protocol_type": ProtocolType.STANDARD if i % 2 == 0 else ProtocolType.FAST_TRACK
            })
            
            # Add some metrics
            await session_manager.update_round_metrics(session_id, {
                "quality": 0.75 + i * 0.02,
                "consensus": 0.70 + i * 0.03,
                "duration_ms": 2000 + i * 100
            })
        
        # Get analytics
        analytics = await session_manager.get_analytics(
            time_range="24h",
            protocol_type=None,
            min_quality=None
        )
        
        assert analytics["total_sessions"] == 5
        assert analytics["active_sessions"] == 5
        assert analytics["average_rounds"] > 0
        assert analytics["average_quality"] > 0
        assert analytics["average_consensus"] > 0
        assert isinstance(analytics["protocol_usage"], dict)
        assert isinstance(analytics["quality_trends"], list)
    
    @pytest.mark.asyncio
    async def test_coordinate_sessions(self, session_manager):
        """Test session coordination"""
        from src.models.talkhier_api_models import CoordinationRequest
        
        # Register sessions
        session_ids = ["session-1", "session-2", "session-3"]
        for session_id in session_ids:
            await session_manager.register_session(session_id, {})
        
        # Create coordination
        request = CoordinationRequest(
            session_ids=session_ids,
            coordination_type="parallel",
            share_context=True,
            aggregate_results=True
        )
        
        status = await session_manager.coordinate_sessions(request)
        
        assert status.coordination_id
        assert len(status.session_statuses) == 3
        assert 0.0 <= status.overall_progress <= 1.0
        assert 0.0 <= status.aggregated_quality <= 1.0
        assert isinstance(status.coordination_insights, list)


class TestTalkHierWebSocketHandler:
    """Test TalkHier WebSocket handler"""
    
    @pytest.fixture
    def websocket_handler(self):
        """Create WebSocket handler instance"""
        return TalkHierWebSocketHandler()
    
    @pytest.mark.asyncio
    async def test_register_session_connection(self, websocket_handler):
        """Test session connection registration"""
        session_id = "test-session"
        connection_id = "conn-123"
        websocket = MagicMock(spec=WebSocket)
        
        await websocket_handler.register_session_connection(
            session_id,
            connection_id,
            websocket
        )
        
        assert session_id in websocket_handler.session_connections
        assert connection_id in websocket_handler.session_connections[session_id]
        assert connection_id in websocket_handler.connections
        assert websocket_handler.connections[connection_id] == websocket
    
    @pytest.mark.asyncio
    async def test_broadcast_round_started(self, websocket_handler):
        """Test round started event broadcasting"""
        session_id = "test-session"
        connection_id = "conn-456"
        websocket = AsyncMock(spec=WebSocket)
        
        # Register connection
        await websocket_handler.register_session_connection(
            session_id,
            connection_id,
            websocket
        )
        
        # Broadcast event
        await websocket_handler.broadcast_round_started(
            session_id,
            round_number=2,
            participants=["agent1", "agent2"]
        )
        
        # Verify WebSocket send was called
        websocket.send_json.assert_called_once()
        sent_data = websocket.send_json.call_args[0][0]
        assert sent_data["event_type"] == "round_started"
        assert sent_data["session_id"] == session_id
        assert sent_data["round_number"] == 2
    
    @pytest.mark.asyncio
    async def test_interactive_session_management(self, websocket_handler):
        """Test interactive session management"""
        session_id = "interactive-session"
        connection_id = "conn-789"
        websocket = AsyncMock(spec=WebSocket)
        
        # Register interactive session
        await websocket_handler.register_interactive_session(
            session_id,
            connection_id,
            websocket
        )
        
        assert session_id in websocket_handler.interactive_sessions
        assert connection_id in websocket_handler.interactive_sessions[session_id]
        
        # Handle interactive message
        from src.models.talkhier_api_models import InteractiveMessage
        
        message = InteractiveMessage(
            content="Test message",
            role=MessageRole.WORKER,
            agent_id="test-agent",
            confidence=0.85
        )
        
        await websocket_handler.handle_interactive_message(
            session_id,
            connection_id,
            message
        )
        
        # Message should be broadcast (but not to sender in this test setup)
        # In real scenario, would verify broadcast to other participants
    
    @pytest.mark.asyncio
    async def test_coordination_monitoring(self, websocket_handler):
        """Test coordination monitoring"""
        coordination_id = "coord-123"
        connection_id = "monitor-001"
        websocket = AsyncMock(spec=WebSocket)
        
        # Register monitor
        await websocket_handler.register_coordination_monitor(
            coordination_id,
            connection_id,
            websocket
        )
        
        assert coordination_id in websocket_handler.coordination_monitors
        assert connection_id in websocket_handler.coordination_monitors[coordination_id]
        
        # Broadcast update
        await websocket_handler.broadcast_coordination_update(
            coordination_id,
            {
                "overall_progress": 0.5,
                "session_count": 3,
                "average_quality": 0.83
            }
        )
        
        # Verify broadcast
        websocket.send_json.assert_called_once()
        sent_data = websocket.send_json.call_args[0][0]
        assert sent_data["type"] == "coordination_update"
        assert sent_data["coordination_id"] == coordination_id


class TestTalkHierAPIIntegration:
    """Integration tests for TalkHier API endpoints"""
    
    @pytest.fixture
    def client(self):
        """Create test client"""
        from src.api.main import app
        return TestClient(app)
    
    def test_list_protocols(self, client):
        """Test protocol listing endpoint"""
        response = client.get("/api/v1/talkhier/protocols")
        
        assert response.status_code == 200
        data = response.json()
        assert "protocols" in data
        assert "default_protocol" in data
        assert "recommended_protocols" in data
        assert len(data["protocols"]) >= 5
    
    def test_health_check(self, client):
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
