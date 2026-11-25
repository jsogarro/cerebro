"""Evaluation bindings admit as ordinary, distinct durable runs."""

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from src.ai_brain.router.routing_types import RoutingExecutionPolicy
from src.core.config import get_settings
from src.core.contracts import ProviderModelRoute
from src.evals.trial_setup import build_harvest_service, build_trial_authority
from src.models.db.run_config_snapshot import AgentRunConfigSnapshot
from src.models.db.run_lifecycle import AgentRun
from src.models.execution_authority import ExecutionAuthorityReference
from src.models.research_project import ResearchProject, ResearchQuery

pytestmark = [pytest.mark.integration]

ORG_ID = "00000000-0000-0000-0000-0000000000e1"
ROUTE = ProviderModelRoute(provider="openrouter", model="anthropic/claude-sonnet-4.6")


@pytest.mark.asyncio
async def test_two_trials_of_one_query_admit_two_runs_and_two_snapshots(
    test_engine: AsyncEngine,
) -> None:
    settings = get_settings().model_copy(
        update={
            "EVAL_RUN_DATABASE_URL": test_engine.url.render_as_string(
                hide_password=False
            )
        }
    )
    project = ResearchProject(
        title="evaluation trial",
        query=ResearchQuery(
            text="Compare two retrieval methods.", domains=["research"]
        ),
        user_id="evaluation-harness",
    )

    async with build_harvest_service(settings, model_route=ROUTE) as (
        service,
        resolver,
    ):
        run_ids = []
        execution_ids = []
        for _ in range(2):
            binding = build_trial_authority(
                domain="research",
                organization_id=ORG_ID,
                resolver=resolver,
                per_run_cap=Decimal("0.30"),
                task_timeout_seconds=240,
                model_route=ROUTE,
                tool_grants=("mcp.academic_search",),
            )
            run_ids.append(binding.run.run_id)
            execution_ids.append(
                await service.start_research_execution(
                    project,
                    execution_policy=RoutingExecutionPolicy.fixture(),
                    fixture_result={"summary": "synthetic"},
                    authority_reference=ExecutionAuthorityReference(
                        authority_id=binding.authority_id,
                        authority_version=binding.authority_version,
                    ),
                    organization_id=ORG_ID,
                )
            )
        for _ in range(100):
            if all(
                service.active_executions[e].status == "completed"
                for e in execution_ids
            ):
                break
            await asyncio.sleep(0.05)

        async with service.session_factory() as session:
            runs = (
                (
                    await session.execute(
                        select(AgentRun).where(AgentRun.run_id.in_(run_ids))
                    )
                )
                .scalars()
                .all()
            )
            snapshots = (
                (
                    await session.execute(
                        select(AgentRunConfigSnapshot).where(
                            AgentRunConfigSnapshot.run_id.in_(run_ids)
                        )
                    )
                )
                .scalars()
                .all()
            )

    assert len(set(execution_ids)) == 2
    assert sorted(run.run_id for run in runs) == sorted(run_ids)
    assert len({run.idempotency_key for run in runs}) == 2
    assert all(run.status == "succeeded" for run in runs)
    assert len(snapshots) == 2
    assert {Decimal(s.configuration["budget"]["max_cost_usd"]) for s in snapshots} == {
        Decimal("0.30")
    }
