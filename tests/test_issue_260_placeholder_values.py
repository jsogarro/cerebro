"""Entry points must report real values or fail, never placeholders."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.ai_brain.router.masr import MASRouter
from src.ai_brain.router.query_analyzer import ComplexityLevel
from src.ai_brain.router.routing_types import CollaborationMode, RoutingStrategy
from src.api.routes import query_api
from src.api.services.masr_routing_service import MASRRoutingService
from src.core.config import settings
from src.core.contracts.execution_plan import (
    COLLABORATION_MODE_SUPPORT,
    CollaborationModeSupport,
)
from src.models.report import ReportFormat, ReportGenerationRequest
from src.services.report_config import ReportSettings
from src.services.report_generator import ReportGenerationError, ReportGenerator
from tests.test_fast_path import make_complexity


@pytest.fixture
def generator(tmp_path: Any) -> ReportGenerator:
    return ReportGenerator(
        ReportSettings(
            report_storage_path=str(tmp_path / "out"),
            template_path=str(tmp_path / "templates"),
            enable_pdf_generation=False,
            enable_latex_generation=False,
            enable_visualizations=False,
        )
    )


@pytest.mark.asyncio
async def test_report_with_project_id_never_uses_caller_workflow_data(
    generator: ReportGenerator,
) -> None:
    request = ReportGenerationRequest(
        project_id=uuid4(),
        workflow_data={"title": "Caller title", "query": "Caller question"},
        formats=[ReportFormat.HTML],
    )

    with pytest.raises(ReportGenerationError, match="stored results"):
        await generator._extract_input_data(request)


@pytest.mark.asyncio
async def test_report_with_only_project_id_fails_instead_of_inventing_content(
    generator: ReportGenerator,
) -> None:
    request = ReportGenerationRequest(project_id=uuid4(), formats=[ReportFormat.HTML])

    with pytest.raises(ReportGenerationError, match="stored results"):
        await generator._extract_input_data(request)


def _service() -> MASRRoutingService:
    return MASRRoutingService.__new__(MASRRoutingService)


@pytest.mark.parametrize(
    ("deepseek", "llama", "gemini"),
    [(False, False, False), (True, False, True), (True, True, True)],
)
def test_model_availability_reflects_configuration(
    monkeypatch: pytest.MonkeyPatch, deepseek: bool, llama: bool, gemini: bool
) -> None:
    # Configured providers are unprobed, so their readiness is unknown (None).
    monkeypatch.setattr(settings, "DEEPSEEK_ENABLED", deepseek)
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "k" if deepseek else None)
    monkeypatch.setattr(settings, "LLAMA_ENABLED", llama)
    monkeypatch.setattr(settings, "GEMINI_ENABLED", gemini)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "k" if gemini else None)
    monkeypatch.setattr(settings, "OPENROUTER_ENABLED", False)

    assert _service()._check_model_availability() == {
        "deepseek": None if deepseek else False,
        "gemini": None if gemini else False,
        "llama": None if llama else False,
        "openrouter": False,
    }


def test_enabled_provider_without_key_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "GEMINI_ENABLED", True)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", None)

    assert _service()._check_model_availability()["gemini"] is False


class _FakeRouter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], RoutingStrategy | None]] = []

    async def route(
        self,
        query: str,
        context: dict[str, Any] | None = None,
        strategy: RoutingStrategy | None = None,
    ) -> Any:
        self.calls.append((query, context or {}, strategy))
        chosen = strategy or RoutingStrategy.QUALITY_FOCUSED
        return SimpleNamespace(
            routing_strategy=chosen,
            estimated_cost=0.1234,
            estimated_latency_ms=4321,
            estimated_quality=0.77,
            confidence_score=0.66,
            complexity_analysis=SimpleNamespace(
                level=ComplexityLevel.COMPLEX,
                domains=["finance"],
                recommended_agents={"synthesis": 2},
            ),
        )


@pytest.mark.asyncio
async def test_routing_recommendation_comes_from_the_router() -> None:
    router = _FakeRouter()
    service = SimpleNamespace(router=router)

    result = await query_api.get_routing_recommendation(
        query="q", context='{"k": 1}', service=service
    )

    assert router.calls[0] == ("q", {"k": 1}, None)
    recommendation = result["routing_recommendation"]
    assert recommendation["estimated_cost"] == 0.1234
    assert recommendation["estimated_time_seconds"] == pytest.approx(4.321)
    assert recommendation["estimated_quality"] == 0.77
    assert recommendation["suggested_strategy"] == "quality_focused"
    assert result["query_analysis"]["confidence"] == 0.66
    assert result["query_analysis"]["estimated_domains"] == ["finance"]
    assert "quality_focused" not in result["alternative_strategies"]


def test_ws_health_timestamp_is_current() -> None:
    from src.api.main import app

    body = TestClient(app).get("/ws/health").json()

    stamp = datetime.fromisoformat(body["timestamp"])
    assert abs(datetime.now(UTC) - stamp) < timedelta(minutes=1)


@pytest.mark.parametrize("domains", [["a"], ["a", "b"]])
def test_masr_never_selects_a_mode_the_plan_compiler_rejects(
    domains: list[str],
) -> None:
    router = MASRouter(config={"default_strategy": "balanced"})
    complexity = make_complexity(
        level=ComplexityLevel.MODERATE, domains=domains, uncertainty=0.9
    )

    mode = router._determine_collaboration_mode(complexity, SimpleNamespace())

    assert COLLABORATION_MODE_SUPPORT[mode] is CollaborationModeSupport.IMPLEMENTED
    assert mode is not CollaborationMode.DEBATE
