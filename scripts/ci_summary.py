"""Markdown test summary from pytest JUnit (and optional coverage) XML.

A missing or malformed artifact exits nonzero: "unavailable" must never read
as "zero failures".
Usage: ci_summary.py JUNIT_XML [COVERAGE_XML] >> "$GITHUB_STEP_SUMMARY"
"""

import sys
import xml.etree.ElementTree as ET  # nosec B405 - parses our own CI artifacts
from pathlib import Path


class SummaryUnavailableError(Exception):
    pass


def _parse(path: Path) -> ET.Element:
    try:
        return ET.parse(path).getroot()  # nosec B314
    except (OSError, ET.ParseError) as exc:
        raise SummaryUnavailableError(f"{path}: {exc}") from exc


def summarize(junit: Path, coverage: Path | None = None) -> str:
    suites = list(_parse(junit).iter("testsuite"))
    if not suites:
        raise SummaryUnavailableError(f"{junit}: no testsuite totals")
    total = {
        k: sum(int(s.get(k, 0)) for s in suites)
        for k in ("tests", "failures", "errors", "skipped")
    }
    seconds = sum(float(s.get("time", 0)) for s in suites)
    lines = [
        f"{total['tests']} tests, {total['failures']} failed, {total['errors']} errors, "
        f"{total['skipped']} skipped in {seconds:.1f}s"
    ]
    if coverage is not None:
        rate = float(_parse(coverage).get("line-rate", "nan"))
        lines.append(f"Line coverage {rate * 100:.2f}%")
    return "\n\n".join(lines)


if __name__ == "__main__":
    try:
        print(
            summarize(
                Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None
            )
        )
    except SummaryUnavailableError as exc:
        print(f"Test summary unavailable: {exc}")
        sys.exit(1)
