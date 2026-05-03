"""Offline hallucination checks against trusted source evidence."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias

from src.services.evaluation_regression import ExpectedCitation, TrustedSource

ClaimStatus = Literal["supported", "contradicted", "unsupported"]


@dataclass(frozen=True)
class CitationReference:
    title: str
    url: str
    source_type: str = ""


@dataclass(frozen=True)
class CitationCheck:
    citation: CitationReference
    present_in_trusted_sources: bool
    matched_source_titles: tuple[str, ...]


@dataclass(frozen=True)
class ClaimCheck:
    claim: str
    status: ClaimStatus
    matched_source_titles: tuple[str, ...]


@dataclass(frozen=True)
class HallucinationReport:
    claim_checks: tuple[ClaimCheck, ...]
    citation_checks: tuple[CitationCheck, ...]

    @property
    def supported_claims(self) -> tuple[ClaimCheck, ...]:
        return tuple(check for check in self.claim_checks if check.status == "supported")

    @property
    def unsupported_claims(self) -> tuple[ClaimCheck, ...]:
        return tuple(
            check for check in self.claim_checks if check.status == "unsupported"
        )

    @property
    def contradicted_claims(self) -> tuple[ClaimCheck, ...]:
        return tuple(
            check for check in self.claim_checks if check.status == "contradicted"
        )

    @property
    def missing_citations(self) -> tuple[CitationCheck, ...]:
        return tuple(
            check
            for check in self.citation_checks
            if not check.present_in_trusted_sources
        )

    @property
    def hallucination_score(self) -> float:
        total_checks = len(self.claim_checks) + len(self.citation_checks)
        if total_checks == 0:
            return 0.0
        failed_checks = (
            len(self.unsupported_claims)
            + len(self.contradicted_claims)
            + len(self.missing_citations)
        )
        return failed_checks / total_checks

    @property
    def has_hallucinations(self) -> bool:
        return (
            len(self.unsupported_claims) > 0
            or len(self.contradicted_claims) > 0
            or len(self.missing_citations) > 0
        )


CitationInput: TypeAlias = ExpectedCitation | CitationReference | Mapping[str, object] | str
TrustedSourceInput: TypeAlias = TrustedSource | Mapping[str, object]


class HallucinationDetector:
    """Cross-checks generated claims and citations against trusted evidence."""

    def detect(
        self,
        output_text: str,
        *,
        citations: Sequence[CitationInput],
        trusted_sources: Sequence[TrustedSourceInput],
    ) -> HallucinationReport:
        sources = tuple(_coerce_trusted_source(source) for source in trusted_sources)
        claim_checks = tuple(
            self._check_claim(claim, sources) for claim in _extract_claims(output_text)
        )
        citation_checks = tuple(
            self._check_citation(_coerce_citation(citation), sources)
            for citation in citations
        )
        return HallucinationReport(
            claim_checks=claim_checks,
            citation_checks=citation_checks,
        )

    def _check_claim(
        self,
        claim: str,
        sources: Sequence[TrustedSource],
    ) -> ClaimCheck:
        contradicted_sources = tuple(
            source.title
            for source in sources
            if any(
                _meaningfully_matches(claim, contradicted)
                for contradicted in source.contradicted_claims
            )
        )
        if contradicted_sources:
            return ClaimCheck(
                claim=claim,
                status="contradicted",
                matched_source_titles=contradicted_sources,
            )

        supporting_sources = tuple(
            source.title
            for source in sources
            if any(
                _meaningfully_matches(claim, supported)
                for supported in source.supported_claims
            )
        )
        if supporting_sources:
            return ClaimCheck(
                claim=claim,
                status="supported",
                matched_source_titles=supporting_sources,
            )

        return ClaimCheck(
            claim=claim,
            status="unsupported",
            matched_source_titles=(),
        )

    def _check_citation(
        self,
        citation: CitationReference,
        sources: Sequence[TrustedSource],
    ) -> CitationCheck:
        matched_source_titles = tuple(
            source.title
            for source in sources
            if _citation_matches_source(citation, source)
        )
        return CitationCheck(
            citation=citation,
            present_in_trusted_sources=len(matched_source_titles) > 0,
            matched_source_titles=matched_source_titles,
        )


def _extract_claims(output_text: str) -> tuple[str, ...]:
    candidates = re.split(r"(?<=[.!?])\s+|\n+", output_text)
    return tuple(
        claim
        for candidate in candidates
        if (claim := candidate.strip(" \t\r\n-")) and len(claim.split()) >= 4
    )


def _citation_matches_source(
    citation: CitationReference,
    source: TrustedSource,
) -> bool:
    citation_title = _normalize(citation.title)
    citation_url = _normalize(citation.url)
    source_title = _normalize(source.title)
    source_url = _normalize(source.url)
    return (
        bool(citation_url and citation_url == source_url)
        or bool(citation_title and citation_title == source_title)
        or bool(citation_title and _token_overlap(citation_title, source_title) >= 0.85)
    )


def _meaningfully_matches(candidate: str, expected: str) -> bool:
    normalized_candidate = _normalize(candidate)
    normalized_expected = _normalize(expected)
    if not normalized_candidate or not normalized_expected:
        return False
    if (
        normalized_candidate in normalized_expected
        or normalized_expected in normalized_candidate
    ):
        return True
    return _token_overlap(normalized_candidate, normalized_expected) >= 0.85


def _token_overlap(left: str, right: str) -> float:
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(right_tokens)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def _coerce_citation(citation: CitationInput) -> CitationReference:
    if isinstance(citation, CitationReference):
        return citation
    if isinstance(citation, ExpectedCitation):
        return CitationReference(
            title=citation.title,
            url=citation.url,
            source_type=citation.source_type,
        )
    if isinstance(citation, str):
        return CitationReference(title=citation, url="")
    return CitationReference(
        title=_optional_str(citation.get("title")),
        url=_optional_str(citation.get("url")),
        source_type=_optional_str(citation.get("source_type")),
    )


def _coerce_trusted_source(source: TrustedSourceInput) -> TrustedSource:
    if isinstance(source, TrustedSource):
        return source
    return TrustedSource(
        title=_required_str(source.get("title"), "source title"),
        url=_required_str(source.get("url"), "source url"),
        source_type=_required_str(source.get("source_type"), "source source_type"),
        snippet=_required_str(source.get("snippet"), "source snippet"),
        supported_claims=tuple(
            _required_str_list(source.get("supported_claims"), "supported_claims")
        ),
        contradicted_claims=tuple(
            _required_str_list(
                source.get("contradicted_claims", []),
                "contradicted_claims",
            )
        ),
    )


def _optional_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _required_str(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _required_str_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return [_required_str(item, label) for item in value]
