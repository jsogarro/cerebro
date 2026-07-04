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


@pytest.fixture(scope="session", autouse=True)
def _capture_llm_costs(live_eval_report: LiveEvalReport) -> Generator[None, None, None]:
    """Capture every LLM call's cost/model into the report via the telemetry hook."""
    import src.ai_brain.providers.base_provider as bp

    original = bp.record_llm_call

    def wrapper(metrics):  # type: ignore[no-untyped-def]
        live_eval_report.add_cost(
            LiveEvalCostRecord(
                check_name=getattr(metrics, "provider", "unknown"),
                model=getattr(metrics, "model", "unknown") or "unknown",
                input_tokens=int(getattr(metrics, "prompt_tokens", 0) or 0),
                output_tokens=int(getattr(metrics, "completion_tokens", 0) or 0),
                cost_usd=float(getattr(metrics, "cost_usd", 0.0) or 0.0),
            )
        )
        return original(metrics)

    bp.record_llm_call = wrapper
    yield
    bp.record_llm_call = original


@pytest.fixture()
def openrouter_spy() -> Generator[list[dict[str, Any]], None, None]:
    """Record every OpenRouter request payload (model, response_format, max_tokens)."""
    from src.ai_brain.providers.openrouter_provider import OpenRouterProvider

    calls: list[dict[str, Any]] = []
    original = OpenRouterProvider._make_api_request

    async def spy(self, payload):  # type: ignore[no-untyped-def]
        calls.append(
            {
                "model": payload.get("model"),
                "response_format": (payload.get("response_format") or {}).get("type"),
                "max_tokens": payload.get("max_tokens"),
            }
        )
        return await original(self, payload)

    OpenRouterProvider._make_api_request = spy  # type: ignore[method-assign]
    yield calls
    OpenRouterProvider._make_api_request = original  # type: ignore[method-assign]


@pytest.fixture()
def gemini_fallback_guard() -> Generator[dict[str, int], None, None]:
    """Count Gemini fallback invocations - fail-loudly doctrine wants ZERO."""
    from src.services.gemini_service import GeminiService

    counter = {"text": 0, "structured": 0}
    orig_text = GeminiService.generate_content
    orig_structured = GeminiService.generate_structured_content

    async def text_spy(self, prompt):  # type: ignore[no-untyped-def]
        counter["text"] += 1
        return await orig_text(self, prompt)

    async def structured_spy(self, prompt, schema):  # type: ignore[no-untyped-def]
        counter["structured"] += 1
        return await orig_structured(self, prompt, schema)

    GeminiService.generate_content = text_spy  # type: ignore[method-assign]
    GeminiService.generate_structured_content = structured_spy  # type: ignore[method-assign]
    yield counter
    GeminiService.generate_content = orig_text  # type: ignore[method-assign]
    GeminiService.generate_structured_content = orig_structured  # type: ignore[method-assign]
