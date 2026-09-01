"""A committed nonterminal invocation reserves work for safe retries."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest

from src.agents.tools.mediation import InMemoryToolAuditStore
from src.core.contracts import (
    CapabilityDecision,
    ProducerKind,
    ToolInvocation,
    ToolInvocationStatus,
    TrustClassification,
)
from src.core.tools import (
    ToolAuditEvent,
    ToolBoundary,
    ToolCallContext,
    ToolOutcomeNotSuccessfulError,
    ToolOutcomeStatus,
    ToolSpec,
)

from .conftest import (
    ATTEMPT_ID,
    NOW,
    ORG_ID,
    RUN_ID,
    SCOPE,
    TASK_ID,
    TOOL_NAME,
    TOOL_VERSION,
    EchoInput,
    EchoOutput,
    invoke_kwargs,
    make_grant,
)


def _pending_invocation(
    *,
    invocation_id: str = "persisted-request",
    status: ToolInvocationStatus = ToolInvocationStatus.REQUESTED,
) -> ToolInvocation:
    return ToolInvocation(
        tool_invocation_id=invocation_id,
        run_id=RUN_ID,
        task_id=TASK_ID,
        attempt_id=ATTEMPT_ID,
        tool_name=TOOL_NAME,
        tool_version=TOOL_VERSION,
        status=status,
        capability_scope=SCOPE,
        idempotency_key="same-request",
        input={"query": "hello"},
        input_trust=TrustClassification.USER_SUPPLIED,
        producer_kind=ProducerKind.SYSTEM,
        requested_at=NOW,
    )


@dataclass(slots=True)
class PendingLookupStore:
    """A store implementing the optional recovery seam."""

    pending: ToolInvocation
    calls: list[str] = field(default_factory=list)

    async def find_invocation(
        self,
        *,
        run_id: str,
        attempt_id: str,
        organization_id: str | None,
        idempotency_key: str,
    ) -> ToolInvocation | None:
        self.calls.append("terminal-lookup")
        return None

    async def find_pending_invocation(
        self,
        *,
        run_id: str,
        attempt_id: str,
        organization_id: str | None,
        idempotency_key: str,
    ) -> ToolInvocation | None:
        self.calls.append("pending-lookup")
        return self.pending

    async def persist(
        self,
        *,
        invocation: ToolInvocation,
        events: Sequence[ToolAuditEvent],
        organization_id: str | None,
        capability_decision: CapabilityDecision | None,
    ) -> None:
        self.calls.append(f"persist:{invocation.status.value}")


@dataclass(slots=True)
class CoordinatedInMemoryToolAuditStore:
    """Hold both callers after lookup so admission races are deterministic."""

    inner: InMemoryToolAuditStore = field(default_factory=InMemoryToolAuditStore)
    pending_lookup_count: int = 0
    both_pending_lookups: asyncio.Event = field(default_factory=asyncio.Event)
    release_pending_lookups: asyncio.Event = field(default_factory=asyncio.Event)

    async def find_invocation(
        self,
        *,
        run_id: str,
        attempt_id: str,
        organization_id: str | None,
        idempotency_key: str,
    ) -> ToolInvocation | None:
        return await self.inner.find_invocation(
            run_id=run_id,
            attempt_id=attempt_id,
            organization_id=organization_id,
            idempotency_key=idempotency_key,
        )

    async def find_pending_invocation(
        self,
        *,
        run_id: str,
        attempt_id: str,
        organization_id: str | None,
        idempotency_key: str,
    ) -> ToolInvocation | None:
        self.pending_lookup_count += 1
        if self.pending_lookup_count == 2:
            self.both_pending_lookups.set()
        await self.release_pending_lookups.wait()
        return await self.inner.find_pending_invocation(
            run_id=run_id,
            attempt_id=attempt_id,
            organization_id=organization_id,
            idempotency_key=idempotency_key,
        )

    async def reserve_invocation(
        self,
        *,
        invocation: ToolInvocation,
        organization_id: str | None,
    ) -> ToolInvocation | None:
        # The current HEAD has no reservation method. The RED run never calls
        # this method; getattr keeps that run focused on the pre-fix behavior.
        reserve = self.inner.reserve_invocation
        return await reserve(
            invocation=invocation,
            organization_id=organization_id,
        )

    async def persist(
        self,
        *,
        invocation: ToolInvocation,
        events: Sequence[ToolAuditEvent],
        organization_id: str | None,
        capability_decision: CapabilityDecision | None,
    ) -> None:
        await self.inner.persist(
            invocation=invocation,
            events=events,
            organization_id=organization_id,
            capability_decision=capability_decision,
        )


def _boundary_with_pending(
    boundary_dependencies: dict[str, Any],
    store: PendingLookupStore,
    order: list[str],
    handler_calls: list[str],
) -> ToolBoundary:
    def decide(**kwargs: Any) -> CapabilityDecision:
        order.append("authorize")
        from src.core.contracts.capabilities import decide_capability

        return decide_capability(**kwargs)

    async def handler(args: EchoInput, context: ToolCallContext) -> Mapping[str, Any]:
        handler_calls.append("handler")
        return {"echoed": args.query}

    dependencies = {
        **boundary_dependencies,
        "audit_store": store,
        "decide": decide,
    }
    boundary = ToolBoundary(**dependencies)
    boundary.register(
        ToolSpec(
            name=TOOL_NAME,
            version=TOOL_VERSION,
            sensitivity=make_grant().sensitivity,
            input_model=EchoInput,
            output_model=EchoOutput,
            timeout_seconds=5.0,
            handler=handler,
        )
    )
    return boundary


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pending_status",
    [ToolInvocationStatus.REQUESTED, ToolInvocationStatus.RUNNING],
)
async def test_authorization_precedes_pending_lookup_and_pending_work_is_not_replayed(
    boundary_dependencies: dict[str, Any], pending_status: ToolInvocationStatus
) -> None:
    order: list[str] = []
    handler_calls: list[str] = []
    store = PendingLookupStore(pending=_pending_invocation(status=pending_status))
    boundary = _boundary_with_pending(
        boundary_dependencies, store, order, handler_calls
    )

    outcome = await boundary.invoke(
        **invoke_kwargs(idempotency_key="same-request", grants=[make_grant()])
    )

    assert outcome.status is ToolOutcomeStatus.IN_PROGRESS
    assert outcome.retry.value == "retriable"
    assert outcome.invocation.tool_invocation_id == "persisted-request"
    assert outcome.invocation.output is None
    with pytest.raises(ToolOutcomeNotSuccessfulError):
        outcome.unwrap()
    assert order == ["authorize"]
    assert store.calls == ["terminal-lookup", "pending-lookup"]
    assert handler_calls == []


@pytest.mark.asyncio
async def test_in_memory_terminal_lookup_does_not_return_a_pending_row() -> None:
    store = InMemoryToolAuditStore()
    pending = _pending_invocation()
    await store.persist(
        invocation=pending,
        events=(),
        organization_id=ORG_ID,
        capability_decision=None,
    )

    assert (
        await store.find_invocation(
            run_id=RUN_ID,
            attempt_id=ATTEMPT_ID,
            organization_id=ORG_ID,
            idempotency_key=pending.idempotency_key,
        )
        is None
    )
    assert (
        await store.find_pending_invocation(
            run_id=RUN_ID,
            attempt_id=ATTEMPT_ID,
            organization_id=ORG_ID,
            idempotency_key=pending.idempotency_key,
        )
        == pending
    )


@pytest.mark.asyncio
async def test_in_memory_lookup_is_tenant_scoped_and_denials_do_not_shadow_success() -> (
    None
):
    store = InMemoryToolAuditStore()
    terminal = ToolInvocation(
        tool_invocation_id="terminal-invocation",
        run_id=RUN_ID,
        task_id=TASK_ID,
        attempt_id=ATTEMPT_ID,
        tool_name=TOOL_NAME,
        tool_version=TOOL_VERSION,
        status=ToolInvocationStatus.SUCCEEDED,
        capability_scope=SCOPE,
        idempotency_key="shared-key",
        input={"query": "hello"},
        input_trust=TrustClassification.USER_SUPPLIED,
        output={"echoed": "hello"},
        output_trust=TrustClassification.EXTERNAL_UNTRUSTED,
        producer_kind=ProducerKind.SYSTEM,
        requested_at=NOW,
        completed_at=NOW,
    )
    denied = terminal.model_copy(
        update={
            "tool_invocation_id": "denied-invocation",
            "status": ToolInvocationStatus.DENIED,
            "output": None,
            "output_trust": None,
            "error_code": "capability_denied",
        }
    )
    pending = _pending_invocation(invocation_id="pending-invocation")

    await store.persist(
        invocation=terminal,
        events=(),
        organization_id=ORG_ID,
        capability_decision=None,
    )
    await store.persist(
        invocation=denied,
        events=(),
        organization_id=ORG_ID,
        capability_decision=None,
    )
    await store.persist(
        invocation=pending,
        events=(),
        organization_id=ORG_ID,
        capability_decision=None,
    )

    assert (
        await store.find_invocation(
            run_id=RUN_ID,
            attempt_id=ATTEMPT_ID,
            organization_id=ORG_ID,
            idempotency_key="shared-key",
        )
    ) == terminal
    assert (
        await store.find_invocation(
            run_id=RUN_ID,
            attempt_id=ATTEMPT_ID,
            organization_id="other-org",
            idempotency_key="shared-key",
        )
        is None
    )
    assert (
        await store.find_pending_invocation(
            run_id=RUN_ID,
            attempt_id=ATTEMPT_ID,
            organization_id="other-org",
            idempotency_key=pending.idempotency_key,
        )
        is None
    )


@pytest.mark.asyncio
async def test_concurrent_identical_calls_atomically_reserve_before_dispatch(
    boundary_dependencies: dict[str, Any],
) -> None:
    """Only the atomic reservation winner is allowed to run the handler."""

    store = CoordinatedInMemoryToolAuditStore()
    handler_calls: list[str] = []
    handler_started = asyncio.Event()
    release_handler = asyncio.Event()

    async def handler(args: EchoInput, context: ToolCallContext) -> Mapping[str, Any]:
        handler_calls.append("handler")
        handler_started.set()
        await release_handler.wait()
        return {"echoed": args.query}

    boundary = ToolBoundary(
        **{**boundary_dependencies, "audit_store": store},
    )
    boundary.register(
        ToolSpec(
            name=TOOL_NAME,
            version=TOOL_VERSION,
            sensitivity=make_grant().sensitivity,
            input_model=EchoInput,
            output_model=EchoOutput,
            timeout_seconds=5.0,
            handler=handler,
        )
    )

    first = asyncio.create_task(
        boundary.invoke(**invoke_kwargs(idempotency_key="concurrent-key"))
    )
    second = asyncio.create_task(
        boundary.invoke(**invoke_kwargs(idempotency_key="concurrent-key"))
    )
    try:
        await asyncio.wait_for(store.both_pending_lookups.wait(), timeout=1)
        store.release_pending_lookups.set()
        await asyncio.wait_for(handler_started.wait(), timeout=1)
        release_handler.set()
        outcomes = await asyncio.wait_for(
            asyncio.gather(first, second),
            timeout=1,
        )
    finally:
        store.release_pending_lookups.set()
        release_handler.set()
        await asyncio.gather(first, second, return_exceptions=True)

    assert {outcome.status for outcome in outcomes} == {
        ToolOutcomeStatus.SUCCEEDED,
        ToolOutcomeStatus.IN_PROGRESS,
    }
    assert handler_calls == ["handler"]
    assert [invocation.status for invocation in store.inner.invocations] == [
        ToolInvocationStatus.REQUESTED,
        ToolInvocationStatus.SUCCEEDED,
    ]
    assert len(store.inner.events) == 2
