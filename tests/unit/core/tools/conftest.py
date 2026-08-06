"""Shared fixtures for the tool-execution boundary.

The fakes here are deliberately unhelpful. ``RecordingAuditStore`` records the
order it was called in and nothing else, because the guarantee under test is an
*ordering* one — persist before publish — and a fake that quietly reorders
would let a boundary that publishes first pass. Likewise ``FrozenClock`` counts
its reads: "the clock is read exactly once per call" is a real guarantee, and
counting is the only way to see it.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import BaseModel

from src.core.contracts.capabilities import (
    ApprovalRef,
    CapabilityDecision,
    CapabilityGrant,
    SensitivityClass,
)
from src.core.contracts.provenance import ToolInvocation
from src.core.contracts.trust import TrustClassification
from src.core.tools import (
    MappingSecretProvider,
    PromptIdentityVerifier,
    PromptRegistry,
    PromptRenderer,
    PromptTemplate,
    ToolAuditEvent,
    ToolBoundary,
    ToolCallContext,
    ToolSpec,
)

NOW = datetime(2026, 8, 6, 12, 0, 0, tzinfo=UTC)
RUN_ID = "run-1"
TASK_ID = "task-1"
ATTEMPT_ID = "attempt-1"
ORG_ID = "org-1"
SCOPE = "scope-search"
GRANT_ID = "grant-1"
TOOL_NAME = "echo"
TOOL_VERSION = "1.0.0"
PROMPT_ID = "summarize"
PROMPT_VERSION = "1.0.0"
PROMPT_TEMPLATE = "Summarize $topic for a reviewer."


class EchoInput(BaseModel):
    query: str


class EchoOutput(BaseModel):
    echoed: str


@dataclass(slots=True)
class RecordingAuditStore:
    """An audit store that remembers what it was told, and when."""

    calls: list[str] = field(default_factory=list)
    invocations: list[ToolInvocation] = field(default_factory=list)
    events: list[ToolAuditEvent] = field(default_factory=list)
    decisions: list[CapabilityDecision | None] = field(default_factory=list)
    replay: ToolInvocation | None = None
    fail_on_persist: bool = False

    async def find_invocation(
        self, *, run_id: str, organization_id: str | None, idempotency_key: str
    ) -> ToolInvocation | None:
        return self.replay

    async def persist(
        self,
        *,
        invocation: ToolInvocation,
        events: Sequence[ToolAuditEvent],
        organization_id: str | None,
        capability_decision: CapabilityDecision | None,
    ) -> None:
        if self.fail_on_persist:
            self.calls.append("persist-failed")
            raise RuntimeError("the database is down")
        self.calls.append(f"persist:{invocation.status.value}")
        self.invocations.append(invocation)
        self.events.extend(events)
        self.decisions.append(capability_decision)


@dataclass(slots=True)
class RecordingPublisher:
    """A publisher that appends to the store's shared call log."""

    calls: list[str]
    published: list[ToolAuditEvent] = field(default_factory=list)

    async def publish(self, events: Sequence[ToolAuditEvent]) -> None:
        for event in events:
            self.calls.append(f"publish:{event.event_type}")
            self.published.append(event)


@dataclass(slots=True)
class FrozenClock:
    """A clock that does not move, and counts how often it is asked."""

    now: datetime = NOW
    reads: int = 0

    def __call__(self) -> datetime:
        self.reads += 1
        return self.now


@dataclass(slots=True)
class SequentialIds:
    """Deterministic identifiers, so assertions can name them."""

    prefix: str = "id"
    issued: int = 0

    def __call__(self) -> str:
        self.issued += 1
        return f"{self.prefix}-{self.issued}"


async def echo_handler(args: EchoInput, context: ToolCallContext) -> Mapping[str, Any]:
    return {"echoed": args.query}


@pytest.fixture
def audit_store() -> RecordingAuditStore:
    return RecordingAuditStore()


