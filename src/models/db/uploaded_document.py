"""Owner-private uploaded documents and the usage ledger that meters them.

Both tables cascade from ``users``, so erasure leaves nothing behind even for an
upload still in flight. Text, filename and declared authors are stored encrypted (``*_enc``); the
original bytes are discarded after extraction. ``usage_ledger`` rows carry ids
and numbers only and outlive the documents they count, so deleting and
re-uploading cannot reset a quota.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.models.db.base import UUID, Base

LEDGER_KINDS = (
    "upload",
    "critique",
    "similarity_check",
    "similarity_open_sources",
    "revision",
)
DOCUMENT_MEDIA_TYPES = ("pdf", "markdown", "plain")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class UploadedDocument(Base):
    __tablename__ = "uploaded_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    filename_enc: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pages: Mapped[list[list[int]]] = mapped_column(JSON, nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(100), nullable=False)
    invisible_chars_removed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    text_enc: Mapped[str] = mapped_column(Text, nullable=False)
    declared_authors_enc: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            _in("media_type", DOCUMENT_MEDIA_TYPES),
            name="ck_uploaded_documents_media_type",
        ),
        CheckConstraint("char_count > 0", name="ck_uploaded_documents_char_count"),
        Index(
            "ix_uploaded_documents_owner_created",
            "organization_id",
            "user_id",
            "created_at",
        ),
        Index("ix_uploaded_documents_expires_at", "expires_at"),
    )


class UsageLedger(Base):
    __tablename__ = "usage_ledger"

    id: Mapped[uuid.UUID] = mapped_column(UUID(), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    units: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(_in("kind", LEDGER_KINDS), name="ck_usage_ledger_kind"),
        Index(
            "ix_usage_ledger_owner_kind_created",
            "organization_id",
            "user_id",
            "kind",
            "created_at",
        ),
        # Global budget checks filter by kind and window across every org.
        Index(
            "ix_usage_ledger_kind_created",
            "kind",
            "created_at",
            postgresql_include=["units"],
        ),
    )
