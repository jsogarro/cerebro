"""A durable research run writes evidence rows to real Postgres.

Drives the live path rather than the recorder alone: the supervisor bridge
builds the mediated MCP integration for a plan worker, the worker searches, and
the sources that come back must already be durable, run-owned evidence rows
when the worker receives them. Read back by SQL, because what the call returned
is what it said happened and the subject is what the database holds.
"""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import src.ai_brain.integration.masr_supervisor_bridge as bridge_module
from src.agents.integrations.mcp_integration import MCPIntegration
from src.agents.integrations.mcp_tool_specs import (
    MCP_OUTBOUND_SENSITIVITY,
    MCP_TOOL_OUTBOUND_HOSTS,
    TOOL_ACADEMIC_SEARCH,
    TOOL_VERSION,
)
from src.agents.models import AgentResult, AgentTask
from src.agents.tools.durable_audit import SessionToolAuditStore
from src.ai_brain.integration.masr_supervisor_bridge import MASRSupervisorBridge
from src.core.acquisition.resolution import resolve
from src.core.acquisition.search_evidence import SearchEvidenceRecorder
from src.core.acquisition.snapshots import FilesystemSnapshotStore
from src.core.capabilities import CAPABILITY_GRANTS_CONTEXT_KEY
from src.core.contracts import (
    CapabilityGrant,
    TrustClassification,
    WorkerAssignment,
)
from src.core.contracts.provenance import ToolInvocation
from src.core.contracts.redaction import snapshot_digest
from src.repositories.capability_repository import CapabilityRepository
from src.repositories.evidence_repository import EvidenceRepository
from tests.integration.wave4_helpers import seed_run_task_attempt

pytestmark = [pytest.mark.integration]

ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
ARXIV_URL = "https://arxiv.org/abs/2401.00001"
ABSTRACT = "We show that graph neural networks forecast inflation."
SOURCES = [
    {
        "id": "2401.00001",
        "title": "GNNs for inflation",
        "abstract": ABSTRACT,
        "url": ARXIV_URL,
        "source": "arxiv",
    },
    {"id": "123", "title": "No abstract", "abstract": "", "url": ARXIV_URL + "9"},
    {"id": "9", "title": "No uri", "abstract": "has text", "source": "pubmed"},
    {"id": "8", "title": "Bad uri", "abstract": "x", "url": "file:///etc/passwd"},
]


@pytest_asyncio.fixture(name="factory")
async def factory_fixture(
    test_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        await seed_run_task_attempt(session, organization_id=ORG_ID)
        await CapabilityRepository(session).create_grant(
            _grant(), organization_id=ORG_ID
        )
        await session.commit()
    return factory


def _grant() -> CapabilityGrant:
    now = datetime.now(UTC)
    return CapabilityGrant(
        grant_id="grant-search",
        run_id="run-1",
        task_id="task-1",
        capability_scope="scope-search",
        tool_name=TOOL_ACADEMIC_SEARCH,
        tool_versions=(TOOL_VERSION,),
        sensitivity=MCP_OUTBOUND_SENSITIVITY,
        max_input_trust=TrustClassification.EXTERNAL_UNTRUSTED,
        requires_approval=False,
        allowed_hosts=MCP_TOOL_OUTBOUND_HOSTS[TOOL_ACADEMIC_SEARCH],
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=1),
    )


def _client(sources: list[dict[str, Any]]) -> AsyncMock:
    client = AsyncMock()
    client.health_check.return_value = {"client": "healthy"}
    client.search_academic.return_value = {"success": True, "results": sources}
    return client


