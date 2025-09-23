"""
Test suite for Gemini response parsing.

These tests verify response parsing and data extraction functionality.
"""

import json

import pytest


class TestJSONParser:
    """Test JSON response parsing."""

    def test_parse_valid_json(self):
        """Test parsing valid JSON response."""
        from src.services.parsers.json_parser import parse_json_response

        response = json.dumps(
            {"result": "success", "data": {"key": "value"}, "items": [1, 2, 3]}
        )

        result = parse_json_response(response)

        assert result["result"] == "success"
        assert result["data"]["key"] == "value"
        assert len(result["items"]) == 3

    def test_parse_json_with_markdown(self):
        """Test parsing JSON embedded in markdown."""
        from src.services.parsers.json_parser import parse_json_response

        response = """
        Here is the analysis:
        
        ```json
        {
            "analysis": "complete",
            "score": 0.95
        }
        ```
        
        Additional text here
        """

        result = parse_json_response(response)

        assert result["analysis"] == "complete"
        assert result["score"] == 0.95

    def test_parse_invalid_json(self):
        """Test handling of invalid JSON."""
        from src.services.parsers.json_parser import parse_json_response

        response = "This is not JSON"

        with pytest.raises(ValueError) as exc_info:
            parse_json_response(response)

        assert "Invalid JSON" in str(exc_info.value)

    def test_validate_json_schema(self):
        """Test JSON schema validation."""
        from src.services.parsers.json_parser import validate_schema

        data = {"title": "Test", "items": ["a", "b"], "count": 2}

        schema = {"title": str, "items": list, "count": int}

        assert validate_schema(data, schema) is True

        # Missing field
        incomplete_data = {"title": "Test"}
        assert validate_schema(incomplete_data, schema) is False

    def test_extract_nested_json(self):
        """Test extraction of nested JSON structures."""
        from src.services.parsers.json_parser import extract_nested

        data = {"level1": {"level2": {"level3": {"value": "deep"}}}}

        result = extract_nested(data, "level1.level2.level3.value")
        assert result == "deep"

        # Non-existent path
        result = extract_nested(data, "level1.missing.path", default="not found")
        assert result == "not found"


class TestTextParser:
    """Test text/markdown response parsing."""

    def test_parse_markdown_sections(self):
        """Test parsing markdown with sections."""
        from src.services.parsers.text_parser import parse_markdown_sections

        response = """
        # Summary
        This is the summary section.
        
        ## Key Findings
        - Finding 1
        - Finding 2
        
        ## Recommendations
        1. Recommendation A
        2. Recommendation B
        """

        result = parse_markdown_sections(response)

        assert "Summary" in result
        assert "Key Findings" in result
        assert "Recommendations" in result
        assert "Finding 1" in result["Key Findings"]

    def test_extract_bullet_points(self):
        """Test extraction of bullet points."""
        from src.services.parsers.text_parser import extract_bullet_points

        text = """
        Here are the main points:
        - First point
        - Second point with detail
        - Third point
        
        Additional text
        * Alternative bullet
        • Unicode bullet
        """

        points = extract_bullet_points(text)

        assert len(points) >= 3
        assert "First point" in points
        assert "Second point with detail" in points

    def test_extract_numbered_list(self):
        """Test extraction of numbered lists."""
        from src.services.parsers.text_parser import extract_numbered_list

        text = """
        Steps to follow:
        1. First step
        2. Second step
        3. Third step
        
        Another section:
        1) Alternative format
        2) Second item
        """

        items = extract_numbered_list(text)

        assert len(items) >= 3
        assert "First step" in items[0]

    def test_extract_key_value_pairs(self):
        """Test extraction of key-value pairs."""
        from src.services.parsers.text_parser import extract_key_value_pairs

        text = """
        Title: Research Paper
        Author: Dr. Smith
        Year: 2024
        Score: 0.95
        """

        pairs = extract_key_value_pairs(text)

        assert pairs["Title"] == "Research Paper"
        assert pairs["Author"] == "Dr. Smith"
        assert pairs["Year"] == "2024"
        assert pairs["Score"] == "0.95"

    def test_extract_entities(self):
        """Test entity extraction from text."""
        from src.services.parsers.text_parser import extract_entities

        text = """
        Dr. Jane Smith from MIT published a paper in Nature about 
        artificial intelligence applications in Boston, Massachusetts.
        The research was funded by NSF with a budget of $2.5 million.
        """

        entities = extract_entities(text)

        assert "Dr. Jane Smith" in entities.get("people", [])
        assert "MIT" in entities.get("organizations", [])
        assert "Boston" in entities.get("locations", [])
        assert "$2.5 million" in entities.get("monetary", [])


