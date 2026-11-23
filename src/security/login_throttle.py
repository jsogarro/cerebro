"""Per-account failed-login lockout on the shared fixed-window counter store."""

from __future__ import annotations

import hashlib

from src.api.middleware.rate_limiter import RateLimitStore


class LoginThrottle:
    """Lock an account identifier after repeated failed logins.

    Keyed on the normalized identifier whether or not an account exists, so the
    lockout is observable only by someone who already knows the identifier and
    never distinguishes a real account from an unknown one. Attempts are counted
    in a fixed window that starts at the first one; once the limit is exceeded
    the identifier stays locked until that window ends. The window is never
    refreshed, so hammering a locked account cannot extend it.
    """

    def __init__(
        self, store: RateLimitStore, *, max_failures: int, lockout_seconds: int
    ) -> None:
        self._store = store
        self._max = max_failures
        self._window = lockout_seconds

    @staticmethod
    def _key(identifier: str) -> str:
        normalized = identifier.strip().casefold()
        return "acct:" + hashlib.sha256(normalized.encode()).hexdigest()

    async def begin(self, identifier: str) -> int:
        """Count a login attempt; return seconds to wait if the identifier is locked.

        The attempt is counted before the password is checked, so concurrent
        guesses are serialised by the store's atomic increment instead of all
        passing a read-then-write check. A successful login calls ``clear``,
        which makes the count a count of consecutive failures.
        """
        count, ttl = await self._store.increment(self._key(identifier), self._window)
        return ttl if count > self._max else 0

    async def clear(self, identifier: str) -> None:
        await self._store.reset(self._key(identifier))
