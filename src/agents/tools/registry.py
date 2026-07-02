"""Tool registry for internal agent tools.

Provides tool registration, discovery, and execution infrastructure for
LLMWorkerAgentBase subclasses. Tools are pure-internal (no external APIs,
no network, no filesystem) and safe for autonomous execution.
"""

from typing import Any

from src.agents.tools.base_tool import AgentTool, ToolResult


class ToolRegistry:
    """Registry for internal agent tools."""

    def __init__(self) -> None:
        self._tools: dict[str, AgentTool[Any]] = {}

    def register(self, tool: AgentTool[Any]) -> None:
        """Register a tool by name."""
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
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

    async def execute(self, name: str, params: dict[str, Any]) -> ToolResult:
        """Execute a tool by name with the given parameters.

        Returns a ToolResult with success=False if the tool is not registered.
        """
        tool = self.get(name)
        if tool is None:
            return ToolResult(success=False, error=f"Unknown tool: {name}")
        return await tool.execute(params)

    def has_tool(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def __len__(self) -> int:
        """Number of registered tools."""
        return len(self._tools)


def create_default_registry() -> ToolRegistry:
    """Create a registry with all built-in internal tools."""
    from src.agents.tools.arithmetic_tool import ArithmeticTool
    from src.agents.tools.datetime_tool import DatetimeTool
    from src.agents.tools.unit_conversion_tool import UnitConversionTool

    registry = ToolRegistry()
    registry.register(ArithmeticTool())
    registry.register(DatetimeTool())
    registry.register(UnitConversionTool())
    return registry


__all__ = ["ToolRegistry", "create_default_registry"]
