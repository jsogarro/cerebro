"""Per-run evaluation authority and the isolated evaluation service."""

import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from src.ai_brain.integration.execution_plan_topology import (
    ExecutionPlanTopologyExecutor,
)
from src.ai_brain.router.execution_plan_compiler import ExecutionPlanCompiler
from src.api.services.component_catalog import build_application_component_registry
from src.api.services.execution_authority_resolver import (
    ExecutionAuthorityUnavailableError,
    PersistedExecutionAuthorityResolver,
)
from src.core.capabilities.issuer import PlanCapabilityIssuer
from src.core.config import get_settings
from src.core.contracts import ProviderModelRoute
from src.core.kernel.component_keys import AGENT_KEYS
from src.evals.trial_setup import (
    DOMAIN_WORKERS,
    LiveEnvironmentError,
    PinnedPlanRouter,
    build_harvest_service,
    build_trial_authority,
)
from src.models.execution_authority import ExecutionAuthorityReference

ORG_A = "00000000-0000-0000-0000-00000000000a"
ORG_B = "00000000-0000-0000-0000-00000000000b"
ROUTE = ProviderModelRoute(provider="openrouter", model="anthropic/claude-sonnet-4.6")
TOOL = "mcp.academic_search"


def _mint(resolver: PersistedExecutionAuthorityResolver, **overrides: object):
    kwargs: dict[str, object] = {
        "domain": "research",
        "organization_id": ORG_A,
        "resolver": resolver,
        "per_run_cap": Decimal("0.30"),
        "task_timeout_seconds": 240,
        "model_route": ROUTE,
    }
    kwargs.update(overrides)
    return build_trial_authority(**kwargs)  # type: ignore[arg-type]


def _ref(binding) -> ExecutionAuthorityReference:
    return ExecutionAuthorityReference(
        authority_id=binding.authority_id,
        authority_version=binding.authority_version,
    )


def test_binding_resolves_only_for_its_own_organization() -> None:
    resolver = PersistedExecutionAuthorityResolver()
    binding = _mint(resolver)

    assert binding.run.tenant_id == ORG_A
    assert resolver.resolve(_ref(binding), organization_id=ORG_A) is binding
    for other in (ORG_B, None):
        with pytest.raises(ExecutionAuthorityUnavailableError):
            resolver.resolve(_ref(binding), organization_id=other)


def test_every_binding_is_fresh() -> None:
    resolver = PersistedExecutionAuthorityResolver()
    bindings = [_mint(resolver) for _ in range(8)]

    assert len({b.run.run_id for b in bindings}) == 8
    assert len({b.run.idempotency_key for b in bindings}) == 8
    assert len({(b.authority_id, b.authority_version) for b in bindings}) == 8
    first = resolver.resolve(_ref(bindings[0]), organization_id=ORG_A)
    assert first.run.run_id == bindings[0].run.run_id


def test_binding_is_cached_not_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_register(*args: object, **kwargs: object) -> None:
        raise AssertionError("register must not be called before admission")

    monkeypatch.setattr(PersistedExecutionAuthorityResolver, "register", _no_register)
    resolver = PersistedExecutionAuthorityResolver()
    binding = _mint(resolver)

    assert resolver.resolve(_ref(binding), organization_id=ORG_A) is binding
    with pytest.raises(ExecutionAuthorityUnavailableError):
        PersistedExecutionAuthorityResolver().resolve(
            _ref(binding), organization_id=ORG_A
        )


def test_binding_budget_and_deadline() -> None:
    before = datetime.now(UTC)
    binding = _mint(PersistedExecutionAuthorityResolver())
    after = datetime.now(UTC)

    assert binding.budget.max_cost_usd == Decimal("0.30")
    assert binding.budget.max_attempts_per_task == 1
    assert binding.budget.task_timeout_seconds == 240
    assert binding.budget.max_parallel_tasks == len(DOMAIN_WORKERS["research"])
    wall = timedelta(seconds=240 + 60)
    assert before + wall <= binding.deadline <= after + wall


def test_unknown_domain_raises() -> None:
    with pytest.raises(LiveEnvironmentError, match="service"):
        _mint(PersistedExecutionAuthorityResolver(), domain="service")


