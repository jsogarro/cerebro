"""Pin the current behavior of the internal-tool path (`src/agents/tools/`).

Path 1 of the Wave 4 tool-boundary preflight: pure-internal tools reached via
`AgentTool`/`ToolRegistry`, plus the `finance_math` module that is imported
directly by two agents and never touches this registry at all. No network,
no capability check, no provenance, no timeout — this file pins that absence
so a future boundary (packet 4C/4D) has a diff to prove against.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from src.agents.tools import finance_math
from src.agents.tools.arithmetic_tool import ArithmeticTool
from src.agents.tools.base_tool import AgentTool, ToolResult
from src.agents.tools.registry import ToolRegistry, create_default_registry


class _EchoParams(BaseModel):
    value: int


class _RaisingTool(AgentTool[_EchoParams]):
    """A tool whose _execute_impl violates the "never raise" docstring contract."""

    @property
    def name(self) -> str:
        return "raising_tool"

    @property
    def description(self) -> str:
        return "Always raises a non-ValueError exception"

    @property
    def params_model(self) -> type[_EchoParams]:
        return _EchoParams

    async def _execute_impl(self, params: _EchoParams) -> Any:
        raise RuntimeError("boom")


class TestAgentToolExecuteContract:
    """`AgentTool.execute` claims it "never raises" — pin what that means today."""

    @pytest.mark.asyncio
    async def test_invalid_input_returns_error_result_not_raise(self) -> None:
        tool = ArithmeticTool()

        result = await tool.execute({"expression": 123})

        assert isinstance(result, ToolResult)
        assert result.success is False
        assert result.error is not None
        assert "Invalid input" in result.error

    @pytest.mark.asyncio
    async def test_missing_required_field_returns_error_result(self) -> None:
        tool = ArithmeticTool()

        result = await tool.execute({})

        assert result.success is False
        assert result.error is not None
        assert "expression" in result.error

    @pytest.mark.asyncio
    async def test_execute_impl_raising_a_non_valueerror_is_still_swallowed(
        self,
    ) -> None:
        """CHARACTERIZATION: the base wrapper catches bare Exception, so a
        subclass that violates its own "should never raise" docstring is
        papered over here rather than surfaced as a contract violation.
        Wave 4 packet 4C should decide whether that is still the right
        behavior at the mediated boundary.
        """
        tool = _RaisingTool()

        result = await tool.execute({"value": 1})

        assert result.success is False
        assert result.error is not None
        assert "Execution error" in result.error
        assert "boom" in result.error

    @pytest.mark.asyncio
    async def test_extra_unknown_fields_are_silently_ignored_by_default(self) -> None:
        """CHARACTERIZATION: Pydantic's default `model_config` (no
        `extra="forbid"`) accepts and drops unrecognized input keys instead
        of rejecting them. An attacker or a caller with a typo gets a
        successful result with no signal that a field was ignored.
        """
        tool = ArithmeticTool()

        result = await tool.execute(
            {"expression": "2 + 2", "unexpected_field": "anything at all"}
        )

        assert result.success is True
        assert result.value["result"] == 4.0


class TestToolRegistryUnmediatedPath:
    """No capability check, no timeout, no provenance at the registry layer."""

    @pytest.mark.asyncio
    async def test_unregistered_tool_returns_error_result_not_exception(self) -> None:
        registry = ToolRegistry()

        result = await registry.execute("does-not-exist", {})

        assert result.success is False
        assert result.error == "Unknown tool: does-not-exist"

    @pytest.mark.asyncio
    async def test_registry_execute_has_no_timeout_parameter(self) -> None:
        """CHARACTERIZATION: `ToolRegistry.execute` takes exactly (name,
        params) — there is no timeout, deadline, or cancellation token in
        the signature. A tool that hangs inside `_execute_impl` hangs the
        caller indefinitely; nothing in this layer can stop it.
        """
        import inspect

        sig = inspect.signature(ToolRegistry.execute)
        assert list(sig.parameters) == ["self", "name", "params"]

    def test_register_does_not_accept_or_check_any_capability_scope(self) -> None:
        """CHARACTERIZATION: `register` takes only the tool instance. There
        is no caller identity, task scope, or capability grant anywhere in
        this path — any code holding a `ToolRegistry` reference can execute
        any registered tool with any input.
        """
        import inspect

        sig = inspect.signature(ToolRegistry.register)
        assert list(sig.parameters) == ["self", "tool"]

    @pytest.mark.asyncio
    async def test_no_event_or_log_record_is_produced_by_a_tool_execution(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """CHARACTERIZATION: pins the absence of provenance. Executing a
        tool through the registry produces no structured event, no audit
        row, and (unlike the MCP tool path) not even a log line — there is
        nothing in this repository today that can answer "which tool ran,
        with what input, and what did it return" after the fact for this
        path.
        """
        registry = create_default_registry()

        result = await registry.execute("arithmetic", {"expression": "1 + 1"})

        assert result.success is True
        # No log record was emitted by the tool/registry layer itself.
        assert caplog.records == []


class TestFinanceMathBypassesTheToolContractEntirely:
    """`finance_math` is a plain module of functions, not an `AgentTool`.

    It is never registered in `create_default_registry()`, but it is fully
    reachable in production: `financial_calculator_agent.py` and
    `finance_agents.py` both import its functions directly. It therefore
    has none of `AgentTool.execute`'s guarantees — no Pydantic input
    validation, no guaranteed `ToolResult` wrapping, and exceptions other
    than the ones each call site happens to catch propagate to the caller.
    """

    def test_finance_math_is_not_in_the_default_registry(self) -> None:
        registry = create_default_registry()

        assert registry.has_tool("finance_math") is False
        # None of the three registered tools happen to collide with any
        # finance_math operation name either.
        for op_name in finance_math.AVAILABLE_OPERATIONS:
            assert registry.has_tool(op_name) is False

    def test_calculate_raises_valueerror_directly_not_a_toolresult(self) -> None:
        """CHARACTERIZATION: unlike every `AgentTool`, a bad call into
        `finance_math.calculate` raises directly. There is no `ToolResult`,
        no `success`/`error` shape — callers must each remember to catch
        `ValueError` themselves (and `financial_calculator_agent.py` does;
        a caller that forgets would propagate this as an unhandled 500).
        """
        with pytest.raises(ValueError, match="Unknown operation"):
            finance_math.calculate("not_a_real_operation", {})

    def test_calculate_raises_valueerror_on_missing_required_param(self) -> None:
        """CHARACTERIZATION: a missing parameter is a bare `KeyError`
        re-raised as `ValueError` inside `calculate`, not a structured,
        per-field validation error the way `AgentTool.execute` produces one
        via Pydantic. There is no equivalent of "expression: field
        required" here — only a Python `KeyError` repr embedded in a
        string.
        """
        with pytest.raises(ValueError, match="Missing required parameter"):
            finance_math.calculate("dcf", {"fcf_next": 100})

    def test_calculate_has_no_input_schema_arbitrary_dict_accepted(self) -> None:
        """CHARACTERIZATION: there is no Pydantic params_model equivalent.
        Any extra keys are silently ignored (the lambda only reads the keys
        it wants) and there is no static description of what a valid input
        looks like beyond the lambda body itself.
        """
        result = finance_math.calculate(
            "compound_interest",
            {
                "principal": 1000,
                "annual_rate": 0.05,
                "years": 1,
                "totally_unexpected_key": object(),
            },
        )
        assert result["future_value"] == 1050.0
