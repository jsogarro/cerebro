"""
Cache manager for advanced caching operations.

This module provides a sophisticated cache manager with support for
various caching strategies, compression, and batch operations.
"""

import gzip
import json
import logging
from typing import Any

from redis import asyncio as aioredis

from src.services.cache.cache_strategies import CacheStrategy, TTLStrategy

logger = logging.getLogger(__name__)


class CacheManager:
    """
    Advanced cache manager with support for multiple strategies.

    Features:
    - Multiple caching strategies
    - Compression for large values
    - Batch operations
    - Cache warming
    - Metrics tracking
    """

    def __init__(
        self,
        redis_client: aioredis.Redis,
        strategy: CacheStrategy | None = None,
        compression_threshold: int = 1024,  # Compress values larger than 1KB
        namespace: str = "gemini",
    ):
        """
        Initialize cache manager.

        Args:
            redis_client: Redis client instance
            strategy: Caching strategy to use
            compression_threshold: Size threshold for compression (bytes)
            namespace: Cache key namespace
        """
        self.redis = redis_client
        self.strategy = strategy or TTLStrategy(ttl=3600)
        self.compression_threshold = compression_threshold
        self.namespace = namespace

        # Metrics
        self.metrics = {
            "hits": 0,
            "misses": 0,
            "sets": 0,
            "deletes": 0,
            "errors": 0,
        }

    def _make_key(self, key: str) -> str:
        """Create namespaced cache key."""
        return f"{self.namespace}:{key}"

    def _compress_value(self, value: str) -> tuple[bytes, bool]:
        """
        Compress value if it exceeds threshold.

        Args:
            value: Value to potentially compress

        Returns:
            Tuple of (compressed/original bytes, was_compressed)
        """
        value_bytes = value.encode("utf-8")

        if len(value_bytes) > self.compression_threshold:
            compressed = gzip.compress(value_bytes)
            # Only use compression if it actually reduces size
            if len(compressed) < len(value_bytes):
                return compressed, True

        return value_bytes, False

    def _decompress_value(self, value: bytes, compressed: bool) -> str:
        """
        Decompress value if it was compressed.

        Args:
            value: Potentially compressed value
            compressed: Whether value is compressed

        Returns:
            Decompressed string value
        """
        if compressed:
            return gzip.decompress(value).decode("utf-8")
        return value.decode("utf-8")

    async def get(self, key: str) -> dict[str, Any] | None:
        """
        Get value from cache.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found
        """
        full_key = self._make_key(key)

        try:
            # Get value and metadata
            pipe = self.redis.pipeline()
            pipe.get(full_key)
            pipe.get(f"{full_key}:meta")
            results = await pipe.execute()

            value_bytes = results[0]
            meta_bytes = results[1]

            if not value_bytes:
                self.metrics["misses"] += 1
                return None

            # Check if value is compressed
            compressed = False
            if meta_bytes:
                meta = json.loads(meta_bytes)
                compressed = meta.get("compressed", False)

            # Decompress and parse
            value_str = self._decompress_value(value_bytes, compressed)
            value = json.loads(value_str)

            # Update strategy (for LRU, etc.)
            await self.strategy.on_get(full_key, self.redis)

            self.metrics["hits"] += 1
            logger.debug(f"Cache hit for key: {key}")
            return value

        except Exception as e:
            logger.error(f"Cache get error for key {key}: {e}")
            self.metrics["errors"] += 1
            return None

    async def set(
        self,
        key: str,
        value: dict[str, Any],
        ttl: int | None = None,
        dependencies: list[str] | None = None,
    ) -> bool:
        """
        Set value in cache with optional dependencies.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Optional TTL override
            dependencies: Optional list of dependency keys

        Returns:
            True if successful
        """
        full_key = self._make_key(key)

        try:
            # Serialize value
            value_str = json.dumps(value)
            value_bytes, compressed = self._compress_value(value_str)

            # Prepare metadata
            meta = {
                "compressed": compressed,
                "size": len(value_bytes),
                "original_size": len(value_str),
            }

            # Set value and metadata
            pipe = self.redis.pipeline()
            pipe.set(full_key, value_bytes)
            pipe.set(f"{full_key}:meta", json.dumps(meta))

            # Apply strategy
            await self.strategy.on_set(full_key, self.redis, ttl, dependencies)

            await pipe.execute()

            self.metrics["sets"] += 1
            logger.debug(f"Cache set for key: {key} (compressed: {compressed})")
            return True

        except Exception as e:
            logger.error(f"Cache set error for key {key}: {e}")
            self.metrics["errors"] += 1
            return False

    async def delete(self, key: str) -> bool:
        """
        Delete value from cache.

        Args:
            key: Cache key

        Returns:
            True if key existed and was deleted
        """
        full_key = self._make_key(key)

        try:
            # Delete value and metadata
            pipe = self.redis.pipeline()
            pipe.delete(full_key)
            pipe.delete(f"{full_key}:meta")

            # Apply strategy
            await self.strategy.on_delete(full_key, self.redis)

            results = await pipe.execute()

            deleted = results[0] > 0
            if deleted:
                self.metrics["deletes"] += 1
                logger.debug(f"Cache delete for key: {key}")

            return deleted

        except Exception as e:
            logger.error(f"Cache delete error for key {key}: {e}")
            self.metrics["errors"] += 1
            return False

    async def batch_get(self, keys: list[str]) -> dict[str, dict[str, Any] | None]:
        """
        Get multiple values from cache.

        Args:
            keys: List of cache keys

        Returns:
            Dictionary mapping keys to values (None if not found)
        """
        if not keys:
            return {}

        results = {}

        # Use pipeline for efficiency
        pipe = self.redis.pipeline()
        full_keys = []

        for key in keys:
            full_key = self._make_key(key)
            full_keys.append(full_key)
            pipe.get(full_key)
            pipe.get(f"{full_key}:meta")

        try:
            pipe_results = await pipe.execute()

            # Process results in pairs (value, meta)
            for i, key in enumerate(keys):
                value_bytes = pipe_results[i * 2]
                meta_bytes = pipe_results[i * 2 + 1]

                if not value_bytes:
                    results[key] = None
                    self.metrics["misses"] += 1
                else:
                    # Check compression
                    compressed = False
                    if meta_bytes:
                        meta = json.loads(meta_bytes)
                        compressed = meta.get("compressed", False)

                    # Decompress and parse
                    value_str = self._decompress_value(value_bytes, compressed)
                    results[key] = json.loads(value_str)
                    self.metrics["hits"] += 1

            return results

        except Exception as e:
            logger.error(f"Batch get error: {e}")
            self.metrics["errors"] += 1
            return dict.fromkeys(keys)

    async def batch_set(
        self,
        items: dict[str, dict[str, Any]],
        ttl: int | None = None,
    ) -> bool:
        """
        Set multiple values in cache.

        Args:
            items: Dictionary mapping keys to values
            ttl: Optional TTL for all items

        Returns:
            True if all successful
        """
        if not items:
            return True

        pipe = self.redis.pipeline()

        try:
            for key, value in items.items():
                full_key = self._make_key(key)

                # Serialize and compress
                value_str = json.dumps(value)
                value_bytes, compressed = self._compress_value(value_str)

                # Metadata
                meta = {
                    "compressed": compressed,
                    "size": len(value_bytes),
                }

                # Add to pipeline
                pipe.set(full_key, value_bytes)
                pipe.set(f"{full_key}:meta", json.dumps(meta))

                # Apply TTL if specified
                if ttl:
                    pipe.expire(full_key, ttl)
                    pipe.expire(f"{full_key}:meta", ttl)

            await pipe.execute()

            self.metrics["sets"] += len(items)
            logger.debug(f"Batch set {len(items)} items")
            return True

        except Exception as e:
            logger.error(f"Batch set error: {e}")
            self.metrics["errors"] += 1
            return False

    async def clear_namespace(self) -> int:
        """
        Clear all keys in the namespace.

        Returns:
            Number of keys deleted
        """
        try:
            # Find all keys in namespace
            pattern = f"{self.namespace}:*"
            cursor = 0
            deleted_count = 0

            while True:
                cursor, keys = await self.redis.scan(cursor, match=pattern, count=100)

                if keys:
                    deleted_count += await self.redis.delete(*keys)

                if cursor == 0:
                    break

            logger.info(f"Cleared {deleted_count} keys from namespace {self.namespace}")
            return deleted_count

        except Exception as e:
            logger.error(f"Clear namespace error: {e}")
            return 0

    async def warm_cache(
        self,
        data_generator: Any,
        batch_size: int = 10,
    ) -> int:
        """
        Warm cache with pre-generated data.

        Args:
            data_generator: Async generator yielding (key, value) pairs
            batch_size: Number of items to set at once

        Returns:
            Number of items cached
        """
        count = 0
        batch = {}

        try:
            async for key, value in data_generator:
                batch[key] = value

                if len(batch) >= batch_size:
                    if await self.batch_set(batch):
                        count += len(batch)
                    batch = {}

            # Set remaining items
            if batch and await self.batch_set(batch):
                count += len(batch)

            logger.info(f"Warmed cache with {count} items")
            return count

        except Exception as e:
            logger.error(f"Cache warming error: {e}")
            return count

    def get_metrics(self) -> dict[str, Any]:
        """
        Get cache metrics.

        Returns:
            Dictionary of metrics
        """
        total = self.metrics["hits"] + self.metrics["misses"]
        hit_rate = (self.metrics["hits"] / total * 100) if total > 0 else 0

        return {
            **self.metrics,
            "total_requests": total,
            "hit_rate": round(hit_rate, 2),
        }

    async def invalidate_dependencies(self, key: str) -> int:
        """
        Invalidate all keys dependent on the given key.

        Args:
            key: Key whose dependents should be invalidated

        Returns:
            Number of keys invalidated
        """
        full_key = self._make_key(key)
        return await self.strategy.invalidate_dependencies(full_key, self.redis)
