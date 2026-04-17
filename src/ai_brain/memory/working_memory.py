"""
Working Memory Manager

Manages short-term memory for active conversations, current context,
and temporary state. Uses Redis for fast access with TTL-based expiration.

Working memory stores:
- Current conversation context
- Active agent states
- Temporary variables and computation results
- Session-specific information
- Real-time interaction data
"""

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

from src.utils.async_helpers import BackgroundTaskTracker
from src.utils.serialization import deserialize_from_cache, serialize_for_cache

try:
    import redis.asyncio as redis_module
    from redis.asyncio import Redis as RedisType
    REDIS_AVAILABLE = True
except ImportError:
    redis_module = None  # type: ignore[assignment]
    RedisType = type(None)  # type: ignore[assignment, misc]
    REDIS_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class WorkingMemoryItem:
    """Individual item stored in working memory."""

    key: str
    value: Any
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: datetime | None = None
    access_count: int = 0
    last_accessed: datetime = field(default_factory=datetime.now)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConversationContext:
    """Conversation context stored in working memory."""

    session_id: str
    user_id: str | None = None
    agent_id: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    current_task: str | None = None
    context_variables: dict[str, Any] = field(default_factory=dict)
    agent_state: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    last_updated: datetime = field(default_factory=datetime.now)


