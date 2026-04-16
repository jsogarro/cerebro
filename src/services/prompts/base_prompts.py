"""
Base prompt utilities and templates.

This module provides pure functions for prompt composition and manipulation.
"""

import re
from typing import Any


def substitute_template(template: str, variables: dict[str, str]) -> str:
    """
    Substitute variables in a template string.

    Pure function that replaces {variable} patterns with values.

    Args:
        template: Template string with {variable} placeholders
        variables: Dictionary of variable values

    Returns:
        Template with variables substituted
    """
    result = template
    for key, value in variables.items():
        result = result.replace(f"{{{key}}}", str(value))
    return result


def compose_prompt(parts: list[str], separator: str = "\n\n") -> str:
    """
    Compose multiple prompt parts into a single prompt.

    Pure function that joins prompt parts with separators.

    Args:
        parts: List of prompt parts to join
        separator: Separator between parts

    Returns:
        Composed prompt string
    """
    return separator.join(part.strip() for part in parts if part.strip())


def validate_prompt_length(prompt: str, max_tokens: int = 4000) -> bool:
    """
    Validate prompt length against token limits.

    Pure function that estimates token count and validates.

    Args:
        prompt: Prompt text to validate
        max_tokens: Maximum allowed tokens

    Returns:
        True if prompt is within limits
    """
    # Rough estimation: 1 token ≈ 4 characters
    estimated_tokens = len(prompt) / 4
    return estimated_tokens <= max_tokens


def sanitize_prompt(prompt: str) -> str:
    """
    Sanitize prompt for safety.

    Pure function that removes potentially problematic content.

    Args:
        prompt: Raw prompt text

    Returns:
        Sanitized prompt
    """
    # Remove potential prompt injection attempts
    dangerous_patterns = [
        r"ignore\s+previous\s+instructions",
        r"forget\s+everything",
        r"pretend\s+you\s+are",
        r"act\s+as\s+if",
        r"system\s*:",
        r"human\s*:",
        r"assistant\s*:",
    ]

    sanitized = prompt
    for pattern in dangerous_patterns:
        sanitized = re.sub(pattern, "", sanitized, flags=re.IGNORECASE)

    return sanitized.strip()


def add_output_format(prompt: str, schema: dict[str, Any]) -> str:
    """
    Add structured output format specification to prompt.

    Pure function that appends format requirements.

    Args:
        prompt: Base prompt
        schema: Expected output schema

    Returns:
        Prompt with format specification
    """
    format_instruction = f"""

Please provide your response in JSON format following this exact schema:
{_format_schema(schema)}

Ensure your response is valid JSON that can be parsed programmatically.
"""

    return prompt + format_instruction


def _format_schema(schema: dict[str, Any], indent: int = 0) -> str:
    """
    Format schema into readable text.

    Pure function that converts schema dict to string representation.
    """
    lines = []
    prefix = "  " * indent

    lines.append(f"{prefix}{{")

    for key, value_type in schema.items():
        if isinstance(value_type, dict):
            lines.append(f'{prefix}  "{key}": {{')
            lines.append(_format_schema(value_type, indent + 2))
            lines.append(f"{prefix}  }},")
        elif isinstance(value_type, list) and value_type:
            item_type = value_type[0] if value_type else "string"
            lines.append(f'{prefix}  "{key}": ["{item_type}"],')
        else:
            type_name = (
                value_type.__name__
                if hasattr(value_type, "__name__")
                else str(value_type)
            )
            lines.append(f'{prefix}  "{key}": "{type_name}",')

    lines.append(f"{prefix}}}")

    return "\n".join(lines)


def create_system_prompt(role: str, context: str, constraints: list[str] | None = None) -> str:
    """
    Create a system prompt with role and constraints.

    Pure function that builds structured system prompts.

    Args:
        role: AI role description
        context: Context information
        constraints: List of constraints or guidelines

    Returns:
        Formatted system prompt
    """
    parts = [
        f"You are a {role}.",
        f"Context: {context}",
    ]

    if constraints:
        parts.append("Guidelines:")
        for i, constraint in enumerate(constraints, 1):
            parts.append(f"{i}. {constraint}")

    return compose_prompt(parts)


def add_examples(prompt: str, examples: list[dict[str, str]]) -> str:
    """
    Add examples to a prompt for few-shot learning.

    Pure function that appends examples to prompts.

    Args:
        prompt: Base prompt
        examples: List of input/output examples

    Returns:
        Prompt with examples
    """
    if not examples:
        return prompt

    example_parts = [prompt, "Examples:"]

    for i, example in enumerate(examples, 1):
        example_parts.extend(
            [
                f"Example {i}:",
                f"Input: {example.get('input', '')}",
                f"Output: {example.get('output', '')}",
            ]
        )

    example_parts.append("Now, please process the following:")

    return compose_prompt(example_parts)


def create_research_context(
    query: str, domains: list[str], depth: str, scope: dict[str, Any] | None = None
) -> str:
    """
    Create research context from query parameters.

    Pure function that builds research context.

    Args:
        query: Research question
        domains: Research domains
        depth: Research depth level
        scope: Additional scope parameters

    Returns:
        Formatted research context
    """
    parts = [
        f"Research Question: {query}",
        f"Research Domains: {', '.join(domains)}",
        f"Research Depth: {depth}",
    ]

    if scope:
        if scope.get("max_sources"):
            parts.append(f"Maximum Sources: {scope['max_sources']}")
        if scope.get("languages"):
            parts.append(f"Languages: {', '.join(scope['languages'])}")
        if scope.get("time_period_start") or scope.get("time_period_end"):
            start = scope.get("time_period_start", "earliest")
            end = scope.get("time_period_end", "latest")
            parts.append(f"Time Period: {start} to {end}")

    return compose_prompt(parts, separator="\n")
