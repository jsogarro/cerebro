"""Base abstraction for internal tools.

Pure-internal tools with validated inputs/outputs — no external APIs, no network,
no filesystem, no subprocess. Safe for execution by any worker agent.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Final, Generic, TypeVar

from pydantic import BaseModel, ValidationError

DEFAULT_INTERNAL_TOOL_TIMEOUT_SECONDS: Final[float] = 5.0
"""The deadline every pure-internal tool inherits unless it declares its own.

The tool boundary deliberately gives ``ToolSpec`` no default timeout, on the
grounds that a default deadline is one nobody chose and nobody will tune. This
is a chosen one, and the choice is narrow enough to defend: an ``AgentTool`` is
contractually pure-internal — no network, no filesystem, no subprocess — so it
cannot block on I/O, and five seconds of CPU is already far outside what
arithmetic, date formatting, or unit conversion should need. A tool that could
legitimately run longer is a tool that is no longer pure-internal, and it
overrides :attr:`AgentTool.timeout_seconds` rather than inheriting this.
"""


class ToolResult(BaseModel):
    """Structured result from a tool execution."""

    success: bool
    value: Any = None
    error: str | None = None


TParams = TypeVar("TParams", bound=BaseModel)


class AgentTool(ABC, Generic[TParams]):
    """Base class for internal agent tools.

    Subclasses define:
    - name: unique tool identifier
    - description: one-line summary
    - params_model: Pydantic model for input validation
    - _execute_impl: the actual computation
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique tool identifier (e.g., 'arithmetic', 'datetime_info')."""

    @property
    @abstractmethod
    def description(self) -> str:
        """One-line description of what the tool does."""

    @property
    @abstractmethod
    def params_model(self) -> type[TParams]:
        """Pydantic model for input validation."""

    @property
    def version(self) -> str:
        """The version the boundary authorizes and records this tool under."""

        return "1.0.0"

    @property
    def timeout_seconds(self) -> float:
        """The deadline this tool runs under. See the module constant."""

        return DEFAULT_INTERNAL_TOOL_TIMEOUT_SECONDS

    @abstractmethod
    async def _execute_impl(self, params: TParams) -> Any:
        """Execute the tool with validated parameters.

        Subclasses implement the actual computation here. Should never raise —
        return error result instead.
        """

    async def execute(self, params_dict: dict[str, Any]) -> ToolResult:
        """Execute tool with input validation.

        Validates inputs via params_model, runs _execute_impl, and returns a
        structured ToolResult. Never raises — errors become error results.
        """
        try:
            # Validate inputs
            params = self.params_model.model_validate(params_dict)
        except ValidationError as exc:
            # Input validation failed
            errors = "; ".join(f"{err['loc'][0]}: {err['msg']}" for err in exc.errors())
            return ToolResult(success=False, error=f"Invalid input: {errors}")

        try:
            # Execute the tool
            result = await self._execute_impl(params)
            return ToolResult(success=True, value=result)
        except Exception as exc:
            # Tool execution failed
            return ToolResult(success=False, error=f"Execution error: {exc}")


__all__ = [
    "DEFAULT_INTERNAL_TOOL_TIMEOUT_SECONDS",
    "AgentTool",
    "ToolResult",
]
