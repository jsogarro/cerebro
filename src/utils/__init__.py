"""
Utility modules for the Cerebro research platform.

This package contains shared utility functions and helpers used across the application.
"""

from src.utils.async_helpers import BackgroundTaskTracker
from src.utils.serialization import (
    deserialize,
    deserialize_from_cache,
    serialize,
    serialize_for_cache,
    serialize_to_str,
)
from src.utils.type_coercion import coerce_float, coerce_int

__all__ = [
    "BackgroundTaskTracker",
    "coerce_float",
    "coerce_int",
    "deserialize",
    "deserialize_from_cache",
    "serialize",
    "serialize_for_cache",
    "serialize_to_str",
]
