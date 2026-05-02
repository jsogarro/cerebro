"""Fixed-window API rate limiting middleware."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import redis.asyncio as redis
import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.status import HTTP_429_TOO_MANY_REQUESTS

from src.api.middleware.error_envelope import build_error_payload

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class RateLimitResult:
    """Rate-limit check result."""

    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int


class RateLimitStore(Protocol):
    """Storage contract for fixed-window rate counters."""

    async def increment(
        self,
        key: str,
        window_seconds: int,
    ) -> tuple[int, int]:
        """Increment a counter and return count plus seconds until reset."""


class InMemoryRateLimitStore:
    """Small in-process fixed-window counter store."""

    def __init__(self, now: Callable[[], float] | None = None) -> None:
        self._now = now or time.time
        self._counters: dict[str, tuple[int, float]] = {}

    async def increment(
        self,
        key: str,
        window_seconds: int,
    ) -> tuple[int, int]:
        now = self._now()
        count, reset_at = self._counters.get(key, (0, now + window_seconds))
        if reset_at <= now:
            count = 0
            reset_at = now + window_seconds
        count += 1
        self._counters[key] = (count, reset_at)
        return count, max(1, int(reset_at - now))


class RedisRateLimitStore:
    """Redis-backed fixed-window counter store."""

    def __init__(self, redis_url: str, key_prefix: str = "rate_limit") -> None:
        self._redis_url = redis_url
        self._key_prefix = key_prefix
        self._client: redis.Redis[Any] | None = None

    async def increment(
        self,
        key: str,
        window_seconds: int,
    ) -> tuple[int, int]:
        client = await self._get_client()
        redis_key = f"{self._key_prefix}:{key}"
        count = await client.incr(redis_key)
        if int(count) == 1:
            await client.expire(redis_key, window_seconds)
        ttl = await client.ttl(redis_key)
        return int(count), max(1, int(ttl))

    async def _get_client(self) -> redis.Redis[Any]:
        if self._client is None:
            self._client = redis.from_url(
                self._redis_url,
                socket_connect_timeout=0.2,
                socket_timeout=0.2,
                decode_responses=True,
            )
        return self._client


class ResilientRateLimitStore:
    """Redis-primary store with local fallback when Redis is unavailable."""

    def __init__(
        self,
        primary: RateLimitStore,
        fallback: RateLimitStore | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback or InMemoryRateLimitStore()

    async def increment(
        self,
        key: str,
        window_seconds: int,
    ) -> tuple[int, int]:
        try:
            return await self._primary.increment(key, window_seconds)
        except Exception as exc:
            logger.warning("Rate-limit primary store unavailable", error=str(exc))
            return await self._fallback.increment(key, window_seconds)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply per-user or per-client fixed-window rate limits."""

    def __init__(
        self,
        app: Any,
        store: RateLimitStore | None = None,
        requests_per_minute: int = 100,
        window_seconds: int = 60,
        enabled: bool = True,
        exclude_paths: tuple[str, ...] = (
            "/health",
            "/ready",
            "/live",
            "/metrics",
            "/docs",
            "/openapi.json",
        ),
    ) -> None:
        super().__init__(app)
        self.store = store or InMemoryRateLimitStore()
        self.requests_per_minute = requests_per_minute
        self.window_seconds = window_seconds
        self.enabled = enabled
        self.exclude_paths = exclude_paths

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not self.enabled or self._is_excluded(request):
            return await call_next(request)

        result = await self.check_request(request)
        if not result.allowed:
            return JSONResponse(
                status_code=HTTP_429_TOO_MANY_REQUESTS,
                content=build_error_payload(
                    code="RATE_LIMIT_EXCEEDED",
                    message="Rate limit exceeded",
                    details={
                        "limit": result.limit,
                        "remaining": result.remaining,
                        "reset_seconds": result.reset_seconds,
                    },
                ),
                headers={
                    "Retry-After": str(result.reset_seconds),
                    "X-RateLimit-Limit": str(result.limit),
                    "X-RateLimit-Remaining": str(result.remaining),
                    "X-RateLimit-Reset": str(result.reset_seconds),
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(result.limit)
        response.headers["X-RateLimit-Remaining"] = str(result.remaining)
        response.headers["X-RateLimit-Reset"] = str(result.reset_seconds)
        return response

    async def check_request(self, request: Request) -> RateLimitResult:
        """Check and increment the requester's current rate-limit window."""
        identifier = self._identifier_for_request(request)
        count, reset_seconds = await self.store.increment(
            identifier,
            self.window_seconds,
        )
        remaining = max(0, self.requests_per_minute - count)
        return RateLimitResult(
            allowed=count <= self.requests_per_minute,
            limit=self.requests_per_minute,
            remaining=remaining,
            reset_seconds=reset_seconds,
        )

    def _identifier_for_request(self, request: Request) -> str:
        user_id = getattr(request.state, "user_id", None)
        if user_id:
            return f"user:{user_id}"
        api_key = request.headers.get("X-API-Key")
        if api_key:
            return f"api_key:{api_key}"
        client_host = request.client.host if request.client else "unknown"
        return f"ip:{client_host}"

    def _is_excluded(self, request: Request) -> bool:
        return any(request.url.path.startswith(path) for path in self.exclude_paths)

