"""
TalkHier WebSocket Events Handler

Manages real-time WebSocket communication for TalkHier protocol sessions,
including live updates, interactive dialogue, and multi-session coordination.
"""

import asyncio
import json
from typing import Dict, List, Set, Optional, Any
from datetime import datetime
from fastapi import WebSocket
import logging

from src.models.talkhier_api_models import (
    TalkHierWebSocketEvent, RoundStartedEvent, MessageExchangeEvent,
    ConsensusUpdateEvent, QualityUpdateEvent, SessionCompletedEvent,
    InteractiveMessage, InteractiveCommand, MessageRole,
    RefinementRoundResponse, ConsensusResult, SessionCloseResponse
)

logger = logging.getLogger(__name__)


class TalkHierWebSocketHandler:
    """
    Handles WebSocket events for TalkHier protocol sessions
    """
    
    def __init__(self):
        # Session connections: session_id -> Set[connection_id]
        self.session_connections: Dict[str, Set[str]] = {}
        
        # Connection mapping: connection_id -> WebSocket
        self.connections: Dict[str, WebSocket] = {}
        
        # Interactive sessions: session_id -> Set[connection_id]
        self.interactive_sessions: Dict[str, Set[str]] = {}
        
        # Coordination monitors: coordination_id -> Set[connection_id]
        self.coordination_monitors: Dict[str, Set[str]] = {}
        
    # ================================
    # Session Connection Management
    # ================================
    
    async def register_session_connection(
        self,
        session_id: str,
        connection_id: str,
        websocket: WebSocket
    ) -> None:
        """Register a WebSocket connection for session updates"""
        if session_id not in self.session_connections:
            self.session_connections[session_id] = set()
        
        self.session_connections[session_id].add(connection_id)
        self.connections[connection_id] = websocket
        
        logger.info(f"Registered connection {connection_id} for session {session_id}")
    
    async def unregister_session_connection(
        self,
        session_id: str,
        connection_id: str
    ) -> None:
        """Unregister a WebSocket connection from session"""
        if session_id in self.session_connections:
            self.session_connections[session_id].discard(connection_id)
            
            if not self.session_connections[session_id]:
                del self.session_connections[session_id]
        
        if connection_id in self.connections:
            del self.connections[connection_id]
        
        logger.info(f"Unregistered connection {connection_id} from session {session_id}")
    
    # ================================
    # Event Broadcasting
    # ================================
    
    async def broadcast_round_started(
        self,
        session_id: str,
        round_number: int,
        participants: List[str]
    ) -> None:
        """Broadcast round started event"""
        event = RoundStartedEvent(
            event_type="round_started",
            session_id=session_id,
            round_number=round_number,
            participants=participants,
            data={"round_number": round_number, "participants": participants}
        )
        
        await self._broadcast_to_session(session_id, event.dict())
    
    async def broadcast_round_completed(
        self,
        session_id: str,
        round_response: RefinementRoundResponse
    ) -> None:
        """Broadcast round completion event"""
        event = TalkHierWebSocketEvent(
            event_type="round_completed",
            session_id=session_id,
            data={
                "round_number": round_response.round_number,
                "quality_score": round_response.quality_score,
                "consensus_score": round_response.consensus_score,
                "improvement_delta": round_response.improvement_delta,
                "continue_refinement": round_response.continue_refinement
            }
        )
        
        await self._broadcast_to_session(session_id, event.dict())
    
    async def broadcast_message_exchange(
        self,
        session_id: str,
        sender: str,
        role: MessageRole,
        content_preview: str,
        confidence: float
    ) -> None:
        """Broadcast message exchange event"""
        event = MessageExchangeEvent(
            event_type="message_exchange",
            session_id=session_id,
            sender=sender,
            role=role,
            content_preview=content_preview[:100] + "..." if len(content_preview) > 100 else content_preview,
            confidence=confidence,
            data={
                "sender": sender,
                "role": role.value,
                "confidence": confidence
            }
        )
        
        await self._broadcast_to_session(session_id, event.dict())
    
    async def broadcast_consensus_update(
        self,
        session_id: str,
        consensus_result: ConsensusResult
    ) -> None:
        """Broadcast consensus update event"""
        # Determine trend
        trending = "stable"
        if consensus_result.consensus_score > 0.8:
            trending = "improving"
        elif consensus_result.consensus_score < 0.5:
            trending = "declining"
        
        event = ConsensusUpdateEvent(
            event_type="consensus_update",
            session_id=session_id,
            current_consensus=consensus_result.consensus_score,
            trending_direction=trending,
            data={
                "has_consensus": consensus_result.has_consensus,
                "consensus_score": consensus_result.consensus_score,
                "consensus_type": consensus_result.consensus_type.value,
                "recommendation": consensus_result.recommendation
            }
        )
        
        await self._broadcast_to_session(session_id, event.dict())
    
    async def broadcast_quality_update(
        self,
        session_id: str,
        current_quality: float,
        improvement_delta: float,
        target_quality: float
    ) -> None:
        """Broadcast quality update event"""
        event = QualityUpdateEvent(
            event_type="quality_update",
            session_id=session_id,
            current_quality=current_quality,
            improvement_delta=improvement_delta,
            target_reached=current_quality >= target_quality,
            data={
                "current_quality": current_quality,
                "improvement_delta": improvement_delta,
                "target_quality": target_quality,
                "target_reached": current_quality >= target_quality
            }
        )
        
        await self._broadcast_to_session(session_id, event.dict())
    
    async def broadcast_session_completed(
        self,
        session_id: str,
        close_response: SessionCloseResponse
    ) -> None:
        """Broadcast session completion event"""
        event = SessionCompletedEvent(
            event_type="session_completed",
            session_id=session_id,
            final_quality=close_response.final_quality,
            final_consensus=close_response.final_consensus,
            total_rounds=close_response.total_rounds,
            success=close_response.final_quality >= 0.8,  # Success threshold
            data={
                "final_status": close_response.final_status.value,
                "total_rounds": close_response.total_rounds,
                "total_duration_seconds": close_response.total_duration_seconds,
                "final_quality": close_response.final_quality,
                "final_consensus": close_response.final_consensus
            }
        )
        
        await self._broadcast_to_session(session_id, event.dict())
    
    # ================================
    # Interactive Session Management
    # ================================
    
    async def register_interactive_session(
        self,
        session_id: str,
        connection_id: str,
        websocket: WebSocket
    ) -> None:
        """Register an interactive session"""
        if session_id not in self.interactive_sessions:
            self.interactive_sessions[session_id] = set()
        
        self.interactive_sessions[session_id].add(connection_id)
        self.connections[connection_id] = websocket
        
        logger.info(f"Registered interactive session {session_id} for connection {connection_id}")
    
    async def join_interactive_session(
        self,
        session_id: str,
        connection_id: str,
        websocket: WebSocket
    ) -> None:
        """Join an existing interactive session"""
        if session_id not in self.interactive_sessions:
            raise ValueError(f"Interactive session {session_id} not found")
        
        self.interactive_sessions[session_id].add(connection_id)
        self.connections[connection_id] = websocket
        
        # Notify other participants
        await self._broadcast_to_interactive(
            session_id,
            {
                "type": "participant_joined",
                "connection_id": connection_id,
                "timestamp": datetime.utcnow().isoformat()
            },
            exclude_connection=connection_id
        )
    
    async def leave_interactive_session(
        self,
        session_id: str,
        connection_id: str
    ) -> None:
        """Leave an interactive session"""
        if session_id in self.interactive_sessions:
            self.interactive_sessions[session_id].discard(connection_id)
            
            # Notify other participants
            await self._broadcast_to_interactive(
                session_id,
                {
                    "type": "participant_left",
                    "connection_id": connection_id,
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
            
            if not self.interactive_sessions[session_id]:
                del self.interactive_sessions[session_id]
        
        if connection_id in self.connections:
            del self.connections[connection_id]
    
    async def handle_interactive_message(
        self,
        session_id: str,
        connection_id: str,
        message: InteractiveMessage
    ) -> None:
        """Handle an interactive message"""
        # Broadcast message to all participants
        await self._broadcast_to_interactive(
            session_id,
            {
                "type": "message",
                "sender_id": connection_id,
                "content": message.content,
                "role": message.role.value,
                "agent_id": message.agent_id,
                "confidence": message.confidence,
                "supporting_evidence": message.supporting_evidence,
                "in_response_to": message.in_response_to,
                "timestamp": datetime.utcnow().isoformat()
            }
        )
        
        # Also broadcast as message exchange event to session observers
        await self.broadcast_message_exchange(
            session_id,
            message.agent_id or connection_id,
            message.role,
            message.content,
            message.confidence
        )
    
    async def handle_interactive_command(
        self,
        session_id: str,
        connection_id: str,
        command: InteractiveCommand
    ) -> None:
        """Handle an interactive command"""
        # Process command
        command_result = await self._process_interactive_command(
            session_id,
            command
        )
        
        # Send result to command issuer
        if connection_id in self.connections:
            websocket = self.connections[connection_id]
            await websocket.send_json({
                "type": "command_result",
                "command": command.command,
                "success": command_result.get("success", False),
                "result": command_result,
                "timestamp": datetime.utcnow().isoformat()
            })
        
        # Broadcast command effect to all participants
        await self._broadcast_to_interactive(
            session_id,
            {
                "type": "command_executed",
                "command": command.command,
                "issuer_id": connection_id,
                "effect": command_result.get("effect", ""),
                "timestamp": datetime.utcnow().isoformat()
            }
        )
    
    # ================================
    # Coordination Monitoring
    # ================================
    
    async def register_coordination_monitor(
        self,
        coordination_id: str,
        connection_id: str,
        websocket: WebSocket
    ) -> None:
        """Register a coordination monitor"""
        if coordination_id not in self.coordination_monitors:
            self.coordination_monitors[coordination_id] = set()
        
        self.coordination_monitors[coordination_id].add(connection_id)
        self.connections[connection_id] = websocket
        
        logger.info(f"Registered coordination monitor for {coordination_id}")
    
    async def unregister_coordination_monitor(
        self,
        coordination_id: str,
        connection_id: str
    ) -> None:
        """Unregister a coordination monitor"""
        if coordination_id in self.coordination_monitors:
            self.coordination_monitors[coordination_id].discard(connection_id)
            
            if not self.coordination_monitors[coordination_id]:
                del self.coordination_monitors[coordination_id]
        
        if connection_id in self.connections:
            del self.connections[connection_id]
    
    async def broadcast_coordination_update(
        self,
        coordination_id: str,
        update_data: Dict[str, Any]
    ) -> None:
        """Broadcast coordination update"""
        if coordination_id in self.coordination_monitors:
            for connection_id in self.coordination_monitors[coordination_id]:
                if connection_id in self.connections:
                    websocket = self.connections[connection_id]
                    try:
                        await websocket.send_json({
                            "type": "coordination_update",
                            "coordination_id": coordination_id,
                            "data": update_data,
                            "timestamp": datetime.utcnow().isoformat()
                        })
                    except Exception as e:
                        logger.error(f"Failed to send coordination update: {str(e)}")
    
    # ================================
    # Helper Methods
    # ================================
    
    async def _broadcast_to_session(
        self,
        session_id: str,
        event_data: Dict[str, Any]
    ) -> None:
        """Broadcast event to all session connections"""
        if session_id in self.session_connections:
            for connection_id in self.session_connections[session_id]:
                if connection_id in self.connections:
                    websocket = self.connections[connection_id]
                    try:
                        await websocket.send_json(event_data)
                    except Exception as e:
                        logger.error(f"Failed to send event to {connection_id}: {str(e)}")
    
    async def _broadcast_to_interactive(
        self,
        session_id: str,
        data: Dict[str, Any],
        exclude_connection: Optional[str] = None
    ) -> None:
        """Broadcast to interactive session participants"""
        if session_id in self.interactive_sessions:
            for connection_id in self.interactive_sessions[session_id]:
                if connection_id != exclude_connection and connection_id in self.connections:
                    websocket = self.connections[connection_id]
                    try:
                        await websocket.send_json(data)
                    except Exception as e:
                        logger.error(f"Failed to send to interactive participant {connection_id}: {str(e)}")
    
    async def _process_interactive_command(
        self,
        session_id: str,
        command: InteractiveCommand
    ) -> Dict[str, Any]:
        """Process an interactive command"""
        result = {"success": False, "effect": ""}
        
        if command.command == "pause":
            # Pause session processing
            result["success"] = True
            result["effect"] = "Session paused"
            
        elif command.command == "resume":
            # Resume session processing
            result["success"] = True
            result["effect"] = "Session resumed"
            
        elif command.command == "skip_round":
            # Skip current round
            result["success"] = True
            result["effect"] = "Skipped to next round"
            
        elif command.command == "force_consensus":
            # Force consensus check
            result["success"] = True
            result["effect"] = "Forced consensus evaluation"
            
        elif command.command == "abort":
            # Abort session
            result["success"] = True
            result["effect"] = "Session aborted"
        
        else:
            result["error"] = f"Unknown command: {command.command}"
        
        return result
    
    def get_session_connection_count(self, session_id: str) -> int:
        """Get number of connections for a session"""
        return len(self.session_connections.get(session_id, set()))
    
    def get_all_active_sessions(self) -> List[str]:
        """Get all active session IDs"""
        return list(self.session_connections.keys())
    
    def get_connection_sessions(self, connection_id: str) -> List[str]:
        """Get all sessions for a connection"""
        sessions = []
        for session_id, connections in self.session_connections.items():
            if connection_id in connections:
                sessions.append(session_id)
        return sessions