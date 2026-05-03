from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.ai_brain.memory.episodic_memory import (
    Episode,
    EpisodeQuery,
    EpisodicMemoryManager,
)
from src.ai_brain.memory.multi_tier_memory import MultiTierMemorySystem
from src.ai_brain.memory.procedural_memory import Procedure
from src.ai_brain.memory.semantic_memory import SemanticItem
from src.ai_brain.memory.working_memory import WorkingMemoryManager
from src.api.routes import query_api
from src.core.pii_redactor import PIIRedactor, redact_pii
from src.models.db.agent_task import AgentTask
from src.models.db.research_project import ResearchProject
from src.models.db.session import get_session
from src.models.db.user import User


def test_pii_redactor_masks_common_identifiers() -> None:
    raw = (
        "Contact Jane at jane.doe@example.com or (415) 555-2671. "
        "SSN 123-45-6789, card 4111 1111 1111 1111."
    )

    redacted = PIIRedactor.redact(raw)

    assert "jane.doe@example.com" not in redacted
    assert "(415) 555-2671" not in redacted
    assert "123-45-6789" not in redacted
    assert "4111 1111 1111 1111" not in redacted
    assert "[EMAIL]" in redacted
    assert "[PHONE]" in redacted
    assert "[SSN]" in redacted
    assert "[CREDIT_CARD]" in redacted


def test_redact_pii_converts_non_string_values() -> None:
    assert redact_pii({"email": "user@example.com"}) == "{'email': '[EMAIL]'}"


def test_query_api_logs_do_not_interpolate_raw_query_text() -> None:
    source = inspect.getsource(query_api)

    assert "Intelligent analysis query: {request.query" not in source
    assert "Intelligent synthesis query: {request.query" not in source
    assert "query_preview=redact_pii" in source


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.values[key] = value

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def ttl(self, key: str) -> int:
        return 300

    async def keys(self, pattern: str) -> list[bytes]:
        prefix = pattern.rstrip("*")
        return [key.encode() for key in self.values if key.startswith(prefix)]

    async def delete(self, key: str) -> int:
        return 1 if self.values.pop(key, None) is not None else 0


def test_working_memory_encrypts_redis_payload_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        monkeypatch.setenv("MEMORY_ENCRYPTION_KEY", Fernet.generate_key().decode())
        memory = WorkingMemoryManager({"key_prefix": "test:"})
        fake_redis = FakeRedis()
        memory.redis_client = cast(Any, fake_redis)

        assert await memory.store(
            "query",
            {"query": "contact jane.doe@example.com"},
            metadata={"user_id": "user-1"},
        )

        stored = fake_redis.values["test:query"]
        assert "jane.doe@example.com" not in stored
        assert await memory.retrieve("query") == {"query": "contact jane.doe@example.com"}

    asyncio.run(run())


def test_episodic_memory_encrypts_stored_episode_payload_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setenv("MEMORY_ENCRYPTION_KEY", Fernet.generate_key().decode())
        memory = EpisodicMemoryManager({})
        episode = Episode(
            session_id="session-1",
            user_id="user-1",
            event_data={"query": "call me at 415-555-2671"},
            context={"email": "jane.doe@example.com"},
            metadata={"source": "test"},
        )

        assert await memory.store_episode(episode)
        stored = memory._fallback_storage[0]
        assert "415-555-2671" not in str(stored.event_data)
        assert "jane.doe@example.com" not in str(stored.context)

        retrieved = await memory.retrieve_episodes(EpisodeQuery(user_id="user-1"))
        assert retrieved[0].event_data == {"query": "call me at 415-555-2671"}
        assert retrieved[0].context == {"email": "jane.doe@example.com"}

    asyncio.run(run())


def test_multi_tier_memory_purge_removes_user_data_from_all_fallback_tiers(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        memory = MultiTierMemorySystem(
            {"procedural_memory": {"storage_path": str(tmp_path / "procedures.json")}}
        )
        memory.working_memory.redis_client = None
        assert await memory.working_memory.store(
            "owned-working",
            {"value": "working"},
            metadata={"user_id": "user-1"},
        )
        assert await memory.episodic_memory.store_episode(
            Episode(id="owned-episode", session_id="s1", user_id="user-1")
        )
        memory.semantic_memory._fallback_storage = [
            SemanticItem(id="owned-semantic", metadata={"user_id": "user-1"}),
        ]
        memory.procedural_memory.procedures = {
            "owned-procedure": Procedure(
                id="owned-procedure",
                metadata={"user_id": "user-1"},
            )
        }

        deleted = await memory.purge_user_data("user-1")

        assert deleted == {
            "working_memory": 1,
            "episodic_memory": 1,
            "semantic_memory": 1,
            "procedural_memory": 1,
        }

    asyncio.run(run())


class FakeMemorySystem:
    def __init__(self) -> None:
        self.purged_user_ids: list[str] = []

    async def purge_user_data(self, user_id: str) -> dict[str, int]:
        self.purged_user_ids.append(user_id)
        return {"working_memory": 2, "episodic_memory": 1}


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_gdpr_delete_purges_user_database_rows_and_memory() -> None:
    from src.api.routes import users

    user_id = uuid4()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(User.__table__.create)
        await conn.run_sync(ResearchProject.__table__.create)
        await conn.run_sync(AgentTask.__table__.create)

    async with session_factory() as db_session:
        await db_session.execute(
            User.__table__.insert().values(
                id=user_id,
                email="gdpr@example.com",
                username="gdpr-user",
                hashed_password="hashed",
            )
        )
        project_id = uuid4()
        await db_session.execute(
            ResearchProject.__table__.insert().values(
                id=project_id,
                title="PII project",
                query="email gdpr@example.com",
                user_id=str(user_id),
                domains=["privacy"],
                status="DRAFT",
            )
        )
        await db_session.execute(
            AgentTask.__table__.insert().values(
                id=uuid4(),
                project_id=project_id,
                agent_type="privacy",
                status="PENDING",
                input_data={"query": "gdpr@example.com"},
            )
        )
        await db_session.commit()

        memory = FakeMemorySystem()
        app = FastAPI()
        app.include_router(users.router)
        app.dependency_overrides[get_session] = lambda: db_session
        app.dependency_overrides[users.get_memory_system] = lambda: memory

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.delete(f"/users/{user_id}/gdpr")

        assert response.status_code == 200
        assert response.json()["deleted"]["users"] == 1
        assert response.json()["deleted"]["memory"]["working_memory"] == 2
        assert memory.purged_user_ids == [str(user_id)]
        assert (await db_session.execute(select(User))).scalars().all() == []
        assert (await db_session.execute(select(ResearchProject))).scalars().all() == []
        assert (await db_session.execute(select(AgentTask))).scalars().all() == []

    await engine.dispose()
