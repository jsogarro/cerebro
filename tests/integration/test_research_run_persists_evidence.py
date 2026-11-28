"""A plan-backed research run writes run-owned evidence by default.

Drives the production path end to end: ``DirectExecutionService`` admits the
run against real Postgres, builds its own supervisor bridge, and the bridge
runs the real ``LiteratureReviewAgent`` with an LLM service configured. Only
the network edges are faked: the MCP client and the agent's model call. The
result is read back by SQL, because what the database holds is the subject.
"""

import asyncio
import dataclasses
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import src.ai_brain.integration.masr_supervisor_bridge as bridge_module
from src.agents.integrations.mcp_integration import MCPIntegration
from src.agents.integrations.mcp_tool_specs import TOOL_ACADEMIC_SEARCH
from src.agents.literature_review_agent import LiteratureReviewAgent
from src.agents.schemas.literature_review import LiteratureAnalysisSchema
from src.ai_brain.providers import ModelRouter
from src.ai_brain.router.routing_types import RoutingStrategy
from src.api.services.direct_execution_service import DirectExecutionService
from src.api.services.execution_authority_resolver import (
    MappingExecutionAuthorityResolver,
)
from src.core.contracts import (
    ExecutionBudget,
    FallbackMode,
    ProviderModelPolicy,
    ProviderModelRoute,
    RoutingEdge,
    WorkerAssignment,
)
from src.models.execution_authority import (
    ExecutionAuthorityBinding,
    ExecutionAuthorityReference,
)
from src.models.research_project import (
    ResearchDepth,
    ResearchProject,
    ResearchQuery,
    ResearchScope,
)

pytestmark = [pytest.mark.integration]

ORG_ID = "00000000-0000-0000-0000-0000000000ef"
AUTHORITY_ID = "evidence-authority"
WORKER_ID = "worker-1"
ARXIV_URL = "https://arxiv.org/abs/2401.00001"
SOURCE = {
    "id": "2401.00001",
    "title": "GNNs for inflation",
    "abstract": "We show that graph neural networks forecast inflation.",
    "url": ARXIV_URL,
    "source": "arxiv",
}
ANALYSIS_JSON = (
    '{"literature_analysis": {"key_findings": ["GNNs forecast inflation"], '
    '"research_gaps": ["No out-of-sample test"], '
    '"methodologies_used": ["Graph neural networks"], '
    '"quality_assessment": "One retrieved source"}}'
)


@dataclass
class _AllocStub:
    supervisor_type: str = "research"
    worker_count: int = 1
    worker_types: list[str] = field(default_factory=lambda: ["literature_review"])


@dataclass
class _ComplexityAnalysisStub:
    domains: list[str] = field(default_factory=lambda: ["research"])
    decomposition: None = None


@dataclass
class _RoutingDecisionStub:
    query_id: str = "evidence-query"
    collaboration_mode: str = "hierarchical"
    agent_allocation: _AllocStub = field(default_factory=_AllocStub)
    complexity_analysis: _ComplexityAnalysisStub = field(
        default_factory=_ComplexityAnalysisStub
    )
    estimated_cost: float = 0.01
    estimated_latency_ms: int = 1000
    estimated_quality: float = 0.9
    confidence_score: float = 0.9
    context: dict[str, Any] = field(default_factory=dict)
    routing_strategy: RoutingStrategy = RoutingStrategy.BALANCED
    optimization_result: Any = field(
        default_factory=lambda: type(
            "_Optimization",
            (),
            {
                "primary_model": type(
                    "_Model",
                    (),
                    {"provider": "gemini", "model_name": "gemini-2.5-pro"},
                )(),
                "fallback_models": [],
            },
        )()
    )