def test_tool_grant_reaches_workers_and_fits_the_deadline_for_60_seconds() -> None:
    binding = _mint(PersistedExecutionAuthorityResolver(), tool_grants=(TOOL,))
    minted_at = binding.deadline - timedelta(seconds=240 + 60)

    for worker in binding.workers:
        assert worker.tool_allowlist == (TOOL,)
        assert worker.permission_scopes
    grants = PlanCapabilityIssuer.issue(
        binding, run_id=binding.run.run_id, task_id="t", issued_at=minted_at
    )
    assert {grant.tool_name for grant in grants} == {TOOL}
    PlanCapabilityIssuer.issue(
        binding,
        run_id=binding.run.run_id,
        task_id="t",
        issued_at=minted_at + timedelta(seconds=59),
    )
    with pytest.raises(ValueError, match="deadline"):
        PlanCapabilityIssuer.issue(
            binding,
            run_id=binding.run.run_id,
            task_id="t",
            issued_at=minted_at + timedelta(seconds=61),
        )


@pytest.mark.parametrize("domain", sorted(DOMAIN_WORKERS))
def test_no_grant_without_parameter(domain: str) -> None:
    binding = _mint(PersistedExecutionAuthorityResolver(), domain=domain)

    assert all(worker.tool_allowlist == () for worker in binding.workers)
    assert binding.budget.max_tool_invocations == 0


@pytest.mark.parametrize("domain", sorted(DOMAIN_WORKERS))
@pytest.mark.asyncio
async def test_pinned_proposal_compiles_and_admits_against_its_binding(
    domain: str,
) -> None:
    registry = build_application_component_registry()
    binding = _mint(PersistedExecutionAuthorityResolver(), domain=domain)
    proposal = await PinnedPlanRouter(ROUTE).route(
        query="q", context={"domains": [domain]}
    )

    plan = ExecutionPlanCompiler().compile(proposal, binding)  # type: ignore[arg-type]
    ExecutionPlanTopologyExecutor(
        worker_executor=None,  # type: ignore[arg-type]
        capability_checker=lambda worker_type: (
            AGENT_KEYS.get(worker_type) in registry.keys
        ),
    ).admit(plan)


@pytest.mark.parametrize("domains", [[], ["service"], ["research", "finance"]])
@pytest.mark.asyncio
async def test_router_refuses_anything_but_one_known_domain(
    domains: list[str],
) -> None:
    with pytest.raises(LiveEnvironmentError):
        await PinnedPlanRouter(ROUTE).route(query="q", context={"domains": domains})


@pytest.mark.asyncio
async def test_harvest_service_requires_the_evaluation_database() -> None:
    settings = get_settings().model_copy(update={"EVAL_RUN_DATABASE_URL": None})

    with pytest.raises(LiveEnvironmentError, match="EVAL_RUN_DATABASE_URL"):
        async with build_harvest_service(settings, model_route=ROUTE):
            pass


@pytest.mark.asyncio
async def test_harvest_service_is_isolated_and_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _no_warm(*args: object, **kwargs: object) -> int:
        raise AssertionError("the evaluation resolver must not warm from snapshots")

    monkeypatch.setattr(
        PersistedExecutionAuthorityResolver, "warm_from_snapshots", _no_warm
    )
    settings = get_settings().model_copy(
        update={
            "EVAL_RUN_DATABASE_URL": "postgresql+asyncpg://u:p@127.0.0.1:1/cerebro_pilot"
        }
    )

    async with build_harvest_service(settings, model_route=ROUTE) as (
        service,
        resolver,
    ):
        assert service.execution_authority_resolver is resolver
        assert isinstance(service.masr_router, PinnedPlanRouter)
        assert service.event_publisher is None
        assert service.gemini_service is None
        assert service.session_factory is not None
    assert service.closed


def test_trial_setup_is_not_imported_by_production_code() -> None:
    root = Path(__file__).resolve().parents[3]
    offenders = []
    for path in [
        *root.joinpath("src").rglob("*.py"),
        *root.joinpath("alembic").rglob("*.py"),
    ]:
        if "evals" in path.relative_to(root).parts[:2]:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            if any(name.startswith("src.evals") for name in names):
                offenders.append(str(path.relative_to(root)))
    assert offenders == []
