"""Characterization tests for API correctness middleware."""

from collections.abc import Awaitable, Callable
from typing import Any, cast

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import Response

from src.api.middleware.error_envelope import (
    http_exception_handler,
    validation_exception_handler,
)
from src.api.middleware.idempotency import (
    IdempotencyMiddleware,
    InMemoryIdempotencyStore,
)
from src.api.middleware.rate_limiter import (
    InMemoryRateLimitStore,
    RateLimitMiddleware,
)


class QueryRequest(BaseModel):
    """Minimal request model for test endpoints."""

    query: str = Field(..., min_length=3)


def create_test_app() -> tuple[FastAPI, dict[str, int]]:
    app = FastAPI()
    calls = {"query": 0}

    app.add_exception_handler(
        HTTPException,
        cast(
            "Callable[[Request, Exception], Response | Awaitable[Response]]",
            http_exception_handler,
        ),
    )
    app.add_exception_handler(
        RequestValidationError,
        cast(
            "Callable[[Request, Exception], Response | Awaitable[Response]]",
            validation_exception_handler,
        ),
    )
    app.add_middleware(
        IdempotencyMiddleware,
        store=InMemoryIdempotencyStore(),
        path_prefixes=("/api/v1/query",),
    )
    app.add_middleware(
        RateLimitMiddleware,
        store=InMemoryRateLimitStore(),
        requests_per_minute=2,
        exclude_paths=(),
    )

    @app.post("/api/v1/query/research")
    async def query_endpoint(request: QueryRequest) -> dict[str, Any]:
        calls["query"] += 1
        return {"call_count": calls["query"], "query": request.query}

    @app.get("/api/v1/query/failure")
    async def failure_endpoint() -> None:
        raise HTTPException(status_code=404, detail="Missing query")

    return app, calls


def test_idempotency_key_replays_cached_post_response() -> None:
    app, calls = create_test_app()

    with TestClient(app) as client:
        first = client.post(
            "/api/v1/query/research",
            headers={"Idempotency-Key": "retry-1", "X-API-Key": "idem-key"},
            json={"query": "repeatable research"},
        )
        second = client.post(
            "/api/v1/query/research",
            headers={"Idempotency-Key": "retry-1", "X-API-Key": "idem-key"},
            json={"query": "repeatable research"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert calls["query"] == 1


def test_idempotency_cache_includes_request_body_hash() -> None:
    app, calls = create_test_app()

    with TestClient(app) as client:
        first = client.post(
            "/api/v1/query/research",
            headers={"Idempotency-Key": "retry-2", "X-API-Key": "body-key"},
            json={"query": "first research"},
        )
        second = client.post(
            "/api/v1/query/research",
            headers={"Idempotency-Key": "retry-2", "X-API-Key": "body-key"},
            json={"query": "second research"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["call_count"] == 1
    assert second.json()["call_count"] == 2
    assert calls["query"] == 2


def test_http_errors_use_standard_error_envelope() -> None:
    app, _calls = create_test_app()

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/query/failure",
            headers={"X-API-Key": "errors"},
        )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "NOT_FOUND",
            "message": "Missing query",
            "details": {},
        }
    }


def test_validation_errors_use_standard_error_envelope() -> None:
    app, _calls = create_test_app()

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/query/research",
            headers={"X-API-Key": "validation"},
            json={"query": "no"},
        )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["message"] == "Invalid request"
    assert body["error"]["details"]["errors"][0]["loc"] == ["body", "query"]


def test_rate_limiter_enforces_per_api_key_limit() -> None:
    app, _calls = create_test_app()

    with TestClient(app) as client:
        first = client.post(
            "/api/v1/query/research",
            headers={"X-API-Key": "limited"},
            json={"query": "first request"},
        )
        second = client.post(
            "/api/v1/query/research",
            headers={"X-API-Key": "limited"},
            json={"query": "second request"},
        )
        third = client.post(
            "/api/v1/query/research",
            headers={"X-API-Key": "limited"},
            json={"query": "third request"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert int(third.headers["Retry-After"]) > 0
