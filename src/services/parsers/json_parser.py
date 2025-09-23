"""
JSON response parsing utilities.

This module provides functions for parsing and validating JSON responses
from the Gemini API, following functional programming principles.
"""

import json
import re
from typing import Any, Union


def parse_json_response(response: str) -> dict[str, Any]:
    """
    Parse JSON from Gemini response.

    Handles various formats including:
    - Direct JSON strings
    - JSON embedded in markdown code blocks
    - JSON with surrounding text

    Args:
        response: Raw response string from Gemini

    Returns:
        Parsed JSON as dictionary

    Raises:
        ValueError: If no valid JSON can be extracted
    """
    # Try direct JSON parse first
    try:
        return json.loads(response.strip())
    except json.JSONDecodeError:
        pass

    # Try to extract JSON from markdown code block
    json_pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
    matches = re.findall(json_pattern, response)

    for match in matches:
        try:
            return json.loads(match.strip())
        except json.JSONDecodeError:
            continue

    # Try to find JSON-like structure in the text
    # Look for content between curly braces
    brace_pattern = r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}"
    brace_matches = re.findall(brace_pattern, response)

    for match in brace_matches:
        try:
            return json.loads(match)
        except json.JSONDecodeError:
            continue

    # Try to find JSON array
    array_pattern = r"\[[^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*\]"
    array_matches = re.findall(array_pattern, response)

    for match in array_matches:
        try:
            result = json.loads(match)
            # Wrap array in object if needed
            if isinstance(result, list):
                return {"items": result}
            return result
        except json.JSONDecodeError:
            continue

    raise ValueError("Invalid JSON: Could not extract valid JSON from response")


def validate_schema(data: dict[str, Any], schema: dict[str, type]) -> bool:
    """
    Validate data against a simple type schema.

    Pure function that checks if data matches expected types.

    Args:
        data: Data to validate
        schema: Dictionary mapping keys to expected types

    Returns:
        True if data matches schema, False otherwise
    """
    if not isinstance(data, dict):
        return False

    for key, expected_type in schema.items():
        if key not in data:
            return False

        value = data[key]

        # Handle Union types
        if hasattr(expected_type, "__origin__") and expected_type.__origin__ is Union:
            type_args = expected_type.__args__
            if not any(isinstance(value, t) for t in type_args):
                return False
        # Handle List types
        elif expected_type is list or (
            hasattr(expected_type, "__origin__") and expected_type.__origin__ is list
        ):
            if not isinstance(value, list):
                return False
        # Handle Dict types
        elif expected_type is dict or (
            hasattr(expected_type, "__origin__") and expected_type.__origin__ is dict
        ):
            if not isinstance(value, dict):
                return False
        # Handle regular types
        elif not isinstance(value, expected_type):
            return False

    return True


def extract_nested(
    data: dict[str, Any], path: str, default: Any | None = None, separator: str = "."
) -> Any:
    """
    Extract nested value from dictionary using dot notation.

    Pure function for safe nested dictionary access.

    Args:
        data: Dictionary to extract from
        path: Dot-separated path (e.g., "level1.level2.key")
        default: Default value if path not found
        separator: Path separator (default: ".")

    Returns:
        Value at path or default if not found
    """
    if not data or not path:
        return default

    keys = path.split(separator)
    current = data

    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        elif isinstance(current, list):
            # Try to parse as index
            try:
                index = int(key)
                if 0 <= index < len(current):
                    current = current[index]
                else:
                    return default
            except (ValueError, IndexError):
                return default
        else:
            return default

    return current


def clean_json_string(json_str: str) -> str:
    """
    Clean common issues in JSON strings.

    Handles:
    - Trailing commas
    - Single quotes (converts to double)
    - Unquoted keys (adds quotes)
    - Comments (removes them)

    Args:
        json_str: Potentially malformed JSON string

    Returns:
        Cleaned JSON string
    """
    # Remove comments
    json_str = re.sub(r"//.*?$", "", json_str, flags=re.MULTILINE)
    json_str = re.sub(r"/\*.*?\*/", "", json_str, flags=re.DOTALL)

    # Remove trailing commas
    json_str = re.sub(r",\s*}", "}", json_str)
    json_str = re.sub(r",\s*\]", "]", json_str)

    # Convert single quotes to double quotes (careful with escaped quotes)
    # This is a simplified approach
    json_str = re.sub(r"(?<!\\)'", '"', json_str)

    # Add quotes to unquoted keys (simplified - may not handle all cases)
    json_str = re.sub(r"(\w+):", r'"\1":', json_str)

    return json_str


def merge_json_objects(obj1: dict[str, Any], obj2: dict[str, Any]) -> dict[str, Any]:
    """
    Deep merge two JSON objects.

    Pure function that merges obj2 into obj1 recursively.

    Args:
        obj1: Base object
        obj2: Object to merge in

    Returns:
        Merged object (new object, doesn't modify inputs)
    """
    result = obj1.copy()

    for key, value in obj2.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_json_objects(result[key], value)
        elif (
            key in result and isinstance(result[key], list) and isinstance(value, list)
        ):
            result[key] = result[key] + value
        else:
            result[key] = value

    return result


def flatten_json(
    data: dict[str, Any], parent_key: str = "", separator: str = "."
) -> dict[str, Any]:
    """
    Flatten nested JSON structure.

    Pure function that converts nested dict to flat dict with dot notation keys.

    Args:
        data: Nested dictionary
        parent_key: Prefix for keys
        separator: Key separator

    Returns:
        Flattened dictionary
    """
    items: list[tuple] = []

    for key, value in data.items():
        new_key = f"{parent_key}{separator}{key}" if parent_key else key

        if isinstance(value, dict):
            items.extend(flatten_json(value, new_key, separator).items())
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    items.extend(
                        flatten_json(item, f"{new_key}[{i}]", separator).items()
                    )
                else:
                    items.append((f"{new_key}[{i}]", item))
        else:
            items.append((new_key, value))

    return dict(items)
