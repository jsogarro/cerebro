"""Similarity checks over an uploaded document, the matches they keep, and egress audit rows.

All three cascade or detach in the database, because erasure uses core deletes
that never fire ORM cascades. Titles and excerpts are stored encrypted
(``*_enc``). No source URL is stored: the report builds links from validated
ids. ``egress_events`` carries no query terms, URLs or ids by construction, so a
deleted check keeps its audit rows with the link cleared. There is no report
column: the report is rendered on each read.
"""

import uuid
from datetime import datetime
from typing import cast

from sqlalchemy import (
    JSON,
    Boolean,
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

from src.models.db.base import UUID, Base
from src.models.db.job_constants import JOB_STATUSES, RUN_JOB_REASONS

MATCH_KINDS = (
    "verbatim",
    "normalized",
    "paraphrase_candidate",
    "translation_candidate",
)
ATTRIBUTIONS = ("quoted_and_cited", "quoted_uncited", "cited_unquoted", "unattributed")
SOURCE_ORIGINS = ("own_upload", "comparison", "openalex", "arxiv")
SOURCE_TEXT_KINDS = ("full_text", "abstract")
EGRESS_HOSTS = ("api.openalex.org", "arxiv.org")
REQUEST_CLASSES = ("search", "pdf")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class SimilarityCheck(Base):
    __tablename__ = "similarity_checks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey("uploaded_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # Written only by the job runner; why a check is partial lives in ``coverage``.
    status_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    options: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    consent_text_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    consent_text_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The usage_ledger row this run was admitted under, released if it never starts.
    ledger_id: Mapped[uuid.UUID | None] = mapped_column(UUID(), nullable=True)
    coverage: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    policy_versions: Mapped[dict[str, object]] = mapped_column(
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
            _in("status", JOB_STATUSES), name="ck_similarity_checks_status"
        ),
        CheckConstraint(
            f"status_reason IS NULL OR {_in('status_reason', RUN_JOB_REASONS)}",
            name="ck_similarity_checks_status_reason",
        ),
        Index("ix_similarity_checks_document", "document_id"),
        Index("ix_similarity_checks_org_status", "organization_id", "status"),
    )


class SimilarityMatch(Base):
    __tablename__ = "similarity_matches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(), primary_key=True, default=uuid.uuid4)
    check_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey("similarity_checks.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    attribution: Mapped[str] = mapped_column(String(20), nullable=False)
    # Provenance only: the source is a document this user uploaded. Never authorship.
    from_user_upload: Mapped[bool] = mapped_column(Boolean, nullable=False)
    doc_start: Mapped[int] = mapped_column(Integer, nullable=False)
    doc_end: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cosine: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_origin: Mapped[str] = mapped_column(String(20), nullable=False)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(),
        ForeignKey("uploaded_documents.id", ondelete="CASCADE"),
        nullable=True,
    )
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_title_enc: Mapped[str] = mapped_column(Text, nullable=False)
    source_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_licence: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    source_text_kind: Mapped[str] = mapped_column(String(12), nullable=False)
    src_start: Mapped[int] = mapped_column(Integer, nullable=False)
    src_end: Mapped[int] = mapped_column(Integer, nullable=False)
    source_excerpt_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalizations_applied: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    declared_author_overlap: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    section: Mapped[str] = mapped_column(String(10), nullable=False, default="body")

    __table_args__ = (
        CheckConstraint(_in("kind", MATCH_KINDS), name="ck_similarity_matches_kind"),
        CheckConstraint(
            _in("attribution", ATTRIBUTIONS), name="ck_similarity_matches_attribution"
        ),
        CheckConstraint(
            _in("source_origin", SOURCE_ORIGINS),
            name="ck_similarity_matches_source_origin",
        ),
        CheckConstraint(
            _in("source_text_kind", SOURCE_TEXT_KINDS),
            name="ck_similarity_matches_source_text_kind",
        ),
        Index("ix_similarity_matches_check", "check_id"),
        Index("ix_similarity_matches_source_document", "source_document_id"),
    )


class EgressEvent(Base):
    __tablename__ = "egress_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(), primary_key=True, default=uuid.uuid4)
    check_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(),
        ForeignKey("similarity_checks.id", ondelete="SET NULL"),
        nullable=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(), nullable=False)
    host: Mapped[str] = mapped_column(String(40), nullable=False)
    request_class: Mapped[str] = mapped_column(String(10), nullable=False)
    term_count: Mapped[int] = mapped_column(Integer, nullable=False)
    bytes_received: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(_in("host", EGRESS_HOSTS), name="ck_egress_events_host"),
        CheckConstraint(
            _in("request_class", REQUEST_CLASSES), name="ck_egress_events_request_class"
        ),
        Index("ix_egress_events_check", "check_id"),
        Index("ix_egress_events_user", "user_id"),
    )


SIMILARITY_CHECKS = cast(Table, SimilarityCheck.__table__)
