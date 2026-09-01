from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from src.agents.tools.arithmetic_tool import ArithmeticTool
from src.agents.tools.mediation import ToolCallIdentity
from src.agents.tools.registry import ToolRegistry
from src.core.contracts import CapabilityGrant, SensitivityClass, TrustClassification

NOW = datetime(2020, 1, 1, tzinfo=UTC)


def grant(tool_name: str, scope: str) -> CapabilityGrant:
    return CapabilityGrant(
        grant_id=f"grant-{tool_name}", run_id="run-1", task_id="task-1",
        capability_scope=scope, tool_name=tool_name, tool_versions=("1.0.0",),
        sensitivity=SensitivityClass.READ_ONLY,
        max_input_trust=TrustClassification.APPLICATION, requires_approval=False,
        issued_at=NOW - timedelta(minutes=1), expires_at=NOW + timedelta(minutes=5),
    )


def registry() -> ToolRegistry:
    value = ToolRegistry()
    value.register(ArithmeticTool())
    return value


IDENTITY = ToolCallIdentity("run-1", "task-1", "attempt-1", "org-1")


@pytest.mark.asyncio
async def test_matching_grant_selects_its_scope_and_passes_all_grants() -> None:
    value = registry()
    grants = (grant("other", "scope-other"), grant("arithmetic", "scope-arithmetic"))
    invoke = AsyncMock(wraps=value.boundary.invoke)
    value.boundary.invoke = invoke  # type: ignore[method-assign]

    result = await value.execute("arithmetic", {"expression": "2 + 3"}, identity=IDENTITY, grants=grants)

    assert result.success is True
    assert invoke.await_args is not None
    assert invoke.await_args.kwargs["capability_scope"] == "scope-arithmetic"
    assert invoke.await_args.kwargs["grants"] == list(grants)


@pytest.mark.asyncio
async def test_wrong_and_empty_grants_deny_without_self_issued_grant() -> None:
    value = registry()
    identity = IDENTITY
    for supplied in ((grant("other", "scope-other"),), ()):
        result = await value.execute("arithmetic", {"expression": "2 + 3"}, identity=identity, grants=supplied)
        assert result.success is False
        assert "self-issued:" not in (result.error or "")


@pytest.mark.asyncio
async def test_grants_omitted_preserves_legacy_self_issued_behavior() -> None:
    result = await registry().execute("arithmetic", {"expression": "2 + 3"})

    assert result.success is True
    assert result.value["result"] == 5.0
