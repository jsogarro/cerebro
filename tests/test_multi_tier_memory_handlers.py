"""The multi-tier coordinator no longer converts tier failures into fake results."""

from unittest.mock import AsyncMock

import pytest

from src.ai_brain.memory.multi_tier_memory import MemoryContext, MultiTierMemorySystem

CTX = MemoryContext(session_id="s", user_id="u", agent_id="a", task_type="t")


@pytest.mark.asyncio
async def test_store_and_learn_propagate_tier_failures() -> None:
    system = MultiTierMemorySystem({})
    system.working_memory.retrieve_conversation_context = AsyncMock(  # type: ignore[method-assign]
        return_value=None
    )
    system.working_memory.store_conversation_context = AsyncMock(  # type: ignore[method-assign]
        return_value=True
    )
    system.episodic_memory.store_episode = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("db")
    )

    with pytest.raises(RuntimeError):
        await system.store_interaction(CTX, {"x": 1}, 0.9)
    with pytest.raises(RuntimeError):
        await system.learn_from_interaction(CTX, {"x": 1}, 0.9, True)


@pytest.mark.asyncio
async def test_consolidation_propagates_cleanup_failure() -> None:
    system = MultiTierMemorySystem({})
    system.episodic_memory.cleanup_old_episodes = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("db")
    )

    with pytest.raises(RuntimeError):
        await system.consolidate_memory()


@pytest.mark.asyncio
async def test_tier_recall_failure_is_isolated_by_gather_not_hidden_per_tier() -> None:
    system = MultiTierMemorySystem({})
    system.episodic_memory.retrieve_episodes = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("db")
    )

    with pytest.raises(RuntimeError):
        await system._recall_from_episodic_memory("q", CTX)

    recall = await system.intelligent_recall("q", CTX)
    assert recall.primary_results == []


@pytest.mark.asyncio
async def test_intelligent_recall_propagates_ranking_bug() -> None:
    system = MultiTierMemorySystem({})
    system._combine_and_rank_results = lambda *a, **k: 1 / 0  # type: ignore[method-assign, assignment]

    with pytest.raises(ZeroDivisionError):
        await system.intelligent_recall("q", CTX)
