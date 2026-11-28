"""Unit tests for Q10: /masr/feedback stored as labelled data only, no live learning."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.ai_brain.router.adaptive_state_store import InMemoryAdaptiveStateStore
from src.ai_brain.router.masr import MASRouter
from src.api.routes.masr_api import get_masr_routing_service, submit_feedback
from src.api.services.masr_routing_service import (
    LabelledRoutingFeedback,
    MASRRoutingService,
)
from src.auth.models import TokenPayload
from src.middleware.auth_middleware import get_jwt_service
from src.models.masr_api_models import RoutingFeedback, RoutingRequest

AUTH_ORG_ID = "11111111-1111-1111-1111-111111111111"
AUTH_TOKEN = "feedback-test-token"


class _TestJWTService:
    async def validate_token(self, token: str) -> TokenPayload:
        assert token == AUTH_TOKEN
        now = datetime.now(UTC)
        return TokenPayload(
            sub="user-1",
            email="test@example.com",
            organization_id=AUTH_ORG_ID,
            jti="jti-feedback",
            iat=now,
            exp=now + timedelta(minutes=5),
        )


def _decision_fingerprint(decision: Any) -> dict[str, Any]:
    raw = asdict(decision)
    raw.pop("query_id", None)
    raw.pop("timestamp", None)
    return raw


@pytest.mark.asyncio
async def test_submit_feedback_stores_labelled_record_without_live_learning() -> None:
    store = InMemoryAdaptiveStateStore()
    router = MASRouter(
        config={"adaptive_routing_enabled": False, "enable_caching": False},
        adaptive_state_store=store,
    )
    record_spy = AsyncMock(wraps=router.record_routing_outcome)
    router.record_routing_outcome = record_spy  # type: ignore[method-assign]
    service = MASRRoutingService(router=router)

    decision_resp = await service.get_routing_decision(
        RoutingRequest(query="Compare two empirical methods for causal inference")
    )
    metrics_before = {k: dict(v) for k, v in service.performance_metrics.items()}
    route_before = await router.route("Compare two empirical methods for causal inference")

    response = await service.submit_feedback(
        RoutingFeedback(
            routing_id=decision_resp.routing_id,
            actual_cost=0.12,
            actual_latency_ms=850,
            quality_score=0.91,
            user_satisfaction=0.88,
            error_occurred=False,
        ),
        organization_id=AUTH_ORG_ID,
    )

    records = service.get_labelled_feedback()
    assert len(records) == 1
    entry = records[0]
    assert isinstance(entry, LabelledRoutingFeedback)
    assert entry.routing_id == decision_resp.routing_id
    assert entry.organization_id == AUTH_ORG_ID
    assert entry.actual_cost == pytest.approx(0.12)
    assert entry.actual_latency_ms == 850
    assert entry.quality_score == pytest.approx(0.91)
    assert entry.user_satisfaction == pytest.approx(0.88)
    assert entry.label_source == "manual"
    assert entry.eligible_for_live_learning is False

    # No live learning: router outcome recording, bandit state, metrics, and routing are untouched.
    assert response["learning_updated"] is False
    assert response["eligible"] is False
    assert response["source"] == "manual"
    record_spy.assert_not_awaited()
    assert (await store.load()).snapshot is None
    assert service.performance_metrics == metrics_before
    route_after = await router.route("Compare two empirical methods for causal inference")
    assert _decision_fingerprint(route_before) == _decision_fingerprint(route_after)

    await service.close()
    assert service.get_labelled_feedback() == []


def test_masr_feedback_route_stores_tenant_scoped_labelled_data_only() -> None:
    from src.api.main import app

    router = MASRouter(config={"adaptive_routing_enabled": False, "enable_caching": False})
    service = MASRRoutingService(router=router)
    app.dependency_overrides[get_masr_routing_service] = lambda: service
    app.dependency_overrides[get_jwt_service] = _TestJWTService
    try:
        client = TestClient(app, headers={"Authorization": f"Bearer {AUTH_TOKEN}"})
        route_resp = client.post(
            "/api/v1/masr/route",
            json={"query": "Evaluate supply chain resilience under demand shocks"},
        )
        assert route_resp.status_code == 200
        routing_id = route_resp.json()["routing_id"]

        fb_resp = client.post(
            "/api/v1/masr/feedback",
            json={
                "routing_id": routing_id,
                "actual_cost": 0.05,
                "actual_latency_ms": 420,
                "quality_score": 0.85,
                "error_occurred": False,
            },
        )
        assert fb_resp.status_code == 200
        body = fb_resp.json()
        assert body["learning_updated"] is False
        assert body["eligible"] is False
        records = service.get_labelled_feedback()
        assert len(records) == 1
        assert records[0].routing_id == routing_id
        assert records[0].organization_id == AUTH_ORG_ID
        assert records[0].eligible_for_live_learning is False
        # OpenAPI / handler docstring must not claim live learning.
        assert "no live learning" in (submit_feedback.__doc__ or "").lower()
    finally:
        app.dependency_overrides.pop(get_masr_routing_service, None)
        app.dependency_overrides.pop(get_jwt_service, None)
