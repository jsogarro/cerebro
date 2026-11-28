"""Boundary registration and typed execution for ``datetime_info`` and ``unit_conversion``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.agents.integrations.mcp_integration import MCPIntegration
from src.agents.integrations.mcp_tool_specs import (
    MCP_TOOL_OUTBOUND_HOSTS,
    MCP_TOOL_SENSITIVITIES,
    TOOL_DATETIME_INFO,
    TOOL_SOURCE_FETCH,
    TOOL_UNIT_CONVERSION,
    TOOL_VERSION,
)
from src.agents.tools.mediation import InMemoryToolAuditStore, ToolCallIdentity
from src.core.contracts import (
    CapabilityGrant,
    SensitivityClass,
    TrustClassification,
)
from src.core.contracts.provenance import ToolInvocationStatus
from src.core.tools import ToolOutcomeStatus


def _grant(tool_name: str) -> CapabilityGrant:
    now = datetime.now(UTC)
    return CapabilityGrant(
        grant_id=f"grant-{tool_name}",
        run_id="run-1",
        task_id="task-1",
        capability_scope=f"plan-issued:{tool_name}",
        tool_name=tool_name,
        tool_versions=(TOOL_VERSION,),
        sensitivity=SensitivityClass.READ_ONLY,
        max_input_trust=TrustClassification.EXTERNAL_UNTRUSTED,
        requires_approval=False,
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=10),
    )


class TestLocalToolsBoundaryRegistration:
    @pytest.mark.asyncio
    async def test_tools_are_registered_on_boundary_as_read_only_with_no_outbound_hosts(
        self,
    ) -> None:
        integration = MCPIntegration(enable_fallback=False)

        for name in (TOOL_DATETIME_INFO, TOOL_UNIT_CONVERSION):
            spec = integration.boundary.specification(name)
            assert spec.sensitivity is SensitivityClass.READ_ONLY
            assert spec.outbound_hosts == ()
            assert spec.input_model.model_config.get("extra") == "forbid"
            assert MCP_TOOL_SENSITIVITIES[name] is SensitivityClass.READ_ONLY
            assert MCP_TOOL_OUTBOUND_HOSTS[name] == ()

        status = await integration.get_tool_status()
        assert TOOL_DATETIME_INFO in status["registered_tools"]
        assert TOOL_UNIT_CONVERSION in status["registered_tools"]
        assert TOOL_SOURCE_FETCH in status["registered_tools"]

    @pytest.mark.asyncio
    async def test_datetime_info_valid_calls_record_provenance_and_latency(
        self,
    ) -> None:
        store = InMemoryToolAuditStore()
        grant = _grant(TOOL_DATETIME_INFO)
        integration = MCPIntegration(
            enable_fallback=False,
            audit_store=store,
            identity=ToolCallIdentity("run-1", "task-1", "att-1", "org-1"),
            grants=(grant,),
        )

        outcome = await integration.boundary.invoke(
            tool_name=TOOL_DATETIME_INFO,
            run_id="run-1",
            task_id="task-1",
            attempt_id="att-1",
            organization_id="org-1",
            capability_scope=grant.capability_scope,
            arguments={"operation": "day_of_week", "date_iso": "2024-01-15"},
            input_trust=TrustClassification.USER_SUPPLIED,
            grants=(grant,),
        )

        assert outcome.succeeded
        payload = dict(outcome.unwrap())
        assert payload["day_of_week"] == "Monday"
        assert payload["day_number"] == 1
        inv = outcome.invocation
        assert inv.status is ToolInvocationStatus.SUCCEEDED
        assert inv.completed_at is not None
        assert (inv.completed_at - inv.requested_at) >= timedelta(0)
        assert store.invocations[-1].tool_invocation_id == inv.tool_invocation_id

        via_method = await integration.get_datetime_info(
            operation="add_days", date_iso="2024-01-15", days=10
        )
        assert via_method["success"] is True
        assert via_method["result_date"] == "2024-01-25"
        assert via_method["days_added"] == 10
        assert via_method["identity_bound"] is True

        diff = await integration.get_datetime_info(
            operation="diff_days",
            date_iso="2024-01-15",
            other_date_iso="2024-01-25",
        )
        assert diff["success"] is True
        assert diff["difference_days"] == 10

        current = await integration.get_datetime_info(operation="current")
        assert current["success"] is True
        assert current["current_datetime_utc"] is not None

    @pytest.mark.asyncio
    async def test_datetime_info_rejects_invalid_input_and_handles_errors(
        self,
    ) -> None:
        grant = _grant(TOOL_DATETIME_INFO)
        integration = MCPIntegration(
            enable_fallback=False,
            identity=ToolCallIdentity("run-1", "task-1", "att-1", "org-1"),
            grants=(grant,),
        )

        # Unknown operation -> INVALID_INPUT
        invalid_op = await integration.boundary.invoke(
            tool_name=TOOL_DATETIME_INFO,
            run_id="run-1",
            task_id="task-1",
            attempt_id="att-1",
            organization_id="org-1",
            capability_scope=grant.capability_scope,
            arguments={"operation": "bogus_op"},
            input_trust=TrustClassification.USER_SUPPLIED,
            grants=(grant,),
        )
        assert invalid_op.status is ToolOutcomeStatus.INVALID_INPUT

        # Extra undeclared field -> INVALID_INPUT
        extra_field = await integration.boundary.invoke(
            tool_name=TOOL_DATETIME_INFO,
            run_id="run-1",
            task_id="task-1",
            attempt_id="att-1",
            organization_id="org-1",
            capability_scope=grant.capability_scope,
            arguments={"operation": "current", "unexpected": "smuggled"},
            input_trust=TrustClassification.USER_SUPPLIED,
            grants=(grant,),
        )
        assert extra_field.status is ToolOutcomeStatus.INVALID_INPUT

        # Missing required parameter for day_of_week -> FAILED tool_error
        missing_date = await integration.boundary.invoke(
            tool_name=TOOL_DATETIME_INFO,
            run_id="run-1",
            task_id="task-1",
            attempt_id="att-1",
            organization_id="org-1",
            capability_scope=grant.capability_scope,
            arguments={"operation": "day_of_week"},
            input_trust=TrustClassification.USER_SUPPLIED,
            grants=(grant,),
        )
        assert missing_date.status is ToolOutcomeStatus.FAILED
        assert missing_date.error_code == "tool_error"
        assert "date_iso" in (missing_date.detail or "")

        # Malformed date -> FAILED tool_error
        bad_date = await integration.get_datetime_info(
            operation="day_of_week", date_iso="not-a-date"
        )
        assert bad_date["success"] is False
        assert bad_date["tool_outcome"] == ToolOutcomeStatus.FAILED.value

        # Durable call without grant -> DENIED
        denied_integration = MCPIntegration(
            enable_fallback=False,
            identity=ToolCallIdentity("run-1", "task-1", "att-1", "org-1"),
            grants=(),
        )
        denied = await denied_integration.get_datetime_info(operation="current")
        assert denied["success"] is False
        assert denied["tool_outcome"] == ToolOutcomeStatus.DENIED.value

    @pytest.mark.asyncio
    async def test_unit_conversion_valid_calls_record_provenance_and_latency(
        self,
    ) -> None:
        store = InMemoryToolAuditStore()
        grant = _grant(TOOL_UNIT_CONVERSION)
        integration = MCPIntegration(
            enable_fallback=False,
            audit_store=store,
            identity=ToolCallIdentity("run-1", "task-1", "att-1", "org-1"),
            grants=(grant,),
        )

        outcome = await integration.boundary.invoke(
            tool_name=TOOL_UNIT_CONVERSION,
            run_id="run-1",
            task_id="task-1",
            attempt_id="att-1",
            organization_id="org-1",
            capability_scope=grant.capability_scope,
            arguments={"value": 1000, "from_unit": "m", "to_unit": "km"},
            input_trust=TrustClassification.USER_SUPPLIED,
            grants=(grant,),
        )

        assert outcome.succeeded
        payload = dict(outcome.unwrap())
        assert payload["result"] == 1.0
        assert payload["from_unit"] == "m"
        assert payload["to_unit"] == "km"
        inv = outcome.invocation
        assert inv.status is ToolInvocationStatus.SUCCEEDED
        assert inv.completed_at is not None
        assert (inv.completed_at - inv.requested_at) >= timedelta(0)
        assert store.invocations[-1].tool_invocation_id == inv.tool_invocation_id

        temp = await integration.convert_units(
            value=0, from_unit="celsius", to_unit="fahrenheit"
        )
        assert temp["success"] is True
        assert temp["result"] == 32.0
        assert temp["identity_bound"] is True

    @pytest.mark.asyncio
    async def test_unit_conversion_rejects_invalid_input_and_handles_errors(
        self,
    ) -> None:
        grant = _grant(TOOL_UNIT_CONVERSION)
        integration = MCPIntegration(
            enable_fallback=False,
            identity=ToolCallIdentity("run-1", "task-1", "att-1", "org-1"),
            grants=(grant,),
        )

        # Wrong type -> INVALID_INPUT
        bad_type = await integration.boundary.invoke(
            tool_name=TOOL_UNIT_CONVERSION,
            run_id="run-1",
            task_id="task-1",
            attempt_id="att-1",
            organization_id="org-1",
            capability_scope=grant.capability_scope,
            arguments={"value": "not-a-number", "from_unit": "m", "to_unit": "km"},
            input_trust=TrustClassification.USER_SUPPLIED,
            grants=(grant,),
        )
        assert bad_type.status is ToolOutcomeStatus.INVALID_INPUT

        # Extra field -> INVALID_INPUT
        extra = await integration.boundary.invoke(
            tool_name=TOOL_UNIT_CONVERSION,
            run_id="run-1",
            task_id="task-1",
            attempt_id="att-1",
            organization_id="org-1",
            capability_scope=grant.capability_scope,
            arguments={"value": 1, "from_unit": "m", "to_unit": "km", "extra": 1},
            input_trust=TrustClassification.USER_SUPPLIED,
            grants=(grant,),
        )
        assert extra.status is ToolOutcomeStatus.INVALID_INPUT

        # Incompatible units -> FAILED tool_error
        incompatible = await integration.convert_units(
            value=10, from_unit="m", to_unit="kg"
        )
        assert incompatible["success"] is False
        assert incompatible["tool_outcome"] == ToolOutcomeStatus.FAILED.value
        assert incompatible["error_code"] == "tool_error"

        # Unknown unit -> FAILED tool_error
        unknown = await integration.convert_units(
            value=10, from_unit="furlongs", to_unit="m"
        )
        assert unknown["success"] is False
        assert unknown["tool_outcome"] == ToolOutcomeStatus.FAILED.value

        # Durable call without grant -> DENIED
        denied_integration = MCPIntegration(
            enable_fallback=False,
            identity=ToolCallIdentity("run-1", "task-1", "att-1", "org-1"),
            grants=(),
        )
        denied = await denied_integration.convert_units(
            value=10, from_unit="m", to_unit="km"
        )
        assert denied["success"] is False
        assert denied["tool_outcome"] == ToolOutcomeStatus.DENIED.value
