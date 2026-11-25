"""Pilot orchestration against a fake service: no database, no provider."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import src.evals.pilot_runner as pilot_runner
from src.api.services.execution_authority_resolver import (
    PersistedExecutionAuthorityResolver,
)
from src.core.contracts import ProviderModelRoute
from src.evals.cost_pilot import PilotError, RunSample
from src.evals.pilot_runner import (
    PilotCase,
    openrouter_key_usage,
    run_pilot,
    sample_from_events,
    settled_key_usage,
)

ORG = "00000000-0000-0000-0000-0000000000f1"
ROUTE = ProviderModelRoute(provider="openrouter", model="anthropic/claude-sonnet-4.6")
T0 = datetime(2026, 11, 25, tzinfo=UTC)


class _FakeService:
    def __init__(self, *, finishes: bool = True) -> None:
        self.finishes = finishes
        self.started: list[Any] = []
        self.running = 0
        self.max_running = 0
        self.cancelled: list[str] = []
        self.polls: dict[str, int] = {}

    async def start_research_execution(self, project: Any, **kwargs: Any) -> str:
        self.started.append((project, kwargs))
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        return f"exec-{len(self.started)}"

    async def get_execution_status(
        self, execution_id: str, *, organization_id: str | None = None
    ) -> Any:
        await asyncio.sleep(0)
        self.polls[execution_id] = self.polls.get(execution_id, 0) + 1
        if not self.finishes or self.polls[execution_id] < 2:
            return SimpleNamespace(status="running")
        self.running -= 1
        return SimpleNamespace(status="completed")

    async def cancel_execution(self, execution_id: str) -> bool:
        self.cancelled.append(execution_id)
        return True


async def _read(run_id: str, domain: str) -> RunSample:
    return RunSample(
        domain=domain,
        outcome="succeeded",
        ledger_cost_usd=Decimal("0.01"),
        wall_seconds=1.0,
        claims=2,
    )


async def _run(service: _FakeService, **overrides: Any) -> list[RunSample]:
    kwargs: dict[str, Any] = {
        "service": service,
        "resolver": PersistedExecutionAuthorityResolver(),
        "cases": [
            PilotCase("r1", "research", "Compare two retrieval methods."),
            PilotCase("a1", "analytics", "Analyze churn metrics."),
        ],
        "trials": 3,
        "concurrency": 2,
        "per_run_cap": Decimal("0.25"),
        "task_timeout_seconds": 240,
        "model_route": ROUTE,
        "organization_id": ORG,
        "read_run": _read,
        "poll_seconds": 0,
    }
    kwargs.update(overrides)
    return await run_pilot(**kwargs)


@pytest.mark.asyncio
async def test_every_trial_gets_its_own_binding() -> None:
    service = _FakeService()

    samples = await _run(service)

    assert len(samples) == 6
    refs = {
        (
            kw["authority_reference"].authority_id,
            kw["authority_reference"].authority_version,
        )
        for _, kw in service.started
    }
    assert len(refs) == 6
    assert all(kw["organization_id"] == ORG for _, kw in service.started)


@pytest.mark.asyncio
async def test_bindings_are_minted_inside_the_concurrency_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _FakeService()
    running_at_mint: list[int] = []
    real = pilot_runner.build_trial_authority

    def spy(**kwargs: Any) -> Any:
        running_at_mint.append(service.running)
        return real(**kwargs)

    monkeypatch.setattr(pilot_runner, "build_trial_authority", spy)

    await _run(service, concurrency=2)

    assert max(running_at_mint) < 2
    assert service.max_running <= 2


@pytest.mark.asyncio
async def test_only_research_runs_get_the_search_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    grants: dict[str, tuple[str, ...]] = {}
    real = pilot_runner.build_trial_authority

    def spy(**kwargs: Any) -> Any:
        grants[kwargs["domain"]] = kwargs["tool_grants"]
        return real(**kwargs)

    monkeypatch.setattr(pilot_runner, "build_trial_authority", spy)

    await _run(_FakeService())

    assert grants == {"research": ("mcp.academic_search",), "analytics": ()}


@pytest.mark.asyncio
async def test_a_run_past_its_wall_time_is_cancelled_and_unpriced() -> None:
    service = _FakeService(finishes=False)
    ticks = iter(range(0, 10_000, 100))

    samples = await _run(
        service,
        cases=[PilotCase("c1", "content", "Draft a launch note.")],
        trials=1,
        clock=lambda: float(next(ticks)),
    )

    assert service.cancelled == ["exec-1"]
    assert samples == [
        RunSample(
            domain="content",
            outcome="timed_out",
            ledger_cost_usd=None,
            wall_seconds=None,
            claims=0,
        )
    ]


def _event(kind: str, seconds: int, payload: dict[str, Any] | None = None) -> Any:
    return SimpleNamespace(
        event_type=kind,
        occurred_at=T0 + timedelta(seconds=seconds),
        payload=payload or {},
    )


def test_sample_reads_outcome_cost_and_wall_time_from_events() -> None:
    usage = {
        "tokens_used": 9,
        "cost_usd": "0.0421",
        "tool_invocations": 1,
        "unknown_usage_calls": 0,
    }
    events = [
        _event("run.admitted", 0),
        _event("run.started", 1),
        _event("run.budget_exceeded", 42, {"usage": usage}),
    ]

    sample = sample_from_events(events, domain="finance", claims=7)

    assert sample == RunSample(
        domain="finance",
        outcome="budget_exceeded",
        ledger_cost_usd=Decimal("0.0421"),
        wall_seconds=42.0,
        claims=7,
    )


def test_a_terminal_event_without_usage_is_unpriced() -> None:
    events = [
        _event("run.admitted", 0),
        _event("run.succeeded", 5, {"fixture_mode": True}),
    ]

    assert (
        sample_from_events(events, domain="research", claims=0).ledger_cost_usd is None
    )


def test_a_run_that_never_ended_raises() -> None:
    with pytest.raises(ValueError, match="non-terminal"):
        sample_from_events([_event("run.admitted", 0)], domain="research", claims=0)


def _usage_client(values: list[str]) -> httpx.AsyncClient:
    reads = iter(values)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://openrouter.ai/api/v1/key"
        assert request.headers["Authorization"] == "Bearer sk-test"
        return httpx.Response(200, json={"data": {"usage": next(reads)}})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _no_sleep(seconds: float) -> None:
    return None


@pytest.mark.asyncio
async def test_key_usage_reads_all_time_usage() -> None:
    async with _usage_client(["1.25"]) as client:
        assert await openrouter_key_usage(client, "sk-test") == Decimal("1.25")


@pytest.mark.asyncio
async def test_settled_usage_waits_for_two_equal_reads() -> None:
    async with _usage_client(["1.00", "1.40", "1.52", "1.52"]) as client:
        settled = await settled_key_usage(client, "sk-test", sleep=_no_sleep)

    assert settled == Decimal("1.52")


@pytest.mark.asyncio
async def test_usage_that_never_settles_raises() -> None:
    async with _usage_client([str(i) for i in range(5)]) as client:
        with pytest.raises(PilotError, match="settle"):
            await settled_key_usage(client, "sk-test", max_reads=3, sleep=_no_sleep)