def _binding(run_id: str) -> ExecutionAuthorityBinding:
    now = datetime.now(UTC)
    raw = ExecutionAuthorityBinding.create_for_test(
        authority_id=AUTHORITY_ID,
        authority_version="1",
        run_id=run_id,
        workflow_definition_id="workflow-1",
        routing_policy_id="policy-1",
        strategy="balanced",
        collaboration_mode="hierarchical",
        domains=("research",),
        supervisor_id="supervisor-1",
        supervisor_type="research",
        workers=(
            WorkerAssignment(
                worker_id=WORKER_ID,
                worker_type="literature_review",
                objective="Find sources",
                output_schema={},
                permission_scopes=("research:read",),
                tool_allowlist=(TOOL_ACADEMIC_SEARCH,),
            ),
        ),
        edges=(
            RoutingEdge(
                source_node_id="supervisor-1",
                target_node_id=WORKER_ID,
                relation="delegates",
            ),
        ),
        provider_model_policy=ProviderModelPolicy(
            primary=ProviderModelRoute(provider="gemini", model="gemini-2.5-pro"),
            fallback_mode=FallbackMode.FAIL_CLOSED,
            fallbacks=(),
            provider_allowlist=("gemini",),
            model_allowlist=("gemini-2.5-pro",),
        ),
        budget=ExecutionBudget(
            max_cost_usd=1.0,
            max_total_tokens=100_000,
            max_tool_invocations=10,
            max_parallel_tasks=1,
            max_attempts_per_task=1,
            task_timeout_seconds=60,
        ),
        stop_conditions=("complete",),
        evaluator_requirements=(),
        deadline=now + timedelta(minutes=10),
        compiled_at=now,
    )
    # ``create_for_test`` hardcodes ``tenant_id="tenant-1"``; a durable run's
    # tenant must be a UUID, and each run needs its own idempotency key.
    return dataclasses.replace(
        raw,
        run=raw.run.model_copy(
            update={"tenant_id": ORG_ID, "idempotency_key": f"key-{run_id}"}
        ),
    )


def _mcp_client(search_result: dict[str, Any]) -> AsyncMock:
    client = AsyncMock()
    client.health_check.return_value = {"client": "healthy"}
    client.search_academic.return_value = search_result
    return client


