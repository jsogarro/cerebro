"""Session-backed persistence for mediated tool invocations and audit events.

The tool boundary can record an allowed call twice: first as ``REQUESTED`` and
then as its terminal state.  This adapter treats both writes as transitions of
one durable invocation row and appends the corresponding run events in the
same short-lived transaction.  It intentionally is not the default store;
callers that have a database session factory must inject it explicitly.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final, Literal, cast

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.contracts import (
    CapabilityDecision,
    ProducerKind,
    PromptBinding,
    ToolInvocation,
    ToolInvocationStatus,
    TrustClassification,
)
from src.core.tools.audit import EVENT_COMPLETED, ToolAuditEvent
from src.core.tools.errors import ToolInvocationConflictError
from src.models.db.run_lifecycle import AgentRun, AgentRunTask, AgentTaskAttempt
from src.models.db.tool_invocation import AgentToolInvocation
from src.repositories.run_event_repository import RunEventRepository
from src.repositories.tenant_scope import (
    MissingOrganizationContextError,
    normalize_organization_id,
    scope_to_organization,
)
from src.repositories.tool_invocation_repository import ToolInvocationRepository

_REPLAYABLE_TERMINAL_STATUSES: Final[tuple[str, ...]] = tuple(
    status.value
    for status in (
        ToolInvocationStatus.SUCCEEDED,
        ToolInvocationStatus.FAILED,
        ToolInvocationStatus.CANCELLED,
        ToolInvocationStatus.TIMED_OUT,
    )
)
_PENDING_STATUSES: Final[tuple[str, ...]] = tuple(
    status.value
    for status in (ToolInvocationStatus.REQUESTED, ToolInvocationStatus.RUNNING)
)
_LEASE_EXPIRED_ERROR_CODE: Final[str] = "unknown_after_lease_expired"
_LEASE_EXPIRED_STATUS_REASON: Final[str] = (
    "Tool outcome is unknown after the durable lease expired; "
    "the invocation must not be re-executed."
)


class SessionToolAuditStore:
    """Persist tool audit records through a fresh session per operation."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Create a store that owns sessions opened from ``session_factory``."""

        self.session_factory = session_factory

    async def find_invocation(
        self,
        *,
        run_id: str,
        attempt_id: str,
        organization_id: str | None,
        idempotency_key: str,
    ) -> ToolInvocation | None:
        """Find one terminal invocation in the exact tenant/attempt scope.

        The durable lookup requires organization context even though the
        protocol permits ``None`` for in-memory stores.  A no-org lookup must
        never become an accidental cross-tenant read at this boundary.
        This is a read, so an unknown or differently owned run/attempt is a
        no-match and returns ``None`` rather than raising the persistence
        path's durable-parent error.
        ``DENIED`` is deliberately excluded. Refusal rows remain available
        through the repository and event stream, but they must not shadow a
        completed invocation that used the same key.
        """

        normalized_organization_id = _normalize_durable_context(
            run_id=run_id,
            task_id=None,
            attempt_id=attempt_id,
            organization_id=organization_id,
        )
        async with self.session_factory() as session:
            query = select(AgentToolInvocation).where(
                AgentToolInvocation.run_id == run_id,
                AgentToolInvocation.attempt_id == attempt_id,
                AgentToolInvocation.idempotency_key == idempotency_key,
                AgentToolInvocation.status.in_(_REPLAYABLE_TERMINAL_STATUSES),
            )
            query = scope_to_organization(
                query,
                AgentToolInvocation.organization_id,
                normalized_organization_id,
            )
            query = query.order_by(
                AgentToolInvocation.requested_at.desc(),
                AgentToolInvocation.created_at.desc(),
            ).limit(1)
            result = await session.execute(query)
            row = result.scalars().first()
            return None if row is None else self._to_contract(row)

    async def find_pending_invocation(
        self,
        *,
        run_id: str,
        attempt_id: str,
        organization_id: str | None,
        idempotency_key: str,
    ) -> ToolInvocation | None:
        """Find admitted work that has not reached a terminal state.

        This is intentionally separate from ``find_invocation``.  A terminal
        row is replayable; a pending row reserves the idempotency key and is
        reported as ``IN_PROGRESS`` so a retry cannot execute the handler a
        second time.
        """

        normalized_organization_id = _normalize_durable_context(
            run_id=run_id,
            task_id=None,
            attempt_id=attempt_id,
            organization_id=organization_id,
        )
        async with self.session_factory() as session:
            query = select(AgentToolInvocation).where(
                AgentToolInvocation.run_id == run_id,
                AgentToolInvocation.attempt_id == attempt_id,
                AgentToolInvocation.idempotency_key == idempotency_key,
                AgentToolInvocation.status.in_(_PENDING_STATUSES),
            )
            query = scope_to_organization(
                query,
                AgentToolInvocation.organization_id,
                normalized_organization_id,
            )
            query = query.order_by(
                AgentToolInvocation.requested_at.desc(),
                AgentToolInvocation.created_at.desc(),
            ).limit(1)
            result = await session.execute(query)
            row = result.scalars().first()
            return None if row is None else self._to_contract(row)

    async def reconcile_pending_invocation(
        self,
        *,
        run_id: str,
        attempt_id: str,
        organization_id: str | None,
        idempotency_key: str,
        now: datetime,
    ) -> ToolInvocation | None:
        """Return live pending work or terminalize a stale pending lease.

        The selected row is locked until commit so two retrying processes
        cannot both decide that they own recovery for the same idempotency
        reservation.
        """

        _require_aware_datetime(now, field_name="now")
        normalized_organization_id = _normalize_durable_context(
            run_id=run_id,
            task_id=None,
            attempt_id=attempt_id,
            organization_id=organization_id,
        )
        async with self.session_factory() as session:
            try:
                query = select(AgentToolInvocation).where(
                    AgentToolInvocation.run_id == run_id,
                    AgentToolInvocation.attempt_id == attempt_id,
                    AgentToolInvocation.idempotency_key == idempotency_key,
                    AgentToolInvocation.organization_id == normalized_organization_id,
                    AgentToolInvocation.status.in_(
                        (*_PENDING_STATUSES, *_REPLAYABLE_TERMINAL_STATUSES)
                    ),
                )
                result = await session.execute(query.limit(1).with_for_update())
                row = result.scalars().first()
                if row is None:
                    await session.commit()
                    return None

                if row.status in _REPLAYABLE_TERMINAL_STATUSES:
                    invocation = self._to_contract(row)
                    await session.commit()
                    return invocation

                if (
                    row.lease_owner_id is not None
                    and row.lease_expires_at is not None
                    and _lease_is_live(row.lease_expires_at, now)
                ):
                    invocation = self._to_contract(row)
                    await session.commit()
                    return invocation

                terminal = await self._terminalize_expired_pending(
                    session,
                    row,
                    now=now,
                    organization_id=normalized_organization_id,
                )
                await session.flush()
                await session.commit()
                return terminal
            except BaseException:
                await session.rollback()
                raise

    async def renew_invocation_lease(
        self,
        *,
        tool_invocation_id: str,
        run_id: str,
        attempt_id: str,
        organization_id: str | None,
        idempotency_key: str,
        lease_owner_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        """Extend a live pending lease owned by the caller."""

        _require_aware_datetime(now, field_name="now")
        _require_aware_datetime(lease_expires_at, field_name="lease_expires_at")
        if lease_expires_at <= now:
            return False

        normalized_organization_id = _normalize_durable_context(
            run_id=run_id,
            task_id=None,
            attempt_id=attempt_id,
            organization_id=organization_id,
        )
        async with self.session_factory() as session:
            try:
                statement = (
                    update(AgentToolInvocation)
                    .where(
                        AgentToolInvocation.tool_invocation_id == tool_invocation_id,
                        AgentToolInvocation.run_id == run_id,
                        AgentToolInvocation.attempt_id == attempt_id,
                        AgentToolInvocation.organization_id
                        == normalized_organization_id,
                        AgentToolInvocation.idempotency_key == idempotency_key,
                        AgentToolInvocation.lease_owner_id == lease_owner_id,
                        AgentToolInvocation.status.in_(_PENDING_STATUSES),
                        AgentToolInvocation.lease_expires_at.is_not(None),
                        AgentToolInvocation.lease_expires_at > now,
                    )
                    .values(lease_expires_at=lease_expires_at)
                    .returning(AgentToolInvocation.tool_invocation_id)
                )
                result = await session.execute(statement)
                renewed = result.scalar_one_or_none() is not None
                await session.commit()
                return renewed
            except BaseException:
                await session.rollback()
                raise

    async def persist(
        self,
        *,
        invocation: ToolInvocation,
        events: Sequence[ToolAuditEvent],
        organization_id: str | None,
        capability_decision: CapabilityDecision | None,
    ) -> None:
        """Write one invocation transition and all events atomically.

        ``ToolInvocationRepository`` and ``RunEventRepository`` flush but do
        not commit.  This method owns their transaction and propagates every
        failure after rolling it back; there is deliberately no in-memory
        fallback when durable auditing is requested.
        """

        for event in events:
            self._assert_event_association(event, invocation)

        normalized_organization_id = _normalize_durable_context(
            run_id=invocation.run_id,
            task_id=invocation.task_id,
            attempt_id=invocation.attempt_id,
            organization_id=organization_id,
        )

        async with self.session_factory() as session:
            try:
                await _require_durable_parent_context(
                    session,
                    run_id=invocation.run_id,
                    task_id=invocation.task_id,
                    attempt_id=invocation.attempt_id,
                    organization_id=normalized_organization_id,
                )
                invocation_repository = ToolInvocationRepository(session)
                existing_result = await session.execute(
                    select(AgentToolInvocation)
                    .where(
                        AgentToolInvocation.tool_invocation_id
                        == invocation.tool_invocation_id,
                        AgentToolInvocation.organization_id
                        == normalized_organization_id,
                    )
                    .with_for_update()
                )
                existing = existing_result.scalar_one_or_none()
                if existing is None:
                    try:
                        await invocation_repository.create_tool_invocation(
                            invocation,
                            organization_id=normalized_organization_id,
                            capability_decision=capability_decision,
                        )
                    except IntegrityError as error:
                        # The partial idempotency index is the cross-process
                        # reservation primitive. A read-before-write check
                        # cannot close that race, so reload the row after the
                        # losing transaction is rolled back and let the
                        # boundary return a replay or IN_PROGRESS outcome.
                        await session.rollback()
                        conflict = await _find_by_idempotency_key(
                            session,
                            run_id=invocation.run_id,
                            attempt_id=invocation.attempt_id,
                            idempotency_key=invocation.idempotency_key,
                            organization_id=normalized_organization_id,
                        )
                        if conflict is None:
                            raise
                        raise ToolInvocationConflictError(
                            self._to_contract(conflict)
                        ) from error
                else:
                    self._assert_transition_identity(existing, invocation)
                    if existing.status not in _PENDING_STATUSES:
                        raise ToolInvocationConflictError(self._to_contract(existing))
                    if (
                        invocation.status in _REPLAYABLE_TERMINAL_STATUSES
                        and invocation.completed_at is not None
                        and existing.lease_expires_at is not None
                        and not _lease_is_live(
                            existing.lease_expires_at, invocation.completed_at
                        )
                    ):
                        terminal = await self._terminalize_expired_pending(
                            session,
                            existing,
                            now=invocation.completed_at,
                            organization_id=normalized_organization_id,
                        )
                        await session.flush()
                        await session.commit()
                        raise ToolInvocationConflictError(terminal)
                    await invocation_repository.record_transition(
                        invocation,
                        organization_id=normalized_organization_id,
                    )

                for event in events:
                    await self._append_event(
                        session,
                        event=event,
                        organization_id=normalized_organization_id,
                    )

                # Both repositories flush each individual write. Keep this
                # final flush immediately before commit so the transaction's
                # durability boundary remains obvious and future repository
                # changes cannot move a pending write past the commit point.
                await session.flush()
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    async def _terminalize_expired_pending(
        self,
        session: AsyncSession,
        row: AgentToolInvocation,
        *,
        now: datetime,
        organization_id: uuid.UUID,
    ) -> ToolInvocation:
        """Fence an expired owner and append its one durable terminal event."""

        row.status = ToolInvocationStatus.FAILED.value
        row.output = None
        row.output_trust = None
        row.output_sha256 = None
        row.error_code = _LEASE_EXPIRED_ERROR_CODE
        row.status_reason = _LEASE_EXPIRED_STATUS_REASON
        row.completed_at = now
        row.lease_owner_id = None
        row.lease_expires_at = None
        await session.flush()

        terminal = self._to_contract(row)
        await self._append_event(
            session,
            event=ToolAuditEvent(
                event_id=f"{row.tool_invocation_id}:{_LEASE_EXPIRED_ERROR_CODE}",
                run_id=row.run_id,
                task_id=row.task_id,
                attempt_id=row.attempt_id,
                aggregate_id=row.tool_invocation_id,
                event_type=EVENT_COMPLETED,
                occurred_at=now,
                producer="session-tool-audit-store",
                deduplication_key=(
                    f"{row.tool_invocation_id}:{_LEASE_EXPIRED_ERROR_CODE}"
                ),
                payload={
                    "tool_name": row.tool_name,
                    "tool_version": row.tool_version,
                    "outcome": terminal.status.value,
                    "error_code": terminal.error_code,
                    "status_reason": terminal.status_reason,
                },
            ),
            organization_id=organization_id,
        )
        return terminal

    @staticmethod
    def _assert_event_association(
        event: ToolAuditEvent, invocation: ToolInvocation
    ) -> None:
        """Reject an event that is not about the invocation being persisted."""

        associations = (
            ("run_id", event.run_id, invocation.run_id),
            ("task_id", event.task_id, invocation.task_id),
            ("attempt_id", event.attempt_id, invocation.attempt_id),
            ("aggregate_id", event.aggregate_id, invocation.tool_invocation_id),
        )
        for field_name, event_value, invocation_value in associations:
            if event_value != invocation_value:
                raise ValueError(f"event {field_name} does not match invocation")

    @staticmethod
    def _assert_transition_identity(
        row: AgentToolInvocation, invocation: ToolInvocation
    ) -> None:
        """Reject a transition that changes the request represented by a row."""

        immutable_fields = (
            ("run_id", row.run_id, invocation.run_id),
            ("task_id", row.task_id, invocation.task_id),
            ("attempt_id", row.attempt_id, invocation.attempt_id),
            ("tool_name", row.tool_name, invocation.tool_name),
            ("tool_version", row.tool_version, invocation.tool_version),
            ("capability_scope", row.capability_scope, invocation.capability_scope),
            ("idempotency_key", row.idempotency_key, invocation.idempotency_key),
            ("input_trust", row.input_trust, invocation.input_trust.value),
            ("producer_kind", row.producer_kind, invocation.producer_kind.value),
            ("requested_at", row.requested_at, invocation.requested_at),
            ("schema_version", row.contract_schema_version, invocation.schema_version),
        )
        for field_name, persisted_value, requested_value in immutable_fields:
            if persisted_value != requested_value:
                raise ValueError(
                    f"tool invocation transition changed immutable field {field_name}"
                )

        dumped = invocation.model_dump(mode="json")
        if row.input != dumped["input"]:
            raise ValueError("tool invocation transition changed immutable field input")

        binding = invocation.prompt_binding
        prompt_fields = (
            (
                "prompt_id",
                row.prompt_id,
                None if binding is None else binding.prompt_id,
            ),
            (
                "prompt_version",
                row.prompt_version,
                None if binding is None else binding.prompt_version,
            ),
            (
                "template_sha256",
                row.template_sha256,
                None if binding is None else binding.template_sha256,
            ),
            (
                "rendered_sha256",
                row.rendered_sha256,
                None if binding is None else binding.rendered_sha256,
            ),
        )
        for (
            prompt_field_name,
            persisted_prompt_value,
            requested_prompt_value,
        ) in prompt_fields:
            if persisted_prompt_value != requested_prompt_value:
                raise ValueError(
                    "tool invocation transition changed immutable field "
                    f"{prompt_field_name}"
                )

    @staticmethod
    def _to_contract(row: AgentToolInvocation) -> ToolInvocation:
        """Reconstruct the public invocation contract from a durable row."""

        schema_version = _validated_schema_version(row.contract_schema_version)
        return ToolInvocation(
            schema_version=schema_version,
            tool_invocation_id=row.tool_invocation_id,
            run_id=row.run_id,
            task_id=row.task_id,
            attempt_id=row.attempt_id,
            tool_name=row.tool_name,
            tool_version=row.tool_version,
            status=ToolInvocationStatus(row.status),
            capability_scope=row.capability_scope,
            idempotency_key=row.idempotency_key,
            input=cast(Mapping[str, Any], row.input),
            input_trust=TrustClassification(row.input_trust),
            output=(
                None if row.output is None else cast(Mapping[str, Any], row.output)
            ),
            output_trust=(
                None
                if row.output_trust is None
                else TrustClassification(row.output_trust)
            ),
            producer_kind=ProducerKind(row.producer_kind),
            prompt_binding=SessionToolAuditStore._prompt_binding(row),
            error_code=row.error_code,
            status_reason=row.status_reason,
            requested_at=row.requested_at,
            completed_at=row.completed_at,
            lease_owner_id=row.lease_owner_id,
            lease_expires_at=row.lease_expires_at,
        )

    @staticmethod
    def _prompt_binding(row: AgentToolInvocation) -> PromptBinding | None:
        """Rebuild a complete optional prompt binding, rejecting corruption."""

        values = (
            row.prompt_id,
            row.prompt_version,
            row.template_sha256,
            row.rendered_sha256,
        )
        if all(value is None for value in values):
            return None
        if any(value is None for value in values):
            raise ValueError("tool invocation row contains a partial prompt binding")
        prompt_id, prompt_version, template_sha256, rendered_sha256 = values
        assert (
            prompt_id is not None
            and prompt_version is not None
            and template_sha256 is not None
            and rendered_sha256 is not None
        )
        return PromptBinding(
            prompt_id=prompt_id,
            prompt_version=prompt_version,
            template_sha256=template_sha256,
            rendered_sha256=rendered_sha256,
        )

    @staticmethod
    async def _append_event(
        session: AsyncSession,
        *,
        event: ToolAuditEvent,
        organization_id: uuid.UUID,
    ) -> None:
        """Append a tool audit event through the run-event repository."""

        payload: dict[str, Any] = dict(event.payload)
        # These identifiers are authoritative event fields. Writing them into
        # the payload is the translation required by the run-event repository,
        # whose append API has no separate task_id or attempt_id parameters.
        payload["task_id"] = event.task_id
        payload["attempt_id"] = event.attempt_id
        await RunEventRepository(session).append_event(
            run_id=event.run_id,
            organization_id=organization_id,
            event_id=event.event_id,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            event_type=event.event_type,
            event_type_version=event.event_type_version,
            occurred_at=event.occurred_at,
            producer=event.producer,
            deduplication_key=event.deduplication_key,
            payload=payload,
            correlation_id=event.correlation_id,
            causation_event_id=event.causation_event_id,
            destinations=event.destinations,
        )


def _validated_schema_version(value: str) -> Literal["1.0"]:
    """Accept only the schema version understood by ``ToolInvocation``."""

    if value != "1.0":
        raise ValueError(
            f"unsupported tool invocation contract schema version {value!r}"
        )
    return "1.0"


def _require_aware_datetime(value: datetime, *, field_name: str) -> None:
    """Require caller-injected timestamps to be timezone-aware."""

    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _lease_is_live(expires_at: datetime, now: datetime) -> bool:
    """Compare dialect-normalized lease timestamps without losing UTC meaning."""

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at > now


def _normalize_durable_context(
    *,
    run_id: str,
    task_id: str | None,
    attempt_id: str,
    organization_id: str | None,
) -> uuid.UUID:
    """Normalize and require the complete context needed by durable rows."""

    for field_name, value in (
        ("run_id", run_id),
        ("task_id", task_id),
        ("attempt_id", attempt_id),
    ):
        if value is None:
            continue
        if not value:
            raise ValueError(f"{field_name} is required for durable tool auditing")

    if organization_id is None or (
        isinstance(organization_id, str) and not organization_id.strip()
    ):
        raise MissingOrganizationContextError(
            "organization_id is required for durable tool auditing"
        )
    try:
        return normalize_organization_id(organization_id)
    except ValueError as error:
        raise ValueError(
            "organization_id must be a valid UUID for durable tool auditing"
        ) from error


async def _require_durable_parent_context(
    session: AsyncSession,
    *,
    run_id: str,
    task_id: str | None,
    attempt_id: str,
    organization_id: uuid.UUID,
) -> None:
    """Require lifecycle rows that prove the identity is durable.

    The foreign keys would reject unknown identifiers eventually, but their
    database errors are both late and dialect-specific. Checking the complete
    run/task/attempt chain here makes a non-durable identity fail closed before
    any audit row is staged, and names the missing context field.
    """

    run = await session.scalar(
        select(AgentRun.run_id).where(
            AgentRun.run_id == run_id,
            AgentRun.organization_id == organization_id,
        )
    )
    if run is None:
        raise ValueError(f"run_id {run_id!r} is not a durable run in this organization")

    if task_id is not None:
        task = await session.scalar(
            select(AgentRunTask.task_id).where(
                AgentRunTask.task_id == task_id,
                AgentRunTask.run_id == run_id,
                AgentRunTask.organization_id == organization_id,
            )
        )
        if task is None:
            raise ValueError(
                f"task_id {task_id!r} is not a durable task in this run and organization"
            )

    attempt_query = select(AgentTaskAttempt.attempt_id).where(
        AgentTaskAttempt.attempt_id == attempt_id,
        AgentTaskAttempt.run_id == run_id,
        AgentTaskAttempt.organization_id == organization_id,
    )
    if task_id is not None:
        attempt_query = attempt_query.where(AgentTaskAttempt.task_id == task_id)
    attempt = await session.scalar(attempt_query)
    if attempt is None:
        raise ValueError(
            f"attempt_id {attempt_id!r} is not a durable attempt in this run and organization"
        )


async def _find_by_idempotency_key(
    session: AsyncSession,
    *,
    run_id: str,
    attempt_id: str,
    idempotency_key: str,
    organization_id: uuid.UUID,
) -> AgentToolInvocation | None:
    """Reload a non-denied reservation after an idempotency conflict."""

    query = select(AgentToolInvocation).where(
        AgentToolInvocation.run_id == run_id,
        AgentToolInvocation.attempt_id == attempt_id,
        AgentToolInvocation.idempotency_key == idempotency_key,
        AgentToolInvocation.status != ToolInvocationStatus.DENIED.value,
        AgentToolInvocation.organization_id == organization_id,
    )
    result = await session.execute(query.limit(1))
    return result.scalars().first()


__all__ = ["SessionToolAuditStore"]
