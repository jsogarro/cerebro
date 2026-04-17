from typing import Any


def coerce_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return default


def coerce_int(value: Any, default: int = 0) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    return default
