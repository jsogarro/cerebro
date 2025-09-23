"""
Base parser utilities for response processing.

This module provides fundamental parsing and validation functions
used across different parser types.
"""

import re


def sanitize_html(text: str) -> str:
    """
    Remove HTML tags and clean text.

    Args:
        text: Text potentially containing HTML

    Returns:
        Clean text with HTML removed
    """
    # Remove script and style tags with content
    text = re.sub(
        r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)

    # Convert some tags to markdown equivalents before removing
    text = re.sub(r"<b>(.*?)</b>", r"**\1**", text, flags=re.IGNORECASE)
    text = re.sub(r"<strong>(.*?)</strong>", r"**\1**", text, flags=re.IGNORECASE)
    text = re.sub(r"<i>(.*?)</i>", r"*\1*", text, flags=re.IGNORECASE)
    text = re.sub(r"<em>(.*?)</em>", r"*\1*", text, flags=re.IGNORECASE)

    # Remove remaining HTML tags
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

    # Clean up whitespace
    text = re.sub(r"\s+", " ", text)

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
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "[EMAIL REDACTED]", text
    )

    # Phone numbers (various formats)
    phone_patterns = [
        r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b",  # 123-456-7890 or 123.456.7890 or 1234567890
        r"\b\(\d{3}\)\s*\d{3}[-.]?\d{4}\b",  # (123) 456-7890
        r"\b\+\d{1,3}\s*\d{1,14}\b",  # International
    ]
    for pattern in phone_patterns:
        text = re.sub(pattern, "[PHONE REDACTED]", text)

    # SSN patterns
    text = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[SSN REDACTED]", text)

    # Credit card patterns (basic)
    text = re.sub(
        r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b", "[CARD REDACTED]", text
    )

    # IP addresses
    text = re.sub(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "[IP REDACTED]", text)

    return text


def validate_length(
    text: str, min_length: int | None = None, max_length: int | None = None
) -> bool:
    """
    Validate text length is within bounds.

    Args:
        text: Text to validate
        min_length: Minimum required length
        max_length: Maximum allowed length

    Returns:
        True if text length is valid
    """
    text_length = len(text)

    if min_length is not None and text_length < min_length:
        return False

    if max_length is not None and text_length > max_length:
        return False

    return True


def detect_language(text: str) -> str:
    """
    Detect the language of text (simplified heuristic).

    Args:
        text: Text to analyze

    Returns:
        Language code (e.g., 'en', 'fr', 'mixed')
    """
    # Count language indicators
    languages_detected = []

    # Check for Chinese characters
    if re.search(r"[\u4e00-\u9fff]", text):
        languages_detected.append("zh")

    # Check for Japanese characters
    if re.search(r"[\u3040-\u309f\u30a0-\u30ff]", text):
        languages_detected.append("ja")

    # Check for Korean characters
    if re.search(r"[\uac00-\ud7af]", text):
        languages_detected.append("ko")

    # Check for Arabic characters
    if re.search(r"[\u0600-\u06ff]", text):
        languages_detected.append("ar")

    # Check for Cyrillic characters
    if re.search(r"[\u0400-\u04ff]", text):
        languages_detected.append("ru")

    # Check for French indicators
    french_words = [
        " et ",
        " le ",
        " la ",
        " les ",
        " de ",
        " du ",
        " des ",
        " un ",
        " une ",
    ]
    french_count = sum(1 for word in french_words if word in text.lower())
    if french_count >= 2 or re.search(
        r"[àâäáåãæçèéêëíîïñóôöøùúûüýÀÂÄÁÅÃÆÇÈÉÊËÍÎÏÑÓÔÖØÙÚÛÜÝ]", text
    ):
        if french_count >= 2:
            languages_detected.append("fr")

    # Check for Spanish indicators
    spanish_words = [
        " y ",
        " el ",
        " la ",
        " los ",
        " las ",
        " de ",
        " del ",
        " un ",
        " una ",
    ]
    spanish_count = sum(1 for word in spanish_words if word in text.lower())
    if spanish_count >= 2 and "fr" not in languages_detected:
        languages_detected.append("es")

    # Check for German indicators
    german_words = [
        " und ",
        " der ",
        " die ",
        " das ",
        " den ",
        " dem ",
        " ein ",
        " eine ",
    ]
    german_count = sum(1 for word in german_words if word in text.lower())
    if german_count >= 2:
        languages_detected.append("de")

    # If multiple languages detected, return "mixed"
    if len(languages_detected) > 1:
        return "mixed"
    elif len(languages_detected) == 1:
        return languages_detected[0]

    # Default to English
    return "en"


def clean_whitespace(text: str) -> str:
    """
    Clean and normalize whitespace in text.

    Args:
        text: Text to clean

    Returns:
        Text with normalized whitespace
    """
    # Replace multiple spaces with single space
    text = re.sub(r" +", " ", text)

    # Replace multiple newlines with double newline
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Remove trailing whitespace from lines
    lines = text.split("\n")
    lines = [line.rstrip() for line in lines]
    text = "\n".join(lines)

    return text.strip()


def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
    """
    Truncate text to maximum length with suffix.

    Args:
        text: Text to truncate
        max_length: Maximum length
        suffix: Suffix to add when truncated

    Returns:
        Truncated text
    """
    if len(text) <= max_length:
        return text

    # Account for suffix length
    truncate_at = max_length - len(suffix)

    # Try to truncate at word boundary
    if " " in text[:truncate_at]:
        # Find last space before limit
        last_space = text[:truncate_at].rfind(" ")
        if last_space > truncate_at // 2:  # Only if we're not losing too much
            truncate_at = last_space

    return text[:truncate_at] + suffix


def is_valid_json_key(key: str) -> bool:
    """
    Check if a string is a valid JSON object key.

    Args:
        key: String to validate

    Returns:
        True if valid JSON key
    """
    # JSON keys should be strings without control characters
    if not key or not isinstance(key, str):
        return False

    # Check for control characters
    if any(ord(char) < 32 for char in key):
        return False

    return True


def normalize_quotes(text: str) -> str:
    """
    Normalize various quote styles to standard quotes.

    Args:
        text: Text with various quote styles

    Returns:
        Text with normalized quotes
    """
    # Smart quotes to regular quotes
    text = text.replace('"', '"').replace('"', '"')
    text = text.replace(""", "'").replace(""", "'")

    # Guillemets to quotes
    text = text.replace("«", '"').replace("»", '"')

    # Normalize dashes
    text = text.replace("–", "-").replace("—", "-")

    return text
