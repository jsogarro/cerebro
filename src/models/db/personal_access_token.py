"""Personal access tokens for long-lived, tenant-bound MCP client authentication."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.models.db.base import UUID, Base


class PersonalAccessToken(Base):
    """Hashed personal access token bound to one user and one organization."""

    __tablename__ = "personal_access_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    issued_at_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "organization_id IS NOT NULL",
            name="ck_pat_organization_id_not_null",
        ),
        CheckConstraint("length(trim(name)) > 0", name="ck_pat_name_non_empty"),
        CheckConstraint("length(token_hash) = 64", name="ck_pat_token_hash_len"),
        CheckConstraint(
            "length(token_prefix) > 0", name="ck_pat_token_prefix_non_empty"
        ),
        CheckConstraint("issued_at_ns > 0", name="ck_pat_issued_at_ns_positive"),
        CheckConstraint("expires_at > created_at", name="ck_pat_expires_after_created"),
        Index("uq_personal_access_tokens_token_hash", "token_hash", unique=True),
        Index("ix_personal_access_tokens_org_user", "organization_id", "user_id"),
        Index("ix_personal_access_tokens_user_id", "user_id"),
    )


__all__ = ["PersonalAccessToken"]
