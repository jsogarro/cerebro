"""Mounted MASR HTTP compatibility contracts."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.ai_brain.router.masr import MASRouter
from src.api.routes.masr_api import get_masr_routing_service, router
from src.api.services.masr_routing_service import MASRRoutingService
from src.models.masr_api_models import RoutingDecisionResponse


@pytest.fixture
def routing_service() -> MASRRoutingService:
    return MASRRoutingService(router=MASRouter(config={"enable_caching": False}))


@pytest.fixture
def client(routing_service: MASRRoutingService) -> Iterator[TestClient]:
    from src.api.main import app

    app.dependency_overrides[get_masr_routing_service] = lambda: routing_service
    try:
        # Deliberately do not enter lifespan: the dependency override supplies
        # the service, while the real app supplies the canonical handlers.
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_masr_routing_service, None)


def _decision() -> RoutingDecisionResponse:
    return RoutingDecisionResponse(
        routing_id="route-1",
        domain="research",
        complexity="moderate",
        strategy="balanced",
        collaboration_mode="hierarchical",
        supervisor_allocations=[],
        selected_models=[],
        estimated_cost=0.1,
        estimated_latency_ms=1000,
        confidence_score=0.8,
        reasoning="Compatibility fixture",
        alternatives=[],
    )


def test_route_preserves_request_and_response_fields(
    client: TestClient,
    routing_service: MASRRoutingService,
) -> None:
    routing_service.get_routing_decision = AsyncMock(return_value=_decision())

    response = client.post(
        "/api/v1/masr/route",
        json={
            "query": "Compare two approaches",
            "context": {"domain": "research"},
            "strategy": "balanced",
            "max_cost": 1.0,
            "min_quality": 0.7,
            "timeout_ms": 5000,
        },
    )

    assert response.status_code == 200
    assert set(response.json()) == {
        "routing_id",
        "domain",
        "complexity",
        "strategy",
        "collaboration_mode",
        "supervisor_allocations",
        "selected_models",
        "estimated_cost",
        "estimated_latency_ms",
        "confidence_score",
        "reasoning",
        "alternatives",
    }


def test_route_preserves_structured_400_error(
    client: TestClient,
    routing_service: MASRRoutingService,
) -> None:
    routing_service.get_routing_decision = AsyncMock(
        side_effect=ValueError("invalid routing request")
    )

    response = client.post("/api/v1/masr/route", json={"query": "test"})

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "INVALID_REQUEST",
            "message": "invalid routing request",
            "details": {
                "request": {
                    "query": "test",
                    "context": None,
                    "strategy": None,
                    "max_cost": None,
                    "min_quality": None,
                    "timeout_ms": None,
                }
            },
        }
    }


def test_feedback_preserves_legacy_fields(
    client: TestClient,
    routing_service: MASRRoutingService,
) -> None:
    routing_service.submit_feedback = AsyncMock(
        return_value={
            "status": "success",
            "routing_id": "route-1",
            "feedback_processed": True,
            "learning_updated": False,
        }
    )

    response = client.post(
        "/api/v1/masr/feedback",
        json={
            "routing_id": "route-1",
            "actual_cost": 0.2,
            "actual_latency_ms": 1200,
            "quality_score": 0.9,
            "error_occurred": False,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "routing_id": "route-1",
        "feedback_processed": True,
        "learning_updated": False,
    }


@pytest.mark.parametrize(
    ("path", "expected_fields"),
    [
        (
            "/api/v1/masr/strategies",
            {"strategies", "default_strategy", "total_count"},
        ),
        (
            "/api/v1/masr/models",
            {"models", "tiers", "total_count", "providers"},
        ),
        (
            "/api/v1/masr/status",
            {
                "status",
                "uptime_seconds",
                "total_routes",
                "average_latency_ms",
                "success_rate",
                "active_supervisors",
                "performance_metrics",
                "model_availability",
                "learning_metrics",
                "last_error",
                "last_error_time",
            },
        ),
    ],
)
def test_read_endpoints_preserve_top_level_contract(
    client: TestClient,
    path: str,
    expected_fields: set[str],
) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert set(response.json()) == expected_fields


def test_mounted_api_without_lifespan_returns_503() -> None:
    test_app = FastAPI()
    test_app.include_router(router)

    response = TestClient(test_app).get("/api/v1/masr/strategies")

    assert response.status_code == 503
    assert response.json()["detail"] == "MASR runtime is unavailable"
