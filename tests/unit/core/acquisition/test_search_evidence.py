"""What a search record contributes as evidence, and what it never does."""

import pytest

from src.core.acquisition.search_evidence import (
    MAX_SOURCE_URI_LENGTH,
    source_text,
    source_uri,
)


def test_a_record_without_an_abstract_has_no_text() -> None:
    assert source_text({"title": "Only a title", "abstract": "  "}) == ""


def test_text_is_the_title_and_abstract() -> None:
    assert source_text({"title": "T", "abstract": "A"}) == "T\n\nA"


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (
            {"url": "https://arxiv.org/abs/1", "doi": "10.1234/x"},
            "https://arxiv.org/abs/1",
        ),
        ({"doi": "10.1234/x"}, "https://doi.org/10.1234/x"),
        ({"url": "javascript:alert(1)"}, None),
        ({"url": "file:///etc/passwd"}, None),
        ({"url": "https://user:pw@example.org/p"}, None),
        ({"url": "", "doi": "10.1234/x"}, "https://doi.org/10.1234/x"),
        ({"url": "https://" + "a" * MAX_SOURCE_URI_LENGTH}, None),
        ({"doi": "not-a-doi"}, None),
        ({"source": "pubmed", "id": "99"}, None),
        ({}, None),
    ],
)
def test_a_source_is_named_only_by_an_http_url_or_a_doi(
    record: dict[str, str], expected: str | None
) -> None:
    assert source_uri(record) == expected
