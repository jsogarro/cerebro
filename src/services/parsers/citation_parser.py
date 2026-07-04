"""
Citation parsing and formatting utilities.

This module provides functions for parsing, extracting, and formatting
academic citations in various styles (APA, MLA, Chicago).
"""

import re


def parse_citation(citation: str, style: str = "APA") -> dict[str, str]:
    """
    Parse a citation string into components.

    Args:
        citation: Citation string
        style: Citation style (APA, MLA, Chicago)

    Returns:
        Dictionary with citation components
    """
    result = {}

    if style.upper() == "APA":
        result = parse_apa_citation(citation)
    elif style.upper() == "MLA":
        result = parse_mla_citation(citation)
    elif style.upper() == "CHICAGO":
        result = parse_chicago_citation(citation)
    else:
        # Try to auto-detect and parse
        result = auto_parse_citation(citation)

    return result


def parse_apa_citation(citation: str) -> dict[str, str]:
    """
    Parse APA format citation.

    Format: Author, A. A. (Year). Title. Journal, Volume(Issue), pages.

    Args:
        citation: APA formatted citation

    Returns:
        Dictionary with parsed components
    """
    result = {}

    # Extract author(s)
    author_pattern = r"^([^(\d]+?)(?:\s*\()"
    author_match = re.match(author_pattern, citation)
    if author_match:
        result["author"] = author_match.group(1).strip()

    # Extract year
    year_pattern = r"\((\d{4})\)"
    year_match = re.search(year_pattern, citation)
    if year_match:
        result["year"] = year_match.group(1)

    # Extract title (between year and journal)
    if year_match:
        after_year = citation[year_match.end() :].strip()
        # Title ends at the next period followed by a capital letter or journal name
        title_pattern = r"^\.?\s*([^.]+?)\."
        title_match = re.match(title_pattern, after_year)
        if title_match:
            result["title"] = title_match.group(1).strip()

            # Extract journal info
            journal_part = after_year[title_match.end() :].strip()

            # Journal name (before comma followed by volume number)
            # Use greedy matching up to comma with number
            journal_pattern = r"^(.+?),\s*(\d+)"
            journal_match = re.match(journal_pattern, journal_part)
            if not journal_match:
                # Fallback: just get everything before first comma
                journal_pattern = r"^([^,]+)"
                journal_match = re.match(journal_pattern, journal_part)
            if journal_match:
                result["journal"] = journal_match.group(1).strip()
                if len(journal_match.groups()) > 1 and journal_match.group(2):
                    result["volume"] = journal_match.group(2)

            # Extract issue
            issue_pattern = r"\((\d+)\)"
            issue_match = re.search(issue_pattern, journal_part)
            if issue_match:
                result["issue"] = issue_match.group(1)

            # Extract pages
            pages_pattern = r"(\d+[-–]\d+|\d+)"  # noqa: RUF001
            pages_matches = re.findall(pages_pattern, journal_part)
            if pages_matches:
                result["pages"] = pages_matches[-1]  # Take last match

    return result


def parse_mla_citation(citation: str) -> dict[str, str]:
    """
    Parse MLA format citation.

    Format: Author. "Title." Journal, vol. #, no. #, Year, pp. pages.

    Args:
        citation: MLA formatted citation

    Returns:
        Dictionary with parsed components
    """
    result = {}

    # Extract author (before first period)
    author_pattern = r"^([^.]+)\."
    author_match = re.match(author_pattern, citation)
    if author_match:
        result["author"] = author_match.group(1).strip()

    # Extract title (in quotes, handle period inside or outside)
    title_pattern = r'"([^"]+\.?)"'
    title_match = re.search(title_pattern, citation)
    if title_match:
        title = title_match.group(1)
        # Remove trailing period if present
        if title.endswith("."):
            title = title[:-1]
        result["title"] = title

    # Extract journal (after title, before vol.)
    if title_match:
        after_title = citation[title_match.end() :].strip()
        journal_pattern = r"^[.,]?\s*([^,]+?)(?:,\s*vol\.)"
        journal_match = re.match(journal_pattern, after_title)
        if journal_match:
            result["journal"] = journal_match.group(1).strip()

    # Extract volume
    vol_pattern = r"vol\.\s*(\d+)"
    vol_match = re.search(vol_pattern, citation)
    if vol_match:
        result["volume"] = vol_match.group(1)

    # Extract issue/number
    no_pattern = r"no\.\s*(\d+)"
    no_match = re.search(no_pattern, citation)
    if no_match:
        result["issue"] = no_match.group(1)

    # Extract year
    year_pattern = r",\s*(\d{4})\s*,"
    year_match = re.search(year_pattern, citation)
    if year_match:
        result["year"] = year_match.group(1)

    # Extract pages
    pages_pattern = r"pp?\.\s*(\d+[-–]\d+|\d+)"  # noqa: RUF001
    pages_match = re.search(pages_pattern, citation)
    if pages_match:
        result["pages"] = pages_match.group(1)

    return result


