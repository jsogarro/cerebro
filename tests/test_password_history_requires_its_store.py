"""Password history must fail closed when its Redis store is not injected."""

from __future__ import annotations

import pytest

from src.auth.password_service import (
    PasswordHistoryStoreUnavailableError,
    PasswordService,
)


class FakeRedis:
    """Minimal Redis list implementation for password-history tests."""

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.expirations: dict[str, int] = {}

    async def lpush(self, key: str, value: str) -> None:
        self.lists.setdefault(key, []).insert(0, value)

    async def ltrim(self, key: str, start: int, end: int) -> None:
        self.lists[key] = self.lists.get(key, [])[start : end + 1]

    async def expire(self, key: str, seconds: int) -> None:
        self.expirations[key] = seconds

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        values = self.lists.get(key, [])
        return values[start : None if end == -1 else end + 1]


@pytest.fixture
def service_without_store() -> PasswordService:
    """Construct the service with its default, missing history store."""
    return PasswordService(check_breaches=False)


async def test_check_password_history_without_store_fails_loudly(
    service_without_store: PasswordService,
) -> None:
    """A missing store must not be reported as an empty history."""
    with pytest.raises(PasswordHistoryStoreUnavailableError, match="password history"):
        await service_without_store.check_password_history("user-1", "password")


async def test_add_to_password_history_without_store_fails_loudly(
    service_without_store: PasswordService,
) -> None:
    """A missing store must not be reported as a successful write."""
    with pytest.raises(PasswordHistoryStoreUnavailableError, match="password history"):
        await service_without_store.add_to_password_history("user-1", "hash")


async def test_injected_store_records_and_checks_password_history() -> None:
    """An injected store preserves password-history enforcement."""
    fake_redis = FakeRedis()
    service = PasswordService(
        redis_client=fake_redis,  # type: ignore[arg-type]
        bcrypt_rounds=4,
        check_breaches=False,
    )
    password = "Str0ng-password!"
    password_hash = service.hash_password(password)

    await service.add_to_password_history("user-1", password_hash)

    assert await service.check_password_history("user-1", password) is True
    assert await service.check_password_history("user-1", "Different-pass!9") is False
    assert fake_redis.lists[f"{service.history_prefix}user-1"] == [password_hash]
    assert fake_redis.expirations[f"{service.history_prefix}user-1"] == 365 * 24 * 3600
