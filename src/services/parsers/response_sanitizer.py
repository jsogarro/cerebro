"""
Response sanitization utilities.

This module provides functions for sanitizing and validating
responses from the Gemini API.
"""

import re


def sanitize_html(text: str) -> str:
    """
    Remove HTML tags from text.

    Args:
        text: Text potentially containing HTML

    Returns:
        Sanitized text without HTML tags
    """
    # Remove script and style tags with content
    text = re.sub(
        r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)

    # Remove all HTML tags
    text = re.sub(r"<[^>]+>", "", text)

    # Decode HTML entities
    html_entities = {
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&quot;": '"',
        "&#39;": "'",
        "&nbsp;": " ",
    }
    for entity, char in html_entities.items():
        text = text.replace(entity, char)

    return text.strip()


def remove_personal_info(text: str) -> str:
    """
    Remove potential personal information from text.

    Args:
        text: Text potentially containing personal info

    Returns:
        Text with personal info redacted
    """
    # Email addresses
    text = re.sub(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "[EMAIL]", text
    )

    # Phone numbers (various formats)
    phone_patterns = [
        r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b",  # 123-456-7890
        r"\b\(\d{3}\)\s*\d{3}[-.]?\d{4}\b",  # (123) 456-7890
        r"\b\+\d{1,3}\s*\d{1,14}\b",  # International
    ]
    for pattern in phone_patterns:
        text = re.sub(pattern, "[PHONE]", text)

    # SSN-like patterns
    text = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[SSN]", text)

    # Credit card-like patterns (basic)
    text = re.sub(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b", "[CARD]", text)

    # IP addresses
    text = re.sub(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "[IP]", text)

    return text


def validate_response_length(text: str, max_length: int = 10000) -> str:
    """
    Validate and truncate response if too long.

    Args:
        text: Response text
        max_length: Maximum allowed length

    Returns:
        Validated/truncated text
    """
    if len(text) <= max_length:
        return text

    # Truncate and add indicator
    return text[: max_length - 3] + "..."


def detect_language(text: str) -> str:
    """
    Detect the language of text (simplified).

    Args:
        text: Text to analyze

    Returns:
        Detected language code
    """
    # Very simplified language detection based on character patterns
    # In production, use a proper library like langdetect

    # Check for Chinese characters
    if re.search(r"[\u4e00-\u9fff]", text):
        return "zh"

    # Check for Japanese characters
    if re.search(r"[\u3040-\u309f\u30a0-\u30ff]", text):
        return "ja"

    # Check for Korean characters
    if re.search(r"[\uac00-\ud7af]", text):
        return "ko"

    # Check for Arabic characters
    if re.search(r"[\u0600-\u06ff]", text):
        return "ar"

    # Check for Cyrillic characters
    if re.search(r"[\u0400-\u04ff]", text):
        return "ru"

    # Check for common non-English European language patterns
    if re.search(r"[àâäáåãæçèéêëíîïñóôöøùúûüý]", text, re.IGNORECASE):
        # Could be French, Spanish, German, etc.
        if " et " in text.lower() or " le " in text.lower() or " la " in text.lower():
            return "fr"
        elif " y " in text.lower() or " el " in text.lower() or " la " in text.lower():
            return "es"
        elif (
            " und " in text.lower()
            or " der " in text.lower()
            or " die " in text.lower()
        ):
            return "de"

    # Default to English
    return "en"


def validate_citation(citation_data: dict[str, str]) -> bool:
    """
    Validate that citation has required fields.

    Args:
        citation_data: Citation components dictionary

    Returns:
        True if citation has minimum required fields
    """
    # Minimum required fields
    required_fields = ["author", "title", "year"]

    for field in required_fields:
        if field not in citation_data or not citation_data[field]:
            return False

    # Validate year format
    if "year" in citation_data:
        year = citation_data["year"]
        if not re.match(r"^\d{4}$", str(year)):
            return False

    return True
