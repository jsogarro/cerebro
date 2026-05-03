"""Tests for shared LLM observability instrumentation."""

import ast
import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from src.ai_brain.providers.base_provider import (
    BaseProvider,
    ModelRequest,
    ModelResponse,
)
from src.api.middleware.cost_drift import LLMCostDriftMiddleware
from src.core.observability import (
    LLMCallMetrics,
    get_llm_request_cost_tracking,
    record_llm_call,
    set_llm_request_estimated_cost,
)

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
PROVIDERS_DIR = SRC_DIR / "ai_brain" / "providers"
MODELS_DB_DIR = SRC_DIR / "models" / "db"
RELIABILITY_DIR = SRC_DIR / "reliability"
MEMORY_DIR = SRC_DIR / "ai_brain" / "memory"
API_SERVICE_WEBSOCKET_MODULES = [
    SRC_DIR / "api" / "services" / "agent_execution_service.py",
    SRC_DIR / "api" / "services" / "direct_execution_service.py",
    SRC_DIR / "api" / "services" / "supervisor_progress_tracker.py",
    SRC_DIR / "api" / "services" / "talkhier_session_manager.py",
    SRC_DIR / "api" / "services" / "talkhier_session_service.py",
    SRC_DIR / "api" / "websocket" / "event_publisher.py",
    SRC_DIR / "api" / "websocket" / "talkhier_websocket_events.py",
]
REPOSITORY_PROMPT_PLATFORM_MODULES = [
    SRC_DIR / "repositories" / "base.py",
    SRC_DIR / "repositories" / "report_repository.py",
    SRC_DIR / "prompts" / "manager.py",
    SRC_DIR / "prompts" / "versioning.py",
    SRC_DIR / "research_platform" / "__init__.py",
]
MCP_MODULES = [
    SRC_DIR / "mcp" / "base.py",
    SRC_DIR / "mcp" / "client.py",
    SRC_DIR / "mcp" / "registry.py",
    SRC_DIR / "mcp" / "server.py",
    SRC_DIR / "mcp" / "tools" / "academic_search_tool.py",
    SRC_DIR / "mcp" / "tools" / "citation_tool.py",
    SRC_DIR / "mcp" / "tools" / "knowledge_graph_tool.py",
    SRC_DIR / "mcp" / "tools" / "statistics_tool.py",
]
REPORT_SERVICE_MODULES = [
    SRC_DIR / "services" / "template_renderer.py",
    SRC_DIR / "services" / "report_generator.py",
    SRC_DIR / "services" / "report_output_generator.py",
    SRC_DIR / "services" / "report_structure_builder.py",
    SRC_DIR / "services" / "report_storage.py",
    SRC_DIR / "services" / "visualization_generator.py",
    SRC_DIR / "services" / "exporters" / "pdf_exporter.py",
    SRC_DIR / "services" / "exporters" / "docx_exporter.py",
    SRC_DIR / "services" / "exporters" / "latex_exporter.py",
    SRC_DIR / "services" / "cache" / "cache_manager.py",
]


class StubProvider(BaseProvider):  # type: ignore[misc]
    """Minimal concrete provider for base instrumentation tests."""

    def _get_provider_name(self) -> str:
        return "stub"

    async def generate(
        self,
        request: ModelRequest,
        model_name: str | None = None,
    ) -> ModelResponse:
        return ModelResponse(request_id=request.request_id)


def sample_value(name: str, labels: dict[str, str]) -> float:
    value = REGISTRY.get_sample_value(name, labels)
    return float(value or 0.0)


def test_record_llm_call_updates_prometheus_metrics() -> None:
    labels = {"model": "unit-model", "provider": "unit-provider"}
    prompt_labels = {**labels, "type": "prompt"}
    completion_labels = {**labels, "type": "completion"}

    before_prompt = sample_value("llm_tokens_total", prompt_labels)
    before_completion = sample_value("llm_tokens_total", completion_labels)
    before_count = sample_value("llm_call_duration_seconds_count", labels)
    before_cost = sample_value("llm_cost_usd_total", labels)

    record_llm_call(
        LLMCallMetrics(
            provider="unit-provider",
            model="unit-model",
            prompt_tokens=7,
            completion_tokens=11,
            latency_ms=250,
            cost_usd=0.012,
            request_id="request-1",
        )
    )

    assert sample_value("llm_tokens_total", prompt_labels) == before_prompt + 7
    assert (
        sample_value("llm_tokens_total", completion_labels)
        == before_completion + 11
    )
    assert sample_value("llm_call_duration_seconds_count", labels) == before_count + 1
    assert sample_value("llm_cost_usd_total", labels) == pytest.approx(
        before_cost + 0.012
    )


