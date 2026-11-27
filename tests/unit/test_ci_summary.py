import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "ci_summary", Path(__file__).resolve().parents[2] / "scripts" / "ci_summary.py"
)
ci_summary = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ci_summary)

JUNIT = (
    '<testsuites><testsuite tests="5" failures="1" errors="1" skipped="1" time="2.5">'
    "</testsuite></testsuites>"
)
COVERAGE = '<coverage line-rate="0.4321"></coverage>'


def test_summary_reports_measured_counts(tmp_path: Path) -> None:
    junit = tmp_path / "j.xml"
    junit.write_text(JUNIT)
    out = ci_summary.summarize(junit)
    assert "5 tests" in out and "1 failed" in out and "1 errors" in out
    assert "1 skipped" in out and "2.5s" in out


def test_summary_includes_coverage_when_given(tmp_path: Path) -> None:
    junit, cov = tmp_path / "j.xml", tmp_path / "c.xml"
    junit.write_text(JUNIT)
    cov.write_text(COVERAGE)
    assert "43.21%" in ci_summary.summarize(junit, cov)


def test_missing_artifact_is_an_error_not_zero_failures(tmp_path: Path) -> None:
    with pytest.raises(ci_summary.SummaryUnavailableError):
        ci_summary.summarize(tmp_path / "absent.xml")


def test_malformed_artifact_is_an_error(tmp_path: Path) -> None:
    junit = tmp_path / "j.xml"
    junit.write_text("<testsuites><oops")
    with pytest.raises(ci_summary.SummaryUnavailableError):
        ci_summary.summarize(junit)


def test_xml_without_totals_is_an_error(tmp_path: Path) -> None:
    junit = tmp_path / "j.xml"
    junit.write_text("<testsuites></testsuites>")
    with pytest.raises(ci_summary.SummaryUnavailableError):
        ci_summary.summarize(junit)
