"""Owner-scoped persistence for uploaded documents.

Every method takes the caller's organization and user and filters through
``scope_to_owner``; ``purge_expired`` is the one system-wide operation.
``get_meta`` and ``get_text`` are the only places a document column is
decrypted.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar, cast

from sqlalchemy import Select, delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from src.core.acquisition.pdf_text import ExtractedText, PageSpan
from src.core.memory_encryption import ENCRYPTED_TEXT_PREFIX, MemoryEncryption
from src.models.db.uploaded_document import UploadedDocument
from src.repositories.tenant_scope import (
    MissingOrganizationContextError,
    normalize_organization_id,
    scope_to_owner,
)

MAX_LIST = 50
_BODY_COLUMNS = (defer(UploadedDocument.text_enc), defer(UploadedDocument.pages))
_SelectT = TypeVar("_SelectT", bound=Select[Any])


@dataclass(frozen=True)
class DocumentMeta:
    id: uuid.UUID
    filename: str
    media_type: str
    declared_authors: list[str]
    char_count: int
    page_count: int | None
    extractor_version: str
    invisible_chars_removed: int
    source_sha256: str
    text_sha256: str
    expires_at: datetime
    created_at: datetime


class UploadedDocumentRepository:
    def __init__(self, session: AsyncSession, cipher: MemoryEncryption) -> None:
        self._session = session
        self._cipher = cipher

    def _decrypt(self, value: str) -> str:
        if not value.startswith(ENCRYPTED_TEXT_PREFIX):
            raise ValueError("stored document value is not encrypted")
        return cast(str, self._cipher.decrypt_text(value))

    def _owned(self, query: _SelectT, org: uuid.UUID, user: uuid.UUID) -> _SelectT:
        return scope_to_owner(
            query,
            org_column=UploadedDocument.organization_id,
            user_column=UploadedDocument.user_id,
            organization_id=org,
            user_id=user,
        )

    def _meta(self, row: UploadedDocument) -> DocumentMeta:
        return DocumentMeta(
            id=row.id,
            filename=self._decrypt(row.filename_enc),
            media_type=row.media_type,
            declared_authors=json.loads(self._decrypt(row.declared_authors_enc)),
            char_count=row.char_count,
            page_count=row.page_count,
            extractor_version=row.extractor_version,
            invisible_chars_removed=row.invisible_chars_removed,
            source_sha256=row.source_sha256,
            text_sha256=row.text_sha256,
            expires_at=row.expires_at,
            created_at=row.created_at,
        )

    async def create(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        filename: str,
        media_type: str,
        extracted: ExtractedText,
        extractor_version: str,
        invisible_chars_removed: int,
        declared_authors: list[str],
        expires_at: datetime,
        page_count: int | None = None,
    ) -> UploadedDocument:
        if user_id is None:
            raise MissingOrganizationContextError(
                "an upload needs an authenticated user"
            )
        encrypt = self._cipher.encrypt_text
        row = UploadedDocument(
            organization_id=normalize_organization_id(organization_id),
            user_id=user_id,
            filename_enc=encrypt(filename),
            media_type=media_type,
            source_sha256=extracted.source_sha256,
            text_sha256=extracted.text_sha256,
            char_count=len(extracted.text),
            page_count=page_count,
            pages=[[s.page, s.start, s.end] for s in extracted.pages],
            extractor_version=extractor_version,
            invisible_chars_removed=invisible_chars_removed,
            text_enc=encrypt(extracted.text),
            declared_authors_enc=encrypt(json.dumps(declared_authors)),
            expires_at=expires_at,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def _get(
        self,
        document_id: uuid.UUID,
        org: uuid.UUID,
        user: uuid.UUID,
        *,
        body: bool = False,
    ) -> UploadedDocument | None:
        query = select(UploadedDocument).where(
            UploadedDocument.id == document_id,
            UploadedDocument.expires_at > datetime.now(UTC),
        )
        if not body:
            query = query.options(*_BODY_COLUMNS)
        query = self._owned(query, org, user)
        return (await self._session.execute(query)).scalar_one_or_none()

    async def get_meta(
        self, document_id: uuid.UUID, *, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> DocumentMeta | None:
        row = await self._get(document_id, organization_id, user_id)
        return None if row is None else self._meta(row)

    async def get_text(
        self, document_id: uuid.UUID, *, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> ExtractedText | None:
        row = await self._get(document_id, organization_id, user_id, body=True)
        if row is None:
            return None
        return ExtractedText(
            text=self._decrypt(row.text_enc),
            pages=tuple(PageSpan(*span) for span in row.pages),
            text_sha256=row.text_sha256,
            source_sha256=row.source_sha256,
        )

    async def list_recent(
        self, *, organization_id: uuid.UUID, user_id: uuid.UUID, limit: int = MAX_LIST
    ) -> list[DocumentMeta]:
        query = self._owned(
            select(UploadedDocument)
            .where(UploadedDocument.expires_at > datetime.now(UTC))
            .options(*_BODY_COLUMNS)
            .order_by(UploadedDocument.created_at.desc())
            .limit(max(1, min(limit, MAX_LIST))),
            organization_id,
            user_id,
        )
        rows = (await self._session.execute(query)).scalars()
        return [self._meta(row) for row in rows]

    async def delete(
        self, document_id: uuid.UUID, *, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> bool:
        row = await self._get(document_id, organization_id, user_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def purge_expired(self, now: datetime) -> int:
        """Delete every expired document across all owners.

        On Postgres the row policy admits this only when the transaction says
        it is the sweep, and then only for expired rows.
        """
        if self._session.get_bind().dialect.name == "postgresql":
            await self._session.execute(
                text("SELECT set_config('app.purge_expired', 'on', true)")
            )
        result = await self._session.execute(
            delete(UploadedDocument).where(UploadedDocument.expires_at <= now)
        )
        return result.rowcount  # type: ignore[attr-defined,no-any-return]
