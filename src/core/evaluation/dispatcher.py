"""Run registered evaluators against one finished run and store the outcomes.

Each evaluator's outcomes commit in their own transaction, so one evaluator's
failure never discards a sibling's row. Redelivery is cheap and idempotent: an
evaluator already recorded for a trigger is not called again, and result ids
are deterministic so a repeated write returns the stored row. An ``error`` row
does not count as done, so the next delivery retries that evaluator.

Delivery is the outbox handler's job (``EvaluationOutboxHandler``); nothing
here can authorize tools, edit the report, or change the run's state.
"""

import uuid
from collections.abc import Callable, Collection
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.core.contracts import EvaluationResult, EvaluationStatus
from src.core.evaluation.events import (
    EVALUATOR_CONTRACT_VIOLATION_CODE,
    EVALUATOR_EXCEPTION_CODE,
)
from src.core.evaluation.registry import (
    ClaimView,
    EvaluationContext,
    Evaluator,
    EvaluatorRegistry,
)
from src.repositories.claim_support_repository import ClaimSupportRepository
from src.repositories.evaluation_result_repository import EvaluationResultRepository
from src.repositories.tenant_scope import (
    TenantMismatchError,
    get_run_organization_id,
    normalize_organization_id,
)

logger = get_logger()


class EvaluationDispatcher:
    def __init__(
        self,
        *,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
        registry: EvaluatorRegistry,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session_factory = session_factory
        self.registry = registry
        self._clock = clock

    async def evaluate_run(
        self,
        *,
        run_id: str,
        organization_id: uuid.UUID | str,
        trigger: str,
        artifact_id: str | None = None,
        evaluator_ids: Collection[str] | None = None,
    ) -> int:
        """Evaluate ``run_id`` and return how many rows this call recorded.

        ``artifact_id`` is the report whose claims are judged; without it,
        evaluators that need a report abstain rather than guess one.
        ``evaluator_ids=None`` runs every registered evaluator.

        Raises:
            TenantMismatchError: ``organization_id`` is not the run's owner.
        """
        organization = normalize_organization_id(organization_id)
        async with self.session_factory() as session:
            owner = await get_run_organization_id(session, run_id)
            if owner is None:
                return 0
            if owner != organization:
                raise TenantMismatchError(
                    f"run {run_id!r} does not belong to organization {organization}"
                )
            ctx = EvaluationContext(
                run_id=run_id,
                organization_id=organization,
                task_id=None,
                artifact_id=artifact_id,
                claims=await self._claims(session, run_id, organization, artifact_id),
                trigger=trigger,
                completed_at=self._clock(),
                session=session,
            )
            repository = EvaluationResultRepository(session)
            done = {
                (row.evaluator_id, row.evaluator_version)
                for row in await repository.list_evaluations_for_run(
                    run_id, organization_id=organization, trigger=trigger, limit=10_000
                )
                if row.status != EvaluationStatus.ERROR.value
            }
            recorded = 0
            claims_stale = False
            # Evaluators that read claim support run last, on a fresh read, so a
            # pass appended earlier in this delivery is what they judge.
            for evaluator in sorted(
                self.registry.evaluators(), key=lambda e: e.reads_claim_support
            ):
                if (
                    evaluator_ids is not None
                    and evaluator.evaluator_id not in evaluator_ids
                ):
                    continue
                if (evaluator.evaluator_id, evaluator.evaluator_version) in done:
                    continue
                if evaluator.reads_claim_support and claims_stale:
                    ctx = replace(
                        ctx,
                        claims=await self._claims(
                            session, run_id, organization, artifact_id
                        ),
                    )
                    claims_stale = False
                written = await self._run_one(evaluator, ctx, repository)
                recorded += written
                claims_stale = claims_stale or bool(written)
            return recorded

    @staticmethod
    async def _claims(
        session: AsyncSession,
        run_id: str,
        organization: uuid.UUID,
        artifact_id: str | None,
    ) -> tuple[ClaimView, ...]:
        if artifact_id is None:
            return ()
        rows = await ClaimSupportRepository(session).list_claim_supports_for_run(
            run_id, organization_id=organization, latest_per_claim=True
        )
        return tuple(
            ClaimView(row.claim_id, row.status, row.absent_evidence_reason)
            for row in rows
            if row.artifact_id == artifact_id
        )

    async def _run_one(
        self,
        evaluator: Evaluator,
        ctx: EvaluationContext,
        repository: EvaluationResultRepository,
    ) -> int:
        try:
            results = self._validated(evaluator, ctx, await _evaluate(evaluator, ctx))
        except _ContractViolationError:
            results = [self._error(evaluator, ctx, EVALUATOR_CONTRACT_VIOLATION_CODE)]
        except _EvaluatorRaisedError as failure:
            # A raised write (a lost race on a unique key) leaves the session
            # unusable, and nothing the evaluator wrote should survive it.
            await ctx.session.rollback()
            exc = failure.__cause__
            logger.warning(
                "evaluator_raised",
                evaluator_id=evaluator.evaluator_id,
                run_id=ctx.run_id,
                exception_class=type(exc).__name__,
            )
            results = [
                self._error(
                    evaluator,
                    ctx,
                    EVALUATOR_EXCEPTION_CODE,
                    details={"exception_class": type(exc).__name__},
                )
            ]
        try:
            written = 0
            for result in results:
                _, is_new = await repository.append_evaluation(
                    result, organization_id=ctx.organization_id
                )
                written += is_new
            await ctx.session.commit()
        except Exception:
            await ctx.session.rollback()
            raise
        return written

    @staticmethod
    def _validated(
        evaluator: Evaluator,
        ctx: EvaluationContext,
        results: Collection[EvaluationResult],
    ) -> list[EvaluationResult]:
        stamped = [
            r.model_copy(update={"trigger": ctx.trigger}) if r.trigger is None else r
            for r in results
        ]
        ids = {r.evaluation_result_id for r in stamped}
        subjects = {(r.dimension, r.subject_id) for r in stamped}
        if (
            len(ids) != len(stamped)
            or len(subjects) != len(stamped)
            or any(
                (
                    r.run_id,
                    r.evaluator_id,
                    r.evaluator_version,
                    r.dimension,
                    r.artifact_id,
                    r.trigger,
                    r.deterministic,
                )
                != (
                    ctx.run_id,
                    evaluator.evaluator_id,
                    evaluator.evaluator_version,
                    evaluator.dimension,
                    ctx.artifact_id,
                    ctx.trigger,
                    True,  # no judge path exists yet; model-judged results are refused
                )
                for r in stamped
            )
        ):
            raise _ContractViolationError
        return stamped

    @staticmethod
    def _error(
        evaluator: Evaluator,
        ctx: EvaluationContext,
        code: str,
        *,
        details: dict[str, str] | None = None,
    ) -> EvaluationResult:
        # One fixed id per (evaluator, trigger): a repeated failure on redelivery
        # returns this row instead of writing a second error.
        return EvaluationResult(
            evaluation_result_id=ctx.result_id(evaluator, discriminator="error"),
            run_id=ctx.run_id,
            artifact_id=ctx.artifact_id,
            evaluator_id=evaluator.evaluator_id,
            evaluator_version=evaluator.evaluator_version,
            dimension=evaluator.dimension,
            status=EvaluationStatus.ERROR,
            deterministic=True,
            error_code=code,
            details=details or {},
            trigger=ctx.trigger,
            completed_at=ctx.completed_at,
        )


class _ContractViolationError(Exception):
    """An evaluator returned a batch the dispatcher refuses to store."""


class _EvaluatorRaisedError(Exception):
    """An evaluator raised; its failure is recorded as an error row."""


async def _evaluate(
    evaluator: Evaluator, ctx: EvaluationContext
) -> Collection[EvaluationResult]:
    # Evaluators are pluggable, so any failure is translated at this one
    # boundary rather than letting one evaluator abort its siblings.
    try:
        return await evaluator.evaluate(ctx)
    except Exception as exc:
        raise _EvaluatorRaisedError from exc
