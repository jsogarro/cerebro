"""Repository for ``agent_tool_invocations``.

Follows the Wave 3 pattern: writes take the canonical ``ToolInvocation``
contract that already validated its status/output-trust invariants — this
repository records the result, it never re-implements
``ToolInvocation.validate_status_fields``. Rows are mutable (``BaseModel``):
an invocation moves ``requested`` -> ``running`` -> a terminal status on the
same row, mirroring ``agent_task_attempts``.

``ToolInvocation`` itself carries no capability-decision fields — the
authorization outcome is a separate contract
(``src.core.contracts.capabilities.CapabilityDecision``) produced by
``decide_capability``, not part of the invocation contract. Every write
method here therefore takes the governing ``CapabilityDecision`` as an
explicit, separate argument, and ``ck_agent_tool_invocation_capability_denial``
is what keeps a denied decision from ever being paired with a non-denied
invocation status.

``ToolInvocation`` also carries no digest fields — ``input_sha256`` and
``output_sha256`` are computed here via ``boundary_digest`` over the
redacted ``input``/``output`` JSON (the same digest function the redaction
contract uses for every other boundary record), and
``request_fingerprint``/``capability_denial_reason`` are copied from the
governing ``CapabilityDecision`` rather than recomputed.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.contracts import CapabilityDecision, ToolInvocation, boundary_digest
from src.models.db.tool_invocation import AgentToolInvocation
from src.repositories.tenant_scope import (
    TenantMismatchError,
    normalize_organization_id,
)

OrganizationId = uuid.UUID | str | None


class ToolInvocationRepository:
    """Durable persistence for ``agent_tool_invocations``."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_tool_invocation(
        self,
        invocation: ToolInvocation,
        *,
        organization_id: OrganizationId,
        capability_decision: CapabilityDecision,
    ) -> AgentToolInvocation:
        """Insert a new tool invocation row.

        Args:
            invocation: The validated invocation contract.
            organization_id: The authenticated tenant boundary.
            capability_decision: The decision that authorized (or denied)
                this invocation. A ``DENY`` decision requires
                ``invocation.status`` to be ``ToolInvocationStatus.DENIED`` —
                enforced by ``ck_agent_tool_invocation_capability_denial``.

        Returns:
            The persisted row.

        Raises:
            MissingOrganizationContextError: No org context was supplied.
        """
        normalized_organization_id = normalize_organization_id(organization_id)
        binding = invocation.prompt_binding
        # `JsonObject`'s `AfterValidator` freezes recursively into
        # `MappingProxyType` at every nesting level, not just the top one, so
        # `dict(invocation.input)` only thaws the top level -- any nested
        # mapping stays a `mappingproxy`, which asyncpg cannot serialize into
        # the JSON column. `model_dump()` runs the field's `PlainSerializer`,
        # which recursively produces plain dicts/lists at every level; using
        # the same dumped value for both the row and the digest also means
        # the digest is provably over what actually gets persisted.
        dumped = invocation.model_dump()
        input_payload = dumped["input"]
        output_payload = dumped["output"]
        row = AgentToolInvocation(
            tool_invocation_id=invocation.tool_invocation_id,
            run_id=invocation.run_id,
            task_id=invocation.task_id,
            attempt_id=invocation.attempt_id,
            organization_id=normalized_organization_id,
            tool_name=invocation.tool_name,
            tool_version=invocation.tool_version,
            status=invocation.status.value,
            capability_scope=invocation.capability_scope,
            idempotency_key=invocation.idempotency_key,
            input=input_payload,
            input_trust=invocation.input_trust.value,
            input_sha256=boundary_digest(input_payload),
            output=output_payload,
            output_trust=(
                invocation.output_trust.value
                if invocation.output_trust is not None
                else None
            ),
            output_sha256=(
                boundary_digest(output_payload) if output_payload is not None else None
            ),
            capability_decision_effect=capability_decision.effect.value,
            capability_grant_id=capability_decision.grant_id,
            capability_approval_id=capability_decision.approval_id,
            capability_denial_reason=(
                capability_decision.denial_reason.value
                if capability_decision.denial_reason is not None
                else None
            ),
            request_fingerprint=capability_decision.request_fingerprint,
            producer_kind=invocation.producer_kind.value,
            prompt_id=binding.prompt_id if binding else None,
            prompt_version=binding.prompt_version if binding else None,
            template_sha256=binding.template_sha256 if binding else None,
            rendered_sha256=binding.rendered_sha256 if binding else None,
            error_code=invocation.error_code,
            status_reason=invocation.status_reason,
            requested_at=invocation.requested_at,
            completed_at=invocation.completed_at,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def record_transition(
        self, invocation: ToolInvocation, *, organization_id: OrganizationId
    ) -> AgentToolInvocation:
        """Persist an invocation contract's current state onto its existing row.

        The capability decision is not revisited on a transition — it was
        fixed at creation and does not change as an invocation runs to
        completion.

        Raises:
            MissingOrganizationContextError: No org context was supplied.
            TenantMismatchError: The org context disagrees with the row's
                stored organization.
            ValueError: No row exists for ``invocation.tool_invocation_id``.
        """
        normalized_organization_id = normalize_organization_id(organization_id)
        row = await self._get_row(invocation.tool_invocation_id)
        if row is None:
            raise ValueError(
                f"tool invocation {invocation.tool_invocation_id!r} does not exist"
            )
        if row.organization_id != normalized_organization_id:
            raise TenantMismatchError(
                f"tool invocation {invocation.tool_invocation_id!r} does not "
                f"belong to organization {normalized_organization_id}"
            )
        # See create_tool_invocation: model_dump() (not dict(...)) is what
        # thaws every nesting level of a frozen JsonObject, not just the top.
        output_payload = invocation.model_dump()["output"]
        row.status = invocation.status.value
        row.output = output_payload
        row.output_trust = (
            invocation.output_trust.value
            if invocation.output_trust is not None
            else None
        )
        row.output_sha256 = (
            boundary_digest(output_payload) if output_payload is not None else None
        )
        row.error_code = invocation.error_code
        row.status_reason = invocation.status_reason
        row.completed_at = invocation.completed_at
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def get_tool_invocation(
        self, tool_invocation_id: str, *, organization_id: OrganizationId = None
    ) -> AgentToolInvocation | None:
        """Fetch one tool invocation row, optionally scoped to an organization."""
        return await self._get_row(tool_invocation_id, organization_id=organization_id)

    async def list_tool_invocations_for_attempt(
        self, attempt_id: str, *, organization_id: OrganizationId = None
    ) -> list[AgentToolInvocation]:
        """Return every tool invocation for one attempt, in request order."""
        query = (
            select(AgentToolInvocation)
            .where(AgentToolInvocation.attempt_id == attempt_id)
            .order_by(AgentToolInvocation.requested_at)
        )
        if organization_id is not None:
            query = query.where(
                AgentToolInvocation.organization_id
                == normalize_organization_id(organization_id)
            )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def _get_row(
        self, tool_invocation_id: str, *, organization_id: OrganizationId = None
    ) -> AgentToolInvocation | None:
        query = select(AgentToolInvocation).where(
            AgentToolInvocation.tool_invocation_id == tool_invocation_id
        )
        if organization_id is not None:
            query = query.where(
                AgentToolInvocation.organization_id
                == normalize_organization_id(organization_id)
            )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()


__all__ = ["ToolInvocationRepository"]
