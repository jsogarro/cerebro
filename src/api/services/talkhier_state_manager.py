"""State management for TalkHier sessions."""

from typing import Any


class TalkHierStateManager:
    """Owns in-memory TalkHier session state and session metrics."""

    def __init__(self) -> None:
        self.sessions: dict[str, Any] = {}
        self.session_metrics: dict[str, dict[str, Any]] = {}

    def store_session(self, session_id: str, session: Any) -> None:
        """Store a session by ID."""
        self.sessions[session_id] = session

    def get_session(self, session_id: str) -> Any:
        """Get session by ID with validation."""
        if session_id not in self.sessions:
            raise ValueError(f"Session {session_id} not found")
        return self.sessions[session_id]

    def initialize_metrics(self, session_id: str) -> None:
        """Initialize metrics for a session."""
        self.session_metrics[session_id] = self._new_metrics()

    def ensure_metrics(self, session_id: str) -> dict[str, Any]:
        """Return existing metrics or initialize missing metrics."""
        return self.session_metrics.setdefault(session_id, self._new_metrics())

    def record_round(
        self,
        session_id: str,
        quality_score: float,
        consensus_score: float,
    ) -> None:
        """Record completed-round metrics."""
        metrics = self.ensure_metrics(session_id)
        metrics["rounds_completed"] += 1
        metrics["quality_progression"].append(quality_score)
        metrics["consensus_progression"].append(consensus_score)

    def _new_metrics(self) -> dict[str, Any]:
        """Create default session metrics."""
        return {
            "rounds_completed": 0,
            "quality_progression": [],
            "consensus_progression": [],
            "message_count": 0,
        }


__all__ = ["TalkHierStateManager"]