def test_base_provider_records_llm_metrics_after_postprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[LLMCallMetrics] = []

    def capture_metrics(metrics: LLMCallMetrics) -> None:
        captured.append(metrics)

    monkeypatch.setattr(
        "src.ai_brain.providers.base_provider.record_llm_call",
        capture_metrics,
    )

    provider = StubProvider({})
    response = ModelResponse(
        request_id="request-2",
        provider="stub",
        model_name="stub-model",
        prompt_tokens=3,
        completion_tokens=5,
        latency_ms=120,
        cost_estimate=0.004,
        success=True,
    )

    processed = asyncio.run(provider._postprocess_response(response, ModelRequest()))

    assert processed is response
    assert provider.request_count == 1
    assert provider.error_count == 0
    assert captured == [
        LLMCallMetrics(
            provider="stub",
            model="stub-model",
            prompt_tokens=3,
            completion_tokens=5,
            latency_ms=120,
            cost_usd=0.004,
            request_id="request-2",
            success=True,
        )
    ]


def test_provider_modules_use_structlog_logger() -> None:
    for provider_path in PROVIDERS_DIR.glob("*.py"):
        assert_no_stdlib_logging_logger(provider_path)


def test_models_db_session_modules_use_structlog_logger() -> None:
    for module_path in [
        MODELS_DB_DIR / "migrations.py",
        MODELS_DB_DIR / "session.py",
    ]:
        assert_no_stdlib_logging_logger(module_path)


def test_reliability_modules_use_structlog_logger() -> None:
    for module_path in RELIABILITY_DIR.glob("*.py"):
        assert_no_stdlib_logging_logger(module_path)


def test_memory_modules_use_structlog_logger() -> None:
    for module_path in MEMORY_DIR.glob("*.py"):
        assert_no_stdlib_logging_logger(module_path)


def test_api_service_websocket_modules_use_structlog_logger() -> None:
    for module_path in API_SERVICE_WEBSOCKET_MODULES:
        assert_no_stdlib_logging_logger(module_path)


def test_repository_prompt_platform_modules_use_structlog_logger() -> None:
    for module_path in REPOSITORY_PROMPT_PLATFORM_MODULES:
        assert_no_stdlib_logging_logger(module_path)


def test_mcp_modules_use_structlog_logger() -> None:
    for module_path in MCP_MODULES:
        assert_no_stdlib_logging_logger(module_path)


def test_report_service_modules_use_structlog_logger() -> None:
    for module_path in REPORT_SERVICE_MODULES:
        assert_no_stdlib_logging_logger(module_path)


def assert_no_stdlib_logging_logger(module_path: Path) -> None:
    tree = ast.parse(module_path.read_text())

    logging_imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "logging"
    ]
    get_logger_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "logging"
        and node.func.attr == "getLogger"
    ]

    assert logging_imports == [], module_path
    assert get_logger_calls == [], module_path


def test_cost_drift_middleware_records_threshold_event() -> None:
    app = FastAPI()
    app.add_middleware(LLMCostDriftMiddleware, threshold_ratio=0.2)

    @app.post("/llm-cost")
    async def llm_cost_endpoint() -> dict[str, bool]:
        set_llm_request_estimated_cost(0.01)
        record_llm_call(
            LLMCallMetrics(
                provider="unit-provider",
                model="unit-model",
                prompt_tokens=2,
                completion_tokens=4,
                latency_ms=100,
                cost_usd=0.013,
                request_id="drift-request",
            )
        )
        return {"ok": True}

    labels = {"method": "POST", "route": "/llm-cost", "direction": "over"}
    before_drift_events = sample_value("llm_cost_drift_events_total", labels)
    before_drift_count = sample_value(
        "llm_request_cost_drift_ratio_count",
        {"method": "POST", "route": "/llm-cost"},
    )

    with TestClient(app) as client:
        response = client.post("/llm-cost")

    assert response.status_code == 200
    assert sample_value("llm_cost_drift_events_total", labels) == (
        before_drift_events + 1
    )
    assert sample_value(
        "llm_request_cost_drift_ratio_count",
        {"method": "POST", "route": "/llm-cost"},
    ) == (before_drift_count + 1)
    assert get_llm_request_cost_tracking() is None
