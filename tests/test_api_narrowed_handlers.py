"""Failures the API layer used to swallow now surface; expected ones still degrade."""

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import OperationalError

from src.api.middleware.idempotency import (
    InMemoryIdempotencyStore,
    ResilientIdempotencyStore,
)
from src.api.middleware.rate_limiter import (
    InMemoryRateLimitStore,
    ResilientRateLimitStore,
)
from src.api.routes import research
from src.api.services.direct_execution_service import DirectExecutionService

ORG = "11111111-1111-1111-1111-111111111111"


class _FailingStore:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def increment(self, *_args: Any) -> tuple[int, int]:
        raise self.error

    async def reset(self, *_args: Any) -> None:
        raise self.error

    async def get(self, *_args: Any) -> None:
        raise self.error

    async def reserve(self, *_args: Any) -> str:
        raise self.error


async def test_rate_limit_falls_back_when_redis_is_down() -> None:
    store = ResilientRateLimitStore(
        _FailingStore(RedisConnectionError("down")), InMemoryRateLimitStore()
    )
    count, _ttl = await store.increment("k", 60)
    assert count == 1


async def test_rate_limit_does_not_hide_a_store_bug() -> None:
    store = ResilientRateLimitStore(
        _FailingStore(TypeError("bug")), InMemoryRateLimitStore()
    )
    with pytest.raises(TypeError):
        await store.increment("k", 60)


async def test_idempotency_falls_back_when_redis_is_down() -> None:
    store = ResilientIdempotencyStore(
        _FailingStore(RedisConnectionError("down")), InMemoryIdempotencyStore()
    )
    assert await store.get("k") is None
    assert await store.reserve("k", 60) is not None


async def test_idempotency_does_not_hide_a_store_bug() -> None:
    store = ResilientIdempotencyStore(
        _FailingStore(TypeError("bug")), InMemoryIdempotencyStore()
    )
    with pytest.raises(TypeError):
        await store.reserve("k", 60)


async def test_progress_endpoint_does_not_report_fake_zero_progress() -> None:
    repo = SimpleNamespace(
        get_for_user=AsyncMock(return_value=SimpleNamespace(status="draft"))
    )

    class _BrokenService:
        @property
        def active_executions(self) -> dict[str, Any]:
            raise RuntimeError("execution state unavailable")

    with pytest.raises(RuntimeError, match="unavailable"):
        await research.get_research_progress(
            uuid.uuid4(),
            repo=repo,
            tenant_context=SimpleNamespace(user_id="u", organization_id=ORG),
            execution_service=_BrokenService(),
        )


def _service(session_factory: Any) -> DirectExecutionService:
    return DirectExecutionService(
        masr_router=AsyncMock(),
        supervisor_bridge=AsyncMock(),
        supervisor_factory=AsyncMock(),
        event_publisher=AsyncMock(),
        session_factory=session_factory,
    )


def _failing_session_factory() -> Any:
    raise OperationalError("SELECT", {}, Exception("db down"))


async def test_resume_does_not_turn_a_database_outage_into_nothing_to_resume() -> None:
    service = _service(_failing_session_factory)
    with pytest.raises(OperationalError):
        await service.resume_execution(uuid.uuid4(), organization_id=ORG)


async def test_checkpoint_does_not_hide_a_non_database_bug() -> None:
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    service = _service(lambda: session)
    status = SimpleNamespace(
        execution_id="e",
        project_id=str(uuid.uuid4()),
        started_at=None,  # .isoformat() raises AttributeError: a bug, not an outage
    )
    with (
        patch("src.api.services.direct_execution_service.CheckpointRepository"),
        pytest.raises(AttributeError),
    ):
        await service._checkpoint(status, "phase")
