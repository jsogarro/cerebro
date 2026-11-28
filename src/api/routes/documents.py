"""Upload, list, read and delete the caller's own documents.

No route returns document text. Every read is scoped to the caller's
organization and user; a miss is always 404.
"""

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Message

from src.core.config import settings
from src.middleware.tenant_context import (
    TenantContext,
    get_tenant_context,
    owner_ids,
    set_postgres_tenant_context,
)
from src.models.db.session import SessionDep
from src.repositories.uploaded_document_repository import (
    DocumentMeta,
    UploadedDocumentRepository,
    UsageLedgerRepository,
)
from src.services.document_ingestion import (
    DocumentEncryptionUnavailable,
    DocumentIngestionError,
    document_cipher,
    extract_upload,
    store_document,
)

router = APIRouter(prefix="/documents", tags=["documents"])

_BODY_SLACK = 64 * 1024
_MAX_AUTHORS_FIELD = 4 * 1024
_STATUS_BY_CODE = {
    "too_large": status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    "unsupported_type": status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    "type_mismatch": status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    "extraction_busy": status.HTTP_503_SERVICE_UNAVAILABLE,
}


class DocumentOut(BaseModel):
    id: uuid.UUID
    filename: str
    media_type: str
    char_count: int
    page_count: int | None
    text_sha256: str
    invisible_chars_removed: int
    expires_at: str

    @classmethod
    def of(cls, meta: DocumentMeta) -> "DocumentOut":
        return cls(
            id=meta.id,
            filename=meta.filename,
            media_type=meta.media_type,
            char_count=meta.char_count,
            page_count=meta.page_count,
            text_sha256=meta.text_sha256,
            invisible_chars_removed=meta.invisible_chars_removed,
            expires_at=meta.expires_at.isoformat(),
        )


def _error(code: int, name: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"code": name})


def _require_enabled() -> None:
    if not settings.DOCUMENT_UPLOADS_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


def _require_retention(request: Request) -> None:
    """Refuse to store what nothing is going to expire."""
    if not getattr(request.app.state, "document_purge_active", True):
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "retention_unavailable")


def _repository(session: AsyncSession) -> UploadedDocumentRepository:
    try:
        return UploadedDocumentRepository(session, document_cipher())
    except DocumentEncryptionUnavailable:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE, "document_storage_unavailable"
        ) from None


def _check_content_length(request: Request) -> None:
    header = request.headers.get("content-length")
    if header is None or not header.isdigit():
        raise _error(status.HTTP_411_LENGTH_REQUIRED, "length_required")
    if int(header) > settings.DOCUMENT_MAX_BYTES + _BODY_SLACK:
        raise _error(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "too_large")


def _bounded(request: Request) -> Request:
    """A request whose body stops at the limit, whatever the headers claimed."""
    limit = settings.DOCUMENT_MAX_BYTES + _BODY_SLACK
    received = 0

    async def receive() -> Message:
        nonlocal received
        message = await request._receive()
        received += len(message.get("body", b""))
        if received > limit:
            raise _error(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "too_large")
        return message

    return Request(request.scope, receive)


def _parse_authors(raw: object) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, str) or len(raw) > _MAX_AUTHORS_FIELD:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_authors")
    try:
        authors = json.loads(raw)
    except ValueError:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_authors") from None
    if (
        not isinstance(authors, list)
        or len(authors) > 20
        or not all(isinstance(a, str) and len(a) <= 100 for a in authors)
    ):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_authors")
    return authors


@router.post("", status_code=status.HTTP_201_CREATED, response_model=DocumentOut)
async def upload_document(
    request: Request,
    session: AsyncSession = SessionDep,
    tenant: TenantContext = Depends(get_tenant_context),
) -> DocumentOut:
    _require_enabled()
    _require_retention(request)
    _check_content_length(request)
    ids = owner_ids(tenant)
    repository = _repository(session)

    reserved = await UsageLedgerRepository(session).reserve(
        organization_id=ids.organization_id,
        user_id=ids.user_id,
        kind="upload",
        limit=settings.DOCUMENT_UPLOADS_PER_DAY,
    )
    # Committed before the body is parsed, so a failed extraction still counts.
    await session.commit()
    if not reserved:
        raise _error(status.HTTP_429_TOO_MANY_REQUESTS, "upload_quota_exceeded")

    try:
        form = await _bounded(request).form(max_files=1, max_fields=3)
    except StarletteHTTPException as error:
        if isinstance(error.detail, dict):
            raise
        raise _error(error.status_code, "invalid_multipart") from None
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "file_required")
        authors = _parse_authors(form.get("declared_authors"))
        raw = await upload.read(settings.DOCUMENT_MAX_BYTES + 1)
        try:
            # No pooled connection is held while the body is parsed or the
            # extraction child runs; the session reopens only to store.
            prepared = await extract_upload(
                raw,
                upload.content_type or "",
                upload.filename or "document",
                authors,
            )
        except DocumentIngestionError as error:
            raise _error(
                _STATUS_BY_CODE.get(error.code, status.HTTP_422_UNPROCESSABLE_CONTENT),
                error.code,
            ) from None
    finally:
        await form.close()

    await set_postgres_tenant_context(session, str(ids.organization_id))
    row = await store_document(
        session,
        prepared,
        organization_id=ids.organization_id,
        user_id=ids.user_id,
    )
    meta = await repository.get_meta(
        row.id, organization_id=ids.organization_id, user_id=ids.user_id
    )
    if meta is None:
        raise _error(status.HTTP_500_INTERNAL_SERVER_ERROR, "document_missing")
    await session.commit()
    return DocumentOut.of(meta)


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
    session: AsyncSession = SessionDep,
    tenant: TenantContext = Depends(get_tenant_context),
) -> list[DocumentOut]:
    _require_enabled()
    ids = owner_ids(tenant)
    rows = await _repository(session).list_recent(
        organization_id=ids.organization_id, user_id=ids.user_id, limit=limit
    )
    return [DocumentOut.of(meta) for meta in rows]


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: uuid.UUID,
    session: AsyncSession = SessionDep,
    tenant: TenantContext = Depends(get_tenant_context),
) -> DocumentOut:
    _require_enabled()
    ids = owner_ids(tenant)
    meta = await _repository(session).get_meta(
        document_id, organization_id=ids.organization_id, user_id=ids.user_id
    )
    if meta is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return DocumentOut.of(meta)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    session: AsyncSession = SessionDep,
    tenant: TenantContext = Depends(get_tenant_context),
) -> Response:
    ids = owner_ids(tenant)
    deleted = await UploadedDocumentRepository(session, None).delete(
        document_id, organization_id=ids.organization_id, user_id=ids.user_id
    )
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
