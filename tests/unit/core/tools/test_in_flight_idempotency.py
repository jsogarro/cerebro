"""A committed nonterminal invocation reserves work for safe retries."""

from __future__ import annotations

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
