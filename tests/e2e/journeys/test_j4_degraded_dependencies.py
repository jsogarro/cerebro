"""J4: a down dependency is reported, never papered over."""

import httpx
import pytest

from tests.e2e.journeys.harness import BASE_URL

pytestmark = pytest.mark.e2e


async def _checks() -> dict[str, str]:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        response = await client.get("/ready")
    assert response.status_code == 200, response.text
    return response.json()["checks"]


async def test_readiness_reports_database_and_redis_ok():
    checks = await _checks()
    assert checks["database"] == "ok"
    assert checks["redis"] == "ok"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="P16: Qdrant is not a readiness dependency",
)
async def test_readiness_reports_qdrant():
    assert "qdrant" in await _checks()


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="P22: grammar checker is not a readiness dependency",
)
async def test_readiness_reports_grammar_checker():
    assert "grammar" in await _checks()


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="P04: provider circuit state is not surfaced by readiness",
)
async def test_readiness_reports_provider_circuit():
    assert "providers" in await _checks()
