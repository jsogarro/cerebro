"""
Base model configuration for all database models.

Provides common fields and functionality for all SQLAlchemy models.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, TypeDecorator, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import CHAR


class PortableUUID(TypeDecorator):
    """Platform-independent UUID type. Uses PostgreSQL UUID on Postgres, CHAR(36) elsewhere."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):  # type: ignore[no-untyped-def]
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):  # type: ignore[no-untyped-def]
        if value is None:
            return value
        if dialect.name == "postgresql":
            return value
        return str(value)

    def process_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
        if value is None:
            return value
        if not isinstance(value, uuid.UUID):
            return uuid.UUID(value)
        return value


# Alias for convenience — use this everywhere instead of postgresql.UUID
UUID = PortableUUID


class Base(DeclarativeBase):
    pass


class BaseModel(Base):
    """
    Abstract base model with common fields.

    Provides:
    - UUID primary key
    - Timestamps (created_at, updated_at)
    - Soft delete support (deleted_at)
    - Audit fields (created_by, updated_by)
    """

    __abstract__ = True

    # Primary key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, default=uuid.uuid4, nullable=False
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Soft delete
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    # Audit fields
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    @hybrid_property
    def is_deleted(self) -> bool:
        """Check if the record is soft deleted."""
        return self.deleted_at is not None

    def soft_delete(self, deleted_by: str | None = None) -> None:
        """
        Soft delete the record.

        Args:
            deleted_by: User who deleted the record
        """
        self.deleted_at = datetime.utcnow()
        if deleted_by:
            self.updated_by = deleted_by

    def restore(self, restored_by: str | None = None) -> None:
        """
        Restore a soft deleted record.

        Args:
            restored_by: User who restored the record
        """
        self.deleted_at = None
        if restored_by:
            self.updated_by = restored_by

    def to_dict(self) -> dict[str, Any]:
        """
        Convert model to dictionary.

        Returns:
            Dictionary representation of the model
        """
        result = {}
        for column in self.__table__.columns:
            value = getattr(self, column.name)
            if isinstance(value, datetime):
                value = value.isoformat()
            elif isinstance(value, uuid.UUID):
                value = str(value)
            result[column.name] = value
        return result

    def __repr__(self) -> str:
        """String representation of the model."""
        return f"<{self.__class__.__name__}(id={self.id})>"
