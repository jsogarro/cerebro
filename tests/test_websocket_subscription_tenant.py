"""Subscriptions made by message over an open WebSocket honour the tenant boundary.

Runs through ``src.api.main.app`` so the auth middleware and the mounted
WebSocket authentication dependency are the production ones.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.api.routes import websocket as websocket_routes
from src.auth.models import TokenPayload
from src.middleware.auth_middleware import get_jwt_service
from src.models.db.research_project import ProjectStatus, ResearchProject

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "22222222-2222-2222-2222-222222222222"
USER_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
USER_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


class _UserAJWTService:
    async def validate_token(self, token: str) -> TokenPayload:
        now = datetime.now(UTC)
        return TokenPayload(
            sub=USER_A,
            organization_id=ORG_A,
            jti=f"ws-{token}",
            iat=now,
            exp=now + timedelta(minutes=5),
        )


async def _seed(database_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    engine = create_async_engine(database_url)
    from src.models.db.agent_task import AgentTask
    from src.models.db.research_result import ResearchResult
    from src.models.db.workflow_checkpoint import WorkflowCheckpoint

    async with engine.begin() as connection:
        for model in (ResearchProject, AgentTask, ResearchResult, WorkflowCheckpoint):
            await connection.run_sync(model.__table__.create)
    ids = []
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        for user_id, organization_id in ((USER_A, ORG_A), (USER_B, ORG_B)):
            project = ResearchProject(
                title="Confidential project",
                query='{"text": "confidential", "domains": ["ai"]}',
                domains=["ai"],
                status=ProjectStatus.IN_PROGRESS,
                user_id=user_id,
                organization_id=uuid.UUID(organization_id),
                project_metadata={},
            )
            session.add(project)
            await session.commit()
            ids.append(project.id)
    await engine.dispose()
    return ids[0], ids[1]


@pytest.fixture
def ws_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, uuid.UUID, uuid.UUID]]:
    from src.api.main import app

    database_url = f"sqlite+aiosqlite:///{tmp_path / 'ws.db'}"
    own_project, foreign_project = asyncio.run(_seed(database_url))
    engine = create_async_engine(database_url)
    monkeypatch.setattr(
        websocket_routes,
        "get_session_factory",
        lambda: async_sessionmaker(engine, class_=AsyncSession),
    )
    app.dependency_overrides[get_jwt_service] = _UserAJWTService
    try:
        yield TestClient(app), own_project, foreign_project
    finally:
        app.dependency_overrides.pop(get_jwt_service, None)


def _subscribe(websocket: Any, project_id: uuid.UUID) -> dict[str, Any]:
    websocket.send_text(
        json.dumps(
            {
                "type": "subscription",
                "data": {"action": "subscribe", "project_id": str(project_id)},
            }
        )
    )
    while True:
        message = json.loads(websocket.receive_text())
        data = message.get("data") or {}
        if "success" in data:
            return data


def test_subscribe_by_message_refuses_another_tenants_project(ws_client) -> None:
    client, own_project, foreign_project = ws_client

    with client.websocket_connect("/ws?token=user-a") as websocket:
        refused = _subscribe(websocket, foreign_project)
        allowed = _subscribe(websocket, own_project)

    assert refused["success"] is False
    assert str(foreign_project) not in map(str, allowed["active_subscriptions"])
    assert allowed["success"] is True


def test_project_socket_refuses_additional_foreign_subscription(ws_client) -> None:
    client, own_project, foreign_project = ws_client

    with client.websocket_connect(
        f"/ws/projects/{own_project}?token=user-a"
    ) as websocket:
        refused = _subscribe(websocket, foreign_project)

    assert refused["success"] is False
    assert str(foreign_project) not in map(str, refused["active_subscriptions"])


def test_public_websocket_health_lists_no_project_ids() -> None:
    from src.api.main import app

    response = TestClient(app).get("/ws/health")

    assert response.status_code == 200
    assert "active_projects" not in response.json()["websocket_stats"]
