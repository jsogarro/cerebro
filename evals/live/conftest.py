"""Pytest configuration for live evaluation suite."""

import os
from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any

import pytest


@dataclass
class LiveEvalCostRecord:
    """Single LLM call cost record."""

    check_name: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass
class LiveEvalReport:
    """Aggregate report for live eval run."""

    total_checks: int = 0
    passed_checks: int = 0
    failed_checks: int = 0
    total_cost_usd: float = 0.0
    cost_records: list[LiveEvalCostRecord] = field(default_factory=list)
    check_results: dict[str, dict[str, Any]] = field(default_factory=dict)

    def add_cost(self, record: LiveEvalCostRecord) -> None:
        """Add cost record and update total."""
        self.cost_records.append(record)
        self.total_cost_usd += record.cost_usd

    def add_check_result(self, name: str, status: str, details: dict[str, Any]) -> None:
        """Record check result."""
        self.total_checks += 1
        if status == "passed":
            self.passed_checks += 1
        elif status == "failed":
            self.failed_checks += 1
        self.check_results[name] = {"status": status, **details}


@pytest.fixture(scope="session")
def live_eval_settings_guard() -> None:
    """Guard: require OPENROUTER_API_KEY and ENABLE_LIVE_EVAL=1, or skip entire suite."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    enable_flag = os.getenv("ENABLE_LIVE_EVAL", "0")

    if not api_key or enable_flag != "1":
        pytest.skip(
            "Live eval suite requires OPENROUTER_API_KEY and ENABLE_LIVE_EVAL=1. "
            "Set both environment variables to run live provider checks."
        )


@pytest.fixture(scope="session")
def live_eval_report() -> Generator[LiveEvalReport, None, None]:
    """Session-scoped report collector."""
    report = LiveEvalReport()
    yield report
    # Report is finalized in scripts/run_live_evals.sh via report.py


@pytest.fixture(scope="session")
def live_eval_cost_meter(
    live_eval_report: LiveEvalReport,
) -> Generator[LiveEvalReport, None, None]:
    """Cost meter: track cumulative cost and abort if budget exceeded."""
    budget_usd = float(os.getenv("LIVE_EVAL_BUDGET_USD", "0.25"))
    yield live_eval_report

    # Post-session check: fail loudly if budget exceeded
    if live_eval_report.total_cost_usd > budget_usd:
        pytest.fail(
            f"Live eval run exceeded budget: "
            f"${live_eval_report.total_cost_usd:.4f} > ${budget_usd:.4f}"
        )


@pytest.fixture(autouse=True)
def _require_settings_guard(live_eval_settings_guard: None) -> None:
    """Auto-apply settings guard to all tests in this directory."""


_session_report: LiveEvalReport | None = None


@pytest.fixture(scope="session", autouse=True)
def _report_finalizer(
    live_eval_report: LiveEvalReport,
) -> Generator[None, None, None]:
    """Auto-run fixture to finalize report at session end."""
    global _session_report
    _session_report = live_eval_report
    yield
    # Generate report at session teardown
    from pathlib import Path

    from evals.live.report import generate_report

    if _session_report is not None:
        output_dir = Path("evals/out")
        generate_report(_session_report, output_dir)
