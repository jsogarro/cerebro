"""
Text and markdown response parsing utilities.

This module provides functions for parsing text-based responses,
extracting structured information from unstructured text.
"""

import re


def parse_markdown_sections(text: str) -> dict[str, str]:
    """
    Parse markdown text into sections based on headers.

    Pure function that extracts sections from markdown.

    Args:
        text: Markdown formatted text

    Returns:
        Dictionary mapping section headers to content
    """
    sections = {}
    current_section = "Introduction"
    current_content: list[str] = []

    lines = text.strip().split("\n")

    for line in lines:
        # Strip line for header checking
        stripped_line = line.strip()

        # Check for markdown headers
        header_match = re.match(r"^(#{1,6})\s+(.+)$", stripped_line)

        if header_match:
            # Save previous section
            if current_content:
                sections[current_section] = "\n".join(current_content).strip()

            # Start new section
            _level = len(header_match.group(1))
            current_section = header_match.group(2).strip()
            current_content = []
        elif stripped_line:  # Only add non-empty lines
            current_content.append(stripped_line)

    # Save last section
    if current_content:
        sections[current_section] = "\n".join(current_content).strip()

    return sections


def extract_bullet_points(text: str) -> list[str]:
    """
    Extract bullet points from text.

    Handles various bullet formats: -, *, •, +

    Args:
        text: Text containing bullet points

    Returns:
        List of bullet point contents
    """
    bullet_patterns = [
        r"^\s*[-*•+]\s+(.+)$",  # Standard bullets
        r"^\s*→\s+(.+)$",  # Arrow bullets
        r"^\s*>\s+(.+)$",  # Quote-style bullets
    ]

    points = []
    lines = text.split("\n")

    for line in lines:
        for pattern in bullet_patterns:
            match = re.match(pattern, line)
            if match:
                points.append(match.group(1).strip())
                break

    return points


def extract_numbered_list(text: str) -> list[str]:
    """
    Extract numbered list items from text.

    Handles formats: 1. item, 1) item, (1) item

    Args:
        text: Text containing numbered lists

    Returns:
        List of numbered items (without numbers)
    """
    patterns = [
        r"^\s*\d+\.\s+(.+)$",  # 1. item
        r"^\s*\d+\)\s+(.+)$",  # 1) item
        r"^\s*\(\d+\)\s+(.+)$",  # (1) item
        r"^\s*[a-z]\.\s+(.+)$",  # a. item
        r"^\s*[a-z]\)\s+(.+)$",  # a) item
    ]

    items = []
    lines = text.split("\n")

    for line in lines:
        for pattern in patterns:
            match = re.match(pattern, line, re.IGNORECASE)
            if match:
                items.append(match.group(1).strip())
                break

    return items


def extract_key_value_pairs(text: str) -> dict[str, str]:
    """
    Extract key-value pairs from text.

    Handles formats: "Key: Value", "Key = Value", "Key - Value"

    Args:
        text: Text containing key-value pairs

    Returns:
        Dictionary of extracted pairs
    """
    patterns = [
        r"^([^:=\-]+):\s*(.+)$",  # Key: Value
        r"^([^:=\-]+)=\s*(.+)$",  # Key = Value
        r"^([^:=\-]+)\-\s*(.+)$",  # Key - Value
    ]

    pairs = {}
    lines = text.split("\n")

    for line in lines:
        line = line.strip()
        if not line:
            continue

        for pattern in patterns:
            match = re.match(pattern, line)
            if match:
                key = match.group(1).strip()
                value = match.group(2).strip()
                pairs[key] = value
                break

    return pairs


