"""Datetime information and arithmetic tool.

Pure datetime operations with UTC awareness. No timezone database access,
no network time sync — deterministic operations on system time.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from src.agents.tools.base_tool import AgentTool


class DatetimeParams(BaseModel):
    """Parameters for datetime operations."""

    operation: str = Field(
        ...,
        description="Operation: 'current', 'day_of_week', 'add_days', 'diff_days'",
    )
    # For add_days/diff_days
    date_iso: str | None = Field(None, description="ISO 8601 date (e.g., '2024-01-15')")
    days: int | None = Field(None, description="Number of days to add")
    other_date_iso: str | None = Field(
        None, description="Second date for diff_days operation"
    )


class DatetimeTool(AgentTool[DatetimeParams]):
    """Datetime information and arithmetic operations (UTC-aware)."""

    @property
    def name(self) -> str:
        return "datetime_info"

    @property
    def description(self) -> str:
        return "Current datetime, day-of-week, date arithmetic (UTC)"

    @property
    def params_model(self) -> type[DatetimeParams]:
        return DatetimeParams

    def _parse_date(self, date_str: str) -> datetime:
        """Parse ISO 8601 date string to UTC datetime."""
        try:
            # Try datetime with timezone
            dt = datetime.fromisoformat(date_str)
            # Convert to UTC if aware, assume UTC if naive
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except ValueError:
            # Try date-only format
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return dt.replace(tzinfo=UTC)

    async def _execute_impl(self, params: DatetimeParams) -> Any:
        """Execute datetime operation."""
        operation = params.operation.lower()

        if operation == "current":
            now = datetime.now(UTC)
            return {
                "current_datetime_utc": now.isoformat(),
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M:%S"),
                "day_of_week": now.strftime("%A"),
                "timestamp": now.timestamp(),
            }

        elif operation == "day_of_week":
            if not params.date_iso:
                raise ValueError("date_iso is required for day_of_week operation")
            dt = self._parse_date(params.date_iso)
            return {
                "date": params.date_iso,
                "day_of_week": dt.strftime("%A"),
                "day_number": dt.isoweekday(),  # 1=Monday, 7=Sunday
            }

        elif operation == "add_days":
            if not params.date_iso or params.days is None:
                raise ValueError(
                    "date_iso and days are required for add_days operation"
                )
            dt = self._parse_date(params.date_iso)
            new_dt = dt + timedelta(days=params.days)
            return {
                "original_date": params.date_iso,
                "days_added": params.days,
                "result_date": new_dt.strftime("%Y-%m-%d"),
                "result_datetime_utc": new_dt.isoformat(),
            }

        elif operation == "diff_days":
            if not params.date_iso or not params.other_date_iso:
                raise ValueError(
                    "date_iso and other_date_iso are required for diff_days operation"
                )
            dt1 = self._parse_date(params.date_iso)
            dt2 = self._parse_date(params.other_date_iso)
            diff = dt2 - dt1
            return {
                "start_date": params.date_iso,
                "end_date": params.other_date_iso,
                "difference_days": diff.days,
                "difference_seconds": diff.total_seconds(),
            }

        else:
            raise ValueError(
                f"Unknown operation '{operation}'. "
                "Supported: current, day_of_week, add_days, diff_days"
            )


__all__ = ["DatetimeParams", "DatetimeTool"]
