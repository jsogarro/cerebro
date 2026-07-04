"""
Routing Cache Manager for MASR

Manages caching of routing decisions to improve response time and reduce
redundant complexity analysis and optimization for similar queries.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .masr import RoutingDecision


class RoutingCacheManager:
    """
    Manages routing decision caching with configurable size limits.

    Implements LRU-style eviction when cache size exceeds maximum.
    """

    def __init__(
        self,
        enabled: bool = True,
        max_size: int = 1000,
        eviction_batch_size: int = 100,
    ):
        """
        Initialize routing cache manager.

        Args:
            enabled: Whether caching is enabled
            max_size: Maximum number of cached decisions
            eviction_batch_size: Number of entries to evict when max_size reached
        """
        self.enabled = enabled
        self.max_size = max_size
        self.eviction_batch_size = eviction_batch_size
        self.decision_cache: dict[str, RoutingDecision] = {}

    def check_cache(
        self,
        query: str,
        context: dict[str, Any] | None,
        strategy: Any = None,
        constraints: dict[str, Any] | None = None,
    ) -> RoutingDecision | None:
        """
        Check if we have a cached routing decision.

        Args:
            query: The query string
            context: Additional context for cache key generation
            strategy: Requested routing strategy (part of the cache identity)
            constraints: Custom routing constraints (part of the cache identity)

        Returns:
            Cached RoutingDecision if found, None otherwise
        """
        if not self.enabled:
            return None

        cache_key = self._generate_cache_key(query, context, strategy, constraints)
        return self.decision_cache.get(cache_key)

    def cache_decision(
        self,
        query: str,
        context: dict[str, Any] | None,
        decision: RoutingDecision,
        strategy: Any = None,
        constraints: dict[str, Any] | None = None,
    ) -> None:
        """
        Cache a routing decision.

        Args:
            query: The query string
            context: Additional context for cache key generation
            decision: The routing decision to cache
            strategy: Requested routing strategy (part of the cache identity)
            constraints: Custom routing constraints (part of the cache identity)
        """
        if not self.enabled:
            return

        cache_key = self._generate_cache_key(query, context, strategy, constraints)
        self.decision_cache[cache_key] = decision

        # Evict oldest entries if cache exceeds max size
        if len(self.decision_cache) > self.max_size:
            self._evict_oldest_entries()

    def _generate_cache_key(
        self,
        query: str,
        context: dict[str, Any] | None,
        strategy: Any = None,
        constraints: dict[str, Any] | None = None,
    ) -> str:
        """
        Generate a cache key for query, context, strategy and constraints.

        The strategy and constraints are part of the key because the same
        query can route very differently under different strategies (e.g.
        ``quality_focused`` vs ``cost_efficient``) or cost/quality/latency
        constraints. Omitting them caused distinct requests to collide and
        receive a stale decision from the first caller.

        Args:
            query: The query string
            context: Additional context (user_id, domain, etc.)
            strategy: Requested routing strategy
            constraints: Custom routing constraints

        Returns:
            MD5 hash string as cache key
        """
        # Create hash from query and relevant context
        cache_data = {
            "query": query.lower().strip(),
            "user_id": context.get("user_id") if context else None,
            "domain": context.get("domain") if context else None,
            # ``strategy`` may be an enum or a bare string; normalize to str so
            # both forms hash identically.
            "strategy": str(strategy) if strategy is not None else None,
            "constraints": str(sorted(constraints.items())) if constraints else None,
        }

        cache_string = str(sorted(cache_data.items()))
        return hashlib.md5(cache_string.encode()).hexdigest()

    def _evict_oldest_entries(self) -> None:
        """Remove oldest entries from cache based on timestamp."""
        oldest_keys = sorted(
            self.decision_cache.keys(),
            key=lambda k: self.decision_cache[k].timestamp,
        )[: self.eviction_batch_size]

        for key in oldest_keys:
            del self.decision_cache[key]

    def clear(self) -> None:
        """Clear all cached decisions."""
        self.decision_cache.clear()

    def get_cache_size(self) -> int:
        """Get current cache size."""
        return len(self.decision_cache)


__all__ = ["RoutingCacheManager"]
