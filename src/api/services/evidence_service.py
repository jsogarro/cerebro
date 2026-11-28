"""Tenant-scoped read service for run evidence metadata."""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any, Final

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.db.session import tenant_session
from src.repositories.evidence_repository import EvidenceRepository
from src.repositories.tenant_scope import (
    get_run_organization_id,
    normalize_organization_id,
)

DEFAULT_EVIDENCE_LIMIT: Final[int] = 100
MAX_EVIDENCE_LIMIT: Final[int] = 500


class RunNotFoundError(LookupError):
    """Raised when a run does not exist or belongs to another organization."""


class InvalidEvidenceCursorError(ValueError):
    """Raised when a pagination cursor cannot be decoded into (acquired_at, id)."""


class RunEvidenceItem(BaseModel):
    """Metadata-only view of one persisted evidence row (no snapshot body)."""

    evidence_id: str
    source_type: str
    source_uri: str
    locator: str
    content_sha256: str
    trust: str
    acquired_at: str


class RunEvidencePage(BaseModel):
    """One keyset-paginated slice of a run's evidence metadata."""

    run_id: str
    items: list[RunEvidenceItem]
    next_cursor: str | None = None
    has_more: bool = False


def _encode_cursor(acquired_at: datetime, row_id: uuid.UUID) -> str:
    payload = json.dumps(
        {"acquired_at": acquired_at.isoformat(), "id": str(row_id)},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("cursor payload must be a JSON object")
        acquired_at = datetime.fromisoformat(str(data["acquired_at"]))
        row_id = uuid.UUID(str(data["id"]))
        return acquired_at, row_id
    except (ValueError, KeyError, TypeError) as exc:
        raise InvalidEvidenceCursorError("Invalid evidence pagination cursor") from exc


async def list_run_evidence(
    session: AsyncSession,
    run_id: str,
    *,
    organization_id: str,
    cursor: str | None = None,
    limit: int = DEFAULT_EVIDENCE_LIMIT,
) -> RunEvidencePage:
    """Return metadata-only evidence rows for ``run_id`` within ``organization_id``."""
    normalized_org = normalize_organization_id(organization_id)
    if limit < 1 or limit > MAX_EVIDENCE_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_EVIDENCE_LIMIT}")

    after = _decode_cursor(cursor) if cursor is not None else None

    run_org = await get_run_organization_id(session, run_id)
    if run_org is None or run_org != normalized_org:
        raise RunNotFoundError("Run not found")

    rows = await EvidenceRepository(session).list_evidence_page_for_run(
        run_id,
        organization_id=normalized_org,
        after=after,
        limit=limit,
    )
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = (
        _encode_cursor(page_rows[-1].acquired_at, page_rows[-1].id)
        if has_more and page_rows
        else None
    )
    items = [
        RunEvidenceItem(
            evidence_id=row.evidence_id,
            source_type=row.source_type,
            source_uri=row.source_uri,
            locator=row.locator,
            content_sha256=row.content_sha256,
            trust=row.trust,
            acquired_at=row.acquired_at.isoformat(),
        )
        for row in page_rows
    ]
    return RunEvidencePage(
        run_id=run_id,
        items=items,
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def list_run_evidence_with_factory(
    session_factory: Callable[[], Any],
    run_id: str,
    *,
    organization_id: str,
    cursor: str | None = None,
    limit: int = DEFAULT_EVIDENCE_LIMIT,
) -> RunEvidencePage:
    """Open a tenant-scoped session and list evidence metadata for ``run_id``."""
    normalized_org = normalize_organization_id(organization_id)
    async with tenant_session(session_factory, normalized_org) as session:
        return await list_run_evidence(
            session,
            run_id,
            organization_id=str(normalized_org),
            cursor=cursor,
            limit=limit,
        )


__all__ = [
    "DEFAULT_EVIDENCE_LIMIT",
    "MAX_EVIDENCE_LIMIT",
    "InvalidEvidenceCursorError",
    "RunEvidenceItem",
    "RunEvidencePage",
    "RunNotFoundError",
    "list_run_evidence",
    "list_run_evidence_with_factory",
]
