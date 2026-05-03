"""Tests for shared LLM observability instrumentation."""

import ast
import asyncio
from pathlib import Path

import pytest
from prometheus_client import REGISTRY

from src.ai_brain.providers.base_provider import (
    BaseProvider,
    ModelRequest,
    ModelResponse,
)
from src.core.observability import LLMCallMetrics, record_llm_call

PROVIDERS_DIR = Path(__file__).resolve().parents[1] / "src" / "ai_brain" / "providers"


class StubProvider(BaseProvider):
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
        tree = ast.parse(provider_path.read_text())

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

        assert logging_imports == [], provider_path
        assert get_logger_calls == [], provider_path
