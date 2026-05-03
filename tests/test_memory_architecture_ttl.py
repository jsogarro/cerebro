"""Memory architecture tests for TTL and bounded fallback storage."""

import asyncio

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
