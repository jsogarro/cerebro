"""The episodic-memory tables keep the schema they had as legacy ``Column`` models."""

from __future__ import annotations

import pytest
from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text

from src.ai_brain.memory.episodic_memory import (
    HAS_SQLALCHEMY,
    EpisodicEvent,
    EpisodicSession,
)

pytestmark = pytest.mark.skipif(not HAS_SQLALCHEMY, reason="SQLAlchemy unavailable")

# column -> (type, nullable, indexed)
EVENT_COLUMNS = {
    "id": (String, False, False),
    "session_id": (String, False, True),
    "user_id": (String, True, True),
    "agent_id": (String, True, True),
    "event_type": (String, False, True),
    "event_data": (JSON, False, False),
    "context": (JSON, True, False),
    "timestamp": (DateTime, False, True),
    "duration_ms": (Integer, True, False),
    "sequence_number": (Integer, True, False),
    "success": (Boolean, True, False),
    "quality_score": (Float, True, False),
    "user_feedback": (Text, True, False),
    "tags": (JSON, True, False),
    "event_metadata": (JSON, True, False),
    "created_at": (DateTime, True, False),
}

SESSION_COLUMNS = {
    "session_id": (String, False, False),
    "user_id": (String, True, True),
    "session_type": (String, True, False),
    "start_time": (DateTime, False, True),
    "end_time": (DateTime, True, False),
    "duration_ms": (Integer, True, False),
    "event_count": (Integer, True, False),
    "success_rate": (Float, True, False),
    "avg_quality_score": (Float, True, False),
    "session_outcome": (String, True, False),
    "user_satisfaction": (Float, True, False),
    "notes": (Text, True, False),
    "session_metadata": (JSON, True, False),
    "created_at": (DateTime, True, False),
}


@pytest.mark.parametrize(
    ("model", "expected"),
    [(EpisodicEvent, EVENT_COLUMNS), (EpisodicSession, SESSION_COLUMNS)],
)
def test_columns_keep_their_type_nullability_and_index(model, expected) -> None:
    columns = {c.name: c for c in model.__table__.columns}
    assert set(columns) == set(expected)
    for name, (sql_type, nullable, indexed) in expected.items():
        column = columns[name]
        assert type(column.type) is sql_type, name
        assert column.nullable is nullable, name
        assert bool(column.index) is indexed, name