async def _run_worker(
    monkeypatch: pytest.MonkeyPatch,
    factory: async_sessionmaker[AsyncSession],
    snapshot_dir: Path,
    sources: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> None:
    monkeypatch.setattr(
        bridge_module,
        "MCPIntegration",
        lambda **kwargs: MCPIntegration(mcp_client=_client(sources), **kwargs),
    )
    monkeypatch.setattr(bridge_module.settings, "MCP_TOOL_PATH_ENABLED", True)
    monkeypatch.setattr(
        bridge_module.settings, "SOURCE_SNAPSHOT_DIR", str(snapshot_dir)
    )

    class Worker:
        def __init__(self, **kwargs: Any) -> None:
            self.integration = kwargs["config"]["mcp_integration"]

        async def execute(self, task: AgentTask) -> AgentResult:
            from src.agents.tools.mediation import ToolCallIdentity

            results.append(
                await self.integration.search_academic_sources(
                    "inflation", identity=ToolCallIdentity.from_agent_task(task)
                )
            )
            return AgentResult(task.id, "success", {}, 1.0, 0.0)

    bridge = object.__new__(MASRSupervisorBridge)
    bridge.component_registry = Mock()
    bridge.component_registry.resolve.return_value = Worker
    bridge.gemini_service = None
    bridge.session_factory = factory
    bridge.audit_store = None
    worker = WorkerAssignment(
        worker_id="w",
        worker_type="comparative_analysis",
        objective="objective",
        output_schema={},
        permission_scopes=(),
        tool_allowlist=(TOOL_ACADEMIC_SEARCH,),
    )
    task = AgentTask(
        "task-1",
        "worker",
        {},
        {
            "run_id": "run-1",
            "task_id": "task-1",
            "attempt_id": "attempt-1",
            "organization_id": str(ORG_ID),
            CAPABILITY_GRANTS_CONTEXT_KEY: (_grant(),),
        },
    )
    await bridge._execute_plan_worker(worker, task)


async def _rows(factory: async_sessionmaker[AsyncSession], table: str) -> list[Any]:
    async with factory() as session:
        result = await session.execute(text(f"SELECT * FROM {table}"))
        return list(result.mappings().all())


@pytest.mark.asyncio
async def test_a_durable_search_persists_run_owned_evidence(
    monkeypatch: pytest.MonkeyPatch,
    factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    results: list[dict[str, Any]] = []
    await _run_worker(monkeypatch, factory, tmp_path, SOURCES, results)

    (evidence,) = await _rows(factory, "agent_evidence")
    (artifact,) = await _rows(factory, "agent_artifacts")
    (invocation,) = await _rows(factory, "agent_tool_invocations")
    text_bytes = f"GNNs for inflation\n\n{ABSTRACT}".encode()

    assert evidence["run_id"] == "run-1"
    assert evidence["organization_id"] == ORG_ID
    assert evidence["source_uri"] == ARXIV_URL
    assert evidence["source_type"] == "search_result"
    assert evidence["trust"] == "external_untrusted"
    assert evidence["producer_kind"] == "system"
    assert evidence["producer_tool_invocation_id"] == invocation["tool_invocation_id"]
    assert evidence["snapshot_artifact_id"] == artifact["artifact_id"]
    assert evidence["content_sha256"] == snapshot_digest(text_bytes)
    assert artifact["content_sha256"] == evidence["content_sha256"]
    snapshot = FilesystemSnapshotStore(root=tmp_path).get(artifact["storage_uri"])
    assert resolve(evidence["locator"], snapshot) == text_bytes

    summary = results[0]["source_evidence"]
    assert summary["recorded"] == [evidence["evidence_id"]]
    assert summary["skipped"] == 3
    assert summary["failed"] == []


async def _recorded_invocation(
    factory: async_sessionmaker[AsyncSession],
) -> ToolInvocation:
    (row,) = await _rows(factory, "agent_tool_invocations")
    invocation = await SessionToolAuditStore(factory).find_invocation(
        run_id="run-1",
        attempt_id="attempt-1",
        organization_id=str(ORG_ID),
        idempotency_key=row["idempotency_key"],
    )
    assert invocation is not None
    return invocation


@pytest.mark.asyncio
async def test_replaying_a_search_does_not_write_a_second_row(
    monkeypatch: pytest.MonkeyPatch,
    factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    results: list[dict[str, Any]] = []
    await _run_worker(monkeypatch, factory, tmp_path, SOURCES, results)
    await _run_worker(monkeypatch, factory, tmp_path, SOURCES, results)

    assert len(await _rows(factory, "agent_tool_invocations")) == 1
    assert results[1]["source_evidence"]["recorded"] == []
    assert results[1]["source_evidence"]["already_recorded"] == 1
    assert len(await _rows(factory, "agent_evidence")) == 1


@pytest.mark.asyncio
async def test_a_failed_write_reports_the_reason_and_writes_no_rows(
    monkeypatch: pytest.MonkeyPatch,
    factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    await _run_worker(monkeypatch, factory, tmp_path, SOURCES, [])
    invocation = await _recorded_invocation(factory)
    other_store = tmp_path / "other"
    recorder = SearchEvidenceRecorder(
        session_factory=factory, store=FilesystemSnapshotStore(root=other_store)
    )

    # Tenant B must not see tenant A's row, so this is a failure, not a replay.
    # A distinct invocation id gets past the artifact insert and reaches the
    # evidence-level tenant check, whose rollback must discard the artifact.
    foreign = invocation.model_copy(update={"tool_invocation_id": "other-invocation"})
    summary = await recorder(foreign, str(uuid.uuid4()))

    assert summary["recorded"] == []
    assert summary["already_recorded"] == 0
    assert summary["failed"] == [
        {"source_uri": ARXIV_URL, "reason": "TenantMismatchError"}
    ]
    assert len(await _rows(factory, "agent_evidence")) == 1
    assert len(await _rows(factory, "agent_artifacts")) == 1
    # Known ceiling: the snapshot is written before the row, so a failed row
    # leaves an orphan content-addressed file.
    assert [p for p in other_store.rglob("*") if p.is_file()]


@pytest.mark.asyncio
async def test_a_missing_organization_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    await _run_worker(monkeypatch, factory, tmp_path, SOURCES, [])
    recorder = SearchEvidenceRecorder(
        session_factory=factory, store=FilesystemSnapshotStore(root=tmp_path)
    )

    summary = await recorder(await _recorded_invocation(factory), None)

    assert summary["failed"][0]["reason"] == "MissingOrganizationContextError"
    assert len(await _rows(factory, "agent_evidence")) == 1


@pytest.mark.asyncio
async def test_a_concurrent_replay_is_counted_as_already_recorded(
    monkeypatch: pytest.MonkeyPatch,
    factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    await _run_worker(monkeypatch, factory, tmp_path, SOURCES, [])
    invocation = await _recorded_invocation(factory)
    real = EvidenceRepository.get_evidence
    calls = 0

    async def blind_first_lookup(self: EvidenceRepository, *args: Any, **kwargs: Any):
        # The first lookup misses, as it would while a peer's commit is in
        # flight, so the insert collides with the committed row.
        nonlocal calls
        calls += 1
        return None if calls == 1 else await real(self, *args, **kwargs)

    monkeypatch.setattr(EvidenceRepository, "get_evidence", blind_first_lookup)
    recorder = SearchEvidenceRecorder(
        session_factory=factory, store=FilesystemSnapshotStore(root=tmp_path)
    )

    summary = await recorder(invocation, str(ORG_ID))

    assert summary["already_recorded"] == 1
    assert summary["failed"] == []
    assert len(await _rows(factory, "agent_evidence")) == 1
