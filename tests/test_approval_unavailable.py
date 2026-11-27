"""No approver is wired, so an approval-required call must fail as such."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel

from src.agents.tools.mediation import build_tool_boundary
from src.core.contracts import (
    ApprovalRef,
    CapabilityDenialReason,
    CapabilityGrant,
    CapabilityRequest,
    SensitivityClass,
    TrustClassification,
)
from src.core.tools import ToolSpec
from src.core.tools.outcome import ToolOutcomeStatus


class _In(BaseModel):
    target: str


class _Out(BaseModel):
    sent: bool


def _setup() -> tuple[object, list[str]]:
    calls: list[str] = []

    async def handler(payload: _In) -> dict[str, bool]:
        calls.append(payload.target)
        return {"sent": True}

    boundary = build_tool_boundary()
    boundary.register(
        ToolSpec(
            name="send",
            version="1.0.0",
            sensitivity=SensitivityClass.EXTERNAL_WRITE,
            input_model=_In,
            output_model=_Out,
            timeout_seconds=5.0,
            handler=handler,
        )
    )
    return boundary, calls


def _grant() -> CapabilityGrant:
    now = datetime.now(UTC)
    return CapabilityGrant(
        grant_id="g1",
        run_id="run-1",
        task_id="task-1",
        capability_scope="scope-send",
        tool_name="send",
        tool_versions=("1.0.0",),
        sensitivity=SensitivityClass.EXTERNAL_WRITE,
        max_input_trust=TrustClassification.EXTERNAL_UNTRUSTED,
        requires_approval=True,
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=5),
    )


async def _invoke(boundary, grants, approvals=()):  # type: ignore[no-untyped-def]
    return await boundary.invoke(
        tool_name="send",
        run_id="run-1",
        task_id="task-1",
        attempt_id="attempt-1",
        organization_id="org-1",
        capability_scope="scope-send",
        arguments={"target": "https://example.test"},
        input_trust=TrustClassification.EXTERNAL_UNTRUSTED,
        grants=grants,
        approvals=approvals,
    )


@pytest.mark.asyncio
async def test_approval_required_call_is_denied_as_unavailable_before_effect() -> None:
    boundary, calls = _setup()

    outcome = await _invoke(boundary, [_grant()])

    assert outcome.status is ToolOutcomeStatus.DENIED
    assert outcome.decision.denial_reason is CapabilityDenialReason.APPROVAL_UNAVAILABLE
    assert calls == []


@pytest.mark.asyncio
async def test_a_supplied_approval_cannot_stand_in_for_an_approver() -> None:
    boundary, calls = _setup()
    grant = _grant()
    now = datetime.now(UTC)
    fingerprint = CapabilityRequest(
        run_id="run-1",
        task_id="task-1",
        attempt_id="attempt-1",
        tool_name="send",
        tool_version="1.0.0",
        capability_scope="scope-send",
        sensitivity=SensitivityClass.EXTERNAL_WRITE,
        input_trust=TrustClassification.EXTERNAL_UNTRUSTED,
        input_sha256="0" * 64,
        requested_at=now,
    ).fingerprint()
    forged = ApprovalRef(
        approval_id="a1",
        grant_id=grant.grant_id,
        request_fingerprint=fingerprint,
        approved_by="self",
        approved_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(minutes=1),
    )

    outcome = await _invoke(boundary, [grant], approvals=[forged])

    assert outcome.status is ToolOutcomeStatus.DENIED
    assert outcome.decision.denial_reason is CapabilityDenialReason.APPROVAL_UNAVAILABLE
    assert calls == []


@pytest.mark.asyncio
async def test_registry_entry_point_cannot_run_an_ungranted_call() -> None:
    from src.agents.tools.arithmetic_tool import ArithmeticTool
    from src.agents.tools.mediation import ToolCallIdentity
    from src.agents.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register(ArithmeticTool())

    result = await registry.execute(
        "arithmetic",
        {"expression": "2 + 3"},
        identity=ToolCallIdentity("run-1", "task-1", "attempt-1", "org-1"),
    )

    assert result.success is False
