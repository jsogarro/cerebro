"""Unit tests for tool registry."""

import pytest

from src.agents.tools.arithmetic_tool import ArithmeticTool
from src.agents.tools.base_tool import ToolResult
from src.agents.tools.datetime_tool import DatetimeTool
from src.agents.tools.registry import ToolRegistry, create_default_registry


class TestToolRegistry:
    """Test suite for ToolRegistry."""

    def test_register_tool(self):
        """Test registering a tool."""
        registry = ToolRegistry()
        tool = ArithmeticTool()

        registry.register(tool)

        assert registry.has_tool("arithmetic")
        assert registry.get("arithmetic") is tool
        assert len(registry) == 1

    def test_register_duplicate_tool(self):
        """Test registering duplicate tool raises error."""
        registry = ToolRegistry()
        tool = ArithmeticTool()

        registry.register(tool)

        with pytest.raises(ValueError, match="already registered"):
            registry.register(tool)

    def test_get_nonexistent_tool(self):
        """Test getting nonexistent tool returns None."""
        registry = ToolRegistry()

        result = registry.get("nonexistent")

        assert result is None

    def test_list_tools(self):
        """Test listing registered tools."""
        registry = ToolRegistry()
        registry.register(ArithmeticTool())
        registry.register(DatetimeTool())

        tools = registry.list_tools()

        assert len(tools) == 2
        assert any(t["name"] == "arithmetic" for t in tools)
        assert any(t["name"] == "datetime_info" for t in tools)
        assert all("description" in t for t in tools)

    def test_list_tools_empty(self):
        """Test listing tools when registry is empty."""
        registry = ToolRegistry()

        tools = registry.list_tools()

        assert tools == []

    @pytest.mark.asyncio
    async def test_execute_tool(self):
        """Test executing a registered tool."""
        registry = ToolRegistry()
        registry.register(ArithmeticTool())

        result = await registry.execute("arithmetic", {"expression": "2 + 3"})

        assert isinstance(result, ToolResult)
        assert result.success is True
        assert result.value["result"] == 5.0

    @pytest.mark.asyncio
    async def test_execute_nonexistent_tool(self):
        """Test executing nonexistent tool returns error result."""
        registry = ToolRegistry()

        result = await registry.execute("nonexistent", {})

        assert isinstance(result, ToolResult)
        assert result.success is False
        assert "Unknown tool" in result.error

    def test_has_tool(self):
        """Test checking if tool exists."""
        registry = ToolRegistry()
        registry.register(ArithmeticTool())

        assert registry.has_tool("arithmetic") is True
        assert registry.has_tool("nonexistent") is False

    def test_len(self):
        """Test registry length."""
        registry = ToolRegistry()

        assert len(registry) == 0

        registry.register(ArithmeticTool())
        assert len(registry) == 1

        registry.register(DatetimeTool())
        assert len(registry) == 2

    def test_create_default_registry(self):
        """Test creating default registry with built-in tools."""
        registry = create_default_registry()

        assert len(registry) >= 3  # At least arithmetic, datetime, unit_conversion
        assert registry.has_tool("arithmetic")
        assert registry.has_tool("datetime_info")
        assert registry.has_tool("unit_conversion")

    @pytest.mark.asyncio
    async def test_default_registry_tools_work(self):
        """Test that tools in default registry are functional."""
        registry = create_default_registry()

        # Test arithmetic
        result = await registry.execute("arithmetic", {"expression": "10 / 2"})
        assert result.success is True
        assert result.value["result"] == 5.0

        # Test datetime
        result = await registry.execute("datetime_info", {"operation": "current"})
        assert result.success is True
        assert "current_datetime_utc" in result.value

        # Test unit conversion
        result = await registry.execute(
            "unit_conversion", {"value": 1000, "from_unit": "m", "to_unit": "km"},
        )
        assert result.success is True
        assert result.value["result"] == 1.0
