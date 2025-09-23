"""
Base model configuration for all database models.

Provides common fields and functionality for all SQLAlchemy models.
"""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.hybrid import hybrid_property

Base = declarative_base()


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
    id = Column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False
    )

    # Timestamps
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Soft delete
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    # Audit fields
    created_by = Column(String(255), nullable=True)

    updated_by = Column(String(255), nullable=True)

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

    def to_dict(self) -> dict:
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
