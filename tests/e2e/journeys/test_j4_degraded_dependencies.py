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


async def test_readiness_reports_providers_informationally():
    # Unprobed readiness is "unknown", never "ok", and never gates /ready (J4:18).
    assert (await _checks())["providers"] in {"unknown", "unconfigured"}


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="P04-C: observed provider health and circuit state are not surfaced",
)
async def test_readiness_reports_observed_provider_state():
    assert (await _checks())["providers"] not in {"unknown", "unconfigured"}
    pytest.fail("observed provider state landed: assert the circuit state value")
