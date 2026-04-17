"""
Rate limiter implementation using Redis.

Provides flexible rate limiting for API endpoints with support for
different strategies (sliding window, token bucket, fixed window).
"""

from __future__ import annotations

import hashlib
import time
from enum import Enum
from typing import Any

import redis.asyncio as redis
from fastapi import HTTPException, Request, status
from redis.asyncio import Redis
from starlette.responses import Response

from src.models.db.audit_log import AuditEventType, AuditSeverity


class RateLimitStrategy(str, Enum):
    """Rate limiting strategies."""

    SLIDING_WINDOW = "sliding_window"
    TOKEN_BUCKET = "token_bucket"
    FIXED_WINDOW = "fixed_window"
    LEAKY_BUCKET = "leaky_bucket"


class RateLimitScope(str, Enum):
    """Rate limit scopes."""

    GLOBAL = "global"  # Global rate limit across all users
    USER = "user"  # Per-user rate limit
    IP = "ip"  # Per-IP rate limit
    ENDPOINT = "endpoint"  # Per-endpoint rate limit
    API_KEY = "api_key"  # Per-API key rate limit


class RateLimiter:
    """
    Redis-based rate limiter with multiple strategies.

    Supports sliding window, token bucket, fixed window, and
    leaky bucket algorithms for flexible rate limiting.
    """

    def __init__(
        self,
        redis_client: Redis[Any],
        default_limit: int = 100,
        default_window: int = 3600,
        default_strategy: RateLimitStrategy = RateLimitStrategy.SLIDING_WINDOW,
    ):
        """
        Initialize rate limiter.

        Args:
            redis_client: Redis client instance
            default_limit: Default request limit
            default_window: Default time window in seconds
            default_strategy: Default rate limiting strategy
        """
        self.redis = redis_client
        self.default_limit = default_limit
        self.default_window = default_window
        self.default_strategy = default_strategy

        # Rate limit configurations by endpoint
        self.endpoint_limits: dict[str, dict[str, Any]] = {
            # Authentication endpoints - stricter limits
            "/api/v1/auth/login": {
                "limit": 5,
                "window": 300,  # 5 attempts per 5 minutes
                "strategy": RateLimitStrategy.FIXED_WINDOW,
                "scope": RateLimitScope.IP,
            },
            "/api/v1/auth/register": {
                "limit": 3,
                "window": 3600,  # 3 registrations per hour
                "strategy": RateLimitStrategy.FIXED_WINDOW,
                "scope": RateLimitScope.IP,
            },
            "/api/v1/auth/password-reset": {
                "limit": 3,
                "window": 3600,  # 3 reset requests per hour
                "strategy": RateLimitStrategy.FIXED_WINDOW,
                "scope": RateLimitScope.IP,
            },
            # API endpoints - moderate limits
            "/api/v1/research/projects": {
                "limit": 100,
                "window": 3600,  # 100 requests per hour
                "strategy": RateLimitStrategy.SLIDING_WINDOW,
                "scope": RateLimitScope.USER,
            },
            "/api/v1/research/execute": {
                "limit": 10,
                "window": 3600,  # 10 executions per hour
                "strategy": RateLimitStrategy.TOKEN_BUCKET,
                "scope": RateLimitScope.USER,
            },
            # Public endpoints - relaxed limits
            "/api/v1/health": {
                "limit": 1000,
                "window": 60,  # 1000 requests per minute
                "strategy": RateLimitStrategy.SLIDING_WINDOW,
                "scope": RateLimitScope.GLOBAL,
            },
        }

    async def check_rate_limit(
        self,
        request: Request,
        identifier: str | None = None,
        endpoint: str | None = None,
        custom_limit: int | None = None,
        custom_window: int | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """
        Check if request is within rate limits.

        Args:
            request: FastAPI request object
            identifier: Custom identifier (user_id, api_key, etc.)
            endpoint: Endpoint path
            custom_limit: Custom rate limit
            custom_window: Custom time window

        Returns:
            Tuple of (allowed, metadata)
        """
        # Get endpoint configuration
        endpoint = endpoint or request.url.path
        config = self.endpoint_limits.get(endpoint, {})

        # Determine rate limit parameters
        limit = custom_limit or config.get("limit", self.default_limit)
        window = custom_window or config.get("window", self.default_window)
        strategy = config.get("strategy", self.default_strategy)
        scope = config.get("scope", RateLimitScope.IP)

        # Generate rate limit key
        key = await self._generate_key(request, identifier, endpoint, scope)

        # Check rate limit based on strategy
        if strategy == RateLimitStrategy.SLIDING_WINDOW:
            allowed, metadata = await self._check_sliding_window(key, limit, window)
        elif strategy == RateLimitStrategy.TOKEN_BUCKET:
            allowed, metadata = await self._check_token_bucket(key, limit, window)
        elif strategy == RateLimitStrategy.FIXED_WINDOW:
            allowed, metadata = await self._check_fixed_window(key, limit, window)
        elif strategy == RateLimitStrategy.LEAKY_BUCKET:
            allowed, metadata = await self._check_leaky_bucket(key, limit, window)
        else:
            allowed, metadata = await self._check_sliding_window(key, limit, window)

        # Add additional metadata
        metadata.update(
            {
                "endpoint": endpoint,
                "scope": scope.value,
                "strategy": strategy.value,
                "key": key,
            }
        )

        # Log rate limit exceeded events
        if not allowed:
            await self._log_rate_limit_exceeded(request, identifier, metadata)

        return allowed, metadata

    async def _generate_key(
        self,
        request: Request,
        identifier: str | None,
        endpoint: str,
        scope: RateLimitScope,
    ) -> str:
        """Generate rate limit key based on scope."""
        parts = ["rate_limit"]

        if scope == RateLimitScope.GLOBAL:
            parts.append("global")
        elif scope == RateLimitScope.USER and identifier:
            parts.extend(["user", identifier])
        elif scope == RateLimitScope.IP:
            ip = request.client.host if request.client else "unknown"
            parts.extend(["ip", ip])
        elif scope == RateLimitScope.API_KEY and identifier:
            parts.extend(["api_key", identifier])
        elif scope == RateLimitScope.ENDPOINT:
            parts.extend(["endpoint", hashlib.md5(endpoint.encode()).hexdigest()])
        else:
            # Fallback to IP-based
            ip = request.client.host if request.client else "unknown"
            parts.extend(["ip", ip])

        # Add endpoint to key for better granularity
        parts.append(hashlib.md5(endpoint.encode()).hexdigest()[:8])

        return ":".join(parts)

    async def _check_sliding_window(
        self, key: str, limit: int, window: int
    ) -> tuple[bool, dict[str, Any]]:
        """
        Check rate limit using sliding window algorithm.

        Most accurate but memory-intensive algorithm.
        """
        now = time.time()
        window_start = now - window

        # Use Redis sorted set for sliding window
        pipe = self.redis.pipeline()

        # Remove old entries
        pipe.zremrangebyscore(key, 0, window_start)

        # Add current request
        pipe.zadd(key, {str(now): now})

        # Count requests in window
        pipe.zcard(key)

        # Set expiry
        pipe.expire(key, window + 1)

        results = await pipe.execute()
        request_count = results[2]

        allowed = request_count <= limit
        remaining = max(0, limit - request_count)

        # Calculate reset time
        if request_count > 0:
            oldest_request = await self.redis.zrange(key, 0, 0, withscores=True)
            if oldest_request:
                reset_time = oldest_request[0][1] + window
            else:
                reset_time = now + window
        else:
            reset_time = now + window

        metadata = {
            "limit": limit,
            "remaining": remaining,
            "reset": int(reset_time),
            "window": window,
            "current": request_count,
        }

        return allowed, metadata

    async def _check_token_bucket(
        self, key: str, limit: int, window: int
    ) -> tuple[bool, dict[str, Any]]:
        """
        Check rate limit using token bucket algorithm.

        Allows burst traffic while maintaining average rate.
        """
        now = time.time()
        rate = limit / window  # Tokens per second

        # Get current bucket state
        bucket_key = f"{key}:bucket"
        last_refill_key = f"{key}:last_refill"

        # Get current tokens and last refill time
        pipe = self.redis.pipeline()
        pipe.get(bucket_key)
        pipe.get(last_refill_key)
        results = await pipe.execute()

        current_tokens = float(results[0]) if results[0] else limit
        last_refill = float(results[1]) if results[1] else now

        # Calculate tokens to add based on time elapsed
        time_elapsed = now - last_refill
        tokens_to_add = time_elapsed * rate

        # Update token count (cap at limit)
        new_tokens = min(limit, current_tokens + tokens_to_add)

        # Check if request can be served
        if new_tokens >= 1:
            # Consume a token
            new_tokens -= 1
            allowed = True
        else:
            allowed = False

        # Update bucket state
        pipe = self.redis.pipeline()
        pipe.set(bucket_key, new_tokens, ex=window * 2)
        pipe.set(last_refill_key, now, ex=window * 2)
        await pipe.execute()

        metadata = {
            "limit": limit,
            "remaining": int(new_tokens),
            "reset": int(now + (1 / rate) if new_tokens < limit else now),
            "window": window,
            "tokens": new_tokens,
            "rate": rate,
        }

        return allowed, metadata

    async def _check_fixed_window(
        self, key: str, limit: int, window: int
    ) -> tuple[bool, dict[str, Any]]:
        """
        Check rate limit using fixed window algorithm.

        Simple and efficient but can allow burst at window boundaries.
        """
        # Calculate window key based on current time
        window_id = int(time.time() / window)
        window_key = f"{key}:window:{window_id}"

        # Increment counter
        pipe = self.redis.pipeline()
        pipe.incr(window_key)
        pipe.expire(window_key, window + 1)
        results = await pipe.execute()

        request_count = results[0]
        allowed = request_count <= limit
        remaining = max(0, limit - request_count)

        # Calculate reset time (next window)
        reset_time = (window_id + 1) * window

        metadata = {
            "limit": limit,
            "remaining": remaining,
            "reset": reset_time,
            "window": window,
            "current": request_count,
            "window_id": window_id,
        }

        return allowed, metadata

    async def _check_leaky_bucket(
        self, key: str, limit: int, window: int
    ) -> tuple[bool, dict[str, Any]]:
        """
        Check rate limit using leaky bucket algorithm.

        Smooths out bursts by processing requests at constant rate.
        """
        now = time.time()
        leak_rate = limit / window  # Requests per second

        # Get bucket state
        bucket_key = f"{key}:leaky"
        last_leak_key = f"{key}:last_leak"

        pipe = self.redis.pipeline()
        pipe.get(bucket_key)
        pipe.get(last_leak_key)
        results = await pipe.execute()

        bucket_level = float(results[0]) if results[0] else 0
        last_leak = float(results[1]) if results[1] else now

        # Calculate leaked amount
        time_elapsed = now - last_leak
        leaked = time_elapsed * leak_rate

        # Update bucket level
        new_level = max(0, bucket_level - leaked)

        # Check if request can be added
        if new_level < limit:
            new_level += 1
            allowed = True
        else:
            allowed = False

        # Update bucket state
        pipe = self.redis.pipeline()
        pipe.set(bucket_key, new_level, ex=window * 2)
        pipe.set(last_leak_key, now, ex=window * 2)
        await pipe.execute()

        metadata = {
            "limit": limit,
            "remaining": int(limit - new_level),
            "reset": int(now + (new_level / leak_rate)) if new_level > 0 else now,
            "window": window,
            "bucket_level": new_level,
            "leak_rate": leak_rate,
        }

        return allowed, metadata

    async def reset_rate_limit(
        self, identifier: str, endpoint: str | None = None
    ) -> bool:
        """
        Reset rate limit for an identifier.

        Args:
            identifier: User ID, IP, or API key
            endpoint: Specific endpoint to reset

        Returns:
            True if reset successful
        """
        pattern = f"rate_limit:*{identifier}*"
        if endpoint:
            pattern += f"*{hashlib.md5(endpoint.encode()).hexdigest()[:8]}*"

        # Find and delete matching keys
        cursor = 0
        deleted_count = 0

        while True:
            cursor, keys = await self.redis.scan(cursor, match=pattern, count=100)

            if keys:
                deleted_count += await self.redis.delete(*keys)

            if cursor == 0:
                break

        return deleted_count > 0

    async def get_rate_limit_status(
        self, identifier: str, endpoint: str | None = None
    ) -> dict[str, Any]:
        """
        Get current rate limit status for an identifier.

        Args:
            identifier: User ID, IP, or API key
            endpoint: Specific endpoint

        Returns:
            Rate limit status dictionary
        """
        # This would need to be implemented based on the specific
        # requirements and how you want to track/report status
        status: dict[str, Any] = {"identifier": identifier, "endpoint": endpoint, "limits": []}

        # Find matching keys and get their status
        pattern = f"rate_limit:*{identifier}*"
        if endpoint:
            pattern += f"*{hashlib.md5(endpoint.encode()).hexdigest()[:8]}*"

        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match=pattern, count=100)

            for key in keys:
                # Parse key to get endpoint and scope
                _key_parts = key.decode().split(":")

                # Get current count/status based on key type
                if "window" in key:
                    count = await self.redis.get(key)
                    if count:
                        status["limits"].append(
                            {
                                "key": key.decode(),
                                "count": int(count),
                                "type": "fixed_window",
                            }
                        )
                elif "bucket" in key:
                    tokens = await self.redis.get(key)
                    if tokens:
                        status["limits"].append(
                            {
                                "key": key.decode(),
                                "tokens": float(tokens),
                                "type": "token_bucket",
                            }
                        )
                else:
                    # Sliding window - get sorted set size
                    count = await self.redis.zcard(key)
                    if count > 0:
                        status["limits"].append(
                            {
                                "key": key.decode(),
                                "count": count,
                                "type": "sliding_window",
                            }
                        )

            if cursor == 0:
                break

        return status

    async def _log_rate_limit_exceeded(
        self, request: Request, identifier: str | None, metadata: dict[str, Any]
    ) -> None:
        """Log rate limit exceeded event."""
        # This would integrate with your audit logging system
        # For now, we'll just prepare the log entry
        ip_address = request.client.host if request.client else "unknown"

        _audit_entry = {
            "event_type": AuditEventType.RATE_LIMIT_EXCEEDED,
            "severity": AuditSeverity.WARNING,
            "action": "rate_limit_check",
            "result": "exceeded",
            "ip_address": ip_address,
            "resource_type": "endpoint",
            "resource_id": metadata.get("endpoint"),
            "metadata": metadata,
            "description": f"Rate limit exceeded for {metadata.get('endpoint')} from {ip_address}",
        }

        # In production, this would save to database
        # await AuditLog.log_event(**audit_entry)


