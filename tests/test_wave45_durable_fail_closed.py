from unittest.mock import AsyncMock

import pytest

from src.agents.integrations.mcp_integration import MCPIntegration
from src.agents.tools.arithmetic_tool import ArithmeticTool
from src.agents.tools.mediation import ToolCallIdentity
from src.agents.tools.registry import ToolRegistry


def durable_identity() -> ToolCallIdentity:
    return ToolCallIdentity("run-1", "task-1", "attempt-1", "org-1")


def client() -> AsyncMock:
    value = AsyncMock()
    value.health_check.return_value = {"client": "healthy"}
    value.search_academic.return_value = {"success": True, "results": []}
    return value


@pytest.mark.asyncio
async def test_mcp_durable_identity_without_grants_denies_without_self_issuing() -> (
    None
):
    integration = MCPIntegration(
        mcp_client=client(), enable_fallback=False, identity=durable_identity()
    )
    invoke = AsyncMock(wraps=integration.boundary.invoke)
    integration.boundary.invoke = invoke  # type: ignore[method-assign]

    result = await integration.search_academic_sources("query")

    assert result["success"] is False
    assert invoke.await_args is not None
    assert invoke.await_args.kwargs["grants"] == []


@pytest.mark.asyncio
async def test_registry_durable_identity_without_grants_denies_without_self_issuing() -> (
    None
):
    registry = ToolRegistry()
    registry.register(ArithmeticTool())
    invoke = AsyncMock(wraps=registry.boundary.invoke)
    registry.boundary.invoke = invoke  # type: ignore[method-assign]

    result = await registry.execute(
        "arithmetic", {"expression": "2 + 3"}, identity=durable_identity()
    )

    assert result.success is False
    assert invoke.await_args is not None
    assert invoke.await_args.kwargs["grants"] == []
