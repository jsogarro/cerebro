"""Owner-scoped persistence for critique runs and their findings.

Every read goes through ``scope_to_owner``. Findings are the only place a quote
or model-written text is stored, encrypted with the document cipher; a stored
value without the encryption prefix is refused rather than served. ``get`` and
``list_for_document`` fail a stale queued or running row on read, so a job the
process that owned it never finished does not stay running.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar, cast

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.critique.panel import KeptIssue
from src.core.memory_encryption import (
    ENCRYPTED_TEXT_PREFIX,
    DocumentEncryptionUnavailable,
    MemoryEncryption,
)
from src.models.db.document_critique import (
    DOCUMENT_CRITIQUES,
    CritiqueFinding,
    DocumentCritique,
)
from src.repositories.tenant_scope import normalize_organization_id, scope_to_owner
from src.repositories.uploaded_document_repository import UndecryptableDocumentError
from src.services.document_jobs import flip_if_stale, stale_cutoff

CRITIQUE_DEADLINE_S = 900
MAX_LIST = 50
_SEVERITY_RANK = {"critical": 0, "major": 1, "minor": 2}
_SelectT = TypeVar("_SelectT", bound=Select[Any])


@dataclass(frozen=True)
class FindingView:
    role: str
    category: str
    severity: str
    quote: str
    raw_start: int
    raw_end: int
    page: int
    match_kind: str
    problem: str
    fix: str
    second_quote: str | None = None
    second_raw_start: int | None = None
    second_raw_end: int | None = None
    second_page: int | None = None


@dataclass(frozen=True)
class CritiqueView:
    id: uuid.UUID
    document_id: uuid.UUID
    status: str
    status_reason: str | None
    model_id: str
    temperature: float
    prompt_versions: dict[str, str]
    consent_text_version: str
    consent_text_sha256: str
    role_outcomes: dict[str, dict[str, Any]]
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    findings: tuple[FindingView, ...] = ()


async def active_model_jobs(
    session: AsyncSession, organization_id: uuid.UUID, now: datetime
) -> int:
    """Queued or running critiques of one org that are not already stale."""
    return int(
        await session.scalar(
            select(func.count())
            .select_from(DocumentCritique)
            .where(
                DocumentCritique.organization_id == organization_id,
                DocumentCritique.status.in_(("queued", "running")),
                DocumentCritique.created_at >= stale_cutoff(now, CRITIQUE_DEADLINE_S),
            )
        )
        or 0
    )


class CritiqueRepository:
    def __init__(self, session: AsyncSession, cipher: MemoryEncryption | None) -> None:
        self._session = session
        self._cipher = cipher

    def _encrypt(self, value: str) -> str:
        if self._cipher is None:
            raise DocumentEncryptionUnavailable("no cipher was supplied")
        return cast(str, self._cipher.encrypt_text(value))

    def _decrypt(self, value: str) -> str:
        if not value.startswith(ENCRYPTED_TEXT_PREFIX):
            raise ValueError("stored critique value is not encrypted")
        if self._cipher is None:
            raise DocumentEncryptionUnavailable("no cipher was supplied")
        try:
            return cast(str, self._cipher.decrypt_text(value))
        except ValueError as error:
            raise UndecryptableDocumentError(str(error)) from None

    def _owned(
        self,
        query: _SelectT,
        column_org: Any,
        column_user: Any,
        org: uuid.UUID,
        user: uuid.UUID,
    ) -> _SelectT:
        return scope_to_owner(
            query,
            org_column=column_org,
            user_column=column_user,
            organization_id=org,
            user_id=user,
        )

    def _owned_critiques(
        self, query: _SelectT, org: uuid.UUID, user: uuid.UUID
    ) -> _SelectT:
        return self._owned(
            query,
            DocumentCritique.organization_id,
            DocumentCritique.user_id,
            org,
            user,
        )

    async def create(
        self,
        *,
        document_id: uuid.UUID,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        model_id: str,
        temperature: float,
        prompt_versions: dict[str, str],
        consent_text_version: str,
        consent_text_sha256: str,
        reserved_units: int,
    ) -> DocumentCritique:
        now = datetime.now(UTC)
        row = DocumentCritique(
            document_id=document_id,
            organization_id=normalize_organization_id(organization_id),
            user_id=user_id,
            status="queued",
            model_id=model_id,
            temperature=temperature,
            prompt_versions=prompt_versions,
            consent_text_version=consent_text_version,
            consent_text_sha256=consent_text_sha256,
            consent_recorded_at=now,
            reserved_units=reserved_units,
            role_outcomes={},
            created_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def add_findings(
        self,
        *,
        critique_id: uuid.UUID,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        issues: Iterable[KeptIssue],
    ) -> None:
        for issue in issues:
            self._session.add(
                CritiqueFinding(
                    critique_id=critique_id,
                    organization_id=organization_id,
                    user_id=user_id,
                    role=issue.role,
                    category=issue.category,
                    severity=issue.severity,
                    quote_enc=self._encrypt(issue.quote),
                    raw_start=issue.raw_start,
                    raw_end=issue.raw_end,
                    page=issue.page,
                    match_kind=issue.match_kind,
                    second_quote_enc=(
                        self._encrypt(issue.second_quote)
                        if issue.second_quote
                        else None
                    ),
                    second_raw_start=issue.second_raw_start,
                    second_raw_end=issue.second_raw_end,
                    second_page=issue.second_page,
                    problem_enc=self._encrypt(issue.problem),
                    fix_enc=self._encrypt(issue.fix),
                )
            )
        await self._session.flush()

    async def _fresh(self, row: DocumentCritique) -> DocumentCritique:
        if await flip_if_stale(
            self._session, DOCUMENT_CRITIQUES, row, CRITIQUE_DEADLINE_S
        ):
            await self._session.refresh(row)
        return row

    def _view(
        self, row: DocumentCritique, findings: Sequence[CritiqueFinding] = ()
    ) -> CritiqueView:
        decoded = sorted(
            (self._finding(f) for f in findings),
            key=lambda f: (_SEVERITY_RANK[f.severity], f.raw_start),
        )
        return CritiqueView(
            id=row.id,
            document_id=row.document_id,
            status=row.status,
            status_reason=row.status_reason,
            model_id=row.model_id,
            temperature=row.temperature,
            prompt_versions=row.prompt_versions,
            consent_text_version=row.consent_text_version,
            consent_text_sha256=row.consent_text_sha256,
            role_outcomes=row.role_outcomes,
            created_at=row.created_at,
            started_at=row.started_at,
            completed_at=row.completed_at,
            findings=tuple(decoded),
        )

    def _finding(self, row: CritiqueFinding) -> FindingView:
        return FindingView(
            role=row.role,
            category=row.category,
            severity=row.severity,
            quote=self._decrypt(row.quote_enc),
            raw_start=row.raw_start,
            raw_end=row.raw_end,
            page=row.page,
            match_kind=row.match_kind,
            problem=self._decrypt(row.problem_enc),
            fix=self._decrypt(row.fix_enc),
            second_quote=(
                self._decrypt(row.second_quote_enc) if row.second_quote_enc else None
            ),
            second_raw_start=row.second_raw_start,
            second_raw_end=row.second_raw_end,
            second_page=row.second_page,
        )

    async def get(
        self, critique_id: uuid.UUID, *, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> CritiqueView | None:
        row = (
            await self._session.execute(
                self._owned_critiques(
                    select(DocumentCritique).where(DocumentCritique.id == critique_id),
                    organization_id,
                    user_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        row = await self._fresh(row)
        findings = (
            await self._session.execute(
                self._owned(
                    select(CritiqueFinding).where(
                        CritiqueFinding.critique_id == critique_id
                    ),
                    CritiqueFinding.organization_id,
                    CritiqueFinding.user_id,
                    organization_id,
                    user_id,
                )
            )
        ).scalars()
        try:
            return self._view(row, list(findings))
        except UndecryptableDocumentError:
            return None

    async def list_for_document(
        self,
        document_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        limit: int = MAX_LIST,
    ) -> list[CritiqueView]:
        rows = (
            await self._session.execute(
                self._owned_critiques(
                    select(DocumentCritique)
                    .where(DocumentCritique.document_id == document_id)
                    .order_by(DocumentCritique.created_at.desc())
                    .limit(max(1, min(limit, MAX_LIST))),
                    organization_id,
                    user_id,
                )
            )
        ).scalars()
        return [self._view(await self._fresh(row)) for row in list(rows)]

    async def delete(
        self, critique_id: uuid.UUID, *, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> bool:
        row = (
            await self._session.execute(
                self._owned_critiques(
                    select(DocumentCritique).where(DocumentCritique.id == critique_id),
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
