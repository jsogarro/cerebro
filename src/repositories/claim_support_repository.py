"""Repository for the append-only ``agent_claim_supports`` table.

Follows the Wave 3 pattern used for ``agent_run_events``: ``record_claim_support``
is the only write path, and a re-evaluation of the same ``claim_id`` always
inserts a **new** row rather than updating the old one — that is what the
four-state model's audit trail depends on, and it is also why ``claim_id``
carries an index but no uniqueness constraint on this table.

``evidence_count`` is never accepted as a caller-supplied argument. This
repository computes it as ``len(claim.evidence_ids)`` at the single point a
row is constructed, the same way ``RunEventRepository`` allocates
``sequence`` internally rather than trusting a caller to supply the right
value — that is what keeps the denormalized column from drifting out of
agreement with the JSON array the seven CHECK constraints reason about.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.contracts import ClaimSupport
from src.models.db.claim_support import AgentClaimSupport
from src.models.db.run_lifecycle import AgentRun
from src.repositories.tenant_scope import (
    TenantMismatchError,
    normalize_organization_id,
)

OrganizationId = uuid.UUID | str | None


class ClaimSupportRepository:
    """Durable, append-only persistence for ``agent_claim_supports``."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_claim_support(
        self, claim: ClaimSupport, *, organization_id: OrganizationId
    ) -> AgentClaimSupport:
        """Append one claim-support judgment from an admitted contract.

        Args:
            claim: The validated claim-support contract.
            organization_id: The authenticated tenant boundary; must match
                the parent run's stored organization.

        Returns:
            The persisted row.

        Raises:
            MissingOrganizationContextError: No org context was supplied.
            TenantMismatchError: The org context disagrees with the parent
                run's organization.
            ValueError: The parent run does not exist.
        """
        normalized_organization_id = normalize_organization_id(organization_id)
        run_organization_id = await self._get_run_organization_id(claim.run_id)
        if run_organization_id is None:
            raise ValueError(f"run {claim.run_id!r} does not exist")
        if run_organization_id != normalized_organization_id:
            raise TenantMismatchError(
                f"run {claim.run_id!r} does not belong to organization "
                f"{normalized_organization_id}"
            )

        binding = claim.prompt_binding
        row = AgentClaimSupport(
            claim_support_id=claim.claim_support_id,
            run_id=claim.run_id,
            artifact_id=claim.artifact_id,
            claim_id=claim.claim_id,
            organization_id=normalized_organization_id,
            claim_text=claim.claim_text,
            status=claim.status.value,
            evidence_ids=list(claim.evidence_ids),
            evidence_count=len(claim.evidence_ids),
            absent_evidence_reason=(
                claim.absent_evidence_reason.value
                if claim.absent_evidence_reason
                else None
            ),
            evaluator_id=claim.evaluator_id,
            evaluator_version=claim.evaluator_version,
            producer_kind=claim.producer_kind.value,
            prompt_id=binding.prompt_id if binding else None,
            prompt_version=binding.prompt_version if binding else None,
            template_sha256=binding.template_sha256 if binding else None,
            rendered_sha256=binding.rendered_sha256 if binding else None,
            explanation=claim.explanation,
            evaluated_at=claim.evaluated_at,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def get_claim_support(
        self, claim_support_id: str, *, organization_id: OrganizationId = None
    ) -> AgentClaimSupport | None:
        """Fetch one claim-support row, optionally scoped to an organization."""
        query = select(AgentClaimSupport).where(
            AgentClaimSupport.claim_support_id == claim_support_id
        )
        if organization_id is not None:
            query = query.where(
                AgentClaimSupport.organization_id
                == normalize_organization_id(organization_id)
            )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def list_claim_supports_for_claim(
        self, claim_id: str, *, organization_id: OrganizationId = None
    ) -> list[AgentClaimSupport]:
        """Return every judgment recorded for one claim, oldest first.

        A re-evaluation appends a new row rather than replacing the old one,
        so this can return more than one result — the caller decides whether
        it wants the latest or the full history.
        """
        query = (
            select(AgentClaimSupport)
            .where(AgentClaimSupport.claim_id == claim_id)
            .order_by(AgentClaimSupport.evaluated_at)
        )
        if organization_id is not None:
            query = query.where(
                AgentClaimSupport.organization_id
                == normalize_organization_id(organization_id)
            )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def _get_run_organization_id(self, run_id: str) -> uuid.UUID | None:
        query = select(AgentRun.organization_id).where(AgentRun.run_id == run_id)
        result = await self.session.execute(query)
        row = result.first()
        if row is None:
            return None
        organization_id: uuid.UUID | None = row[0]
        return organization_id


__all__ = ["ClaimSupportRepository"]
