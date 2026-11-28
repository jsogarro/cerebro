"""Tenant-scoped persistence for personal access tokens."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import PAT_ALLOWED_SCOPES, PAT_MAX_LIFETIME_DAYS
from src.models.db.personal_access_token import PersonalAccessToken
from src.models.db.user import User
from src.repositories.tenant_scope import normalize_organization_id


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class PersonalAccessTokenRepository:
    """Repository for personal access tokens with fail-closed tenant scoping."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        organization_id: uuid.UUID | str | None,
        name: str,
        token_hash: str,
        token_prefix: str,
        scopes: list[str],
        issued_at_ns: int,
        created_at: datetime,
        expires_at: datetime,
    ) -> PersonalAccessToken:
        org_uuid = normalize_organization_id(organization_id)
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Token name must not be empty")
        if not scopes or any(scope not in PAT_ALLOWED_SCOPES for scope in scopes):
            raise ValueError("Invalid personal access token scopes")

        created_utc = _as_utc(created_at)
        expires_utc = _as_utc(expires_at)
        if expires_utc <= created_utc:
            raise ValueError("Token expiry must be after creation time")
        if expires_utc > created_utc + timedelta(days=PAT_MAX_LIFETIME_DAYS):
            raise ValueError(
                f"Token lifetime must not exceed {PAT_MAX_LIFETIME_DAYS} days"
            )

        record = PersonalAccessToken(
            user_id=user_id,
            organization_id=org_uuid,
            name=cleaned_name,
            token_hash=token_hash,
            token_prefix=token_prefix,
            scopes=list(dict.fromkeys(scopes)),
            issued_at_ns=issued_at_ns,
            created_at=created_utc,
            expires_at=expires_utc,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        organization_id: uuid.UUID | str | None,
    ) -> list[PersonalAccessToken]:
        org_uuid = normalize_organization_id(organization_id)
        result = await self.session.execute(
            select(PersonalAccessToken)
            .where(
                PersonalAccessToken.organization_id == org_uuid,
                PersonalAccessToken.user_id == user_id,
            )
            .order_by(PersonalAccessToken.created_at.desc())
        )
        return list(result.scalars().all())

    async def revoke(
        self,
        pat_id: uuid.UUID,
        *,
        organization_id: uuid.UUID | str | None,
        user_id: uuid.UUID | None,
        revoked_at: datetime | None = None,
    ) -> PersonalAccessToken | None:
        org_uuid = normalize_organization_id(organization_id)
        query = select(PersonalAccessToken).where(
            PersonalAccessToken.id == pat_id,
            PersonalAccessToken.organization_id == org_uuid,
        )
        if user_id is not None:
            query = query.where(PersonalAccessToken.user_id == user_id)

        result = await self.session.execute(query)
        record = result.scalar_one_or_none()
        if record is None:
            return None
        if record.revoked_at is None:
            record.revoked_at = _as_utc(revoked_at or datetime.now(UTC))
            await self.session.flush()
        return record

    async def lookup_with_user_by_hash(
        self,
        token_hash: str,
    ) -> tuple[PersonalAccessToken, User] | None:
        result = await self.session.execute(
            select(PersonalAccessToken, User)
            .join(User, User.id == PersonalAccessToken.user_id)
            .where(PersonalAccessToken.token_hash == token_hash)
        )
        row = result.first()
        if row is None:
            return None
        return row[0], row[1]


__all__ = ["PersonalAccessTokenRepository"]
