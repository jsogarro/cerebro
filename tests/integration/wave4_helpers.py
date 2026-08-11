"""Shared seed helpers for the Wave 4 persistence integration tests.

Not a test module itself (no ``test_`` prefix) — imported by
``test_artifact_repository.py``, ``test_tool_invocation_repository.py``,
``test_evidence_repository.py``, and ``test_claim_support_repository.py`` to
avoid re-deriving the same run/task/attempt/artifact seed data four times.
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.contracts import (
    Artifact,
    ArtifactStatus,
    Attempt,
    AttemptStatus,
    Evidence,
    ProducerKind,
    Run,
    RunStatus,
    Task,
    TaskStatus,
    TrustClassification,
)
from src.repositories.artifact_repository import ArtifactRepository
from src.repositories.evidence_repository import EvidenceRepository
from src.repositories.run_lifecycle_repository import RunLifecycleRepository

NOW = datetime(2026, 8, 6, tzinfo=UTC)


async def seed_run_task_attempt(
    session: AsyncSession,
    *,
    organization_id: object,
    run_id: str = "run-1",
    task_id: str = "task-1",
    attempt_id: str = "attempt-1",
) -> None:
    """Insert a minimal run/task/attempt chain via the Wave 3 repositories."""
    run_repo = RunLifecycleRepository(session)
    run = Run(
        run_id=run_id,
        tenant_id=str(organization_id),
        workflow_definition_id="research",
        workflow_definition_version="1",
        routing_policy_id="default",
        routing_policy_version="1",
        idempotency_key=f"submit-{run_id}",
        requested_by="user-1",
        status=RunStatus.CREATED,
        created_at=NOW,
        updated_at=NOW,
    )
    await run_repo.create_run(run, organization_id=organization_id)

    task = Task(
        task_id=task_id,
        run_id=run_id,
        task_key="literature-review",
        task_type="research",
        objective="Survey the field",
        idempotency_key=f"key-{task_id}",
        status=TaskStatus.PENDING,
        created_at=NOW,
        updated_at=NOW,
    )
    await run_repo.create_task(task, organization_id=organization_id)

    attempt = Attempt(
        attempt_id=attempt_id,
        task_id=task_id,
        ordinal=1,
        idempotency_key=f"attempt-key-{attempt_id}",
        status=AttemptStatus.CREATED,
        created_at=NOW,
        updated_at=NOW,
    )
    await run_repo.create_attempt(
        attempt, run_id=run_id, organization_id=organization_id
    )


async def seed_artifact(
    session: AsyncSession,
    *,
    organization_id: object,
    artifact_id: str = "artifact-1",
    run_id: str = "run-1",
    content_sha256: str = "a" * 64,
) -> None:
    """Insert one final, externally-trusted artifact via ``ArtifactRepository``."""
    artifact_repo = ArtifactRepository(session)
    artifact = Artifact(
        artifact_id=artifact_id,
        run_id=run_id,
        kind="source_snapshot",
        media_type="text/html",
        storage_uri="s3://bucket/key",
        content_sha256=content_sha256,
        status=ArtifactStatus.FINAL,
        trust=TrustClassification.EXTERNAL_UNTRUSTED,
        producer="acquisition-tool",
        created_at=NOW,
    )
    await artifact_repo.create_artifact(artifact, organization_id=organization_id)


async def seed_evidence(
    session: AsyncSession,
    *,
    organization_id: object,
    evidence_id: str = "evidence-1",
    run_id: str = "run-1",
    task_id: str = "task-1",
    snapshot_artifact_id: str = "artifact-1",
    content_sha256: str = "a" * 64,
    locator: str = "char:0-120",
) -> None:
    """Insert one evidence row via ``EvidenceRepository``.

    ``producer_kind=SYSTEM`` with no ``prompt_binding`` keeps this a minimal
    seed -- callers that need a model-produced row build one directly with
    ``Evidence`` instead.
    """
    evidence_repo = EvidenceRepository(session)
    evidence = Evidence(
        evidence_id=evidence_id,
        run_id=run_id,
        task_id=task_id,
        source_type="web_page",
        source_uri="https://example.org/paper",
        snapshot_artifact_id=snapshot_artifact_id,
        content_sha256=content_sha256,
        locator=locator,
        trust=TrustClassification.EXTERNAL_UNTRUSTED,
        producer_kind=ProducerKind.SYSTEM,
        acquired_at=NOW,
    )
    await evidence_repo.record_evidence(evidence, organization_id=organization_id)
