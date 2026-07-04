"""Unit tests for datetime tool."""

from datetime import datetime

import pytest

from src.agents.tools.datetime_tool import DatetimeParams, DatetimeTool


@pytest.fixture
def datetime_tool():
    """Create a datetime tool instance."""
    return DatetimeTool()


class TestDatetimeTool:
    """Test suite for DatetimeTool."""

    @pytest.mark.asyncio
    async def test_current_datetime(self, datetime_tool):
        """Test current datetime operation."""
        result = await datetime_tool.execute({"operation": "current"})
        assert result.success is True
        assert "current_datetime_utc" in result.value
        assert "date" in result.value
        assert "time" in result.value
        assert "day_of_week" in result.value
        assert "timestamp" in result.value

        # Verify UTC timezone
        dt = datetime.fromisoformat(result.value["current_datetime_utc"])
        assert dt.tzinfo is not None

    @pytest.mark.asyncio
    async def test_day_of_week(self, datetime_tool):
        """Test day of week operation."""
        # 2024-01-15 is a Monday
        result = await datetime_tool.execute(
            {"operation": "day_of_week", "date_iso": "2024-01-15"},
        )
        assert result.success is True
        assert result.value["day_of_week"] == "Monday"
        assert result.value["day_number"] == 1

    @pytest.mark.asyncio
    async def test_add_days_positive(self, datetime_tool):
        """Test adding positive days."""
        result = await datetime_tool.execute(
            {"operation": "add_days", "date_iso": "2024-01-15", "days": 10},
        )
        assert result.success is True
        assert result.value["result_date"] == "2024-01-25"
        assert result.value["days_added"] == 10

    @pytest.mark.asyncio
    async def test_add_days_negative(self, datetime_tool):
        """Test adding negative days (subtraction)."""
        result = await datetime_tool.execute(
            {"operation": "add_days", "date_iso": "2024-01-15", "days": -5},
        )
        assert result.success is True
        assert result.value["result_date"] == "2024-01-10"
        assert result.value["days_added"] == -5

    @pytest.mark.asyncio
    async def test_diff_days(self, datetime_tool):
        """Test day difference calculation."""
        result = await datetime_tool.execute(
            {
                "operation": "diff_days",
                "date_iso": "2024-01-15",
                "other_date_iso": "2024-01-25",
            },
        )
        assert result.success is True
        assert result.value["difference_days"] == 10

    @pytest.mark.asyncio
    async def test_diff_days_reverse(self, datetime_tool):
        """Test day difference with reversed dates (negative result)."""
        result = await datetime_tool.execute(
            {
                "operation": "diff_days",
                "date_iso": "2024-01-25",
                "other_date_iso": "2024-01-15",
            },
        )
        assert result.success is True
        assert result.value["difference_days"] == -10

    @pytest.mark.asyncio
    async def test_date_with_timezone(self, datetime_tool):
        """Test date parsing with explicit timezone."""
        result = await datetime_tool.execute(
            {"operation": "day_of_week", "date_iso": "2024-01-15T10:00:00+00:00"},
        )
        assert result.success is True
        assert result.value["day_of_week"] == "Monday"

    @pytest.mark.asyncio
    async def test_unknown_operation(self, datetime_tool):
        """Test unknown operation is rejected."""
        result = await datetime_tool.execute({"operation": "invalid_op"})
        assert result.success is False
        assert "Unknown operation" in result.error

    @pytest.mark.asyncio
    async def test_day_of_week_missing_date(self, datetime_tool):
        """Test day_of_week without date parameter."""
        result = await datetime_tool.execute({"operation": "day_of_week"})
        assert result.success is False
        assert "date_iso is required" in result.error

    @pytest.mark.asyncio
    async def test_add_days_missing_days(self, datetime_tool):
        """Test add_days without days parameter."""
        result = await datetime_tool.execute(
            {"operation": "add_days", "date_iso": "2024-01-15"},
        )
        assert result.success is False
        assert "days are required" in result.error

    @pytest.mark.asyncio
    async def test_diff_days_missing_other_date(self, datetime_tool):
        """Test diff_days without other_date parameter."""
        result = await datetime_tool.execute(
            {"operation": "diff_days", "date_iso": "2024-01-15"},
        )
        assert result.success is False
        assert "other_date_iso are required" in result.error

    @pytest.mark.asyncio
    async def test_invalid_date_format(self, datetime_tool):
        """Test invalid date format is rejected."""
        result = await datetime_tool.execute(
            {"operation": "day_of_week", "date_iso": "not-a-date"},
        )
        assert result.success is False
        assert "Execution error" in result.error

    @pytest.mark.asyncio
    async def test_missing_operation_param(self, datetime_tool):
        """Test missing operation parameter."""
        result = await datetime_tool.execute({})
        assert result.success is False
        assert "Invalid input" in result.error
        assert "operation" in result.error

    @pytest.mark.asyncio
    async def test_tool_properties(self, datetime_tool):
        """Test tool metadata properties."""
        assert datetime_tool.name == "datetime_info"
        assert len(datetime_tool.description) > 0
        assert datetime_tool.params_model == DatetimeParams
