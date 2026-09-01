"""Tool registry for internal agent tools.

Registration and discovery for ``LLMWorkerAgentBase`` subclasses. Execution
goes through the tool boundary: registering a tool here builds its
``ToolSpec`` and admits it, and :meth:`ToolRegistry.execute` calls
``ToolBoundary.invoke`` rather than the tool's own ``execute``.

Packet 4-Char characterized what that replaces. This path had no capability
check, no deadline, no provenance, and — unlike the MCP path — not even a log
line, so nothing in the repository could answer "which tool ran, with what
input, and what did it return" after the fact. All of that now comes from
being registered, not from anything written here.

**The caller contract is unchanged.** :meth:`execute` still returns a
``ToolResult`` and still never raises, because the whole point of routing is
that callers inherit the guarantees rather than being rewritten around them.
What changed is where the answer comes from: ``success`` is now true only when
the boundary minted a successful outcome, and a boundary outcome is the only
thing that can produce one.

**An honest note on output validation.** An ``AgentTool`` declares an input
model but no output model, so the specification built here validates outputs
against a permissive JSON-value wrapper. That catches a tool returning
something unserializable — a real failure mode for anything that will be
persisted — but it is not the schema check the boundary is capable of. Giving
these tools real output models is a change to the tools, not to the routing.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, JsonValue

from src.agents.tools.base_tool import AgentTool, ToolResult
from src.agents.tools.mediation import (
    InMemoryToolAuditStore,
    SelfIssuedPolicy,
    ToolCallIdentity,
    build_tool_boundary,
    plain,
    self_issued_grant,
)
from src.core.contracts.capabilities import CapabilityGrant, SensitivityClass
from src.core.contracts.trust import TrustClassification
from src.core.tools import (
    ToolAuditStore,
    ToolBoundary,
    ToolCallContext,
    ToolNotRegisteredError,
    ToolOutcome,
    ToolOutcomeStatus,
    ToolSpec,
)

INTERNAL_TOOL_SENSITIVITY = SensitivityClass.READ_ONLY
"""Pure-internal tools have no effect outside the run, by their own contract."""

DEFAULT_INTERNAL_INPUT_TRUST = TrustClassification.APPLICATION
"""The trust label a caller that does not state one is taken to be supplying.

A *default*, not a constant: `execute` accepts `input_trust`, so a caller
handling document-derived content can say so and be refused. Without that
parameter the ceiling below could never be exceeded and the check would be
decorative — which is the same defect, one level up, that packet 4A caught in
the grant itself.
"""

INTERNAL_TOOL_MAX_INPUT_TRUST = TrustClassification.USER_SUPPLIED
"""The ceiling declared for every pure-internal tool.

