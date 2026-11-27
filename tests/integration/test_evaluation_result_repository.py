"""The evaluation write and read paths, tenant-scoped, against real Postgres."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.contracts import (
    REDACTION_MARKER,
    AbsentEvidenceReason,
    ClaimSupport,
    ClaimSupportStatus,
    EvaluationResult,
    EvaluationStatus,
    ProducerKind,
)
from src.models.db.evaluation_result import AgentEvaluationResult
from src.repositories.claim_support_repository import ClaimSupportRepository
from src.repositories.evaluation_result_repository import EvaluationResultRepository
from src.repositories.tenant_scope import (
    MissingOrganizationContextError,
    TenantMismatchError,
)
from tests.integration.wave4_helpers import seed_artifact, seed_run_task_attempt

pytestmark = [pytest.mark.integration]

NOW = datetime(2026, 11, 27, tzinfo=UTC)
ORG = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
OTHER_ORG = uuid.UUID("00000000-0000-0000-0000-0000000000bb")


@pytest_asyncio.fixture(name="repo")
async def repo_fixture(db_session: AsyncSession) -> EvaluationResultRepository:
    await seed_run_task_attempt(db_session, organization_id=ORG)
    await seed_artifact(db_session, organization_id=ORG)
    return EvaluationResultRepository(db_session)


def _result(**overrides: object) -> EvaluationResult:
    values: dict[str, object] = {
        "evaluation_result_id": "evaluation-1",
        "run_id": "run-1",
        "evaluator_id": "claims.coverage",
        "evaluator_version": "1.0.0",
        "dimension": "claims.coverage",
        "status": EvaluationStatus.PASSED,
        "deterministic": True,
        "trigger": "trigger-1",
        "completed_at": NOW,
    }
    values.update(overrides)
    return EvaluationResult(**values)  # type: ignore[arg-type]


async def test_a_recorded_result_round_trips(
    repo: EvaluationResultRepository,
) -> None:
    await repo.record_evaluation(_result(score=1.0), organization_id=ORG)

    row = await repo.get_evaluation("evaluation-1", organization_id=ORG)

    assert row is not None
    assert (row.status, row.score, row.organization_id) == ("passed", 1.0, ORG)
    assert row.recorded_at is not None


async def test_a_result_without_a_trigger_is_refused_and_nothing_is_written(
    repo: EvaluationResultRepository, db_session: AsyncSession
) -> None:
    with pytest.raises(ValueError, match="trigger"):
        await repo.record_evaluation(_result(trigger=None), organization_id=ORG)

    count = await db_session.scalar(
        select(func.count()).select_from(AgentEvaluationResult)
    )
    assert count == 0


async def test_recording_the_same_result_twice_returns_the_first_row(
    repo: EvaluationResultRepository, db_session: AsyncSession
) -> None:
    first = await repo.record_evaluation(_result(), organization_id=ORG)
    again = await repo.record_evaluation(
        _result(status=EvaluationStatus.FAILED), organization_id=ORG
    )

    assert again.id == first.id
    assert again.status == "passed"
    count = await db_session.scalar(
        select(func.count()).select_from(AgentEvaluationResult)
    )
    assert count == 1


async def test_a_write_needs_the_runs_organization(
    repo: EvaluationResultRepository,
) -> None:
    with pytest.raises(MissingOrganizationContextError):
        await repo.record_evaluation(_result(), organization_id=None)
    with pytest.raises(TenantMismatchError):
        await repo.record_evaluation(_result(), organization_id=OTHER_ORG)


async def test_details_and_rationale_are_redacted_on_write(
    repo: EvaluationResultRepository,
) -> None:
    await repo.record_evaluation(
        _result(details={"api_key": "sk-live-abcdef0123456789"}),
        organization_id=ORG,
    )

    row = await repo.get_evaluation("evaluation-1", organization_id=ORG)

    assert row is not None
    assert row.details == {"api_key": REDACTION_MARKER}


async def test_another_tenant_reads_nothing(
    repo: EvaluationResultRepository,
) -> None:
    await repo.record_evaluation(_result(input_sha256="a" * 64), organization_id=ORG)

    assert await repo.get_evaluation("evaluation-1", organization_id=OTHER_ORG) is None
    assert await repo.list_evaluations_for_run("run-1", organization_id=OTHER_ORG) == []
    assert (
        await repo.find_by_input_sha256(
            organization_id=OTHER_ORG,
            evaluator_id="claims.coverage",
            evaluator_version="1.0.0",
            input_sha256="a" * 64,
        )
        is None
    )
    # The same read for the owner still sees the row, so the empty results
    # above come from the tenant filter and not from an empty table.
    assert len(await repo.list_evaluations_for_run("run-1", organization_id=ORG)) == 1


async def test_a_read_without_an_organization_is_refused(
    repo: EvaluationResultRepository,
) -> None:
    with pytest.raises(MissingOrganizationContextError):
        await repo.list_evaluations_for_run("run-1", organization_id=None)


async def test_the_canonical_view_is_the_latest_row_by_database_time(
    repo: EvaluationResultRepository, db_session: AsyncSession
) -> None:
    # The earlier error carries the *later* completed_at: a pod with a fast
    # clock must not make a stale failure outrank the success that followed it.
    await repo.record_evaluation(
        _result(
            evaluation_result_id="evaluation-error",
            status=EvaluationStatus.ERROR,
            error_code="evaluator_exception",
            trigger="t-1",
            completed_at=NOW + timedelta(hours=1),
        ),
        organization_id=ORG,
    )
    await db_session.commit()
    await repo.record_evaluation(
        _result(evaluation_result_id="evaluation-pass", trigger="t-2"),
        organization_id=ORG,
    )
    await db_session.commit()

    history = await repo.list_evaluations_for_run("run-1", organization_id=ORG)
    latest = await repo.list_evaluations_for_run(
        "run-1", organization_id=ORG, latest_only=True
    )

    assert len(history) == 2
    assert [row.evaluation_result_id for row in latest] == ["evaluation-pass"]


async def test_latest_is_chosen_per_subject(repo: EvaluationResultRepository) -> None:
    for subject in ("claim-1", "claim-2", None):
        await repo.record_evaluation(
            _result(evaluation_result_id=f"evaluation-{subject}", subject_id=subject),
            organization_id=ORG,
        )

    latest = await repo.list_evaluations_for_run(
        "run-1", organization_id=ORG, latest_only=True
    )

    assert len(latest) == 3


async def test_trigger_and_dimension_filters_apply_before_the_latest_window(
    repo: EvaluationResultRepository, db_session: AsyncSession
) -> None:
    await repo.record_evaluation(
        _result(evaluation_result_id="evaluation-a", trigger="t-a"),
        organization_id=ORG,
    )
    await db_session.commit()
    await repo.record_evaluation(
        _result(evaluation_result_id="evaluation-b", trigger="t-b"),
        organization_id=ORG,
    )
    await db_session.commit()

    rows = await repo.list_evaluations_for_run(
        "run-1", organization_id=ORG, latest_only=True, trigger="t-a"
    )
    none = await repo.list_evaluations_for_run(
        "run-1", organization_id=ORG, dimension="other"
    )

    assert [row.evaluation_result_id for row in rows] == ["evaluation-a"]
    assert none == []


def _support(claim_id: str, claim_support_id: str, **overrides: object) -> ClaimSupport:
    values: dict[str, object] = {
        "claim_support_id": claim_support_id,
        "run_id": "run-1",
        "artifact_id": "artifact-1",
        "claim_id": claim_id,
        "claim_text": "A claim.",
        "status": ClaimSupportStatus.UNSUPPORTED,
        "absent_evidence_reason": AbsentEvidenceReason.NOT_ATTEMPTED,
        "evaluator_id": "claims.resolver",
        "evaluator_version": "1",
        "producer_kind": ProducerKind.SYSTEM,
        "explanation": "No retrieval was run.",
        "evaluated_at": NOW,
    }
    values.update(overrides)
    return ClaimSupport(**values)  # type: ignore[arg-type]


async def test_claim_supports_for_a_run_are_ordered_and_tenant_scoped(
    repo: EvaluationResultRepository, db_session: AsyncSession
) -> None:
    claims = ClaimSupportRepository(db_session)
    for claim_id, support_id in (("claim-b", "s-1"), ("claim-a", "s-2")):
        await claims.record_claim_support(
            _support(claim_id, support_id), organization_id=ORG
        )
        await db_session.commit()
    await claims.record_claim_support(
        _support("claim-a", "s-3", explanation="Retrieved nothing.", evaluated_at=NOW),
        organization_id=ORG,
    )
    await db_session.commit()

    every = await claims.list_claim_supports_for_run("run-1", organization_id=ORG)
    latest = await claims.list_claim_supports_for_run(
        "run-1", organization_id=ORG, latest_per_claim=True
    )
    other = await claims.list_claim_supports_for_run("run-1", organization_id=OTHER_ORG)

    assert [row.claim_support_id for row in every] == ["s-2", "s-3", "s-1"]
    assert [row.claim_support_id for row in latest] == ["s-3", "s-1"]
    assert other == []


async def test_another_tenant_holding_the_same_result_id_cannot_block_the_owner(
    repo: EvaluationResultRepository, db_session: AsyncSession
) -> None:
    await seed_run_task_attempt(
        db_session,
        organization_id=OTHER_ORG,
        run_id="run-b",
        task_id="task-b",
        attempt_id="attempt-b",
    )
    await repo.record_evaluation(
        _result(run_id="run-b"), organization_id=OTHER_ORG
    )

    mine = await repo.record_evaluation(_result(), organization_id=ORG)

    assert mine.organization_id == ORG
    assert (await repo.get_evaluation("evaluation-1", organization_id=ORG)) is not None


async def test_trigger_and_dataset_version_are_redacted_on_write(
    repo: EvaluationResultRepository,
) -> None:
    secret = "sk-ant-api03-" + "A" * 40
    row = await repo.record_evaluation(
        _result(trigger=f"rerun {secret}", dataset_version=f"ds@{secret}"),
        organization_id=ORG,
    )

    assert secret not in row.trigger
    assert secret not in (row.dataset_version or "")


async def test_claim_supports_are_ordered_by_database_time_not_evaluator_time(
    repo: EvaluationResultRepository, db_session: AsyncSession
) -> None:
    claims = ClaimSupportRepository(db_session)
    await claims.record_claim_support(
        _support("claim-a", "s-1", evaluated_at=NOW + timedelta(hours=1)),
        organization_id=ORG,
    )
    await db_session.commit()
    await claims.record_claim_support(
        _support("claim-a", "s-2", evaluated_at=NOW), organization_id=ORG
    )
    await db_session.commit()

    latest = await claims.list_claim_supports_for_run(
        "run-1", organization_id=ORG, latest_per_claim=True
    )

    assert [row.claim_support_id for row in latest] == ["s-2"]
