"""Input the boundary cannot represent is a recorded denial, not an escape.

**The defect.** Preparing the input for the record — serializing it, redacting
it, hashing it, and validating it into ``ToolInvocation`` — runs three separate
recursive walks over the caller's structure, and all of that sat *outside* the
guard that turns a bad input into an outcome. The same payload therefore
produced three different behaviours depending on call-stack depth and
interpreter configuration, none of them a graceful denial:

- shallow enough: accepted and persisted;
- deeper, at the default recursion limit: ``RecursionError`` out of
  ``_json_ready``, propagating past every guard;
- deeper still with the limit raised: ``ValidationError`` from the
  ``ToolInvocation`` construction, also outside the guard.

Verified directly at each of the three, before the fix.

**What is in scope here is the guard's placement, not a size limit.** An input
the boundary cannot process is `INVALID_INPUT` — typed, recorded, denied — the
same shape every other refusal in this module takes. Actual depth and size
ceilings belong to Wave 6 and are deliberately not added.

**Why the record cannot carry the input.** The offending payload is precisely
the thing that cannot be validated into a record, so writing it into the
rejection would fail the rejection the same way. The record therefore carries a
flat placeholder and says why in its status reason. An audit trail that drops
every malformed request is the trail an attacker would choose.
"""

import sys
from collections.abc import Mapping
from typing import Any

import pytest
from pydantic import BaseModel

from src.core.contracts.capabilities import SensitivityClass
from src.core.tools import (
    ToolBoundary,
    ToolCallContext,
    ToolOutcomeStatus,
    ToolSpec,
)

from .conftest import RecordingAuditStore, RecordingPublisher, invoke_kwargs

DEEP_TOOL = "deep"


class DeepInput(BaseModel):
    payload: dict[str, Any]


class DeepOutput(BaseModel):
    ok: bool


def nest(depth: int) -> dict[str, Any]:
    """Build a linearly nested mapping without recursing to do it."""

    value: Any = "leaf"
    for _ in range(depth):
        value = {"n": value}
    return value


@pytest.fixture
def deep_boundary(boundary_dependencies: dict[str, Any]) -> ToolBoundary:
    async def handler(args: DeepInput, context: ToolCallContext) -> Mapping[str, Any]:
        return {"ok": True}

    boundary = ToolBoundary(**boundary_dependencies)
    boundary.register(
        ToolSpec(
            name=DEEP_TOOL,
            version="1.0.0",
            sensitivity=SensitivityClass.READ_ONLY,
            input_model=DeepInput,
            output_model=DeepOutput,
            timeout_seconds=5.0,
            handler=handler,
        )
    )
    return boundary


class TestUnrepresentableInputIsDenied:
    async def test_a_recursion_error_becomes_a_recorded_outcome(
        self, deep_boundary: ToolBoundary
    ) -> None:
        """Depth past the default limit. Previously propagated out of invoke."""

        outcome = await deep_boundary.invoke(
            **invoke_kwargs(tool_name=DEEP_TOOL, arguments={"payload": nest(5000)})
        )

        assert outcome.status is ToolOutcomeStatus.INVALID_INPUT
        assert outcome.error_code == "invalid_input"

    async def test_a_validation_error_becomes_a_recorded_outcome(
        self, deep_boundary: ToolBoundary
    ) -> None:
        """The third behaviour: limit raised, so pydantic's own guard fires.

        The interpreter limit is restored in ``finally`` — leaving it raised
        would change how every later test in the process behaves.
        """

        original = sys.getrecursionlimit()
        sys.setrecursionlimit(8000)
        try:
            outcome = await deep_boundary.invoke(
                **invoke_kwargs(tool_name=DEEP_TOOL, arguments={"payload": nest(2000)})
            )
        finally:
            sys.setrecursionlimit(original)

        assert outcome.status is ToolOutcomeStatus.INVALID_INPUT

    async def test_the_tool_is_never_reached(
        self, boundary_dependencies: dict[str, Any]
    ) -> None:
        reached: list[str] = []

        async def spy(args: DeepInput, context: ToolCallContext) -> Mapping[str, Any]:
            reached.append("ran")
            return {"ok": True}

        boundary = ToolBoundary(**boundary_dependencies)
        boundary.register(
            ToolSpec(
                name=DEEP_TOOL,
                version="1.0.0",
                sensitivity=SensitivityClass.READ_ONLY,
                input_model=DeepInput,
                output_model=DeepOutput,
                timeout_seconds=5.0,
                handler=spy,
            )
        )

        await boundary.invoke(
            **invoke_kwargs(tool_name=DEEP_TOOL, arguments={"payload": nest(5000)})
        )

        assert reached == []


class TestTheRejectionIsAuditable:
    async def test_the_refusal_is_persisted_and_published(
        self,
        deep_boundary: ToolBoundary,
        audit_store: RecordingAuditStore,
        publisher: RecordingPublisher,
    ) -> None:
        await deep_boundary.invoke(
            **invoke_kwargs(tool_name=DEEP_TOOL, arguments={"payload": nest(5000)})
        )

        assert [i.status.value for i in audit_store.invocations] == ["failed"]
        assert publisher.published
        assert audit_store.calls.index("persist:failed") < audit_store.calls.index(
            "publish:tool.invocation.completed"
        )

    async def test_the_record_substitutes_a_placeholder_for_the_payload(
        self, deep_boundary: ToolBoundary, audit_store: RecordingAuditStore
    ) -> None:
        """The payload cannot be stored — that is what made it unprocessable.

        Recording a marker keeps the rejection itself from failing the same way
        the request did, and keeps a row in the audit trail where the request
        happened.
        """

        outcome = await deep_boundary.invoke(
            **invoke_kwargs(tool_name=DEEP_TOOL, arguments={"payload": nest(5000)})
        )

        assert dict(outcome.invocation.input) == {"unprocessable_input": True}
        assert outcome.invocation.status_reason is not None
        assert outcome.invocation.tool_name == DEEP_TOOL

    async def test_an_ordinary_call_still_records_its_real_input(
        self, boundary: ToolBoundary
    ) -> None:
        """The placeholder must not leak into the normal path."""

        outcome = await boundary.invoke(**invoke_kwargs())

        assert dict(outcome.invocation.input) == {"query": "hello"}


class TestSchemaRejectionSurvivesTheSameInput:
    async def test_input_both_malformed_and_unprocessable_still_records(
        self, deep_boundary: ToolBoundary, audit_store: RecordingAuditStore
    ) -> None:
        """The schema-rejection path redacts the raw arguments too.

        It walks the same structure, so it could fail in exactly the way it
        exists to report. Here the arguments fail the tool's schema *and* are
        too deep to serialize — the boundary must still produce a record.
        """

        outcome = await deep_boundary.invoke(
            **invoke_kwargs(tool_name=DEEP_TOOL, arguments={"wrong_field": nest(5000)})
        )

        assert outcome.status is ToolOutcomeStatus.INVALID_INPUT
        assert len(audit_store.invocations) == 1
        assert dict(outcome.invocation.input) == {"unprocessable_input": True}
