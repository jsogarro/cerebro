"""
Centralized serialization utilities using orjson for performance.

This module provides high-performance JSON serialization/deserialization
using orjson, with proper handling of Pydantic models, datetimes, and other
common Python types.
"""

from datetime import date, datetime
from typing import Any, Optional, Union
from uuid import UUID

import orjson
from pydantic import BaseModel


def _default_serializer(obj: Any) -> Any:
    """
    Custom serializer for types not natively supported by orjson.

    Handles:
    - Pydantic models
    - UUID objects
    - datetime/date objects
    - Sets

    Args:
        obj: Object to serialize

    Returns:
        Serializable representation

    Raises:
        TypeError: If object type is not supported
    """
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    elif isinstance(obj, UUID):
        return str(obj)
    elif isinstance(obj, (datetime, date)):
        return obj.isoformat()
    elif isinstance(obj, set):
        return list(obj)
    raise TypeError(f"Type {type(obj)} not serializable")


def serialize(data: Any) -> bytes:
    """
    Serialize data to JSON bytes using orjson.

    This is the fastest serialization method, returning bytes.
    Use for internal APIs, message queues, or when bytes are acceptable.

    Args:
        data: Python object to serialize

    Returns:
        JSON as bytes

    Example:
        >>> serialize({"key": "value"})
        b'{"key":"value"}'
    """
    return orjson.dumps(
        data,
        default=_default_serializer,
        option=orjson.OPT_NON_STR_KEYS | orjson.OPT_SERIALIZE_NUMPY,
    )


def deserialize(data: Union[bytes, str]) -> Any:
    """
    Deserialize JSON bytes or string to Python object using orjson.

    Args:
        data: JSON bytes or string

    Returns:
        Deserialized Python object

    Example:
        >>> deserialize(b'{"key":"value"}')
        {'key': 'value'}
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    return orjson.loads(data)


def serialize_to_str(data: Any) -> str:
    """
    Serialize data to JSON string using orjson.

    Use for APIs or protocols that require strings (like WebSocket messages).

    Args:
        data: Python object to serialize

    Returns:
        JSON as string

    Example:
        >>> serialize_to_str({"key": "value"})
        '{"key":"value"}'
    """
    return serialize(data).decode("utf-8")


def serialize_for_cache(data: Any) -> bytes:
    """
    Serialize data for Redis caching using orjson.

    Optimized for cache storage with compact output.

    Args:
        data: Python object to serialize

    Returns:
        JSON as bytes (compact format)

    Example:
        >>> serialize_for_cache({"user_id": 123, "name": "Alice"})
        b'{"user_id":123,"name":"Alice"}'
    """
    return orjson.dumps(
        data,
        default=_default_serializer,
        option=orjson.OPT_NON_STR_KEYS | orjson.OPT_SERIALIZE_NUMPY,
    )


def deserialize_from_cache(data: Union[bytes, str, None]) -> Any:
    """
    Deserialize data from Redis cache using orjson.

    Handles None values gracefully for cache misses.

    Args:
        data: JSON bytes, string, or None

    Returns:
        Deserialized Python object or None

    Example:
        >>> deserialize_from_cache(b'{"user_id":123}')
        {'user_id': 123}
        >>> deserialize_from_cache(None)
        None
    """
    if data is None:
        return None
    return deserialize(data)
