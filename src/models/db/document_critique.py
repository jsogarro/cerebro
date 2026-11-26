"""Critique runs over an uploaded document, and the findings they keep.

Both tables cascade from ``uploaded_documents`` in the database, because
erasure uses core deletes that never fire ORM cascades. Quotes and the model's
text are stored encrypted (``*_enc``); nothing here holds document text in the
clear. There is no report column: the report is rendered on each read.
"""

import uuid
from datetime import datetime
from typing import cast

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.agents.critique.roles import ROLES
from src.models.db.base import UUID, Base
from src.models.db.job_constants import JOB_STATUSES, RUN_JOB_REASONS

CRITIQUE_REASONS = (*RUN_JOB_REASONS, "all_roles_failed", "tracing_enabled")
ROLE_NAMES = tuple(role.name for role in ROLES)
SEVERITIES = ("critical", "major", "minor")
MATCH_KINDS = ("exact", "normalized")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class DocumentCritique(Base):
    __tablename__ = "document_critiques"

    id: Mapped[uuid.UUID] = mapped_column(UUID(), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey("uploaded_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model_id: Mapped[str] = mapped_column(String(100), nullable=False)
    temperature: Mapped[float] = mapped_column(Float, nullable=False)
    prompt_versions: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    consent_text_version: Mapped[str] = mapped_column(String(50), nullable=False)
    consent_text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    consent_recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reserved_units: Mapped[int] = mapped_column(Integer, nullable=False)
    role_outcomes: Mapped[dict[str, dict[str, object]]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            _in("status", JOB_STATUSES), name="ck_document_critiques_status"
        ),
        CheckConstraint(
            f"status_reason IS NULL OR {_in('status_reason', CRITIQUE_REASONS)}",
            name="ck_document_critiques_status_reason",
        ),
        Index("ix_document_critiques_document", "document_id"),
        Index("ix_document_critiques_org_status", "organization_id", "status"),
    )


class CritiqueFinding(Base):
    __tablename__ = "critique_findings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(), primary_key=True, default=uuid.uuid4)
    critique_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey("document_critiques.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(), nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    quote_enc: Mapped[str] = mapped_column(Text, nullable=False)
    raw_start: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_end: Mapped[int] = mapped_column(Integer, nullable=False)
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    match_kind: Mapped[str] = mapped_column(String(12), nullable=False)
    second_quote_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    second_raw_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    second_raw_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    second_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    problem_enc: Mapped[str] = mapped_column(Text, nullable=False)
    fix_enc: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(_in("role", ROLE_NAMES), name="ck_critique_findings_role"),
        CheckConstraint(
            _in("severity", SEVERITIES), name="ck_critique_findings_severity"
        ),
        CheckConstraint(
            _in("match_kind", MATCH_KINDS), name="ck_critique_findings_match_kind"
        ),
        Index("ix_critique_findings_critique", "critique_id"),
    )


# ``__table__`` is typed as a bare FromClause; the job guards need a Table.
DOCUMENT_CRITIQUES = cast(Table, DocumentCritique.__table__)
