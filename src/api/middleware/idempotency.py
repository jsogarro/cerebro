"""Idempotency middleware for expensive POST query endpoints."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol, cast

import redis.asyncio as redis
import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Message

logger = structlog.get_logger(__name__)

IDEMPOTENCY_HEADER = "Idempotency-Key"
DEFAULT_IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class IdempotencyRecord:
    """Cached idempotent response."""

    status_code: int
    headers: dict[str, str]
    body: bytes
    media_type: str | None = None


class IdempotencyStore(Protocol):
    """Storage contract for idempotent responses."""

    async def get(self, key: str) -> IdempotencyRecord | None:
        """Return a cached response if present."""

    async def set(
        self,
        key: str,
        record: IdempotencyRecord,
        ttl_seconds: int,
    ) -> None:
        """Cache a response."""


class InMemoryIdempotencyStore:
    """Small in-process store used for tests and Redis fallback."""

    def __init__(self, now: Callable[[], float] | None = None) -> None:
        self._records: dict[str, tuple[float, IdempotencyRecord]] = {}
        self._now = now or time.time

    async def get(self, key: str) -> IdempotencyRecord | None:
        cached = self._records.get(key)
        if cached is None:
            return None
        expires_at, record = cached
        if expires_at <= self._now():
            self._records.pop(key, None)
            return None
        return record

    async def set(
        self,
        key: str,
        record: IdempotencyRecord,
        ttl_seconds: int,
    ) -> None:
        self._records[key] = (self._now() + ttl_seconds, record)


class RedisIdempotencyStore:
    """Redis-backed idempotency store."""

    def __init__(self, redis_url: str, key_prefix: str = "idempotency") -> None:
        self._redis_url = redis_url
        self._key_prefix = key_prefix
        self._client: redis.Redis[Any] | None = None

    async def get(self, key: str) -> IdempotencyRecord | None:
        client = await self._get_client()
        raw = await client.get(self._redis_key(key))
        if raw is None:
            return None
        payload = json.loads(raw)
        return IdempotencyRecord(
            status_code=int(payload["status_code"]),
            headers=dict(payload["headers"]),
            body=base64.b64decode(payload["body"]),
            media_type=payload.get("media_type"),
        )

    async def set(
        self,
        key: str,
        record: IdempotencyRecord,
        ttl_seconds: int,
    ) -> None:
        client = await self._get_client()
        payload = {
            "status_code": record.status_code,
            "headers": record.headers,
            "body": base64.b64encode(record.body).decode("ascii"),
            "media_type": record.media_type,
        }
        await client.setex(self._redis_key(key), ttl_seconds, json.dumps(payload))

    async def _get_client(self) -> redis.Redis[Any]:
        if self._client is None:
            self._client = redis.from_url(
                self._redis_url,
                socket_connect_timeout=0.2,
                socket_timeout=0.2,
                decode_responses=True,
            )
        return self._client

    def _redis_key(self, key: str) -> str:
        return f"{self._key_prefix}:{key}"


class ResilientIdempotencyStore:
    """Redis-primary store with local fallback when Redis is unavailable."""

    def __init__(
        self,
        primary: IdempotencyStore,
        fallback: IdempotencyStore | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback or InMemoryIdempotencyStore()

    async def get(self, key: str) -> IdempotencyRecord | None:
        try:
            return await self._primary.get(key)
        except Exception as exc:
            logger.warning("Idempotency primary store unavailable", error=str(exc))
            return await self._fallback.get(key)

    async def set(
        self,
        key: str,
        record: IdempotencyRecord,
        ttl_seconds: int,
    ) -> None:
        try:
            await self._primary.set(key, record, ttl_seconds)
        except Exception as exc:
            logger.warning("Idempotency primary store write failed", error=str(exc))
            await self._fallback.set(key, record, ttl_seconds)


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Cache successful POST responses for clients that send Idempotency-Key."""

    def __init__(
        self,
        app: Any,
        store: IdempotencyStore | None = None,
        ttl_seconds: int = DEFAULT_IDEMPOTENCY_TTL_SECONDS,
        path_prefixes: Iterable[str] = ("/api/v1/query",),
    ) -> None:
        super().__init__(app)
        self.store = store
        self.ttl_seconds = ttl_seconds
        self.path_prefixes = tuple(path_prefixes)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not self._should_cache(request):
            return await call_next(request)

        body = await request.body()
        idempotency_key = request.headers[IDEMPOTENCY_HEADER]
        cache_key = self._build_cache_key(request, idempotency_key, body)

        cached = await self._store.get(cache_key)
        if cached is not None:
            return Response(
                content=cached.body,
                status_code=cached.status_code,
                headers=cached.headers,
                media_type=cached.media_type,
            )

        async def receive() -> Message:
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = receive
        response = await call_next(request)
        response_body = b"".join(
            [chunk async for chunk in cast(Any, response).body_iterator]
        )

        replay = Response(
            content=response_body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )
        if 200 <= response.status_code < 300:
            await self._store.set(
                cache_key,
                IdempotencyRecord(
                    status_code=response.status_code,
                    headers=_cacheable_headers(response.headers.items()),
                    body=response_body,
                    media_type=response.media_type,
                ),
                self.ttl_seconds,
            )
        return replay

    @property
    def _store(self) -> IdempotencyStore:
        if self.store is None:
            self.store = InMemoryIdempotencyStore()
        return self.store

    def _should_cache(self, request: Request) -> bool:
        if request.method.upper() != "POST":
            return False
        if IDEMPOTENCY_HEADER not in request.headers:
            return False
        return any(request.url.path.startswith(prefix) for prefix in self.path_prefixes)

    def _build_cache_key(
        self,
        request: Request,
        idempotency_key: str,
        body: bytes,
    ) -> str:
        body_hash = hashlib.sha256(body).hexdigest()
        raw_key = f"{request.method}:{request.url.path}:{idempotency_key}:{body_hash}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _cacheable_headers(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
    excluded = {"content-length", "transfer-encoding"}
    return {key: value for key, value in headers if key.lower() not in excluded}