class WorkingMemoryManager:
    """
    Manages working memory using Redis for fast, temporary storage.

    Working memory is designed for:
    - Fast read/write operations (< 1ms)
    - Automatic expiration of old data
    - Session-based organization
    - Real-time access patterns
    """

    def __init__(self, config: dict[str, Any]):
        """Initialize working memory manager."""
        self.config = config

        # Redis configuration
        self.redis_url = config.get("redis_url", "redis://localhost:6379/0")
        self.key_prefix = config.get("key_prefix", "cerebro:working:")

        # Memory configuration
        self.default_ttl = config.get("default_ttl", 3600)  # 1 hour
        self.max_memory_mb = config.get("max_memory_mb", 512)
        self.cleanup_interval = config.get("cleanup_interval", 300)  # 5 minutes

        # Redis client
        self.redis_client = None
        if REDIS_AVAILABLE and redis_module:
            self.redis_client = redis_module.from_url(self.redis_url)
        else:
            logger.warning("Redis not available, using in-memory fallback")
            self._memory_fallback: dict[str, Any] = {}

        # Background tasks
        self._bg_tasks = BackgroundTaskTracker()

        # Performance tracking
        self.hit_count = 0
        self.miss_count = 0
        self.write_count = 0

        # Background cleanup task
        self._cleanup_task: asyncio.Task[None] | None = None

    async def initialize(self) -> None:
        """Initialize the working memory system."""
        if self.redis_client:
            try:
                # Test Redis connection
                await self.redis_client.ping()
                logger.info("Working memory initialized with Redis")

                # Start background cleanup
                self._cleanup_task = self._bg_tasks.create_task(self._cleanup_loop())

            except Exception as e:
                logger.error(f"Failed to connect to Redis: {e}")
                self.redis_client = None

        if not self.redis_client:
            logger.warning("Using in-memory fallback for working memory")

    async def store(
        self,
        key: str,
        value: Any,
        ttl: int | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """
        Store an item in working memory.

        Args:
            key: Storage key
            value: Value to store
            ttl: Time-to-live in seconds (None = default TTL)
            tags: Optional tags for categorization
            metadata: Optional metadata

        Returns:
            True if stored successfully
        """

        try:
            # Create working memory item
            item = WorkingMemoryItem(
                key=key,
                value=value,
                expires_at=datetime.now() + timedelta(seconds=ttl or self.default_ttl),
                tags=tags or [],
                metadata=metadata or {},
            )

            # Store in Redis or fallback
            if self.redis_client:
                await self._store_redis(key, item, ttl or self.default_ttl)
            else:
                self._store_fallback(key, item)

            self.write_count += 1
            logger.debug(f"Stored working memory item: {key}")
            return True

        except Exception as e:
            logger.error(f"Failed to store working memory item {key}: {e}")
            return False

    async def retrieve(self, key: str) -> Any | None:
        """
        Retrieve an item from working memory.

        Args:
            key: Storage key

        Returns:
            Stored value or None if not found
        """

        try:
            if self.redis_client:
                item = await self._retrieve_redis(key)
            else:
                item = self._retrieve_fallback(key)

            if item:
                self.hit_count += 1

                # Update access statistics
                item.access_count += 1
                item.last_accessed = datetime.now()

                # Re-store with updated stats (async to avoid blocking)
                if self.redis_client:
                    self._bg_tasks.create_task(self._update_access_stats(key, item))

                return item.value
            else:
                self.miss_count += 1
                return None

        except Exception as e:
            logger.error(f"Failed to retrieve working memory item {key}: {e}")
            self.miss_count += 1
            return None

    async def delete(self, key: str) -> bool:
        """Delete an item from working memory."""

        try:
            if self.redis_client:
                result = await self.redis_client.delete(self._make_key(key))
                return result > 0
            else:
                return self._memory_fallback.pop(key, None) is not None

        except Exception as e:
            logger.error(f"Failed to delete working memory item {key}: {e}")
            return False

    async def exists(self, key: str) -> bool:
        """Check if a key exists in working memory."""

        try:
            if self.redis_client:
                return await self.redis_client.exists(self._make_key(key)) > 0
            else:
                return key in self._memory_fallback

        except Exception as e:
            logger.error(f"Failed to check working memory existence {key}: {e}")
            return False

    async def store_conversation_context(self, context: ConversationContext) -> bool:
        """Store conversation context in working memory."""

        context.last_updated = datetime.now()
        key = f"conversation:{context.session_id}"

        return await self.store(
            key=key,
            value=asdict(context),
            ttl=self.config.get("conversation_ttl", 7200),  # 2 hours
            tags=["conversation", "context"],
            metadata={
                "user_id": context.user_id,
                "agent_id": context.agent_id,
                "message_count": len(context.messages),
            },
        )

    async def retrieve_conversation_context(
        self, session_id: str
    ) -> ConversationContext | None:
        """Retrieve conversation context from working memory."""

        key = f"conversation:{session_id}"
        context_data = await self.retrieve(key)

        if context_data:
            try:
                return ConversationContext(**context_data)
            except Exception as e:
                logger.error(f"Failed to deserialize conversation context: {e}")
                return None

        return None

    async def update_conversation_context(
        self, session_id: str, updates: dict[str, Any]
    ) -> bool:
        """Update specific fields in conversation context."""

        context = await self.retrieve_conversation_context(session_id)
        if not context:
            return False

        # Apply updates
        for attr, value in updates.items():
            if hasattr(context, attr):
                setattr(context, attr, value)

        # Store updated context
        return await self.store_conversation_context(context)

    async def add_message_to_context(
        self, session_id: str, message: dict[str, Any]
    ) -> bool:
        """Add a message to conversation context."""

        context = await self.retrieve_conversation_context(session_id)
        if not context:
            # Create new context
            context = ConversationContext(session_id=session_id)

        # Add message with timestamp
        message_with_timestamp = {**message, "timestamp": datetime.now().isoformat()}
        context.messages.append(message_with_timestamp)

        # Limit message history to prevent memory bloat
        max_messages = self.config.get("max_messages_in_context", 50)
        if len(context.messages) > max_messages:
            context.messages = context.messages[-max_messages:]

        return await self.store_conversation_context(context)

    async def store_agent_state(
        self, agent_id: str, state: dict[str, Any], session_id: str | None = None
    ) -> bool:
        """Store agent state in working memory."""

        key = f"agent_state:{agent_id}"
        if session_id:
            key += f":{session_id}"

        return await self.store(
            key=key,
            value=state,
            ttl=self.config.get("agent_state_ttl", 1800),  # 30 minutes
            tags=["agent", "state"],
            metadata={"agent_id": agent_id, "session_id": session_id},
        )

    async def retrieve_agent_state(
        self, agent_id: str, session_id: str | None = None
    ) -> dict[str, Any] | None:
        """Retrieve agent state from working memory."""

        key = f"agent_state:{agent_id}"
        if session_id:
            key += f":{session_id}"

        return await self.retrieve(key)

    async def get_keys_by_pattern(self, pattern: str) -> list[str]:
        """Get all keys matching a pattern."""

        try:
            if self.redis_client:
                full_pattern = f"{self.key_prefix}{pattern}"
                keys = await self.redis_client.keys(full_pattern)
                # Remove prefix from keys
                return [key.decode().replace(self.key_prefix, "") for key in keys]
            else:
                # Simple pattern matching for fallback
                import fnmatch

                return [
                    key
                    for key in self._memory_fallback
                    if fnmatch.fnmatch(key, pattern)
                ]

        except Exception as e:
            logger.error(f"Failed to get keys by pattern {pattern}: {e}")
            return []

    async def get_keys_by_tags(self, tags: list[str]) -> list[str]:
        """Get all keys that have specific tags."""

        matching_keys = []

        try:
            # Get all keys
            if self.redis_client:
                pattern = f"{self.key_prefix}*"
                all_keys = await self.redis_client.keys(pattern)

                for key in all_keys:
                    try:
                        data = await self.redis_client.get(key)
                        if data:
                            item_data = deserialize_from_cache(data)
                            item_tags = item_data.get("tags", [])

                            # Check if any of the specified tags match
                            if any(tag in item_tags for tag in tags):
                                clean_key = key.decode().replace(self.key_prefix, "")
                                matching_keys.append(clean_key)

                    except Exception as e:
                        logger.debug(f"Failed to check tags for key {key}: {e}")
                        continue
            else:
                # Fallback implementation
                for key, item in self._memory_fallback.items():
                    item_tags = item.tags if hasattr(item, "tags") else []
                    if any(tag in item_tags for tag in tags):
                        matching_keys.append(key)

        except Exception as e:
            logger.error(f"Failed to get keys by tags {tags}: {e}")

        return matching_keys

    async def cleanup_expired(self) -> int:
        """Clean up expired items from working memory."""

        cleaned_count = 0

        try:
            if self.redis_client:
                # Redis handles TTL automatically, but we can check for manual cleanup
                pass
            else:
                # Manual cleanup for fallback
                now = datetime.now()
                expired_keys = []

                for key, item in self._memory_fallback.items():
                    if hasattr(item, "expires_at") and item.expires_at and now > item.expires_at:
                        expired_keys.append(key)

                for key in expired_keys:
                    del self._memory_fallback[key]
                    cleaned_count += 1

        except Exception as e:
            logger.error(f"Failed to cleanup expired items: {e}")

        if cleaned_count > 0:
            logger.info(f"Cleaned up {cleaned_count} expired working memory items")

        return cleaned_count

    async def get_memory_stats(self) -> dict[str, Any]:
        """Get working memory statistics."""

        stats = {
            "hit_count": self.hit_count,
            "miss_count": self.miss_count,
            "write_count": self.write_count,
            "hit_rate": self.hit_count / max(self.hit_count + self.miss_count, 1),
            "total_operations": self.hit_count + self.miss_count + self.write_count,
        }

        try:
            if self.redis_client:
                info = await self.redis_client.info("memory")
                stats.update(
                    {
                        "redis_memory_used": info.get("used_memory", 0),
                        "redis_memory_human": info.get("used_memory_human", "0B"),
                        "redis_connected": True,
                    }
                )
            else:
                stats.update(
                    {
                        "fallback_item_count": len(self._memory_fallback),
                        "redis_connected": False,
                    }
                )

        except Exception as e:
            logger.error(f"Failed to get memory stats: {e}")

        return stats

    def _make_key(self, key: str) -> str:
        """Make Redis key with prefix."""
        return f"{self.key_prefix}{key}"

    async def _store_redis(self, key: str, item: WorkingMemoryItem, ttl: int) -> None:
        """Store item in Redis."""
        redis_key = self._make_key(key)
        item_data = {
            "key": item.key,
            "value": item.value,
            "created_at": item.created_at.isoformat(),
            "expires_at": item.expires_at.isoformat() if item.expires_at else None,
            "access_count": item.access_count,
            "last_accessed": item.last_accessed.isoformat(),
            "tags": item.tags,
            "metadata": item.metadata,
        }

        if self.redis_client is not None:
            await self.redis_client.setex(
                redis_key, ttl, serialize_for_cache(item_data).decode("utf-8")
            )

    async def _retrieve_redis(self, key: str) -> WorkingMemoryItem | None:
        """Retrieve item from Redis."""
        redis_key = self._make_key(key)
        if self.redis_client is None:
            return None
        data = await self.redis_client.get(redis_key)

        if data:
            try:
                item_data = deserialize_from_cache(data)

                # Reconstruct datetime objects
                created_at = datetime.fromisoformat(item_data["created_at"])
                last_accessed = datetime.fromisoformat(item_data["last_accessed"])
                expires_at = None
                if item_data.get("expires_at"):
                    expires_at = datetime.fromisoformat(item_data["expires_at"])

                return WorkingMemoryItem(
                    key=item_data["key"],
                    value=item_data["value"],
                    created_at=created_at,
                    expires_at=expires_at,
                    access_count=item_data.get("access_count", 0),
                    last_accessed=last_accessed,
                    tags=item_data.get("tags", []),
                    metadata=item_data.get("metadata", {}),
                )

            except Exception as e:
                logger.error(f"Failed to deserialize Redis item {key}: {e}")
                return None

        return None

    def _store_fallback(self, key: str, item: WorkingMemoryItem) -> None:
        """Store item in memory fallback."""
        self._memory_fallback[key] = item

    def _retrieve_fallback(self, key: str) -> WorkingMemoryItem | None:
        """Retrieve item from memory fallback."""
        item = self._memory_fallback.get(key)

        # Check expiration
        if item and item.expires_at and datetime.now() > item.expires_at:
            del self._memory_fallback[key]
            return None

        return item

    async def _update_access_stats(self, key: str, item: WorkingMemoryItem) -> None:
        """Update access statistics for an item."""
        try:
            if self.redis_client:
                redis_key = self._make_key(key)
                # Get current TTL
                ttl = await self.redis_client.ttl(redis_key)
                if ttl > 0:
                    # Re-store with updated stats
                    await self._store_redis(key, item, ttl)
        except Exception as e:
            logger.debug(f"Failed to update access stats for {key}: {e}")

    async def _cleanup_loop(self) -> None:
        """Background cleanup loop."""
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval)
                await self.cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cleanup loop error: {e}")

    async def close(self) -> None:
        """Close working memory manager and cleanup resources."""
        if self._cleanup_task:
            self._cleanup_task.cancel()

        if self.redis_client:
            await self.redis_client.close()


__all__ = ["ConversationContext", "WorkingMemoryItem", "WorkingMemoryManager"]
