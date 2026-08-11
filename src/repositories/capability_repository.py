"""Repository for task-scoped capability grants and their approvals.

Follows the Wave 3 pattern: writes take the canonical Pydantic contracts
(``CapabilityGrant``, ``ApprovalRef``) that already validated the invariants
a grant or approval must hold — this repository records their result, it
never re-implements ``decide_capability`` or the contract's construction-time
checks. Rows are mutable (``BaseModel``), matching how grants and approvals
can be revised, unlike the append-only evidence and claim-support tables they
ultimately authorize.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.contracts import ApprovalRef, CapabilityGrant
from src.models.db.capability import AgentCapabilityApproval, AgentCapabilityGrant
from src.repositories.tenant_scope import (
    TenantMismatchError,
    normalize_organization_id,
    scope_to_organization,
)

OrganizationId = uuid.UUID | str | None


class CapabilityRepository:
    """Durable persistence for ``agent_capability_grants`` and
    ``agent_capability_approvals``."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- grants ------------------------------------------------------------

    async def create_grant(
        self, grant: CapabilityGrant, *, organization_id: OrganizationId
    ) -> AgentCapabilityGrant:
        """Insert a new capability grant.

        Unlike a task or attempt, a grant has no persisted parent contract to
        copy its tenant boundary from — ``CapabilityGrant`` carries
        ``run_id``/``task_id`` as plain identifiers, not a foreign object — so
        the caller's authenticated ``organization_id`` is written directly,
        exactly as ``RunLifecycleRepository.create_run`` does for a run.

        Args:
            grant: The validated grant contract.
            organization_id: The authenticated tenant boundary.

        Returns:
            The persisted row.

        Raises:
            MissingOrganizationContextError: No org context was supplied.
        """
        normalized_organization_id = normalize_organization_id(organization_id)
        row = AgentCapabilityGrant(
            grant_id=grant.grant_id,
            run_id=grant.run_id,
            task_id=grant.task_id,
            organization_id=normalized_organization_id,
            capability_scope=grant.capability_scope,
            tool_name=grant.tool_name,
            tool_versions=list(grant.tool_versions),
            sensitivity=grant.sensitivity.value,
            max_input_trust=grant.max_input_trust.value,
            requires_approval=grant.requires_approval,
            issued_at=grant.issued_at,
            expires_at=grant.expires_at,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def get_grant(
        self, grant_id: str, *, organization_id: OrganizationId = None
    ) -> AgentCapabilityGrant | None:
        """Fetch one grant row, optionally scoped to an organization."""
        return await self._get_grant_row(grant_id, organization_id=organization_id)

    async def list_grants_for_task(
        self, run_id: str, task_id: str, *, organization_id: OrganizationId = None
    ) -> list[AgentCapabilityGrant]:
        """Return every grant issued to one task — the candidate set
        ``decide_capability`` evaluates a request against."""
        query = select(AgentCapabilityGrant).where(
            AgentCapabilityGrant.run_id == run_id,
            AgentCapabilityGrant.task_id == task_id,
        )
        query = scope_to_organization(
            query, AgentCapabilityGrant.organization_id, organization_id
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def _get_grant_row(
        self, grant_id: str, *, organization_id: OrganizationId = None
    ) -> AgentCapabilityGrant | None:
        query = select(AgentCapabilityGrant).where(
            AgentCapabilityGrant.grant_id == grant_id
        )
        query = scope_to_organization(
            query, AgentCapabilityGrant.organization_id, organization_id
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    # --- approvals -----------------------------------------------------------

    async def create_approval(
        self, approval: ApprovalRef, *, organization_id: OrganizationId
    ) -> AgentCapabilityApproval:
        """Insert a new approval, copying its tenant boundary from the grant.

        Args:
            approval: The validated approval contract.
            organization_id: The authenticated tenant boundary; must match
                the parent grant's stored organization.

        Returns:
            The persisted row.

        Raises:
            MissingOrganizationContextError: No org context was supplied.
            TenantMismatchError: The org context disagrees with the parent
                grant's organization.
            ValueError: The parent grant does not exist.
        """
        normalized_organization_id = normalize_organization_id(organization_id)
        parent_grant = await self._get_grant_row(approval.grant_id)
        if parent_grant is None:
            raise ValueError(f"grant {approval.grant_id!r} does not exist")
        if parent_grant.organization_id != normalized_organization_id:
            raise TenantMismatchError(
                f"grant {approval.grant_id!r} does not belong to organization "
                f"{normalized_organization_id}"
            )
        row = AgentCapabilityApproval(
            approval_id=approval.approval_id,
            grant_id=approval.grant_id,
            organization_id=parent_grant.organization_id,
            request_fingerprint=approval.request_fingerprint,
            approved_by=approval.approved_by,
            approved_at=approval.approved_at,
            expires_at=approval.expires_at,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def list_approvals_for_grant(
        self, grant_id: str, *, organization_id: OrganizationId = None
    ) -> Sequence[AgentCapabilityApproval]:
        """Return every approval bound to one grant — the candidate set
        ``decide_capability`` matches a request fingerprint against."""
        query = select(AgentCapabilityApproval).where(
            AgentCapabilityApproval.grant_id == grant_id
        )
        query = scope_to_organization(
            query, AgentCapabilityApproval.organization_id, organization_id
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())


__all__ = ["CapabilityRepository"]