def parse_chicago_citation(citation: str) -> dict[str, str]:
    """
    Parse Chicago style citation.

    Format: Author. "Title." Journal Volume, no. Issue (Year): pages.

    Args:
        citation: Chicago formatted citation

    Returns:
        Dictionary with parsed components
    """
    result = {}

    # Extract author
    author_pattern = r"^([^.]+)\."
    author_match = re.match(author_pattern, citation)
    if author_match:
        result["author"] = author_match.group(1).strip()

    # Extract title (in quotes)
    title_pattern = r'"([^"]+)"'
    title_match = re.search(title_pattern, citation)
    if title_match:
        result["title"] = title_match.group(1)

    # Extract journal and volume
    if title_match:
        after_title = citation[title_match.end() :].strip()
        journal_vol_pattern = r"^[.,]?\s*([^,\d]+?)\s*(\d+)?"
        journal_vol_match = re.match(journal_vol_pattern, after_title)
        if journal_vol_match:
            result["journal"] = journal_vol_match.group(1).strip()
            if journal_vol_match.group(2):
                result["volume"] = journal_vol_match.group(2)

    # Extract issue
    issue_pattern = r"no\.\s*(\d+)"
    issue_match = re.search(issue_pattern, citation)
    if issue_match:
        result["issue"] = issue_match.group(1)

    # Extract year (in parentheses)
    year_pattern = r"\((\d{4})\)"
    year_match = re.search(year_pattern, citation)
    if year_match:
        result["year"] = year_match.group(1)

    # Extract pages (after colon)
    pages_pattern = r":\s*(\d+[-–]\d+|\d+)"  # noqa: RUF001
    pages_match = re.search(pages_pattern, citation)
    if pages_match:
        result["pages"] = pages_match.group(1)

    return result


def auto_parse_citation(citation: str) -> dict[str, str]:
    """
    Attempt to parse citation without knowing the style.

    Args:
        citation: Citation string

    Returns:
        Dictionary with best-effort parsed components
    """
    result = {}

    # Try to extract common elements

    # Year (4 digits, possibly in parentheses)
    year_patterns = [r"\((\d{4})\)", r",\s*(\d{4})[,.]", r"\b(\d{4})\b"]
    for pattern in year_patterns:
        match = re.search(pattern, citation)
        if match:
            result["year"] = match.group(1)
            break

    # Title (in quotes if available)
    title_pattern = r'"([^"]+)"'
    title_match = re.search(title_pattern, citation)
    if title_match:
        result["title"] = title_match.group(1)

    # DOI
    doi = extract_doi(citation)
    if doi:
        result["doi"] = doi

    # Pages (various formats)
    pages_patterns = [
        r"pp?\.\s*(\d+[-–]\d+)",  # noqa: RUF001
        r":\s*(\d+[-–]\d+)",  # noqa: RUF001
        r",\s*(\d+[-–]\d+)[,.]?$",  # noqa: RUF001
    ]
    for pattern in pages_patterns:
        match = re.search(pattern, citation)
        if match:
            result["pages"] = match.group(1)
            break

    # Volume and issue
    vol_pattern = r"(?:vol\.|volume)?\s*(\d+)"
    vol_match = re.search(vol_pattern, citation, re.IGNORECASE)
    if vol_match:
        result["volume"] = vol_match.group(1)

    issue_patterns = [r"no\.\s*(\d+)", r"\((\d+)\)", r"issue\s*(\d+)"]
    for pattern in issue_patterns:
        match = re.search(pattern, citation, re.IGNORECASE)
        if match:
            result["issue"] = match.group(1)
            break

    # Author (usually at beginning)
    # Simple heuristic: text before first period or year
    if "year" in result:
        before_year = citation.split(result["year"])[0]
        author_pattern = r"^([^.,\(]+)"
        author_match = re.match(author_pattern, before_year.strip())
        if author_match:
            result["author"] = author_match.group(1).strip()

    return result


def extract_doi(text: str) -> str | None:
    """
    Extract DOI from text.

    Args:
        text: Text potentially containing a DOI

    Returns:
        DOI string or None
    """
    # DOI patterns
    doi_patterns = [
        r"(?:https?://)?(?:dx\.)?doi\.org/(\S+)",  # URL format
        r"(?:DOI|doi):\s*(\S+)",  # DOI: format
        r"\b(10\.\d{4,}/[-._;()/:a-zA-Z0-9]+)\b",  # Raw DOI format
    ]

    for pattern in doi_patterns:
        match = re.search(pattern, text)
        if match:
            doi = match.group(1)
            # Clean up DOI
            doi = doi.rstrip(".,;")
            return doi

    return None


