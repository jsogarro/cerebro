"""Episodic memory reports database failures instead of returning empty results."""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from src.ai_brain.memory.episodic_memory import (
    Episode,
    EpisodeQuery,
    EpisodicMemoryManager,
)


def db_backed(**methods: Any) -> EpisodicMemoryManager:
    memory = EpisodicMemoryManager({})
    memory.engine = object()
    memory.session_factory = object()
    for name, error in methods.items():
        setattr(memory, name, AsyncMock(side_effect=error))
    return memory


@pytest.mark.asyncio
async def test_database_failure_propagates_from_reads_cleanup_and_gdpr_delete() -> None:
    memory = db_backed(
        _retrieve_episodes_db=SQLAlchemyError("down"),
        _get_session_summary_db=SQLAlchemyError("down"),
        _cleanup_old_episodes_db=SQLAlchemyError("down"),
        _delete_by_user_id_db=SQLAlchemyError("down"),
    )

    with pytest.raises(SQLAlchemyError):
        await memory.retrieve_episodes(EpisodeQuery())
    with pytest.raises(SQLAlchemyError):
        await memory.get_session_summary("s")
    with pytest.raises(SQLAlchemyError):
        await memory.cleanup_old_episodes()
    with pytest.raises(SQLAlchemyError):
        await memory.delete_by_user_id("u")


@pytest.mark.asyncio
async def test_store_reports_database_failure_but_not_bugs() -> None:
    assert (
        await db_backed(_store_episode_db=SQLAlchemyError("down")).store_episode(
            Episode(id="e")
        )
        is False
    )

    with pytest.raises(RuntimeError):
        await db_backed(_store_episode_db=RuntimeError("bug")).store_episode(
            Episode(id="e")
        )
