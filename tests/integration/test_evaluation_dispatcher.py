"""``EvaluationDispatcher.evaluate_run`` against real Postgres."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.core.contracts import (
    AbsentEvidenceReason,
    ClaimSupport,
    ClaimSupportStatus,
    EvaluationResult,
    EvaluationStatus,
    ProducerKind,
)
from src.core.evaluation.dispatcher import EvaluationDispatcher
from src.core.evaluation.events import (
    EVALUATOR_CONTRACT_VIOLATION_CODE,
    EVALUATOR_EXCEPTION_CODE,
)
from src.core.evaluation.registry import (
    EvaluationContext,
    EvaluatorRegistry,
    build_default_registry,
)
from src.repositories.claim_support_repository import ClaimSupportRepository
from src.repositories.evaluation_result_repository import EvaluationResultRepository
from src.repositories.tenant_scope import TenantMismatchError
from tests.integration.wave4_helpers import seed_artifact, seed_run_task_attempt

pytestmark = [pytest.mark.integration]

NOW = datetime(2026, 11, 27, tzinfo=UTC)
ORG = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
OTHER_ORG = uuid.UUID("00000000-0000-0000-0000-0000000000bb")


class _Probe:
    """An evaluator that counts its calls and returns what it is told to."""

    dimension = "probe"
    reads_claim_support = False

    def __init__(self, evaluator_id: str = "probe", *, behaviour: Any = None) -> None:
        self.evaluator_id = evaluator_id
        self.evaluator_version = "1"
        self.calls = 0
        self.behaviour = behaviour

    async def evaluate(self, ctx: EvaluationContext) -> Sequence[EvaluationResult]:
        self.calls += 1
        if self.behaviour is not None:
            return self.behaviour(self, ctx)
        return [self.result(ctx)]

    def result(self, ctx: EvaluationContext, **fields: Any) -> EvaluationResult:
        values: dict[str, Any] = {
            "evaluation_result_id": ctx.result_id(self),
            "run_id": ctx.run_id,
            "evaluator_id": self.evaluator_id,
            "evaluator_version": self.evaluator_version,
            "dimension": self.dimension,
            "status": EvaluationStatus.PASSED,
            "deterministic": True,
            "completed_at": ctx.completed_at,
        }
        values.update(fields)
        return EvaluationResult(**values)


@pytest_asyncio.fixture(name="factory")
async def factory_fixture(
    test_engine: AsyncEngine, db_session: AsyncSession
) -> async_sessionmaker[AsyncSession]:
    await seed_run_task_attempt(db_session, organization_id=ORG)
    await seed_artifact(db_session, organization_id=ORG)
    await db_session.commit()
    return async_sessionmaker(test_engine, expire_on_commit=False)


def _dispatcher(
    factory: async_sessionmaker[AsyncSession], *evaluators: _Probe
) -> EvaluationDispatcher:
    registry = EvaluatorRegistry()
    for evaluator in evaluators:
        registry.register(evaluator)
    return EvaluationDispatcher(
        session_factory=factory, registry=registry, clock=lambda: NOW
    )


async def _rows(factory: async_sessionmaker[AsyncSession], **kwargs: Any) -> list[Any]:
    async with factory() as session:
        return await EvaluationResultRepository(session).list_evaluations_for_run(
            "run-1", organization_id=ORG, **kwargs
        )


async def _run(
    dispatcher: EvaluationDispatcher, trigger: str = "t-1", **kwargs: Any
) -> int:
    return await dispatcher.evaluate_run(
        run_id="run-1", organization_id=ORG, trigger=trigger, **kwargs
    )


async def test_each_registered_evaluator_records_one_row_stamped_with_the_trigger(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    a, b = _Probe("a"), _Probe("b")

    assert await _run(_dispatcher(factory, a, b)) == 2

    rows = await _rows(factory)
    assert sorted((r.evaluator_id, r.trigger) for r in rows) == [
        ("a", "t-1"),
        ("b", "t-1"),
    ]


async def test_a_raising_evaluator_becomes_an_error_row_and_siblings_still_record(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    def boom(_: _Probe, __: EvaluationContext) -> list[EvaluationResult]:
        raise RuntimeError("secret message")

    bad, good = _Probe("bad", behaviour=boom), _Probe("good")
    await _run(_dispatcher(factory, bad, good))

    by_id = {r.evaluator_id: r for r in await _rows(factory)}
    assert by_id["bad"].status == "error"
    assert by_id["bad"].error_code == EVALUATOR_EXCEPTION_CODE
    # The class name only: an exception message can carry content or secrets.
    assert by_id["bad"].details == {"exception_class": "RuntimeError"}
    assert by_id["good"].status == "passed"


@pytest.mark.parametrize(
    "bad_fields",
    [
        {"run_id": "other-run"},
        {"evaluator_version": "9"},
        {"trigger": "someone-else"},
        {"dimension": "someone-elses-dimension"},
        {"artifact_id": "artifact-9"},
        # No judge path exists yet, so a model-judged result is refused.
        {
            "deterministic": False,
            "judge_model_id": "judge-1",
            "rubric_sha256": "a" * 64,
        },
    ],
)
async def test_a_result_for_another_run_version_or_trigger_is_a_contract_violation(
    factory: async_sessionmaker[AsyncSession], bad_fields: dict[str, Any]
) -> None:
    probe = _Probe(behaviour=lambda p, ctx: [p.result(ctx, **bad_fields)])

    await _run(_dispatcher(factory, probe))

    (row,) = await _rows(factory)
    assert (row.status, row.error_code) == ("error", EVALUATOR_CONTRACT_VIOLATION_CODE)


@pytest.mark.parametrize("repeat", ["id", "subject"])
async def test_a_repeated_id_or_subject_in_one_batch_is_one_contract_violation(
    factory: async_sessionmaker[AsyncSession], repeat: str
) -> None:
    def batch(p: _Probe, ctx: EvaluationContext) -> list[EvaluationResult]:
        first = p.result(ctx, subject_id="c1")
        if repeat == "id":
            return [first, first]
        return [first, p.result(ctx, evaluation_result_id="other", subject_id="c1")]

    await _run(_dispatcher(factory, _Probe(behaviour=batch)))

    (row,) = await _rows(factory)
    assert row.error_code == EVALUATOR_CONTRACT_VIOLATION_CODE


async def test_redelivery_for_the_same_trigger_does_not_re_run_or_re_record(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    probe = _Probe()
    dispatcher = _dispatcher(factory, probe)

    assert await _run(dispatcher) == 1
    assert await _run(dispatcher) == 0

    assert probe.calls == 1
    assert len(await _rows(factory)) == 1


async def test_an_errored_evaluator_is_retried_and_the_success_supersedes_the_error(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    def flaky(p: _Probe, ctx: EvaluationContext) -> list[EvaluationResult]:
        if p.calls == 1:
            raise RuntimeError
        return [p.result(ctx)]

    dispatcher = _dispatcher(factory, _Probe(behaviour=flaky))
    await _run(dispatcher)
    await _run(dispatcher)

    history = await _rows(factory)
    latest = await _rows(factory, latest_only=True)
    assert sorted(r.status for r in history) == ["error", "passed"]
    assert [r.status for r in latest] == ["passed"]


async def test_an_evaluator_that_errors_again_adds_no_second_error_row(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    def always(_: _Probe, __: EvaluationContext) -> list[EvaluationResult]:
        raise RuntimeError

    probe = _Probe(behaviour=always)
    dispatcher = _dispatcher(factory, probe)
    assert await _run(dispatcher) == 1
    # The repeated error resolves to the stored row, so nothing new is recorded.
    assert await _run(dispatcher) == 0

    assert probe.calls == 2
    assert len(await _rows(factory)) == 1


async def test_a_filter_runs_only_the_named_evaluators_and_a_new_trigger_appends(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    a, b = _Probe("a"), _Probe("b")
    dispatcher = _dispatcher(factory, a, b)

    await _run(dispatcher, evaluator_ids={"a"})
    await _run(dispatcher, trigger="t-2", evaluator_ids={"a"})

    assert (a.calls, b.calls) == (2, 0)
    assert sorted(r.trigger for r in await _rows(factory)) == ["t-1", "t-2"]


async def test_another_tenant_cannot_evaluate_a_run_and_no_evaluator_is_called(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    probe = _Probe()

    with pytest.raises(TenantMismatchError):
        await _dispatcher(factory, probe).evaluate_run(
            run_id="run-1", organization_id=OTHER_ORG, trigger="t-1"
        )

    assert probe.calls == 0
    assert await _rows(factory) == []


async def test_a_missing_run_is_a_no_op(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    probe = _Probe()

    count = await _dispatcher(factory, probe).evaluate_run(
        run_id="no-such-run", organization_id=ORG, trigger="t-1"
    )

    assert (count, probe.calls) == (0, 0)


async def test_coverage_reads_the_runs_claim_supports_for_the_given_artifact(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        await ClaimSupportRepository(session).record_claim_support(
            ClaimSupport(
                claim_support_id="s-1",
                run_id="run-1",
                artifact_id="artifact-1",
                claim_id="claim-1",
                claim_text="A claim.",
                status=ClaimSupportStatus.UNSUPPORTED,
                absent_evidence_reason=AbsentEvidenceReason.NOT_ATTEMPTED,
                evaluator_id="claims.resolver",
                evaluator_version="1",
                producer_kind=ProducerKind.SYSTEM,
                explanation="No retrieval was run.",
                evaluated_at=NOW,
            ),
            organization_id=ORG,
        )
        await session.commit()
    dispatcher = EvaluationDispatcher(
        session_factory=factory, registry=build_default_registry(), clock=lambda: NOW
    )

    await _run(dispatcher, artifact_id="artifact-1")
    await _run(dispatcher, trigger="t-2")

    rows = {r.trigger: r for r in await _rows(factory)}
    assert rows["t-1"].error_code == "claim_support_not_attempted"
    assert rows["t-1"].status == "abstained"
    assert rows["t-2"].error_code == "no_final_report"


async def test_claims_of_another_artifact_are_not_counted(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        await seed_artifact(
            session,
            organization_id=ORG,
            artifact_id="artifact-2",
            content_sha256="b" * 64,
        )
        claims = ClaimSupportRepository(session)
        for artifact, claim, reason in (
            ("artifact-1", "c1", AbsentEvidenceReason.NO_SOURCE_FOUND),
            ("artifact-2", "c2", AbsentEvidenceReason.NOT_ATTEMPTED),
        ):
            await claims.record_claim_support(
                ClaimSupport(
                    claim_support_id=f"s-{claim}",
                    run_id="run-1",
                    artifact_id=artifact,
                    claim_id=claim,
                    claim_text="A claim.",
                    status=ClaimSupportStatus.UNSUPPORTED,
                    absent_evidence_reason=reason,
                    evaluator_id="claims.resolver",
                    evaluator_version="1",
                    producer_kind=ProducerKind.SYSTEM,
                    explanation="x",
                    evaluated_at=NOW,
                ),
                organization_id=ORG,
            )
        await session.commit()
    dispatcher = EvaluationDispatcher(
        session_factory=factory, registry=build_default_registry(), clock=lambda: NOW
    )

    await _run(dispatcher, artifact_id="artifact-1")

    (row,) = await _rows(factory)
    assert (row.status, row.details["material_claims"]) == ("passed", 1)


async def test_a_failed_write_is_raised_not_reported_as_nothing_to_record(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail(*_: Any, **__: Any) -> Any:
        raise OperationalError("INSERT", None, Exception("connection lost"))

    monkeypatch.setattr(EvaluationResultRepository, "append_evaluation", fail)

    with pytest.raises(OperationalError):
        await _run(_dispatcher(factory, _Probe("a")))