class RateLimitMiddleware:
    """
    FastAPI middleware for rate limiting.

    Automatically applies rate limits to all requests based on
    endpoint configuration.
    """

    def __init__(
        self,
        redis_url: str,
        default_limit: int = 100,
        default_window: int = 3600,
        exclude_paths: list[str] | None = None,
    ):
        """
        Initialize rate limit middleware.

        Args:
            redis_url: Redis connection URL
            default_limit: Default request limit
            default_window: Default time window
            exclude_paths: Paths to exclude from rate limiting
        """
        self.redis_url = redis_url
        self.default_limit = default_limit
        self.default_window = default_window
        self.exclude_paths = exclude_paths or ["/docs", "/redoc", "/openapi.json"]
        self.redis_client: Redis[Any] | None = None
        self.rate_limiter: RateLimiter | None = None

    async def __call__(self, request: Request, call_next: Any) -> Response:
        """Process request with rate limiting."""
        # Initialize Redis client if needed
        if not self.redis_client:
            self.redis_client = await redis.from_url(self.redis_url)
            self.rate_limiter = RateLimiter(
                self.redis_client, self.default_limit, self.default_window
            )

        # Skip rate limiting for excluded paths
        if request.url.path in self.exclude_paths:
            response: Response = await call_next(request)
            return response

        # Get user identifier from request
        identifier = None
        if hasattr(request.state, "user_id"):
            identifier = request.state.user_id
        elif hasattr(request.state, "api_key"):
            identifier = request.state.api_key

        # Check rate limit
        if not self.rate_limiter:
            response2: Response = await call_next(request)
            return response2
        allowed, metadata = await self.rate_limiter.check_rate_limit(
            request, identifier=identifier
        )

        if not allowed:
            # Add rate limit headers
            headers = {
                "X-RateLimit-Limit": str(metadata.get("limit", 0)),
                "X-RateLimit-Remaining": str(metadata.get("remaining", 0)),
                "X-RateLimit-Reset": str(metadata.get("reset", 0)),
                "Retry-After": str(metadata.get("reset", 0) - int(time.time())),
            }

            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Please try again later.",
                headers=headers,
            )

        # Add rate limit headers to response
        response3: Response = await call_next(request)
        response3.headers["X-RateLimit-Limit"] = str(metadata.get("limit", 0))
        response3.headers["X-RateLimit-Remaining"] = str(metadata.get("remaining", 0))
        response3.headers["X-RateLimit-Reset"] = str(metadata.get("reset", 0))

        return response3