@pytest.fixture
def publisher(audit_store: RecordingAuditStore) -> RecordingPublisher:
    return RecordingPublisher(calls=audit_store.calls)


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def prompt_registry() -> PromptRegistry:
    registry = PromptRegistry()
    registry.register(
        PromptTemplate(
            prompt_id=PROMPT_ID, version=PROMPT_VERSION, source=PROMPT_TEMPLATE
        )
    )
    return registry


@pytest.fixture
def renderer(prompt_registry: PromptRegistry) -> PromptRenderer:
    return PromptRenderer(
        registry=prompt_registry, secret_provider=MappingSecretProvider({})
    )


@pytest.fixture
def boundary_dependencies(
    audit_store: RecordingAuditStore,
    publisher: RecordingPublisher,
    clock: FrozenClock,
    prompt_registry: PromptRegistry,
) -> dict[str, Any]:
    return {
        "secret_provider": MappingSecretProvider({}),
        "audit_store": audit_store,
        "event_publisher": publisher,
        "clock": clock,
        "id_factory": SequentialIds(),
        "prompt_verifier": PromptIdentityVerifier(registry=prompt_registry),
    }


@pytest.fixture
def echo_spec() -> ToolSpec:
    return ToolSpec(
        name=TOOL_NAME,
        version=TOOL_VERSION,
        sensitivity=SensitivityClass.READ_ONLY,
        input_model=EchoInput,
        output_model=EchoOutput,
        timeout_seconds=5.0,
        handler=echo_handler,  # type: ignore[arg-type]
    )


@pytest.fixture
def boundary(
    boundary_dependencies: dict[str, Any], echo_spec: ToolSpec
) -> ToolBoundary:
    built = ToolBoundary(**boundary_dependencies)
    built.register(echo_spec)
    return built


def make_grant(
    *,
    grant_id: str = GRANT_ID,
    sensitivity: SensitivityClass = SensitivityClass.READ_ONLY,
    max_input_trust: TrustClassification = TrustClassification.EXTERNAL_UNTRUSTED,
    requires_approval: bool = False,
    issued_at: datetime = NOW - timedelta(hours=1),
    expires_at: datetime = NOW + timedelta(hours=1),
    tool_name: str = TOOL_NAME,
    capability_scope: str = SCOPE,
) -> CapabilityGrant:
    return CapabilityGrant(
        grant_id=grant_id,
        run_id=RUN_ID,
        task_id=TASK_ID,
        capability_scope=capability_scope,
        tool_name=tool_name,
        tool_versions=(TOOL_VERSION,),
        sensitivity=sensitivity,
        max_input_trust=max_input_trust,
        requires_approval=requires_approval,
        issued_at=issued_at,
        expires_at=expires_at,
    )


def make_approval(
    *,
    approval_id: str = "approval-1",
    grant_id: str = GRANT_ID,
    request_fingerprint: str,
    approved_at: datetime = NOW - timedelta(minutes=30),
    expires_at: datetime = NOW + timedelta(minutes=30),
) -> ApprovalRef:
    return ApprovalRef(
        approval_id=approval_id,
        grant_id=grant_id,
        request_fingerprint=request_fingerprint,
        approved_by="reviewer-1",
        approved_at=approved_at,
        expires_at=expires_at,
    )


def invoke_kwargs(**overrides: Any) -> dict[str, Any]:
    """The arguments of a well-formed call, so a test overrides only its point.

    The default grant is derived from whichever ``tool_name`` the test asked
    for. An earlier version pinned it to ``echo``, so every test naming a
    different tool was silently denied instead of reaching the behavior it
    meant to exercise — one of them then waited forever on a handler that never
    ran. A default that does not follow its own override is a default that
    makes tests vacuous.
    """

    tool_name = overrides.get("tool_name", TOOL_NAME)
    base: dict[str, Any] = {
        "tool_name": tool_name,
        "run_id": RUN_ID,
        "task_id": TASK_ID,
        "attempt_id": ATTEMPT_ID,
        "organization_id": ORG_ID,
        "capability_scope": SCOPE,
        "arguments": {"query": "hello"},
        "input_trust": TrustClassification.USER_SUPPLIED,
        "grants": [make_grant(tool_name=tool_name)],
    }
    base.update(overrides)
    return base
