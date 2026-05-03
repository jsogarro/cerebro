"""Memory architecture tests for TTL and bounded fallback storage."""

import asyncio
from datetime import datetime, timedelta

from src.ai_brain.memory.episodic_memory import (
    Episode,
    EpisodeQuery,
    EpisodicMemoryManager,
)
from src.ai_brain.memory.working_memory import WorkingMemoryManager


def build_fallback_working_memory(config: dict[str, int] | None = None) -> WorkingMemoryManager:
    manager = WorkingMemoryManager(config or {})
    manager.redis_client = None
    manager._memory_fallback = {}
    return manager


def test_working_memory_fallback_expires_items_by_ttl() -> None:
    async def run() -> None:
        memory = build_fallback_working_memory({"default_ttl": 1})

        assert await memory.store("short", "value", ttl=0)
        assert await memory.retrieve("short") is None
        assert "short" not in memory._memory_fallback

    asyncio.run(run())


def test_working_memory_fallback_evicts_least_recently_used_item() -> None:
    async def run() -> None:
        memory = build_fallback_working_memory({"max_size": 2})

        assert await memory.store("a", "oldest")
        assert await memory.store("b", "middle")
        assert await memory.retrieve("a") == "oldest"
        assert await memory.store("c", "newest")

        assert await memory.retrieve("a") == "oldest"
        assert await memory.retrieve("b") is None
        assert await memory.retrieve("c") == "newest"

    asyncio.run(run())


def test_working_memory_default_bounds_match_plan() -> None:
    memory = build_fallback_working_memory()

    assert memory.max_size == 1000
    assert memory.default_ttl == 3600
    assert memory.cleanup_interval == 300


def build_episodic_memory(config: dict[str, int] | None = None) -> EpisodicMemoryManager:
    return EpisodicMemoryManager(config or {})


def test_episodic_memory_default_bounds_match_plan() -> None:
    memory = build_episodic_memory()

    assert memory.max_size == 10000
    assert memory.retention_days == 7
    assert memory.cleanup_interval == 300


def test_episodic_memory_fallback_evicts_least_recently_used_episode() -> None:
    async def run() -> None:
        memory = build_episodic_memory({"max_size": 2})

        first = Episode(id="first", session_id="s1")
        second = Episode(id="second", session_id="s1")
        third = Episode(id="third", session_id="s1")

        assert await memory.store_episode(first)
        assert await memory.store_episode(second)
        assert await memory.retrieve_episodes(
            EpisodeQuery(limit=1, offset=0, order_direction="asc")
        )
        assert await memory.store_episode(third)

        stored_ids = {episode.id for episode in memory._fallback_storage}
        assert stored_ids == {"first", "third"}

    asyncio.run(run())


def test_episodic_memory_cleanup_uses_seven_day_ttl_default() -> None:
    async def run() -> None:
        memory = build_episodic_memory()
        memory._fallback_storage = [
            Episode(id="expired", timestamp=datetime.now() - timedelta(days=8)),
            Episode(id="fresh", timestamp=datetime.now() - timedelta(days=1)),
        ]

        assert await memory.cleanup_old_episodes() == 1
        assert [episode.id for episode in memory._fallback_storage] == ["fresh"]

    asyncio.run(run())