def format_citation(
    components: dict[str, str], style: str = "APA", include_doi: bool = True
) -> str:
    """
    Format citation components into a citation string.

    Args:
        components: Dictionary with citation components
        style: Target citation style
        include_doi: Whether to include DOI if available

    Returns:
        Formatted citation string
    """
    if style.upper() == "APA":
        return format_apa(components, include_doi)
    elif style.upper() == "MLA":
        return format_mla(components, include_doi)
    elif style.upper() == "CHICAGO":
        return format_chicago(components, include_doi)
    else:
        return format_apa(components, include_doi)  # Default to APA


def format_apa(components: dict[str, str], include_doi: bool = True) -> str:
    """Format as APA citation."""
    parts = []

    if "author" in components:
        parts.append(components["author"])

    if "year" in components:
        parts.append(f"({components['year']})")

    if "title" in components:
        parts.append(f"{components['title']}.")

    if "journal" in components:
        journal_part = components["journal"]
        if "volume" in components:
            journal_part += f", {components['volume']}"
            if "issue" in components:
                journal_part += f"({components['issue']})"
        if "pages" in components:
            journal_part += f", {components['pages']}"
        parts.append(journal_part + ".")

    if include_doi and "doi" in components:
        parts.append(f"https://doi.org/{components['doi']}")

    return " ".join(parts)


def format_mla(components: dict[str, str], include_doi: bool = True) -> str:
    """Format as MLA citation."""
    parts = []

    if "author" in components:
        parts.append(components["author"] + ".")

    if "title" in components:
        parts.append(f'"{components["title"]}."')

    if "journal" in components:
        journal_part = components["journal"]
        if "volume" in components:
            journal_part += f", vol. {components['volume']}"
        if "issue" in components:
            journal_part += f", no. {components['issue']}"
        parts.append(journal_part + ",")

    if "year" in components:
        parts.append(components["year"] + ",")

    if "pages" in components:
        parts.append(f"pp. {components['pages']}.")

    if include_doi and "doi" in components:
        parts.append(f"doi:{components['doi']}")

    return " ".join(parts)


def format_chicago(components: dict[str, str], include_doi: bool = True) -> str:
    """Format as Chicago citation."""
    parts = []

    if "author" in components:
        parts.append(components["author"] + ".")

    if "title" in components:
        parts.append(f'"{components["title"]}."')

    if "journal" in components:
        journal_part = components["journal"]
        if "volume" in components:
            journal_part += f" {components['volume']}"
        if "issue" in components:
            journal_part += f", no. {components['issue']}"
        parts.append(journal_part)

    if "year" in components:
        parts.append(f"({components['year']})")

    if "pages" in components:
        parts[-1] = parts[-1].rstrip() + f": {components['pages']}."

    if include_doi and "doi" in components:
        parts.append(f"https://doi.org/{components['doi']}")

    return " ".join(parts)


def extract_bibtex_fields(bibtex: str) -> dict[str, str]:
    """
    Extract fields from BibTeX entry.

    Args:
        bibtex: BibTeX entry string

    Returns:
        Dictionary of BibTeX fields
    """
    fields = {}

    # Extract entry type
    type_pattern = r"@(\w+)\s*\{"
    type_match = re.match(type_pattern, bibtex.strip())
    if type_match:
        fields["entry_type"] = type_match.group(1).lower()

    # Extract citation key
    key_pattern = r"@\w+\s*\{([^,]+),"
    key_match = re.match(key_pattern, bibtex.strip())
    if key_match:
        fields["citation_key"] = key_match.group(1).strip()

    # Extract fields
    field_pattern = r'(\w+)\s*=\s*["{]([^"}]+)["}]'
    for match in re.finditer(field_pattern, bibtex):
        field_name = match.group(1).lower()
        field_value = match.group(2).strip()
        fields[field_name] = field_value

    return fields


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


def bibtex_to_citation(bibtex: str, style: str = "APA") -> str:
    """
    Convert BibTeX entry to formatted citation.

    Args:
        bibtex: BibTeX entry
        style: Target citation style

    Returns:
        Formatted citation
    """
    fields = extract_bibtex_fields(bibtex)

    # Map BibTeX fields to our components
    components = {}

    if "author" in fields:
        components["author"] = fields["author"].replace(" and ", ", ")

    if "title" in fields:
        components["title"] = fields["title"]

    if "journal" in fields:
        components["journal"] = fields["journal"]
    elif "booktitle" in fields:
        components["journal"] = fields["booktitle"]

    if "year" in fields:
        components["year"] = fields["year"]

    if "volume" in fields:
        components["volume"] = fields["volume"]

    if "number" in fields:
        components["issue"] = fields["number"]

    if "pages" in fields:
        components["pages"] = fields["pages"].replace("--", "-")

    if "doi" in fields:
        components["doi"] = fields["doi"]

    return format_citation(components, style)
