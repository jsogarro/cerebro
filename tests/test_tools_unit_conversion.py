"""Unit tests for unit conversion tool."""

import pytest

from src.agents.tools.unit_conversion_tool import (
    UnitConversionParams,
    UnitConversionTool,
)


@pytest.fixture
def unit_conversion_tool():
    """Create a unit conversion tool instance."""
    return UnitConversionTool()


class TestUnitConversionTool:
    """Test suite for UnitConversionTool."""

    # Length conversions
    @pytest.mark.asyncio
    async def test_meters_to_kilometers(self, unit_conversion_tool):
        """Test meters to kilometers conversion."""
        result = await unit_conversion_tool.execute(
            {"value": 1000, "from_unit": "m", "to_unit": "km"}
        )
        assert result.success is True
        assert result.value["result"] == 1.0

    @pytest.mark.asyncio
    async def test_feet_to_meters(self, unit_conversion_tool):
        """Test feet to meters conversion."""
        result = await unit_conversion_tool.execute(
            {"value": 10, "from_unit": "ft", "to_unit": "m"}
        )
        assert result.success is True
        assert abs(result.value["result"] - 3.048) < 0.001

    @pytest.mark.asyncio
    async def test_miles_to_kilometers(self, unit_conversion_tool):
        """Test miles to kilometers conversion."""
        result = await unit_conversion_tool.execute(
            {"value": 1, "from_unit": "mi", "to_unit": "km"}
        )
        assert result.success is True
        assert abs(result.value["result"] - 1.609344) < 0.001

    # Mass conversions
    @pytest.mark.asyncio
    async def test_kilograms_to_grams(self, unit_conversion_tool):
        """Test kilograms to grams conversion."""
        result = await unit_conversion_tool.execute(
            {"value": 1, "from_unit": "kg", "to_unit": "g"}
        )
        assert result.success is True
        assert result.value["result"] == 1000.0

    @pytest.mark.asyncio
    async def test_pounds_to_kilograms(self, unit_conversion_tool):
        """Test pounds to kilograms conversion."""
        result = await unit_conversion_tool.execute(
            {"value": 1, "from_unit": "lb", "to_unit": "kg"}
        )
        assert result.success is True
        assert abs(result.value["result"] - 0.453592) < 0.001

    # Data size conversions
    @pytest.mark.asyncio
    async def test_bytes_to_kilobytes(self, unit_conversion_tool):
        """Test bytes to kilobytes conversion."""
        result = await unit_conversion_tool.execute(
            {"value": 1024, "from_unit": "B", "to_unit": "KB"}
        )
        assert result.success is True
        assert result.value["result"] == 1.0

    @pytest.mark.asyncio
    async def test_gigabytes_to_megabytes(self, unit_conversion_tool):
        """Test gigabytes to megabytes conversion."""
        result = await unit_conversion_tool.execute(
            {"value": 1, "from_unit": "GB", "to_unit": "MB"}
        )
        assert result.success is True
        assert result.value["result"] == 1024.0

    # Temperature conversions
    @pytest.mark.asyncio
    async def test_celsius_to_fahrenheit(self, unit_conversion_tool):
        """Test Celsius to Fahrenheit conversion."""
        result = await unit_conversion_tool.execute(
            {"value": 0, "from_unit": "celsius", "to_unit": "fahrenheit"}
        )
        assert result.success is True
        assert result.value["result"] == 32.0

    @pytest.mark.asyncio
    async def test_fahrenheit_to_celsius(self, unit_conversion_tool):
        """Test Fahrenheit to Celsius conversion."""
        result = await unit_conversion_tool.execute(
            {"value": 32, "from_unit": "fahrenheit", "to_unit": "celsius"}
        )
        assert result.success is True
        assert abs(result.value["result"] - 0.0) < 0.001

    @pytest.mark.asyncio
    async def test_celsius_to_kelvin(self, unit_conversion_tool):
        """Test Celsius to Kelvin conversion."""
        result = await unit_conversion_tool.execute(
            {"value": 0, "from_unit": "celsius", "to_unit": "kelvin"}
        )
        assert result.success is True
        assert abs(result.value["result"] - 273.15) < 0.01

    @pytest.mark.asyncio
    async def test_kelvin_to_celsius(self, unit_conversion_tool):
        """Test Kelvin to Celsius conversion."""
        result = await unit_conversion_tool.execute(
            {"value": 273.15, "from_unit": "kelvin", "to_unit": "celsius"}
        )
        assert result.success is True
        assert abs(result.value["result"] - 0.0) < 0.001

    # Error cases
    @pytest.mark.asyncio
    async def test_unknown_unit(self, unit_conversion_tool):
        """Test unknown unit is rejected."""
        result = await unit_conversion_tool.execute(
            {"value": 10, "from_unit": "unknown", "to_unit": "kg"}
        )
        assert result.success is False
        assert "Unknown unit" in result.error

    @pytest.mark.asyncio
    async def test_incompatible_units(self, unit_conversion_tool):
        """Test incompatible unit conversion is rejected."""
        result = await unit_conversion_tool.execute(
            {"value": 10, "from_unit": "m", "to_unit": "kg"}
        )
        assert result.success is False
        assert "Cannot convert" in result.error

    @pytest.mark.asyncio
    async def test_temperature_to_length(self, unit_conversion_tool):
        """Test temperature to length conversion is rejected."""
        result = await unit_conversion_tool.execute(
            {"value": 100, "from_unit": "celsius", "to_unit": "m"}
        )
        assert result.success is False
        assert "Cannot convert temperature" in result.error

    @pytest.mark.asyncio
    async def test_missing_value(self, unit_conversion_tool):
        """Test missing value parameter."""
        result = await unit_conversion_tool.execute({"from_unit": "m", "to_unit": "km"})
        assert result.success is False
        assert "Invalid input" in result.error

    @pytest.mark.asyncio
    async def test_missing_from_unit(self, unit_conversion_tool):
        """Test missing from_unit parameter."""
        result = await unit_conversion_tool.execute({"value": 10, "to_unit": "km"})
        assert result.success is False
        assert "Invalid input" in result.error

    @pytest.mark.asyncio
    async def test_case_insensitive_units(self, unit_conversion_tool):
        """Test unit names are case-insensitive."""
        result = await unit_conversion_tool.execute(
            {"value": 1000, "from_unit": "M", "to_unit": "KM"}
        )
        assert result.success is True
        assert result.value["result"] == 1.0

    @pytest.mark.asyncio
    async def test_tool_properties(self, unit_conversion_tool):
        """Test tool metadata properties."""
        assert unit_conversion_tool.name == "unit_conversion"
        assert len(unit_conversion_tool.description) > 0
        assert unit_conversion_tool.params_model == UnitConversionParams