async def _forbidden_provider_call(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("test made a live provider call")


async def _run_to_completion(
    monkeypatch: pytest.MonkeyPatch,
    test_engine: AsyncEngine,
    tmp_path: Path,
    search_result: dict[str, Any],
) -> tuple[DirectExecutionService, str, str, AsyncMock, AsyncMock]:
    """Admit one plan-backed run and drive it to a terminal state."""
    monkeypatch.setattr(
        bridge_module,
        "MCPIntegration",
        lambda **kwargs: MCPIntegration(
            mcp_client=_mcp_client(search_result), **kwargs
        ),
    )
    monkeypatch.setattr(bridge_module.settings, "SOURCE_SNAPSHOT_DIR", str(tmp_path))
    monkeypatch.setattr(bridge_module.settings, "MULTI_PROVIDER_ROUTING_ENABLED", False)
    # Network edges: no test may reach a real model provider.
    monkeypatch.setattr(ModelRouter, "route_and_generate", _forbidden_provider_call)
    model_call = AsyncMock(return_value=(ANALYSIS_JSON, 0.9))
    monkeypatch.setattr(LiteratureReviewAgent, "_generate_with_routing", model_call)
    # Non-None, so the run takes the production branch an LLM deployment takes.
    gemini = AsyncMock()
    gemini.generate_structured_content = AsyncMock(
        return_value=LiteratureAnalysisSchema()
    )

    run_id = f"evidence-run-{uuid.uuid4().hex[:8]}"
    router = AsyncMock()
    router.route.return_value = _RoutingDecisionStub()
    service = DirectExecutionService(
        masr_router=router,
        gemini_service=gemini,
        session_factory=async_sessionmaker(test_engine, expire_on_commit=False),
        execution_authority_resolver=MappingExecutionAuthorityResolver(
            {(AUTHORITY_ID, "1"): _binding(run_id)}
        ),
    )
    project = ResearchProject(
        title="Evidence production",
        query=ResearchQuery(
            text="Do graph neural networks forecast inflation?",
            domains=["research"],
            depth_level=ResearchDepth.COMPREHENSIVE,
        ),
        user_id="user-1",
        scope=ResearchScope(max_sources=5),
    )
    execution_id = await service.start_research_execution(
        project,
        authority_reference=ExecutionAuthorityReference(
            authority_id=AUTHORITY_ID, authority_version="1"
        ),
        organization_id=ORG_ID,
    )
    for _ in range(200):
        if service.active_executions[execution_id].status in ("completed", "failed"):
            break
        await asyncio.sleep(0.05)
    return service, execution_id, run_id, gemini, model_call


async def _rows(test_engine: AsyncEngine, sql: str, **params: Any) -> list[Any]:
    factory = async_sessionmaker(test_engine, class_=AsyncSession)
    async with factory() as session:
        result = await session.execute(text(sql), params)
        return list(result.mappings().all())


@pytest.mark.asyncio
async def test_a_plan_run_with_an_llm_persists_retrieved_evidence_by_default(
    monkeypatch: pytest.MonkeyPatch, test_engine: AsyncEngine, tmp_path: Path
) -> None:
    service, execution_id, run_id, gemini, _ = await _run_to_completion(
        monkeypatch,
        test_engine,
        tmp_path,
        {"success": True, "results": [SOURCE]},
    )

    status = service.active_executions[execution_id]
    assert status.status == "completed", status.errors
    evidence_rows = await _rows(
        test_engine, "SELECT * FROM agent_evidence WHERE run_id = :r", r=run_id
    )
    assert len(evidence_rows) == 1, evidence_rows
    (evidence,) = evidence_rows
    (search,) = await _rows(
        test_engine,
        "SELECT * FROM agent_tool_invocations WHERE run_id = :r AND tool_name = :t",
        r=run_id,
        t=TOOL_ACADEMIC_SEARCH,
    )
    (run,) = await _rows(
        test_engine, "SELECT status FROM agent_runs WHERE run_id = :r", r=run_id
    )
    assert str(evidence["organization_id"]) == ORG_ID
    assert evidence["source_uri"] == ARXIV_URL
    assert evidence["producer_tool_invocation_id"] == search["tool_invocation_id"]
    assert run["status"] == "succeeded"

    output = status.final_output[WORKER_ID]
    assert output.get("data_source") == "mcp_tools", output
    assert [s["url"] for s in output["sources_found"]] == [ARXIV_URL]
    # Real sources only: the LLM is never asked to name sources of its own.
    gemini.generate_structured_content.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_search_records_no_evidence_visibly(
    monkeypatch: pytest.MonkeyPatch, test_engine: AsyncEngine, tmp_path: Path
) -> None:
    results: list[Any] = []
    real_execute = LiteratureReviewAgent.execute

    async def recording_execute(self: Any, task: Any) -> Any:
        results.append(await real_execute(self, task))
        return results[-1]

    monkeypatch.setattr(LiteratureReviewAgent, "execute", recording_execute)
    service, execution_id, run_id, gemini, model_call = await _run_to_completion(
        monkeypatch,
        test_engine,
        tmp_path,
        {"success": False, "error": "upstream unavailable"},
    )

    status = service.active_executions[execution_id]
    # No evidence fails the required worker, and the run with it.
    assert status.status == "failed"
    assert any("INSUFFICIENT_EVIDENCE" in error for error in status.errors), (
        status.errors
    )
    evidence_rows = await _rows(
        test_engine, "SELECT * FROM agent_evidence WHERE run_id = :r", r=run_id
    )
    assert evidence_rows == []
    (search,) = await _rows(
        test_engine,
        "SELECT * FROM agent_tool_invocations WHERE run_id = :r AND tool_name = :t",
        r=run_id,
        t=TOOL_ACADEMIC_SEARCH,
    )
    assert search["status"] != "succeeded"
    (run,) = await _rows(
        test_engine, "SELECT status FROM agent_runs WHERE run_id = :r", r=run_id
    )
    assert run["status"] != "succeeded"

    (worker_result,) = results
    assert worker_result.status == "failed"
    assert worker_result.output["error_code"] == "INSUFFICIENT_EVIDENCE"
    assert worker_result.output["reason"] == "tool_error"
    assert worker_result.output["data_source"] == "no_evidence"
    assert worker_result.output["sources_found"] == []
    assert worker_result.output["total_sources"] == 0
    # No silent substitute: no LLM-named sources and no analysis of nothing.
    gemini.generate_structured_content.assert_not_awaited()
    model_call.assert_not_awaited()
