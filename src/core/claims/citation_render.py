"""Render a citation from held metadata, deterministically.

One minimal author-date style, written from general conventions. No LLM call,
no clock, no run identifier: the same input yields the same bytes.

Gap policy: a missing author or year is never papered over with a placeholder
such as "n.d."; the output is an explicit ``[citation incomplete: ...]`` marker
and the gap is recorded in ``missing_fields``.

Support policy: ``disputed`` still mints the citation but appends ``[disputed]``;
``partially_supported`` appends ``[partial support]``; ``unsupported`` is never
rendered as a citation, only as an explicit marker with no citation span.

``spans`` are the half-open offsets of citation text the renderer emitted, in
the form ``find_unrendered_citation_prose`` takes as ``rendered_spans``.

Known gaps: no same-author-same-year disambiguation (2020a/2020b) and no
Unicode sort-key normalization; neither is needed for a single in-text citation.
"""

from dataclasses import dataclass

from src.core.contracts.citation import CitationMetadata
from src.core.contracts.provenance import ClaimSupportStatus

AUTHOR_DATE_MINIMAL = "author-date-minimal"
_STYLES = frozenset({AUTHOR_DATE_MINIMAL})

UNSUPPORTED_MARKER = "[unsupported claim: no citation rendered]"
PARTIAL_MARKER = "[partial support]"
DISPUTED_MARKER = "[disputed]"


class CitationStyleUnavailableError(ValueError):
    code = "citation.style_unavailable"

    def __init__(self, style: str) -> None:
        super().__init__(f"{self.code}: unknown citation style {style!r}")


@dataclass(frozen=True)
class RenderedCitation:
    text: str
    spans: tuple[tuple[int, int], ...]
    missing_fields: tuple[str, ...]


def _authors(families: list[str]) -> str:
    if len(families) == 1:
        return families[0]
    if len(families) == 2:
        return f"{families[0]} and {families[1]}"
    return f"{families[0]} et al."


def render(
    csl_json: CitationMetadata,
    style: str,
    *,
    support: ClaimSupportStatus,
) -> RenderedCitation:
    if style not in _STYLES:
        raise CitationStyleUnavailableError(style)

    missing = tuple(
        name
        for name, absent in (
            ("author", not csl_json.authors),
            ("year", csl_json.year is None),
        )
        if absent
    )
    if support is ClaimSupportStatus.UNSUPPORTED:
        return RenderedCitation(UNSUPPORTED_MARKER, (), missing)

    if missing:
        text = "[citation incomplete: " + ", ".join(f"no {m}" for m in missing) + "]"
    else:
        assert csl_json.year is not None
        families = [a.value for a in csl_json.authors]
        text = f"({_authors(families)}, {csl_json.year.value})"
    if csl_json.doi is not None:
        text += f" https://doi.org/{csl_json.doi.value}"
    span = (0, len(text))
    if support is ClaimSupportStatus.PARTIALLY_SUPPORTED:
        text += f" {PARTIAL_MARKER}"
    elif support is ClaimSupportStatus.DISPUTED:
        text += f" {DISPUTED_MARKER}"
    return RenderedCitation(text, (span,), missing)