class TestCitationParser:
    """Test citation parsing and formatting."""

    def test_parse_apa_citation(self):
        """Test parsing APA format citations."""
        from src.services.parsers.citation_parser import parse_citation

        citation = "Smith, J. (2024). Title of the paper. Journal Name, 10(2), 123-145."

        result = parse_citation(citation, style="APA")

        assert result["author"] == "Smith, J."
        assert result["year"] == "2024"
        assert result["title"] == "Title of the paper"
        assert result["journal"] == "Journal Name"

    def test_parse_mla_citation(self):
        """Test parsing MLA format citations."""
        from src.services.parsers.citation_parser import parse_citation

        citation = 'Smith, John. "Title of the Paper." Journal Name, vol. 10, no. 2, 2024, pp. 123-145.'

        result = parse_citation(citation, style="MLA")

        assert result["author"] == "Smith, John"
        assert result["title"] == "Title of the Paper"
        assert result["year"] == "2024"

    def test_extract_doi(self):
        """Test DOI extraction from citations."""
        from src.services.parsers.citation_parser import extract_doi

        text_with_doi = "https://doi.org/10.1234/example.2024"
        doi = extract_doi(text_with_doi)
        assert doi == "10.1234/example.2024"

        text_with_doi2 = "DOI: 10.5678/another.example"
        doi2 = extract_doi(text_with_doi2)
        assert doi2 == "10.5678/another.example"

    def test_format_citation(self):
        """Test citation formatting."""
        from src.services.parsers.citation_parser import format_citation

        source = {
            "author": "Smith, J.",
            "year": 2024,
            "title": "Research Paper",
            "journal": "Nature",
            "volume": 10,
            "issue": 2,
            "pages": "123-145",
        }

        apa = format_citation(source, style="APA")
        assert "Smith, J." in apa
        assert "(2024)" in apa
        assert "Nature" in apa

    def test_validate_citation_fields(self):
        """Test citation field validation."""
        from src.services.parsers.citation_parser import validate_citation

        valid_citation = {
            "author": "Author",
            "year": 2024,
            "title": "Title",
            "journal": "Journal",
        }

        assert validate_citation(valid_citation) is True

        invalid_citation = {
            "author": "Author"
            # Missing required fields
        }

        assert validate_citation(invalid_citation) is False


class TestResponseSanitization:
    """Test response sanitization and validation."""

    def test_sanitize_html(self):
        """Test HTML sanitization from responses."""
        from src.services.parsers.base_parser import sanitize_html

        text_with_html = (
            "<p>This is <b>bold</b> text with <script>alert('xss')</script></p>"
        )

        clean_text = sanitize_html(text_with_html)

        assert "<script>" not in clean_text
        assert (
            "This is bold text" in clean_text or "This is **bold** text" in clean_text
        )

    def test_remove_personal_info(self):
        """Test removal of personal information."""
        from src.services.parsers.base_parser import remove_personal_info

        text = """
        Contact John at john.doe@email.com or call 555-123-4567.
        His SSN is 123-45-6789.
        """

        clean_text = remove_personal_info(text)

        assert "john.doe@email.com" not in clean_text
        assert "555-123-4567" not in clean_text
        assert "123-45-6789" not in clean_text

    def test_validate_response_length(self):
        """Test response length validation."""
        from src.services.parsers.base_parser import validate_length

        short_response = "Too short"
        assert validate_length(short_response, min_length=20) is False

        normal_response = "This is a normal length response with adequate content."
        assert validate_length(normal_response, min_length=20, max_length=1000) is True

        long_response = "word " * 1000
        assert validate_length(long_response, max_length=100) is False

    def test_detect_language(self):
        """Test language detection in responses."""
        from src.services.parsers.base_parser import detect_language

        english_text = "This is an English text about research."
        assert detect_language(english_text) == "en"

        # Test with mixed content
        mixed_text = "This is English. Ceci est français."
        lang = detect_language(mixed_text)
        assert lang in ["en", "fr", "mixed"]
