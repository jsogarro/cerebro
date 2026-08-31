"""Compatibility contracts for the historical audit log schema."""

from src.models.db.audit_log import AuditLog


def test_event_metadata_uses_the_historical_metadata_column() -> None:
    """Keep the Python event_metadata API backed by audit_logs.metadata."""
    mapped_column = AuditLog.__mapper__.attrs.event_metadata.columns[0]

    assert mapped_column is AuditLog.__table__.columns["metadata"]
    assert mapped_column.name == "metadata"
    assert "event_metadata" not in AuditLog.__table__.columns
