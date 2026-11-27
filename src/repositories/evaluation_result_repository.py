"""Repository for the append-only ``agent_evaluation_results`` table.

``record_evaluation`` is the only write path. Every read takes an organization
and fails closed without one, unlike the permissive reads some repositories keep
for boot-time scans: an evaluation verdict is tenant data with no
cross-tenant consumer.

Nothing here commits; rows land in the caller's transaction.
"""

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.contracts import EvaluationResult, redact
from src.models.db.evaluation_result import AgentEvaluationResult
from src.repositories.tenant_scope import (
    TenantMismatchError,
    get_run_organization_id,
    normalize_organization_id,
    scope_to_organization,
)

OrganizationId = uuid.UUID | str | None

_ORG = AgentEvaluationResult.organization_id


class EvaluationResultRepository:
    """Durable, append-only persistence for evaluator outcomes."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_evaluation(
        self, result: EvaluationResult, *, organization_id: OrganizationId
    ) -> AgentEvaluationResult:
        """Append one outcome, or return the row already stored under its id.

        Redelivery of the same evaluation therefore never writes a second row;
        an intentional re-evaluation carries a new id and appends.

        Raises:
            MissingOrganizationContextError: No org context was supplied.
            TenantMismatchError: The org disagrees with the run's organization.
            ValueError: The run does not exist, or the result has no trigger.
        """
        organization = normalize_organization_id(organization_id)
        if not result.trigger:
            raise ValueError(
                "an evaluation result must carry the trigger that caused it"
            )
        run_organization = await get_run_organization_id(self.session, result.run_id)
        if run_organization is None:
            raise ValueError(f"run {result.run_id!r} does not exist")
        if run_organization != organization:
            raise TenantMismatchError(
                f"run {result.run_id!r} does not belong to organization {organization}"
            )

        existing = await self.get_evaluation(
            result.evaluation_result_id, organization_id=organization
        )
        if existing is not None:
            return existing

        row = AgentEvaluationResult(
            evaluation_result_id=result.evaluation_result_id,
            run_id=result.run_id,
            task_id=result.task_id,
            artifact_id=result.artifact_id,
            organization_id=organization,
            evaluator_id=result.evaluator_id,
            evaluator_version=result.evaluator_version,
            dimension=result.dimension,
            status=result.status.value,
            deterministic=result.deterministic,
            score=result.score,
            error_code=result.error_code,
            details=redact(result.details),
            rationale=redact(result.rationale),
            rubric_sha256=result.rubric_sha256,
            judge_model_id=result.judge_model_id,
            dataset_version=result.dataset_version,
            subject_id=result.subject_id,
            input_sha256=result.input_sha256,
            trigger=result.trigger,
            completed_at=result.completed_at,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def get_evaluation(
        self, evaluation_result_id: str, *, organization_id: OrganizationId
    ) -> AgentEvaluationResult | None:
        query = select(AgentEvaluationResult).where(
            AgentEvaluationResult.evaluation_result_id == evaluation_result_id
        )
        query = scope_to_organization(
            query, _ORG, normalize_organization_id(organization_id)
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def list_evaluations_for_run(
        self,
        run_id: str,
        *,
        organization_id: OrganizationId,
        limit: int = 200,
        latest_only: bool = False,
        trigger: str | None = None,
        dimension: str | None = None,
    ) -> list[AgentEvaluationResult]:
        """Return a run's rows, oldest first, or its canonical view.

        The canonical view is the latest row per ``(evaluator_id,
        evaluator_version, dimension, subject_id)`` by ``(recorded_at,
        evaluation_result_id)``. ``trigger`` and ``dimension`` filter before
        that window, so the latest row is chosen within the filter.
        """
        conditions: list[Any] = [AgentEvaluationResult.run_id == run_id]
        if trigger is not None:
            conditions.append(AgentEvaluationResult.trigger == trigger)
        if dimension is not None:
            conditions.append(AgentEvaluationResult.dimension == dimension)

        query = select(AgentEvaluationResult).where(*conditions)
        query = scope_to_organization(
            query, _ORG, normalize_organization_id(organization_id)
        )
        if latest_only:
            rank = (
                func.row_number()
                .over(
                    partition_by=(
                        AgentEvaluationResult.evaluator_id,
                        AgentEvaluationResult.evaluator_version,
                        AgentEvaluationResult.dimension,
                        AgentEvaluationResult.subject_id,
                    ),
                    order_by=(
                        AgentEvaluationResult.recorded_at.desc(),
                        AgentEvaluationResult.evaluation_result_id.desc(),
                    ),
                )
                .label("rank")
            )
            ranked = query.add_columns(rank).subquery()
            query = (
                select(AgentEvaluationResult)
                .join(ranked, AgentEvaluationResult.id == ranked.c.id)
                .where(ranked.c.rank == 1)
            )
        query = query.order_by(
            AgentEvaluationResult.recorded_at,
            AgentEvaluationResult.evaluation_result_id,
        ).limit(limit)
        return list((await self.session.execute(query)).scalars().all())

    async def find_by_input_sha256(
        self,
        *,
        organization_id: OrganizationId,
        evaluator_id: str,
        evaluator_version: str,
        input_sha256: str,
    ) -> AgentEvaluationResult | None:
        """Return the newest row an evaluator version recorded for this input."""
        query = (
            select(AgentEvaluationResult)
            .where(
                AgentEvaluationResult.evaluator_id == evaluator_id,
                AgentEvaluationResult.evaluator_version == evaluator_version,
                AgentEvaluationResult.input_sha256 == input_sha256,
            )
            .order_by(
                AgentEvaluationResult.recorded_at.desc(),
                AgentEvaluationResult.evaluation_result_id.desc(),
            )
            .limit(1)
        )
        query = scope_to_organization(
            query, _ORG, normalize_organization_id(organization_id)
        )
        return (await self.session.execute(query)).scalar_one_or_none()


__all__ = ["EvaluationResultRepository"]
