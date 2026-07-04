"""Unit conversion tool.

Deterministic, table-driven conversions for common unit types. No external
libraries or APIs — just multiplication/division by conversion factors.
"""

from typing import Any

from pydantic import BaseModel, Field

from src.agents.tools.base_tool import AgentTool


class UnitConversionParams(BaseModel):
    """Parameters for unit conversion."""

    value: float = Field(..., description="Numeric value to convert")
    from_unit: str = Field(..., description="Source unit")
    to_unit: str = Field(..., description="Target unit")


class UnitConversionTool(AgentTool[UnitConversionParams]):
    """Deterministic unit conversion for common types.

    Supports: length, mass, temperature, data size.
    """

    @property
    def name(self) -> str:
        return "unit_conversion"

    @property
    def description(self) -> str:
        return "Convert between common units (length, mass, temperature, data)"

    @property
    def params_model(self) -> type[UnitConversionParams]:
        return UnitConversionParams

    # Conversion tables: {unit: multiplier_to_base_unit}
    # Base units: meter, kilogram, celsius, byte

    _LENGTH = {
        "m": 1.0,
        "meter": 1.0,
        "meters": 1.0,
        "km": 1000.0,
        "kilometer": 1000.0,
        "kilometers": 1000.0,
        "cm": 0.01,
        "centimeter": 0.01,
        "centimeters": 0.01,
        "mm": 0.001,
        "millimeter": 0.001,
        "millimeters": 0.001,
        "ft": 0.3048,
        "foot": 0.3048,
        "feet": 0.3048,
        "in": 0.0254,
        "inch": 0.0254,
        "inches": 0.0254,
        "mi": 1609.344,
        "mile": 1609.344,
        "miles": 1609.344,
    }

    _MASS = {
        "kg": 1.0,
        "kilogram": 1.0,
        "kilograms": 1.0,
        "g": 0.001,
        "gram": 0.001,
        "grams": 0.001,
        "mg": 0.000001,
        "milligram": 0.000001,
        "milligrams": 0.000001,
        "lb": 0.453592,
        "pound": 0.453592,
        "pounds": 0.453592,
        "oz": 0.0283495,
        "ounce": 0.0283495,
        "ounces": 0.0283495,
    }

    _DATA = {
        "b": 1.0,
        "byte": 1.0,
        "bytes": 1.0,
        "kb": 1024.0,
        "kilobyte": 1024.0,
        "kilobytes": 1024.0,
        "mb": 1024.0**2,
        "megabyte": 1024.0**2,
        "megabytes": 1024.0**2,
        "gb": 1024.0**3,
        "gigabyte": 1024.0**3,
        "gigabytes": 1024.0**3,
        "tb": 1024.0**4,
        "terabyte": 1024.0**4,
        "terabytes": 1024.0**4,
    }

    # Temperature requires special handling (not multiplicative)
    _TEMPERATURE_UNITS = {"celsius", "c", "fahrenheit", "f", "kelvin", "k"}

    def _convert_temperature(self, value: float, from_unit: str, to_unit: str) -> float:
        """Convert between temperature scales."""
        from_u = from_unit.lower()
        to_u = to_unit.lower()

        # Normalize to Celsius first
        if from_u in ("fahrenheit", "f"):
            celsius = (value - 32) * 5 / 9
        elif from_u in ("kelvin", "k"):
            celsius = value - 273.15
        elif from_u in ("celsius", "c"):
            celsius = value
        else:
            raise ValueError(f"Unknown temperature unit: {from_unit}")

        # Convert from Celsius to target
        if to_u in ("fahrenheit", "f"):
            return celsius * 9 / 5 + 32
        elif to_u in ("kelvin", "k"):
            return celsius + 273.15
        elif to_u in ("celsius", "c"):
            return celsius
        else:
            raise ValueError(f"Unknown temperature unit: {to_unit}")

    def _find_conversion_table(self, unit: str) -> dict[str, float] | None:
        """Find which conversion table a unit belongs to."""
        unit_lower = unit.lower()
        if unit_lower in self._LENGTH:
            return self._LENGTH
        elif unit_lower in self._MASS:
            return self._MASS
        elif unit_lower in self._DATA:
            return self._DATA
        elif unit_lower in self._TEMPERATURE_UNITS:
            return None  # Special handling
        return None

    async def _execute_impl(self, params: UnitConversionParams) -> Any:
        """Convert value from one unit to another."""
        from_unit = params.from_unit.lower()
        to_unit = params.to_unit.lower()

        # Handle temperature conversions separately
        if from_unit in self._TEMPERATURE_UNITS:
            if to_unit not in self._TEMPERATURE_UNITS:
                raise ValueError(
                    f"Cannot convert temperature ({params.from_unit}) "
                    f"to non-temperature unit ({params.to_unit})"
                )
            result = self._convert_temperature(params.value, from_unit, to_unit)
            return {
                "value": params.value,
                "from_unit": params.from_unit,
                "to_unit": params.to_unit,
                "result": round(result, 6),
            }

        # Find conversion table
        table = self._find_conversion_table(from_unit)
        if table is None:
            raise ValueError(f"Unknown unit: {params.from_unit}")

        # Check if target unit is in the same table
        if to_unit not in table:
            raise ValueError(
                f"Cannot convert {params.from_unit} to {params.to_unit} "
                "(different unit types)"
            )

        # Convert: value * from_multiplier / to_multiplier
        result = params.value * table[from_unit] / table[to_unit]

        return {
            "value": params.value,
            "from_unit": params.from_unit,
            "to_unit": params.to_unit,
            "result": round(result, 6),
        }


__all__ = ["UnitConversionParams", "UnitConversionTool"]
