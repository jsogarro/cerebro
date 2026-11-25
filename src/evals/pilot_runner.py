"""Drive the agent cost pilot: one fresh authority per trial, bounded concurrency."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

import httpx
from sqlalchemy import func, select

from src.agents.integrations.mcp_tool_specs import TOOL_ACADEMIC_SEARCH
from src.api.services.execution_authority_resolver import (
    PersistedExecutionAuthorityResolver,
)
from src.core.contracts import ProviderModelRoute
from src.evals.cost_pilot import PilotError, RunSample
from src.evals.trial_setup import build_trial_authority, trial_wall_seconds
from src.models.db.claim_support import AgentClaimSupport
from src.models.execution_authority import ExecutionAuthorityReference
from src.models.research_project import ResearchProject, ResearchQuery
from src.repositories.run_event_repository import RunEventRepository

OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_OUTCOMES = {
    "run.succeeded": "succeeded",
    "run.failed": "failed",
    "run.budget_exceeded": "budget_exceeded",
}


@dataclass(frozen=True)
class PilotCase:
    case_id: str
    domain: str
    query: str


class _Service(Protocol):
    async def start_research_execution(
        self, project: ResearchProject, *args: Any, **kwargs: Any
    ) -> str: ...

    async def get_execution_status(
        self, execution_id: str, *, organization_id: str | None = None
    ) -> Any: ...

    async def cancel_execution(self, execution_id: str) -> bool: ...


ReadRun = Callable[[str, str], Awaitable[RunSample]]


def sample_from_events(events: Sequence[Any], *, domain: str, claims: int) -> RunSample:
    """One run's outcome, ledger cost and wall time from its event stream."""
    terminal = events[-1]
    outcome = _OUTCOMES.get(terminal.event_type)
    if outcome is None:
        raise ValueError(f"run ended on non-terminal event {terminal.event_type!r}")
    usage = terminal.payload.get("usage")
    admitted = next(e for e in events if e.event_type == "run.admitted")
    return RunSample(
        domain=domain,
        outcome=outcome,
        ledger_cost_usd=None if usage is None else Decimal(usage["cost_usd"]),
        wall_seconds=(terminal.occurred_at - admitted.occurred_at).total_seconds(),
        claims=claims,
    )


def database_run_reader(session_factory: Any, *, organization_id: str) -> ReadRun:
    """Read a finished run's events and claim rows from the evaluation database."""

    async def read(run_id: str, domain: str) -> RunSample:
        async with session_factory() as session:
            events = await RunEventRepository(session).read_events_after(
                run_id, after_sequence=0, organization_id=organization_id
            )
            claims = await session.scalar(
                select(func.count())
                .select_from(AgentClaimSupport)
                .where(AgentClaimSupport.run_id == run_id)
            )
        return sample_from_events(events, domain=domain, claims=int(claims or 0))

    return read


async def run_pilot(
    *,
    service: _Service,
    resolver: PersistedExecutionAuthorityResolver,
    cases: Sequence[PilotCase],
    trials: int,
    concurrency: int,
    per_run_cap: Decimal,
    task_timeout_seconds: int,
    model_route: ProviderModelRoute,
    organization_id: str,
    read_run: ReadRun,
    poll_seconds: float = 1.0,
    clock: Callable[[], float] = time.monotonic,
) -> list[RunSample]:
    """Run every case ``trials`` times; a run past its wall time is cancelled."""
    semaphore = asyncio.Semaphore(concurrency)

    async def one(case: PilotCase) -> RunSample:
        async with semaphore:
            # Minted inside the semaphore: the deadline starts at mint.
            binding = build_trial_authority(
                domain=case.domain,
                organization_id=organization_id,
                resolver=resolver,
                per_run_cap=per_run_cap,
                task_timeout_seconds=task_timeout_seconds,
                model_route=model_route,
                tool_grants=(
                    (TOOL_ACADEMIC_SEARCH,) if case.domain == "research" else ()
                ),
            )
            execution_id = await service.start_research_execution(
                ResearchProject(
                    title=case.case_id,
                    query=ResearchQuery(text=case.query, domains=[case.domain]),
                    user_id="evaluation-harness",
                ),
                authority_reference=ExecutionAuthorityReference(
                    authority_id=binding.authority_id,
                    authority_version=binding.authority_version,
                ),
                organization_id=organization_id,
            )
            deadline = clock() + trial_wall_seconds(task_timeout_seconds)
            while True:
                status = await service.get_execution_status(
                    execution_id, organization_id=organization_id
                )
                if status is not None and status.status in _TERMINAL_STATUSES:
                    return await read_run(binding.run.run_id, case.domain)
                if clock() >= deadline:
                    await service.cancel_execution(execution_id)
                    return RunSample(
                        domain=case.domain,
                        outcome="timed_out",
                        ledger_cost_usd=None,
                        wall_seconds=None,
                        claims=0,
                    )
                await asyncio.sleep(poll_seconds)

    return list(
        await asyncio.gather(*(one(case) for case in cases for _ in range(trials)))
    )


async def openrouter_key_usage(client: httpx.AsyncClient, api_key: str) -> Decimal:
    """All-time credits (USD) the key has used: ``data.usage``."""
    response = await client.get(
        OPENROUTER_KEY_URL, headers={"Authorization": f"Bearer {api_key}"}
    )
    response.raise_for_status()
    return Decimal(str(response.json()["data"]["usage"]))


async def settled_key_usage(
    client: httpx.AsyncClient,
    api_key: str,
    *,
    interval_seconds: float = 30.0,
    max_reads: int = 20,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Decimal:
    """Key usage once two reads ``interval_seconds`` apart agree."""
    previous = await openrouter_key_usage(client, api_key)
    for _ in range(max_reads):
        await sleep(interval_seconds)
        current = await openrouter_key_usage(client, api_key)
        if current == previous:
            return current
        previous = current
    raise PilotError("OpenRouter key usage did not settle")