Arguments to an expression evaluator or a unit converter should originate with
the application or the user. Content lifted out of a retrieved document has no
business being evaluated, so `external_untrusted` and `derived_untrusted` are
refused. Declared here once, never read off the call — see
`SelfIssuedPolicy`.
"""


class InternalToolOutput(BaseModel):
    """The permissive output contract described in the module docstring."""

    value: JsonValue = None


class ToolRegistry:
    """Registry for internal agent tools, backed by the tool boundary."""

    def __init__(
        self,
        *,
        boundary: ToolBoundary | None = None,
        audit_store: ToolAuditStore | None = None,
    ) -> None:
        self._tools: dict[str, AgentTool[Any]] = {}
        self._boundary = boundary or build_tool_boundary(
            audit_store=audit_store or InMemoryToolAuditStore()
        )

    @property
    def boundary(self) -> ToolBoundary:
        """The mediator every tool in this registry runs through."""

        return self._boundary

    def register(self, tool: AgentTool[Any]) -> None:
        """Register a tool by name and admit its specification."""
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._boundary.register(_spec_for(tool))
        self._tools[tool.name] = tool

    def get(self, name: str) -> AgentTool[Any] | None:
        """Get a tool by name, or None if not registered."""
        return self._tools.get(name)

    def list_tools(self) -> list[dict[str, str]]:
        """List all registered tools with name and description."""
        return [
            {"name": tool.name, "description": tool.description}
            for tool in self._tools.values()
        ]

    async def execute(
        self,
        name: str,
        params: dict[str, Any],
        *,
        identity: ToolCallIdentity | None = None,
        input_trust: TrustClassification = DEFAULT_INTERNAL_INPUT_TRUST,
        grants: Sequence[CapabilityGrant] | None = None,
    ) -> ToolResult:
        """Execute a tool by name, through the boundary.

        ``identity`` correlates the call to a run. When a caller has none,
        :meth:`ToolCallIdentity.unbound` supplies a marked one rather than a
        plausible-looking invention — see that method.

        ``grants`` are injected by a caller that has a real issuer. When
        omitted, non-durable callers retain legacy self-issued behavior;
        durable callers receive no grants and are denied by the boundary.
        """

        tool = self.get(name)
        if tool is None:
            # Preserved verbatim: existing callers and tests match on it, and
            # a refusal is the same answer either way.
            return ToolResult(success=False, error=f"Unknown tool: {name}")

        call_identity = identity or ToolCallIdentity.unbound(label=name)
        now = datetime.now(UTC)
        spec = self._boundary.specification(name)
        effective_grants = (
            list(grants)
            if grants is not None
            else (
                []
                if call_identity.durable
                else [
                    self_issued_grant(
                        tool_name=spec.name,
                        policy=SelfIssuedPolicy(
                            sensitivity=INTERNAL_TOOL_SENSITIVITY,
                            max_input_trust=INTERNAL_TOOL_MAX_INPUT_TRUST,
                            tool_versions=(spec.version,),
                        ),
                        identity=call_identity,
                        now=now,
                    )
                ]
            )
        )
        capability_scope = next(
            (
                grant.capability_scope
                for grant in effective_grants
                if grant.tool_name == name
            ),
            name,
        )

        try:
            outcome = await self._boundary.invoke(
                tool_name=name,
                run_id=call_identity.run_id,
                task_id=call_identity.task_id,
                attempt_id=call_identity.attempt_id,
                organization_id=call_identity.organization_id,
                capability_scope=capability_scope,
                arguments=params,
                input_trust=input_trust,
                grants=effective_grants,
            )
        except ToolNotRegisteredError:  # pragma: no cover - guarded above
            return ToolResult(success=False, error=f"Unknown tool: {name}")

        return _result_of(outcome)

    def has_tool(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def __len__(self) -> int:
        """Number of registered tools."""
        return len(self._tools)


def _spec_for(tool: AgentTool[Any]) -> ToolSpec:
    """Derive the boundary specification an ``AgentTool`` runs under."""

    async def handler(
        params: BaseModel, context: ToolCallContext
    ) -> dict[str, JsonValue]:
        # `_execute_impl` receives the boundary-validated params object, which
        # is an instance of the tool's own `params_model` — the same object the
        # unmediated path built, so no tool needed changing to be routed.
        return {"value": await tool._execute_impl(params)}

    return ToolSpec(
        name=tool.name,
        version=tool.version,
        sensitivity=INTERNAL_TOOL_SENSITIVITY,
        input_model=tool.params_model,
        output_model=InternalToolOutput,
        timeout_seconds=tool.timeout_seconds,
        handler=handler,
    )


def _result_of(outcome: ToolOutcome) -> ToolResult:
    """Project a boundary outcome onto the registry's caller contract.

    A non-success outcome carries no body — the boundary makes that
    unrepresentable — so there is nothing here that could turn a failure into a
    value. The distinct outcome state is preserved in the error text rather
    than collapsed, because 4-Char found an open breaker and a single failure
    rendering identically on the other path.
    """

    if outcome.status is ToolOutcomeStatus.SUCCEEDED:
        recorded = plain(outcome.unwrap())
        return ToolResult(success=True, value=recorded["value"])
    detail = outcome.detail or outcome.error_code or outcome.status.value
    return ToolResult(
        success=False,
        error=f"{outcome.status.value}: {detail}",
    )


def create_default_registry(
    *,
    boundary: ToolBoundary | None = None,
    audit_store: ToolAuditStore | None = None,
) -> ToolRegistry:
    """Create a registry with all built-in internal tools."""
    from src.agents.tools.arithmetic_tool import ArithmeticTool
    from src.agents.tools.datetime_tool import DatetimeTool
    from src.agents.tools.unit_conversion_tool import UnitConversionTool

    registry = ToolRegistry(boundary=boundary, audit_store=audit_store)
    registry.register(ArithmeticTool())
    registry.register(DatetimeTool())
    registry.register(UnitConversionTool())
    return registry


__all__ = [
    "DEFAULT_INTERNAL_INPUT_TRUST",
    "INTERNAL_TOOL_MAX_INPUT_TRUST",
    "INTERNAL_TOOL_SENSITIVITY",
    "InternalToolOutput",
    "ToolRegistry",
    "create_default_registry",
]
