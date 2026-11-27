"""TOTP (RFC 6238) second factor: secrets, code checks and recovery codes.

A code is consumed by an atomic conditional UPDATE, so a replay or a second
use of a recovery code loses the race in the database rather than in a
read-then-write window.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime

import pyotp
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.memory_encryption import document_cipher
from src.models.db.user import MfaRecoveryCode, User

RECOVERY_CODE_COUNT = 10
# One step either side tolerates clock drift between the authenticator and us.
_DRIFT_STEPS = 1


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, account: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(
        name=account, issuer_name=settings.MFA_ISSUER
    )


def encrypt_secret(secret: str) -> str:
    encrypted = document_cipher().encrypt_text(secret)
    assert encrypted is not None
    return encrypted


def _secret_of(user: User) -> str | None:
    if user.totp_secret_enc is None:
        return None
    return document_cipher().decrypt_text(user.totp_secret_enc)


def matching_step(secret: str, code: str, now: float | None = None) -> int | None:
    """Return the time step ``code`` is valid for, or None."""
    now = time.time() if now is None else now
    totp = pyotp.TOTP(secret)
    current = int(now // totp.interval)
    for step in range(current - _DRIFT_STEPS, current + _DRIFT_STEPS + 1):
        if hmac.compare_digest(totp.at(step * totp.interval), code):
            return step
    return None


def _hash_recovery_code(code: str) -> str:
    # Codes are 40 random bits and single use, so a fast hash is enough.
    return hashlib.sha256(code.strip().lower().encode()).hexdigest()


def new_recovery_codes() -> list[str]:
    return [secrets.token_hex(5) for _ in range(RECOVERY_CODE_COUNT)]


async def store_recovery_codes(db: AsyncSession, user: User, codes: list[str]) -> None:
    for existing in await _recovery_rows(db, user):
        await db.delete(existing)
    db.add_all(
        MfaRecoveryCode(user_id=user.id, code_hash=_hash_recovery_code(c))
        for c in codes
    )


async def _recovery_rows(db: AsyncSession, user: User) -> list[MfaRecoveryCode]:
    result = await db.execute(
        select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id)
    )
    return list(result.scalars())


async def consume_totp(db: AsyncSession, user: User, code: str) -> bool:
    """Accept ``code`` once: only a step newer than the last accepted one passes."""
    secret = _secret_of(user)
    step = matching_step(secret, code) if secret else None
    if step is None:
        return False
    claimed = await db.execute(
        update(User)
        .where(User.id == user.id)
        .where((User.totp_last_step.is_(None)) | (User.totp_last_step < step))
        .values(totp_last_step=step)
    )
    return claimed.rowcount == 1


async def consume_recovery_code(db: AsyncSession, user: User, code: str) -> bool:
    claimed = await db.execute(
        update(MfaRecoveryCode)
        .where(MfaRecoveryCode.user_id == user.id)
        .where(MfaRecoveryCode.code_hash == _hash_recovery_code(code))
        .where(MfaRecoveryCode.used_at.is_(None))
        .values(used_at=datetime.now(UTC))
    )
    return claimed.rowcount == 1


async def verify_second_factor(db: AsyncSession, user: User, code: str) -> bool:
    """Check a TOTP code, falling back to a recovery code."""
    code = code.strip()
    if code.isdigit() and len(code) == 6:
        return await consume_totp(db, user, code)
    return await consume_recovery_code(db, user, code)
