"""Owner-scoped persistence for similarity checks, their matches and egress audit rows.

Every read goes through ``scope_to_owner``. Titles and excerpts are encrypted
with the document cipher; a stored value without the encryption prefix is
refused rather than served. ``get`` and ``list_for_document`` fail a stale
queued or running row on read, so a check the process that owned it never
finished does not stay running.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar, cast

from sqlalchemy import Select, case, delete, func, select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.memory_encryption import (
    ENCRYPTED_TEXT_PREFIX,
    DocumentEncryptionUnavailable,
    MemoryEncryption,
)
from src.models.db.job_constants import ACTIVE_STATUSES
from src.models.db.similarity_check import (
    SIMILARITY_CHECKS,
    EgressEvent,
    SimilarityCheck,
    SimilarityMatch,
)
from src.models.db.uploaded_document import UploadedDocument, UsageLedger
from src.repositories.tenant_scope import normalize_organization_id, scope_to_owner
from src.repositories.uploaded_document_repository import UndecryptableDocumentError
from src.services.document_jobs import flip_if_stale, is_stale, stale_cutoff

# Retrieval's 180 s, matching's 120 s, paraphrase's 120 s and a 120 s margin, so the
# stale rule never fails a healthy check.
SIMILARITY_DEADLINE_S = 540
MAX_LIST = 50
MAX_MATCH_PAGE = 200
MAX_REPORT_MATCHES = 1000
ATTRIBUTION_RANK = {
    "unattributed": 0,
    "quoted_uncited": 1,
    "cited_unquoted": 2,
    "quoted_and_cited": 3,
}
_SelectT = TypeVar("_SelectT", bound=Select[Any])
_Model = type[SimilarityCheck] | type[SimilarityMatch] | type[EgressEvent]


@dataclass(frozen=True)
class MatchRecord:
    """One match as the runner hands it over, before encryption."""

    kind: str
    attribution: str
    from_user_upload: bool
    doc_start: int
    doc_end: int
    tokens: int
    source_origin: str
    source_document_id: uuid.UUID | None
    source_id: str
    source_title: str
    source_year: int | None
    source_licence: dict[str, Any]
    source_text_kind: str
    src_start: int
    src_end: int
    source_excerpt: str | None
    normalizations_applied: tuple[str, ...] = ()
    declared_author_overlap: bool = False
    section: str = "body"
    cosine: float | None = None


@dataclass(frozen=True)
class NewCheck:
    document_id: uuid.UUID
    organization_id: uuid.UUID
    user_id: uuid.UUID
    options: dict[str, Any]
    policy_versions: dict[str, Any]
    consent_text_version: str | None = None
    consent_text_sha256: str | None = None
    ledger_id: uuid.UUID | None = None
    open_sources_ledger_id: uuid.UUID | None = None


@dataclass(frozen=True)
class EgressRecord:
    host: str
    request_class: str
    term_count: int
    status: int | str
    bytes_received: int
    duration_ms: int


@dataclass(frozen=True)
class MatchView:
    id: uuid.UUID
    kind: str
    attribution: str
    from_user_upload: bool
    doc_start: int
    doc_end: int
    tokens: int
    cosine: float | None
    source_origin: str
    source_document_id: uuid.UUID | None
    source_id: str
    source_title: str
    source_year: int | None
    source_licence: dict[str, Any]
    source_text_kind: str
    src_start: int
    src_end: int
    source_excerpt: str | None
    normalizations_applied: tuple[str, ...]
    declared_author_overlap: bool
    section: str


@dataclass(frozen=True)
class CheckView:
    id: uuid.UUID
    document_id: uuid.UUID
    status: str
    status_reason: str | None
    options: dict[str, Any]
    consent_text_version: str | None
    consent_text_sha256: str | None
    coverage: dict[str, Any]
    policy_versions: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    matches: tuple[MatchView, ...] = ()
    match_total: int = 0


async def active_similarity_checks(
    session: AsyncSession, organization_id: uuid.UUID, now: datetime
) -> int:
    """Queued or running checks of one org that are not already stale."""
    return int(
        await session.scalar(
            select(func.count())
            .select_from(SimilarityCheck)
            .where(
                SimilarityCheck.organization_id == organization_id,
                SimilarityCheck.status.in_(ACTIVE_STATUSES),
                SimilarityCheck.created_at >= stale_cutoff(now, SIMILARITY_DEADLINE_S),
            )
        )
        or 0
    )


class SimilarityRepository:
    def __init__(self, session: AsyncSession, cipher: MemoryEncryption | None) -> None:
        self._session = session
        self._cipher = cipher

    def _encrypt(self, value: str) -> str:
        if self._cipher is None:
            raise DocumentEncryptionUnavailable("no cipher was supplied")
        return cast(str, self._cipher.encrypt_text(value))

    def _decrypt(self, value: str) -> str:
        if not value.startswith(ENCRYPTED_TEXT_PREFIX):
            raise UndecryptableDocumentError("stored similarity value is not encrypted")
        if self._cipher is None:
            raise DocumentEncryptionUnavailable("no cipher was supplied")
        try:
            return cast(str, self._cipher.decrypt_text(value))
        except ValueError as error:
            raise UndecryptableDocumentError(str(error)) from None

    def _owned(
        self, query: _SelectT, model: _Model, org: uuid.UUID, user: uuid.UUID
    ) -> _SelectT:
        return scope_to_owner(
            query,
            org_column=model.organization_id,
            user_column=model.user_id,
            organization_id=org,
            user_id=user,
        )

    async def create(self, new: NewCheck) -> SimilarityCheck:
        row = SimilarityCheck(
            document_id=new.document_id,
            organization_id=normalize_organization_id(new.organization_id),
            user_id=new.user_id,
            status="queued",
            options=new.options,
            consent_text_version=new.consent_text_version,
            consent_text_sha256=new.consent_text_sha256,
            ledger_id=new.ledger_id,
            open_sources_ledger_id=new.open_sources_ledger_id,
            coverage={},
            policy_versions=new.policy_versions,
            created_at=datetime.now(UTC),
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def add_matches(
        self,
        *,
        check_id: uuid.UUID,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        matches: Iterable[MatchRecord],
    ) -> None:
        for m in matches:
            self._session.add(
                SimilarityMatch(
                    check_id=check_id,
                    organization_id=organization_id,
                    user_id=user_id,
                    kind=m.kind,
                    attribution=m.attribution,
                    from_user_upload=m.from_user_upload,
                    doc_start=m.doc_start,
                    doc_end=m.doc_end,
                    tokens=m.tokens,
                    cosine=m.cosine,
                    source_origin=m.source_origin,
                    source_document_id=m.source_document_id,
                    source_id=m.source_id,
                    source_title_enc=self._encrypt(m.source_title),
                    source_year=m.source_year,
                    source_licence=m.source_licence,
                    source_text_kind=m.source_text_kind,
                    src_start=m.src_start,
                    src_end=m.src_end,
                    source_excerpt_enc=(
                        self._encrypt(m.source_excerpt) if m.source_excerpt else None
                    ),
                    normalizations_applied=list(m.normalizations_applied),
                    declared_author_overlap=m.declared_author_overlap,
                    section=m.section,
                )
            )
        await self._session.flush()

    async def add_egress(
        self,
        event: EgressRecord,
        *,
        check_id: uuid.UUID,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        self._session.add(
            EgressEvent(
                check_id=check_id,
                organization_id=organization_id,
                user_id=user_id,
                host=event.host,
                request_class=event.request_class,
                term_count=event.term_count,
                status=str(event.status),
                bytes_received=event.bytes_received,
                duration_ms=event.duration_ms,
            )
        )
        await self._session.flush()

    async def _fresh(self, row: SimilarityCheck) -> SimilarityCheck | None:
        if is_stale(
            row.status, row.created_at, SIMILARITY_DEADLINE_S, datetime.now(UTC)
        ):
            was_queued = row.status == "queued"
            flipped = await flip_if_stale(
                self._session, SIMILARITY_CHECKS, row, SIMILARITY_DEADLINE_S
            )
            if flipped and was_queued and row.open_sources_ledger_id is not None:
                # The shared guard releases the first ledger row; this is the second.
                await self._session.execute(
                    delete(UsageLedger).where(
                        UsageLedger.id == row.open_sources_ledger_id
                    )
                )
            # Refresh even when the flip lost the race: the job may have just finished.
            try:
                await self._session.refresh(row)
            except InvalidRequestError:
                # Deleted between the flip and the refresh.
                return None
        return row

    def _match(self, row: SimilarityMatch, live: set[uuid.UUID]) -> MatchView:
        gone = row.source_document_id is not None and row.source_document_id not in live
        return MatchView(
            id=row.id,
            kind=row.kind,
            attribution=row.attribution,
            from_user_upload=row.from_user_upload,
            doc_start=row.doc_start,
            doc_end=row.doc_end,
            tokens=row.tokens,
            cosine=row.cosine,
            source_origin=row.source_origin,
            source_document_id=row.source_document_id,
            source_id=row.source_id,
            source_title=(
                "(expired document)" if gone else self._decrypt(row.source_title_enc)
            ),
            source_year=row.source_year,
            source_licence=row.source_licence,
            source_text_kind=row.source_text_kind,
            src_start=row.src_start,
            src_end=row.src_end,
            source_excerpt=(
                self._decrypt(row.source_excerpt_enc)
                if row.source_excerpt_enc and not gone
                else None
            ),
            normalizations_applied=tuple(row.normalizations_applied),
            declared_author_overlap=row.declared_author_overlap,
            section=row.section,
        )

    def _view(
        self,
        row: SimilarityCheck,
        matches: Sequence[SimilarityMatch] = (),
        total: int = 0,
        live: set[uuid.UUID] | None = None,
    ) -> CheckView:
        decoded = [self._match(m, live or set()) for m in matches]
        return CheckView(
            id=row.id,
            document_id=row.document_id,
            status=row.status,
            status_reason=row.status_reason,
            options=row.options,
            consent_text_version=row.consent_text_version,
            consent_text_sha256=row.consent_text_sha256,
            coverage=row.coverage,
            policy_versions=row.policy_versions,
            created_at=row.created_at,
            started_at=row.started_at,
            completed_at=row.completed_at,
            matches=tuple(decoded),
            match_total=total,
        )

    async def _live_sources(
        self,
        matches: Sequence[SimilarityMatch],
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> set[uuid.UUID]:
        """Source documents still readable; an expired one is purged later, not yet gone."""
        wanted = {m.source_document_id for m in matches if m.source_document_id}
        if not wanted:
            return set()
        rows = await self._session.execute(
            scope_to_owner(
                select(UploadedDocument.id).where(
                    UploadedDocument.id.in_(wanted),
                    UploadedDocument.expires_at > datetime.now(UTC),
                ),
                org_column=UploadedDocument.organization_id,
                user_column=UploadedDocument.user_id,
                organization_id=organization_id,
                user_id=user_id,
            )
        )
        return set(rows.scalars())

    async def get(
        self,
        check_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        offset: int = 0,
        limit: int = MAX_MATCH_PAGE,
    ) -> CheckView | None:
        row = (
            await self._session.execute(
                self._owned(
                    select(SimilarityCheck).where(SimilarityCheck.id == check_id),
                    SimilarityCheck,
                    organization_id,
                    user_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        fresh = await self._fresh(row)
        if fresh is None:
            return None
        row = fresh
        matches: list[SimilarityMatch] = []
        total = 0
        if row.status not in ACTIVE_STATUSES:
            total = int(
                await self._session.scalar(
                    self._owned(
                        select(func.count())
                        .select_from(SimilarityMatch)
                        .where(SimilarityMatch.check_id == check_id),
                        SimilarityMatch,
                        organization_id,
                        user_id,
                    )
                )
                or 0
            )
            matches = list(
                (
                    await self._session.execute(
                        self._owned(
                            select(SimilarityMatch)
                            .where(SimilarityMatch.check_id == check_id)
                            .order_by(
                                case(
                                    ATTRIBUTION_RANK, value=SimilarityMatch.attribution
                                ),
                                SimilarityMatch.tokens.desc(),
                                SimilarityMatch.doc_start,
                                SimilarityMatch.id,
                            )
                            .offset(max(0, offset))
                            .limit(max(1, min(limit, MAX_REPORT_MATCHES))),
                            SimilarityMatch,
                            organization_id,
                            user_id,
                        )
                    )
                ).scalars()
            )
        try:
            live = await self._live_sources(matches, organization_id, user_id)
            return self._view(row, matches, total, live)
        except UndecryptableDocumentError:
            # A key rotated away reads as missing, like an undecryptable document.
            return None

    async def list_for_document(
        self,
        document_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        limit: int = MAX_LIST,
    ) -> list[CheckView]:
        rows = (
            await self._session.execute(
                self._owned(
                    select(SimilarityCheck)
                    .where(SimilarityCheck.document_id == document_id)
                    .order_by(SimilarityCheck.created_at.desc())
                    .limit(max(1, min(limit, MAX_LIST))),
                    SimilarityCheck,
                    organization_id,
                    user_id,
                )
            )
        ).scalars()
        fresh = [await self._fresh(row) for row in list(rows)]
        return [self._view(row) for row in fresh if row is not None]

    async def delete(
        self, check_id: uuid.UUID, *, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> bool:
        row = (
            await self._session.execute(
                self._owned(
                    select(SimilarityCheck)
                    .where(SimilarityCheck.id == check_id)
                    # A run starting meanwhile waits for this delete and then finds no row.
                    .with_for_update(),
                    SimilarityCheck,
                    organization_id,
                    user_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True
