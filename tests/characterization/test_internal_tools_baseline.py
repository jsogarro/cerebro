"""Pin the current behavior of the internal-tool path (`src/agents/tools/`).

Path 1 of the Wave 4 tool-boundary preflight: pure-internal tools reached via
`AgentTool`/`ToolRegistry`, plus the `finance_math` module that is imported
directly by two agents and never touches this registry at all.

Written to pin an absence — no capability check, no provenance, no timeout — so
that routing the path through the tool boundary would have a diff to prove
against. `TestToolRegistryUnmediatedPath` now asserts the replacement for all
three; each test names the original assertion it supersedes.

`TestAgentToolExecuteContract` still characterizes `AgentTool.execute`, which
remains as it was. That method is no longer the path the registry uses, and it
is unreachable from the mediated route: `finance_math` is still imported
directly by `financial_calculator_agent.py` and `finance_agents.py`, and
`AgentTool.execute` is still callable by anything holding a tool instance. Both
are unmediated surfaces that survived this wave.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
    """The three absences this path was characterized for, now closed.

    As originally written these tests pinned that the registry layer had no
    capability check, no deadline, and no provenance. Routing it through the
    tool boundary is what closed all three, so each test below now observes the
    replacement rather than the absence. The original assertion is described in
    each docstring, because "this used to be true" is the only reason the
    replacement is worth asserting at all.

    Two of the three originals would still have passed against the routed code
    — they asserted method signatures, and the mediation happens inside the
    method. That is exactly why they were changed: a signature was never the
    thing worth pinning, and leaving them green would have reported a defect as
    still-characterized while it was in fact fixed.
    """

    @pytest.mark.asyncio
    async def test_unregistered_tool_returns_error_result_not_exception(self) -> None:
        registry = ToolRegistry()

        result = await registry.execute("does-not-exist", {})

        assert result.success is False
        assert result.error == "Unknown tool: does-not-exist"

    @pytest.mark.asyncio
    async def test_a_hanging_tool_is_stopped_by_its_declared_deadline(self) -> None:
        """WAS: `execute` took exactly (name, params), so a tool that hung
        inside `_execute_impl` hung the caller indefinitely.

        The deadline is not a parameter of `execute` now either — it is
        declared by the tool and enforced by the boundary, which is a stronger
        arrangement than a caller-supplied timeout because no caller can omit
        it. So this observes the effect instead of the signature.
        """
        import asyncio

        class _HangingTool(AgentTool[_EchoParams]):
            @property
            def name(self) -> str:
                return "hangs"

            @property
            def description(self) -> str:
                return "never returns"

            @property
            def params_model(self) -> type[_EchoParams]:
                return _EchoParams

            @property
            def timeout_seconds(self) -> float:
                return 0.05

            async def _execute_impl(self, params: _EchoParams) -> Any:
                await asyncio.sleep(30)
                return "unreachable"

        registry = ToolRegistry()
        registry.register(_HangingTool())

        result = await asyncio.wait_for(
            registry.execute("hangs", {"value": 1}), timeout=5
        )

        assert result.success is False
        assert "timed_out" in (result.error or "")

    @pytest.mark.asyncio
    async def test_a_call_is_authorized_against_a_grant_naming_its_tool(self) -> None:
        """WAS: `register` took only the tool instance, and any code holding a
        registry reference could execute any registered tool with any input.

        A capability decision is now reached for every call. The grant is
        self-issued while no issuer exists — `src/agents/tools/mediation.py`
        states at length that this is not authorization — but the check itself
        is real, and a grant that names a different tool does not authorize
        this one.
        """
        from src.agents.tools.mediation import ToolCallIdentity, self_issued_grant
        from src.core.contracts.capabilities import SensitivityClass
        from src.core.contracts.trust import TrustClassification

        registry = create_default_registry()
        identity = ToolCallIdentity(
            run_id="r", task_id="t", attempt_id="a", organization_id=None
        )
        wrong_grant = self_issued_grant(
            tool_name="datetime_info",
            tool_version="1.0.0",
            sensitivity=SensitivityClass.READ_ONLY,
            input_trust=TrustClassification.APPLICATION,
            identity=identity,
            now=datetime.now(UTC),
        )

        result = await registry.execute(
            "arithmetic",
            {"expression": "1 + 1"},
            identity=identity,
            grants=[wrong_grant],
        )

        assert result.success is False
        assert "denied" in (result.error or "")

    @pytest.mark.asyncio
    async def test_an_execution_leaves_a_record_naming_its_tool_and_run(self) -> None:
        """WAS: executing a tool produced no structured event, no audit row,
        and not even a log line, so nothing could answer "which tool ran, with
        what input, and what did it return" after the fact.

        It can now. The default store is in-memory — durability needs a
        session-backed store injected at construction — but the record exists,
        carries the run/task/attempt identity, and is written before anything
        is published.
        """
        from src.agents.tools.mediation import InMemoryToolAuditStore, ToolCallIdentity

        store = InMemoryToolAuditStore()
        registry = create_default_registry(audit_store=store)

        result = await registry.execute(
            "arithmetic",
            {"expression": "1 + 1"},
            identity=ToolCallIdentity(
                run_id="run-9", task_id="task-9", attempt_id="attempt-9"
            ),
        )

        assert result.success is True
        recorded = store.invocations[-1]
        assert recorded.tool_name == "arithmetic"
        assert recorded.run_id == "run-9"
        assert recorded.input == {"expression": "1 + 1"}
        assert recorded.output is not None


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
