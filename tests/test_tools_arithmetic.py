"""Unit tests for arithmetic tool."""

import pytest

from src.agents.tools.arithmetic_tool import ArithmeticParams, ArithmeticTool


@pytest.fixture
def arithmetic_tool():
    """Create an arithmetic tool instance."""
    return ArithmeticTool()


class TestArithmeticTool:
    """Test suite for ArithmeticTool."""

    @pytest.mark.asyncio
    async def test_simple_addition(self, arithmetic_tool):
        """Test basic addition."""
        result = await arithmetic_tool.execute({"expression": "2 + 3"})
        assert result.success is True
        assert result.value["result"] == 5.0
        assert result.value["expression"] == "2 + 3"

    @pytest.mark.asyncio
    async def test_complex_expression(self, arithmetic_tool):
        """Test complex expression with multiple operations."""
        result = await arithmetic_tool.execute({"expression": "(2 + 3) * 4 / 2"})
        assert result.success is True
        assert result.value["result"] == 10.0

    @pytest.mark.asyncio
    async def test_exponentiation(self, arithmetic_tool):
        """Test power operation."""
        result = await arithmetic_tool.execute({"expression": "2 ** 10"})
        assert result.success is True
        assert result.value["result"] == 1024.0

    @pytest.mark.asyncio
    async def test_floor_division(self, arithmetic_tool):
        """Test floor division."""
        result = await arithmetic_tool.execute({"expression": "7 // 2"})
        assert result.success is True
        assert result.value["result"] == 3.0

    @pytest.mark.asyncio
    async def test_modulo(self, arithmetic_tool):
        """Test modulo operation."""
        result = await arithmetic_tool.execute({"expression": "10 % 3"})
        assert result.success is True
        assert result.value["result"] == 1.0

    @pytest.mark.asyncio
    async def test_unary_operators(self, arithmetic_tool):
        """Test unary plus and minus."""
        result = await arithmetic_tool.execute({"expression": "-(+5)"})
        assert result.success is True
        assert result.value["result"] == -5.0

    @pytest.mark.asyncio
    async def test_division_by_zero(self, arithmetic_tool):
        """Test division by zero is handled gracefully."""
        result = await arithmetic_tool.execute({"expression": "10 / 0"})
        assert result.success is False
        assert "Division by zero" in result.error

    @pytest.mark.asyncio
    async def test_floor_division_by_zero(self, arithmetic_tool):
        """Test floor division by zero is handled."""
        result = await arithmetic_tool.execute({"expression": "10 // 0"})
        assert result.success is False
        assert "Division by zero" in result.error

    @pytest.mark.asyncio
    async def test_modulo_by_zero(self, arithmetic_tool):
        """Test modulo by zero is handled."""
        result = await arithmetic_tool.execute({"expression": "10 % 0"})
        assert result.success is False
        assert "Division by zero" in result.error

    @pytest.mark.asyncio
    async def test_huge_exponent(self, arithmetic_tool):
        """Test huge exponents are rejected."""
        result = await arithmetic_tool.execute({"expression": "2 ** 10000"})
        assert result.success is False
        assert "Exponent too large" in result.error

    @pytest.mark.asyncio
    async def test_negative_exponent_guard(self, arithmetic_tool):
        """Test large negative exponents are rejected."""
        result = await arithmetic_tool.execute({"expression": "2 ** -10000"})
        assert result.success is False
        assert "Exponent too large" in result.error

    @pytest.mark.asyncio
    async def test_code_injection_attribute_access(self, arithmetic_tool):
        """Test attribute access is rejected (injection attempt)."""
        result = await arithmetic_tool.execute({"expression": "__import__('os')"})
        assert result.success is False
        assert (
            "Disallowed node type" in result.error
            or "Invalid expression" in result.error
        )

    @pytest.mark.asyncio
    async def test_code_injection_function_call(self, arithmetic_tool):
        """Test function calls are rejected (injection attempt)."""
        result = await arithmetic_tool.execute({"expression": "print('hello')"})
        assert result.success is False
        assert (
            "Disallowed node type" in result.error
            or "Invalid expression" in result.error
        )

    @pytest.mark.asyncio
    async def test_code_injection_variable_reference(self, arithmetic_tool):
        """Test variable references are rejected."""
        result = await arithmetic_tool.execute({"expression": "x + 5"})
        assert result.success is False
        assert "Disallowed node type" in result.error

    @pytest.mark.asyncio
    async def test_empty_expression(self, arithmetic_tool):
        """Test empty expression is rejected."""
        result = await arithmetic_tool.execute({"expression": ""})
        assert result.success is False
        assert "Expression cannot be empty" in result.error

    @pytest.mark.asyncio
    async def test_invalid_syntax(self, arithmetic_tool):
        """Test invalid syntax is rejected."""
        result = await arithmetic_tool.execute({"expression": "2 + * 3"})
        assert result.success is False
        assert "Invalid expression syntax" in result.error

    @pytest.mark.asyncio
    async def test_missing_expression_param(self, arithmetic_tool):
        """Test missing expression parameter."""
        result = await arithmetic_tool.execute({})
        assert result.success is False
        assert "Invalid input" in result.error
        assert "expression" in result.error

    @pytest.mark.asyncio
    async def test_whitespace_handling(self, arithmetic_tool):
        """Test expressions with whitespace are handled correctly."""
        result = await arithmetic_tool.execute({"expression": "  2   +   3  "})
        assert result.success is True
        assert result.value["result"] == 5.0

    @pytest.mark.asyncio
    async def test_floating_point_precision(self, arithmetic_tool):
        """Test floating point results are properly rounded."""
        result = await arithmetic_tool.execute({"expression": "1 / 3"})
        assert result.success is True
        # Result should be rounded to 10 decimal places
        assert abs(result.value["result"] - 0.3333333333) < 1e-9

    @pytest.mark.asyncio
    async def test_tool_properties(self, arithmetic_tool):
        """Test tool metadata properties."""
        assert arithmetic_tool.name == "arithmetic"
        assert len(arithmetic_tool.description) > 0
        assert arithmetic_tool.params_model == ArithmeticParams
