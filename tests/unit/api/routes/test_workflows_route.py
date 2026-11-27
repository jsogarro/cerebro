from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import workflows
from src.api.services.workflow_catalog import DEFAULT_RESEARCH_WORKFLOW_ID
from tests.fixtures.authenticated_caller import allow_authenticated


def _client(*, authenticated: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(workflows.router, prefix="/api/v1")
    if authenticated:
        allow_authenticated(app)
    return TestClient(app)


def test_listing_serves_the_golden_run_workflow_and_catalog_hash():
    response = _client().get("/api/v1/workflows")
    assert response.status_code == 200
    body = response.json()
    assert len(body["catalog_hash"]) == 64
    (workflow,) = body["workflows"]
    assert workflow["workflow_id"] == DEFAULT_RESEARCH_WORKFLOW_ID
    assert workflow["supports_dry_run"] is True


def test_listing_requires_an_authenticated_caller():
    assert _client(authenticated=False).get("/api/v1/workflows").status_code in (
        401,
        403,
    )
