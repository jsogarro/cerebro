"""
Caching strategies for different use cases.

This module provides various caching strategies including TTL, LRU,
and dependency-based invalidation.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from redis import asyncio as aioredis

from src.utils.serialization import deserialize_from_cache, serialize_for_cache


class CacheStrategy(ABC):
    """Abstract base class for cache strategies."""

    @abstractmethod
    async def on_get(self, key: str, redis: aioredis.Redis[Any]) -> None:
        """Called when a key is accessed."""
        pass

    @abstractmethod
    async def on_set(
        self,
        key: str,
        redis: aioredis.Redis[Any],
        ttl: int | None = None,
        dependencies: list[str] | None = None,
    ) -> None:
        """Called when a key is set."""
        pass

    @abstractmethod
    async def on_delete(self, key: str, redis: aioredis.Redis[Any]) -> None:
        """Called when a key is deleted."""
        pass

    async def invalidate_dependencies(self, key: str, redis: aioredis.Redis[Any]) -> int:
        """Invalidate dependent keys."""
        return 0


class TTLStrategy(CacheStrategy):
    """
    Time-to-live caching strategy.

    Simple expiration-based caching.
    """

    def __init__(self, ttl: int = 3600):
        """
        Initialize TTL strategy.

        Args:
            ttl: Default time-to-live in seconds
        """
        self.default_ttl = ttl

    async def on_get(self, key: str, redis: aioredis.Redis[Any]) -> None:
        """No action needed on get for TTL strategy."""
        pass

    async def on_set(
        self,
        key: str,
        redis: aioredis.Redis[Any],
        ttl: int | None = None,
        dependencies: list[str] | None = None,
    ) -> None:
        """Set TTL for the key."""
        expiry = ttl or self.default_ttl
        await redis.expire(key, expiry)
        await redis.expire(f"{key}:meta", expiry)

    async def on_delete(self, key: str, redis: aioredis.Redis[Any]) -> None:
        """No additional action needed on delete."""
        pass


class LRUStrategy(CacheStrategy):
    """
    Least Recently Used caching strategy.

    Maintains access timestamps and evicts least recently used items.
    """

    def __init__(self, max_items: int = 1000, ttl: int = 3600):
        """
        Initialize LRU strategy.

        Args:
            max_items: Maximum number of items in cache
            ttl: Time-to-live for items
        """
        self.max_items = max_items
        self.ttl = ttl
        self.access_key = "lru:access_times"

    async def on_get(self, key: str, redis: aioredis.Redis[Any]) -> None:
        """Update access time for the key."""
        # Update access time in sorted set
        await redis.zadd(self.access_key, {key: time.time()})

    async def on_set(
        self,
        key: str,
        redis: aioredis.Redis[Any],
        ttl: int | None = None,
        dependencies: list[str] | None = None,
    ) -> None:
        """Add key to LRU tracking and evict if necessary."""
        # Add to access tracking
        await redis.zadd(self.access_key, {key: time.time()})

        # Set TTL
        expiry = ttl or self.ttl
        await redis.expire(key, expiry)
        await redis.expire(f"{key}:meta", expiry)

        # Check if eviction needed
        count = await redis.zcard(self.access_key)
        if count > self.max_items:
            # Evict oldest items
            to_evict = count - self.max_items
            oldest = await redis.zrange(self.access_key, 0, to_evict - 1)

            if oldest:
                # Delete the keys
                pipe = redis.pipeline()
                for old_key in oldest:
                    pipe.delete(old_key)
                    pipe.delete(f"{old_key}:meta")
                    pipe.zrem(self.access_key, old_key)
                await pipe.execute()

    async def on_delete(self, key: str, redis: aioredis.Redis[Any]) -> None:
        """Remove key from LRU tracking."""
        await redis.zrem(self.access_key, key)


class DependencyStrategy(CacheStrategy):
    """
    Dependency-based caching strategy.

    Allows cache invalidation based on dependencies between keys.
    """

    def __init__(self, ttl: int = 3600):
        """
        Initialize dependency strategy.

        Args:
            ttl: Default time-to-live
        """
        self.ttl = ttl
        self.deps_prefix = "deps:"

    async def on_get(self, key: str, redis: aioredis.Redis[Any]) -> None:
        """No action needed on get."""
        pass

    async def on_set(
        self,
        key: str,
        redis: aioredis.Redis[Any],
        ttl: int | None = None,
        dependencies: list[str] | None = None,
    ) -> None:
        """Set up dependency tracking."""
        # Set TTL
        expiry = ttl or self.ttl
        await redis.expire(key, expiry)
        await redis.expire(f"{key}:meta", expiry)

        if dependencies:
            # Store dependencies for this key
            deps_key = f"{self.deps_prefix}{key}"
            await redis.delete(deps_key)  # Clear old dependencies
            await redis.sadd(deps_key, *dependencies)
            await redis.expire(deps_key, expiry)

            # Add this key as dependent on each dependency
            pipe = redis.pipeline()
            for dep in dependencies:
                reverse_key = f"{self.deps_prefix}reverse:{dep}"
                pipe.sadd(reverse_key, key)
                pipe.expire(reverse_key, expiry)
            await pipe.execute()

    async def on_delete(self, key: str, redis: aioredis.Redis[Any]) -> None:
        """Clean up dependency tracking."""
        # Get dependencies of this key
        deps_key = f"{self.deps_prefix}{key}"
        dependencies = await redis.smembers(deps_key)

        if dependencies:
            # Remove this key from reverse dependencies
            pipe = redis.pipeline()
            for dep in dependencies:
                reverse_key = f"{self.deps_prefix}reverse:{dep}"
                pipe.srem(reverse_key, key)
            await pipe.execute()

        # Delete dependency tracking
        await redis.delete(deps_key)

    async def invalidate_dependencies(self, key: str, redis: aioredis.Redis[Any]) -> int:
        """
        Invalidate all keys dependent on the given key.

        Args:
            key: Key whose dependents should be invalidated
            redis: Redis client

        Returns:
            Number of keys invalidated
        """
        reverse_key = f"{self.deps_prefix}reverse:{key}"
        dependents = await redis.smembers(reverse_key)

        if not dependents:
            return 0

        # Delete all dependent keys
        pipe = redis.pipeline()
        for dep_key in dependents:
            pipe.delete(dep_key)
            pipe.delete(f"{dep_key}:meta")
            pipe.delete(f"{self.deps_prefix}{dep_key}")

        # Clean up reverse dependency
        pipe.delete(reverse_key)

        await pipe.execute()
        return len(dependents)


class HybridStrategy(CacheStrategy):
    """
    Hybrid caching strategy combining multiple strategies.

    Applies multiple strategies in sequence.
    """

    def __init__(self, strategies: list[CacheStrategy]):
        """
        Initialize hybrid strategy.

        Args:
            strategies: List of strategies to apply
        """
        self.strategies = strategies

    async def on_get(self, key: str, redis: aioredis.Redis[Any]) -> None:
        """Apply all strategies on get."""
        for strategy in self.strategies:
            await strategy.on_get(key, redis)

    async def on_set(
        self,
        key: str,
        redis: aioredis.Redis[Any],
        ttl: int | None = None,
        dependencies: list[str] | None = None,
    ) -> None:
        """Apply all strategies on set."""
        for strategy in self.strategies:
            await strategy.on_set(key, redis, ttl, dependencies)

    async def on_delete(self, key: str, redis: aioredis.Redis[Any]) -> None:
        """Apply all strategies on delete."""
        for strategy in self.strategies:
            await strategy.on_delete(key, redis)

    async def invalidate_dependencies(self, key: str, redis: aioredis.Redis[Any]) -> int:
        """Invalidate dependencies using all strategies."""
        total = 0
        for strategy in self.strategies:
            total += await strategy.invalidate_dependencies(key, redis)
        return total


class VersionedCacheStrategy(CacheStrategy):
    """
    Versioned caching strategy.

    Maintains versions of cached data for cache busting.
    """

    def __init__(self, ttl: int = 3600):
        """
        Initialize versioned strategy.

        Args:
            ttl: Default time-to-live
        """
        self.ttl = ttl
        self.version_key = "cache:versions"

    async def on_get(self, key: str, redis: aioredis.Redis[Any]) -> None:
        """Check version on get."""
        # Could implement version checking here if needed
        pass

    async def on_set(
        self,
        key: str,
        redis: aioredis.Redis[Any],
        ttl: int | None = None,
        dependencies: list[str] | None = None,
    ) -> None:
        """Set version for the key."""
        # Increment version
        version = await redis.hincrby(self.version_key, key, 1)

        # Store version in metadata
        meta_key = f"{key}:meta"
        meta = await redis.get(meta_key)
        if meta:
            meta_dict = deserialize_from_cache(meta)
            meta_dict["version"] = version
            await redis.set(meta_key, serialize_for_cache(meta_dict).decode("utf-8"))

        # Set TTL
        expiry = ttl or self.ttl
        await redis.expire(key, expiry)
        await redis.expire(meta_key, expiry)

    async def on_delete(self, key: str, redis: aioredis.Redis[Any]) -> None:
        """Remove version tracking."""
        await redis.hdel(self.version_key, key)

    async def increment_version(self, key: str, redis: aioredis.Redis[Any]) -> int:
        """
        Increment version to invalidate cache.

        Args:
            key: Key to increment version for
            redis: Redis client

        Returns:
            New version number
        """
        return await redis.hincrby(self.version_key, key, 1)
