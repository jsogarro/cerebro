"""Releasing an idempotency reservation only drops the caller's own reservation.

Request A's reservation can expire while A is still running; request B then
takes the key. When A finishes, its release must not delete B's reservation,
or a third duplicate could run concurrently with B.
"""

from __future__ import annotations

import os
import uuid

import pytest
import redis.asyncio as redis

from src.api.middleware.idempotency import (
    InMemoryIdempotencyStore,
    RedisIdempotencyStore,
)


@pytest.mark.asyncio
async def test_in_memory_release_after_expiry_leaves_new_owner_alone() -> None:
    clock = [0.0]
    store = InMemoryIdempotencyStore(now=lambda: clock[0])

    token_a = await store.reserve("k", ttl_seconds=10)
    assert token_a is not None
    clock[0] = 11.0  # A's reservation expires while A is still running
    token_b = await store.reserve("k", ttl_seconds=10)
    assert token_b is not None

    await store.release("k", token_a)

    assert await store.reserve("k", ttl_seconds=10) is None  # B still holds it
    await store.release("k", token_b)
    assert await store.reserve("k", ttl_seconds=10) is not None


@pytest.fixture
async def redis_store():
    url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    probe = redis.from_url(url, socket_connect_timeout=0.5)
    try:
        await probe.ping()
    except Exception:
        pytest.skip(f"no Redis at {url}")
    finally:
        await probe.aclose()
    prefix = f"idempotency-test-{uuid.uuid4().hex}"
    store = RedisIdempotencyStore(url, key_prefix=prefix)
    yield store
    client = await store._get_client()
    keys = [key async for key in client.scan_iter(f"{prefix}:*")]
    if keys:
        await client.delete(*keys)
    await client.aclose()


@pytest.mark.asyncio
async def test_redis_release_after_expiry_leaves_new_owner_alone(
    redis_store: RedisIdempotencyStore,
) -> None:
    token_a = await redis_store.reserve("k", ttl_seconds=10)
    assert token_a is not None
    client = await redis_store._get_client()
    # Simulate A's reservation expiring, then B claiming the key.
    await client.delete(redis_store._redis_key("k") + ":pending")
    token_b = await redis_store.reserve("k", ttl_seconds=10)
    assert token_b is not None

    await redis_store.release("k", token_a)

    assert await redis_store.reserve("k", ttl_seconds=10) is None
    await redis_store.release("k", token_b)
    assert await redis_store.reserve("k", ttl_seconds=10) is not None
