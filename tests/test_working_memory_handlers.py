"""Working memory lets unexpected errors propagate; only Redis/serialisation errors are absorbed."""

from typing import Any

import pytest
from redis.exceptions import RedisError

from src.ai_brain.memory.working_memory import WorkingMemoryManager


class FakeRedis:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def _fail(self, *args: Any, **kwargs: Any) -> Any:
        raise self.error

    ping = setex = get = delete = exists = keys = info = ttl = _fail


def manager_with(error: Exception) -> WorkingMemoryManager:
    memory = WorkingMemoryManager({})
    memory.redis_client = FakeRedis(error)  # type: ignore[assignment]
    return memory


@pytest.mark.asyncio
async def test_redis_errors_degrade_store_retrieve_delete_exists() -> None:
    memory = manager_with(RedisError("down"))

    assert await memory.store("k", "v") is False
    assert await memory.retrieve("k") is None
    assert await memory.delete("k") is False
    assert await memory.exists("k") is False
    assert await memory.get_keys_by_pattern("*") == []
    assert (await memory.get_memory_stats())["hit_count"] == 0


@pytest.mark.asyncio
async def test_unexpected_errors_propagate_from_store_and_retrieve() -> None:
    memory = manager_with(RuntimeError("bug"))

    with pytest.raises(RuntimeError):
        await memory.store("k", "v")
    with pytest.raises(RuntimeError):
        await memory.retrieve("k")
    with pytest.raises(RuntimeError):
        await memory.get_memory_stats()


@pytest.mark.asyncio
async def test_initialize_falls_back_on_redis_error_only() -> None:
    memory = manager_with(RedisError("down"))
    await memory.initialize()
    assert memory.redis_client is None

    with pytest.raises(RuntimeError):
        await manager_with(RuntimeError("bug")).initialize()


@pytest.mark.asyncio
async def test_gdpr_delete_and_tag_lookup_do_not_hide_redis_failure() -> None:
    memory = manager_with(RedisError("down"))

    with pytest.raises(RedisError):
        await memory.delete_by_user_id("u1")
    with pytest.raises(RedisError):
        await memory.get_keys_by_tags(["t"])


@pytest.mark.asyncio
async def test_corrupt_stored_item_reads_as_missing() -> None:
    class Corrupt(FakeRedis):
        async def get(self, *args: Any, **kwargs: Any) -> bytes:
            return b"not json"

    memory = WorkingMemoryManager({})
    memory.redis_client = Corrupt(RuntimeError())  # type: ignore[assignment]

    assert await memory._retrieve_redis("k") is None


@pytest.mark.asyncio
async def test_cleanup_expired_propagates_unexpected_errors() -> None:
    memory = WorkingMemoryManager({})
    memory.redis_client = None
    memory._memory_fallback = {"k": object()}  # type: ignore[dict-item]

    # Items without expires_at are skipped, so a malformed item must not raise
    assert await memory.cleanup_expired() == 0

    class Broken:
        expires_at = "not-a-datetime"

    memory._memory_fallback = {"k": Broken()}  # type: ignore[dict-item]
    with pytest.raises(TypeError):
        await memory.cleanup_expired()
