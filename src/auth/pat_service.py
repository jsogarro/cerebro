"""Personal access token generation and per-request validation."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.auth.jwt_service import JWTService, JWTServiceUnavailableError
from src.auth.models import PAT_PREFIX, PersonalAccessTokenPayload
from src.models.db.user import User
from src.repositories.personal_access_token_repository import (
    PersonalAccessTokenRepository,
)

logger = get_logger(__name__)


class PATValidationError(Exception):
    """Raised when a personal access token is invalid, expired, or revoked."""


class PATServiceUnavailableError(RuntimeError):
    """Raised when a store required to validate a personal access token fails."""


def user_claims(user: User) -> tuple[list[str], list[str]]:
    """Roles and permissions derived from an account row at request time."""
    roles = [user.role] if user.role else ["user"]
    if user.is_superuser:
        return [*roles, "admin"], ["*"]
    return roles, []


def hash_personal_access_token(token: str) -> str:
    """Return the 64-character lowercase SHA-256 hex digest of a token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_personal_access_token() -> tuple[str, str, str]:
    """Generate ``(plaintext, sha256_hex, display_prefix)`` with 256 bits of entropy."""
    plaintext = f"{PAT_PREFIX}{secrets.token_urlsafe(32)}"
    return plaintext, hash_personal_access_token(plaintext), plaintext[:16]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class PersonalAccessTokenService:
    """Validate personal access tokens against Postgres and user-wide revocation."""

    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self._session_factory = session_factory

    async def validate_token(
        self,
        token: str,
        *,
        jwt_service: JWTService,
        now: datetime | None = None,
    ) -> PersonalAccessTokenPayload:
        if not token.startswith(PAT_PREFIX):
            raise PATValidationError("Invalid personal access token format")

        current_time = _as_utc(now or datetime.now(UTC))
        digest = hash_personal_access_token(token)

        try:
            async with self._session_factory() as session:
                repo = PersonalAccessTokenRepository(session)
                matched = await repo.lookup_with_user_by_hash(digest)
                if matched is None:
                    raise PATValidationError("Invalid personal access token")

                pat, user = matched
                if pat.revoked_at is not None:
                    raise PATValidationError("Personal access token has been revoked")
                if _as_utc(pat.expires_at) <= current_time:
                    raise PATValidationError("Personal access token has expired")
                if not user.is_active or user.deleted_at is not None:
                    raise PATValidationError("User account is inactive")
                if (
                    pat.organization_id is None
                    or user.organization_id is None
                    or pat.organization_id != user.organization_id
                ):
                    raise PATValidationError(
                        "Personal access token organization mismatch"
                    )

                if jwt_service.redis_client is None:
                    raise PATServiceUnavailableError(
                        "Token revocation store unavailable"
                    )
                try:
                    user_revoked = await jwt_service._is_user_token_revoked(
                        str(user.id), {"iat_ns": pat.issued_at_ns}
                    )
                except JWTServiceUnavailableError as exc:
                    raise PATServiceUnavailableError(str(exc)) from exc
                except Exception as exc:
                    logger.error(
                        "pat_user_revocation_check_failed", error=type(exc).__name__
                    )
                    raise PATServiceUnavailableError(
                        "Token revocation store unavailable"
                    ) from exc

                if user_revoked:
                    raise PATValidationError("Personal access token has been revoked")

                pat.last_used_at = current_time
                await session.commit()

                roles, permissions = user_claims(user)
                return PersonalAccessTokenPayload(
                    sub=str(user.id),
                    email=user.email,
                    roles=roles,
                    permissions=permissions,
                    organization_id=str(pat.organization_id),
                    pat_id=str(pat.id),
                    scopes=list(pat.scopes),
                    iat=_as_utc(pat.created_at),
                    exp=_as_utc(pat.expires_at),
                )
        except (PATValidationError, PATServiceUnavailableError):
            raise
        except Exception as exc:
            logger.error("pat_validation_store_unavailable", error=type(exc).__name__)
            raise PATServiceUnavailableError(
                "Personal access token store unavailable"
            ) from exc


__all__ = [
    "PATServiceUnavailableError",
    "PATValidationError",
    "PersonalAccessTokenService",
    "generate_personal_access_token",
    "hash_personal_access_token",
    "user_claims",
]
