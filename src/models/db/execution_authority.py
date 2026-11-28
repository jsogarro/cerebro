"""Operator-registered execution authorities.

An authority is the trusted, serialized binding a run is admitted under. It is
registered once, per tenant, ahead of any run, and never changes: admission
reads it and writes the run's own immutable config snapshot.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    String,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.models.db.append_only import register_append_only
from src.models.db.base import UUID, Base


@register_append_only
class AgentExecutionAuthority(Base):
    __tablename__ = "agent_execution_authorities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(), nullable=False)
    authority_id: Mapped[str] = mapped_column(String(255), nullable=False)
    authority_version: Mapped[str] = mapped_column(String(100), nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    is_template: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "authority_id",
            "authority_version",
            name="uq_agent_execution_authority_identity",
        ),
        CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_agent_execution_authority_digest",
        ),
    )
