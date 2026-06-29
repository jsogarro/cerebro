"""Deterministic regression scoring for golden research outputs."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExpectedCitation:
    title: str
    url: str
    source_type: str


@dataclass(frozen=True)
class TrustedSource:
    title: str
    url: str
    source_type: str
    snippet: str
    supported_claims: tuple[str, ...]
    contradicted_claims: tuple[str, ...]


@dataclass(frozen=True)
class QualityThresholds:
    min_citations: int
    min_insights: int


@dataclass(frozen=True)
class GoldenDatasetCase:
    case_id: str
    query: str
    domains: tuple[str, ...]
    expected_citations: tuple[ExpectedCitation, ...]
    expected_insights: tuple[str, ...]
    trusted_sources: tuple[TrustedSource, ...]
    quality_thresholds: QualityThresholds


@dataclass(frozen=True)
class GoldenDataset:
    version: str
    description: str
    cases: tuple[GoldenDatasetCase, ...]


@dataclass(frozen=True)
class AgentOutput:
    text: str
    citations: tuple[ExpectedCitation, ...] = ()


@dataclass(frozen=True)
class RegressionCaseResult:
    case_id: str
    passed: bool
    citation_matches: int
    insight_matches: int
    required_citations: int
    required_insights: int


@dataclass(frozen=True)
class RegressionSuiteResult:
    total_cases: int
    passed_cases: int
    failed_cases: tuple[RegressionCaseResult, ...]
    allowed_degradation: float

    @property
    def pass_rate(self) -> float:
        if self.total_cases == 0:
            return 0.0
        return self.passed_cases / self.total_cases

    @property
    def degradation(self) -> float:
        return 1.0 - self.pass_rate

    @property
    def passed(self) -> bool:
        return self.degradation <= self.allowed_degradation


AgentOutputProvider = Callable[[GoldenDatasetCase], AgentOutput]


class GoldenDatasetRegressionRunner:
    """Scores research outputs against a checked-in golden dataset."""

    def __init__(
        self,
        dataset: GoldenDataset,
        *,
        allowed_degradation: float = 0.10,
    ) -> None:
        self.dataset = dataset
        self.allowed_degradation = allowed_degradation

    def run(self, output_provider: AgentOutputProvider) -> RegressionSuiteResult:
        results = [
            score_agent_output(case, output_provider(case))
            for case in self.dataset.cases
        ]
        failed_cases = tuple(result for result in results if not result.passed)
        return RegressionSuiteResult(
            total_cases=len(results),
            passed_cases=len(results) - len(failed_cases),
            failed_cases=failed_cases,
            allowed_degradation=self.allowed_degradation,
        )


def load_golden_dataset(path: str | Path) -> GoldenDataset:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    root = _require_mapping(raw, "golden dataset")

    cases = tuple(
        _parse_case(case_data)
        for case_data in _require_list(root.get("cases"), "cases")
    )

    return GoldenDataset(
        version=_require_str(root.get("version"), "version"),
        description=_require_str(root.get("description"), "description"),
        cases=cases,
    )


def score_agent_output(
    case: GoldenDatasetCase,
    output: AgentOutput,
) -> RegressionCaseResult:
    normalized_text = _normalize(output.text)
    citation_matches = sum(
        1
        for expected in case.expected_citations
        if _citation_matches(expected, output.citations)
    )
    insight_matches = sum(
        1
        for insight in case.expected_insights
        if _text_matches_expected(normalized_text, insight)
    )

    passed = (
        citation_matches >= case.quality_thresholds.min_citations
        and insight_matches >= case.quality_thresholds.min_insights
    )

    return RegressionCaseResult(
        case_id=case.case_id,
        passed=passed,
        citation_matches=citation_matches,
        insight_matches=insight_matches,
        required_citations=case.quality_thresholds.min_citations,
        required_insights=case.quality_thresholds.min_insights,
    )


def _parse_case(case_data: object) -> GoldenDatasetCase:
    case_map = _require_mapping(case_data, "case")
    thresholds = _require_mapping(
        case_map.get("quality_thresholds"),
        "quality_thresholds",
    )
    return GoldenDatasetCase(
        case_id=_require_str(case_map.get("id"), "id"),
        query=_require_str(case_map.get("query"), "query"),
        domains=tuple(_require_str_list(case_map.get("domains"), "domains")),
        expected_citations=tuple(
            _parse_expected_citation(citation)
            for citation in _require_list(
                case_map.get("expected_citations"),
                "expected_citations",
            )
        ),
        expected_insights=tuple(
            _require_str_list(
                case_map.get("expected_insights"),
                "expected_insights",
            )
        ),
        trusted_sources=tuple(
            _parse_trusted_source(source)
            for source in _require_list(
                case_map.get("trusted_sources"),
                "trusted_sources",
            )
        ),
        quality_thresholds=QualityThresholds(
            min_citations=_require_int(
                thresholds.get("min_citations"),
                "min_citations",
            ),
            min_insights=_require_int(
                thresholds.get("min_insights"),
                "min_insights",
            ),
        ),
    )


def _parse_expected_citation(citation_data: object) -> ExpectedCitation:
    citation = _require_mapping(citation_data, "expected citation")
    return ExpectedCitation(
        title=_require_str(citation.get("title"), "citation title"),
        url=_require_str(citation.get("url"), "citation url"),
        source_type=_require_str(citation.get("source_type"), "citation source_type"),
    )


def _parse_trusted_source(source_data: object) -> TrustedSource:
    source = _require_mapping(source_data, "trusted source")
    return TrustedSource(
        title=_require_str(source.get("title"), "source title"),
        url=_require_str(source.get("url"), "source url"),
        source_type=_require_str(source.get("source_type"), "source source_type"),
        snippet=_require_str(source.get("snippet"), "source snippet"),
        supported_claims=tuple(
            _require_str_list(source.get("supported_claims"), "supported_claims")
        ),
        contradicted_claims=tuple(
            _require_str_list(
                source.get("contradicted_claims", []),
                "contradicted_claims",
            )
        ),
    )


def _citation_matches(
    expected: ExpectedCitation,
    citations: Sequence[ExpectedCitation],
) -> bool:
    expected_url = _normalize(expected.url)
    expected_title = _normalize(expected.title)
    for citation in citations:
        candidate = _normalize(f"{citation.title} {citation.url}")
        if expected_url in candidate or expected_title in candidate:
            return True
    return False


def _text_matches_expected(normalized_text: str, expected: str) -> bool:
    normalized_expected = _normalize(expected)
    if normalized_expected in normalized_text:
        return True

    expected_tokens = set(normalized_expected.split())
    if not expected_tokens:
        return False
    text_tokens = set(normalized_text.split())
    return len(expected_tokens & text_tokens) / len(expected_tokens) >= 0.85


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _require_list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return value


def _require_str(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _require_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    return value


def _require_str_list(value: object, label: str) -> list[str]:
    values = _require_list(value, label)
    return [_require_str(item, label) for item in values]
