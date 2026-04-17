from typing import Any


def coerce_float(
    value: Any,
    default: float = 0.0,
    min_val: float | None = None,
    max_val: float | None = None,
) -> float:
    result: float
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        try:
            result = float(value)
        except (ValueError, OverflowError):
            return default
    else:
        return default
    if min_val is not None and result < min_val:
        return default
    if max_val is not None and result > max_val:
        return default
    return result


def coerce_int(
    value: Any,
    default: int = 0,
    min_val: int | None = None,
    max_val: int | None = None,
) -> int:
    result: int
    if isinstance(value, (int, float)):
        result = int(value)
    elif isinstance(value, str):
        try:
            result = int(float(value))
        except (ValueError, OverflowError):
            return default
    else:
        return default
    if min_val is not None and result < min_val:
        return default
    if max_val is not None and result > max_val:
        return default
    return result