def extract_entities(text: str) -> dict[str, list[str]]:
    """
    Extract named entities from text.

    Basic entity extraction for:
    - People (names with titles)
    - Organizations (capitalized multi-word phrases)
    - Locations (cities, countries)
    - Dates
    - Monetary values
    - Percentages
    - Email addresses
    - URLs

    Args:
        text: Text to extract entities from

    Returns:
        Dictionary categorizing found entities
    """
    entities: dict[str, list[str]] = {
        "people": [],
        "organizations": [],
        "locations": [],
        "dates": [],
        "monetary": [],
        "percentages": [],
        "emails": [],
        "urls": [],
    }

    # People - titles and names
    people_pattern = (
        r"\b(?:Dr\.|Prof\.|Mr\.|Mrs\.|Ms\.|Miss)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*"
    )
    entities["people"] = list(set(re.findall(people_pattern, text)))

    # Organizations - capitalized phrases (simplified)
    org_pattern = r"\b[A-Z]{2,}(?:\s+[A-Z][a-z]*)*\b"
    potential_orgs = re.findall(org_pattern, text)
    # Filter common acronyms that aren't organizations
    common_non_orgs = {"USA", "UK", "EU", "AI", "ML", "API", "URL", "JSON"}
    entities["organizations"] = [
        org
        for org in set(potential_orgs)
        if org not in common_non_orgs and len(org) > 2
    ]

    # Locations - common patterns
    location_pattern = (
        r"\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,\s*[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b"
    )
    entities["locations"] = list(set(re.findall(location_pattern, text)))

    # Also check for standalone city/country names (simplified list)
    known_locations = [
        "Boston",
        "New York",
        "London",
        "Paris",
        "Tokyo",
        "Beijing",
        "Massachusetts",
        "California",
        "Texas",
        "Florida",
        "United States",
        "United Kingdom",
        "China",
        "Japan",
        "Germany",
    ]
    for location in known_locations:
        if location in text and location not in entities["locations"]:
            entities["locations"].append(location)

    # Dates - various formats
    date_patterns = [
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",  # MM/DD/YYYY or MM-DD-YYYY
        r"\b\d{4}[/-]\d{1,2}[/-]\d{1,2}\b",  # YYYY-MM-DD
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b",
        r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}\b",
    ]
    for pattern in date_patterns:
        entities["dates"].extend(re.findall(pattern, text, re.IGNORECASE))
    entities["dates"] = list(set(entities["dates"]))

    # Monetary values
    money_pattern = r"\$[\d,]+(?:\.\d+)?(?:\s*(?:million|billion|thousand|M|B|K))?"
    entities["monetary"] = list(set(re.findall(money_pattern, text, re.IGNORECASE)))

    # Percentages
    percent_pattern = r"\b\d+(?:\.\d+)?%"
    entities["percentages"] = list(set(re.findall(percent_pattern, text)))

    # Email addresses
    email_pattern = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
    entities["emails"] = list(set(re.findall(email_pattern, text)))

    # URLs
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    entities["urls"] = list(set(re.findall(url_pattern, text)))

    return entities


def extract_quotes(text: str) -> list[tuple[str, str | None]]:
    """
    Extract quoted text with optional attribution.

    Args:
        text: Text containing quotes

    Returns:
        List of tuples (quote, attribution)
    """
    quotes = []

    # Pattern for quotes with attribution
    attribution_pattern = r'"([^"]+)"\s*[-–—]\s*([^,\n]+)'  # noqa: RUF001
    for match in re.finditer(attribution_pattern, text):
        quotes.append((match.group(1), match.group(2).strip()))

    # Pattern for standalone quotes
    standalone_pattern = r'"([^"]+)"'
    for match in re.finditer(standalone_pattern, text):
        # Check if this quote was already captured with attribution
        quote_text = match.group(1)
        if not any(q[0] == quote_text for q in quotes):
            quotes.append((quote_text, None))

    return quotes


def extract_code_blocks(text: str) -> dict[str, list[str]]:
    """
    Extract code blocks from markdown text.

    Args:
        text: Text containing code blocks

    Returns:
        Dictionary mapping language to list of code blocks
    """
    code_blocks: dict[str, list[str]] = {}

    pattern = r"```(\w*)\n(.*?)```"

    for match in re.finditer(pattern, text, re.DOTALL):
        language = match.group(1) or "plain"
        code = match.group(2).strip()

        if language not in code_blocks:
            code_blocks[language] = []
        code_blocks[language].append(code)

    return code_blocks


def clean_text(text: str) -> str:
    """
    Clean text by removing extra whitespace and normalizing.

    Args:
        text: Text to clean

    Returns:
        Cleaned text
    """
    # Remove extra whitespace
    text = re.sub(r"\s+", " ", text)

    # Remove leading/trailing whitespace
    text = text.strip()

    # Normalize quotes
    text = text.replace('"', '"').replace('"', '"')
    text = text.replace(""", "'").replace(""", "'")

    # Normalize dashes
    text = text.replace("–", "-").replace("—", "-")  # noqa: RUF001

    return text


def extract_summary(text: str, max_length: int = 500) -> str:
    """
    Extract or generate a summary from text.

    Looks for explicit summary sections or takes first paragraph.

    Args:
        text: Full text
        max_length: Maximum summary length

    Returns:
        Summary text
    """
    # Look for explicit summary section
    sections = parse_markdown_sections(text)

    for key in ["Summary", "Abstract", "Overview", "TLDR", "TL;DR"]:
        if key in sections:
            summary = sections[key]
            if len(summary) <= max_length:
                return summary
            return summary[:max_length] + "..."

    # Take first paragraph
    paragraphs = text.split("\n\n")
    if paragraphs:
        first_para = paragraphs[0].strip()
        if len(first_para) <= max_length:
            return first_para
        return first_para[:max_length] + "..."

    # Fallback to truncating full text
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."
