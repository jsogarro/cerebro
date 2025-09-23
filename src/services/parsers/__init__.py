"""
Response parsing utilities for Gemini service.

This module provides parsers for different response formats from the Gemini API.
"""

from src.services.parsers.citation_parser import (
    extract_doi,
    parse_citation,
    validate_citation,
)
from src.services.parsers.json_parser import (
    extract_nested,
    parse_json_response,
    validate_schema,
)
from src.services.parsers.response_sanitizer import (
    detect_language,
    remove_personal_info,
    sanitize_html,
    validate_response_length,
)
from src.services.parsers.text_parser import (
    extract_bullet_points,
    extract_entities,
    extract_key_value_pairs,
    extract_numbered_list,
    parse_markdown_sections,
)

__all__ = [
    # JSON parsing
    "parse_json_response",
    "validate_schema",
    "extract_nested",
    # Text parsing
    "parse_markdown_sections",
    "extract_bullet_points",
    "extract_numbered_list",
    "extract_key_value_pairs",
    "extract_entities",
    # Citation parsing
    "parse_citation",
    "extract_doi",
    "validate_citation",
    # Response sanitization
    "sanitize_html",
    "remove_personal_info",
    "validate_response_length",
    "detect_language",
]
