"""Provider catalog, vocabulary, readiness and plan-route behaviour that spends nothing."""

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.ai_brain.providers.base_provider import ModelRequest
from src.core.config import settings
from src.core.kernel.component_keys import PROVIDER_KEYS

SENT_PROMPTS: list[str] = []


@pytest.fixture
def gemini_provider(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The real GeminiService; only its network call is stubbed."""
    from src.ai_brain.providers.gemini_provider import GeminiProvider
    from src.services import gemini_service as service_module

    SENT_PROMPTS.clear()

    async def fake_generate(_self: Any, prompt: str) -> str:
        SENT_PROMPTS.append(prompt)
        return "stub answer"

    monkeypatch.setattr(service_module, "ChatGoogleGenerativeAI", lambda **_k: None)
    monkeypatch.setattr(
        service_module.GeminiService, "_generate_content", fake_generate
    )
    monkeypatch.setenv("GEMINI_MODEL", "gemini-env-model")
    return GeminiProvider({"api_key": "k"})


async def test_valid_gemini_request_reaches_the_service(gemini_provider: Any) -> None:
    response = await gemini_provider.generate(
        ModelRequest(prompt="hello"), "gemini-pro"
    )

    assert response.success, response.error_message
    assert response.content == "stub answer"
    assert SENT_PROMPTS == ["hello"]
    # The service runs GEMINI_MODEL, so that is what is reported, not "gemini-pro".
    assert response.model_name == "gemini-env-model"


async def test_concurrent_first_calls_load_the_catalog_once(
    gemini_provider: Any,
) -> None:
    loads = 0
    original = gemini_provider._get_supported_models_legacy

    def counting() -> list[str]:
        nonlocal loads
        loads += 1
        return original()

    gemini_provider._get_supported_models_legacy = counting

    await asyncio.gather(
        *(
            gemini_provider.generate(ModelRequest(prompt="x"), "gemini-pro")
            for _ in range(5)
        )
    )

    assert loads == 1


async def test_unknown_gemini_model_fails_before_dispatch(gemini_provider: Any) -> None:
    response = await gemini_provider.generate(ModelRequest(prompt="x"), "not-a-model")

    assert not response.success
    assert SENT_PROMPTS == []


def test_optimizer_models_use_dispatchable_provider_names() -> None:
    from src.ai_brain.router.cost_optimizer import CostOptimizer

    models = CostOptimizer({})._initialize_models_legacy()

    assert {m.provider for m in models.values()} <= set(PROVIDER_KEYS)


def test_masr_fallback_decision_uses_dispatchable_provider_name() -> None:
    from src.ai_brain.router.masr import MASRouter, RoutingStrategy

    router = MASRouter.__new__(MASRouter)
    router.default_strategy = RoutingStrategy("balanced")
    decision = router._create_fallback_decision("q", "query", RuntimeError("boom"))

    assert decision.optimization_result.primary_model.provider in PROVIDER_KEYS


class _Unprobeable:
    """A provider whose paid health probe must never run on the request path."""

    supported_models = ["m"]

    def __init__(self) -> None:
        self.probed = False

    async def health_check(self) -> Any:
        self.probed = True
        raise AssertionError("paid probe")

    async def generate(self, request: ModelRequest, model_name: str | None) -> Any:
        from src.ai_brain.providers.base_provider import ModelResponse

        return ModelResponse(request_id=request.request_id, provider="p", success=True)


async def test_router_dispatch_does_not_run_a_paid_health_probe() -> None:
    from src.ai_brain.providers.model_router import ModelRouter

    router = ModelRouter({"providers": {}}, component_registry=object())  # type: ignore[arg-type]
    provider = _Unprobeable()
    router.registry.providers["p"] = provider  # type: ignore[assignment]

    response = await router._try_provider(ModelRequest(prompt="x"), "p", "m")

    assert response.success
    assert not provider.probed


def test_plan_route_router_is_built_once_per_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.agents import llm_worker_base

    built: list[Any] = []

    class _Router:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            built.append(self)

    monkeypatch.setattr("src.ai_brain.providers.ModelRouter", _Router)
    worker = llm_worker_base.LLMWorkerAgentBase.__new__(
        llm_worker_base.LLMWorkerAgentBase
    )
    worker.config = {}
    policy = {"primary": {"provider": "gemini", "model": "gemini-pro"}}

    first = worker._build_plan_route(policy)
    second = worker._build_plan_route(policy)

    assert first.router is second.router
    assert len(built) == 1


@pytest.mark.parametrize(("configured", "expected"), [(True, None), (False, False)])
def test_configured_provider_readiness_is_unknown_not_true(
    monkeypatch: pytest.MonkeyPatch, configured: bool, expected: bool | None
) -> None:
    from src.api.services.masr_routing_service import MASRRoutingService

    monkeypatch.setattr(settings, "GEMINI_ENABLED", configured)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "k" if configured else None)

    service = MASRRoutingService.__new__(MASRRoutingService)
    assert service._check_model_availability()["gemini"] is expected


def test_ready_reports_providers_without_gating_or_probing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.api.main import app
    from src.api.routes import health

    async def ok() -> None:
        return None

    monkeypatch.setattr(health, "_check_database", ok)
    monkeypatch.setattr(health, "_check_redis", ok)
    monkeypatch.setattr(health, "_check_audit_logger", lambda _request: ok())
    monkeypatch.setattr(health, "_check_jwt_keys", ok)
    monkeypatch.setattr(settings, "GEMINI_ENABLED", True)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "k")

    response = TestClient(app).get("/ready")

    assert response.status_code == 200
    assert response.json()["checks"]["providers"] == "unknown"


def test_ready_stays_200_when_no_provider_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.api.main import app
    from src.api.routes import health

    async def ok() -> None:
        return None

    monkeypatch.setattr(health, "_check_database", ok)
    monkeypatch.setattr(health, "_check_redis", ok)
    monkeypatch.setattr(health, "_check_audit_logger", lambda _request: ok())
    monkeypatch.setattr(health, "_check_jwt_keys", ok)
    for name in (
        "GEMINI_ENABLED",
        "DEEPSEEK_ENABLED",
        "LLAMA_ENABLED",
        "OPENROUTER_ENABLED",
    ):
        monkeypatch.setattr(settings, name, False)

    response = TestClient(app).get("/ready")

    assert response.status_code == 200
    assert response.json()["checks"]["providers"] == "unconfigured"


@pytest.mark.parametrize("name", ["GEMINI_MAX_CONCURRENT", "GEMINI_TEMPERATURE"])
def test_invalid_gemini_env_value_is_rejected_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    from src.services.gemini_config import GeminiConfig

    monkeypatch.setenv(name, "secret-looking-garbage")

    with pytest.raises(ValueError, match=name) as excinfo:
        GeminiConfig.from_env(api_key="k")
    assert "secret-looking-garbage" not in str(excinfo.value)


async def test_concurrent_first_calls_share_one_async_catalog_load(
    gemini_provider: Any,
) -> None:
    from types import SimpleNamespace

    calls = 0

    class _Manager:
        async def get_provider_configuration(self, _name: str) -> Any:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return SimpleNamespace(enabled=True)

        async def get_models_for_provider(self, _name: str) -> dict[str, Any]:
            return {}

    gemini_provider.model_config_manager = _Manager()

    await asyncio.gather(
        *(gemini_provider.ensure_configuration_loaded() for _ in range(5))
    )

    assert calls == 1


def test_every_model_the_optimizer_emits_is_in_its_providers_catalog() -> None:
    from src.ai_brain.providers.model_router import ModelRouter
    from src.ai_brain.router.cost_optimizer import CostOptimizer
    from src.ai_brain.router.masr import MASRouter, RoutingStrategy

    router = ModelRouter(
        {
            "providers": {
                "llama": {"enabled": True},
                "deepseek": {"enabled": True, "api_key": "k"},
                "gemini": {"enabled": True, "api_key": "k"},
            }
        }
    )
    masr = MASRouter.__new__(MASRouter)
    masr.default_strategy = RoutingStrategy("balanced")
    fallback = masr._create_fallback_decision("q", "q", RuntimeError("x"))
    emitted = [
        (spec.provider, spec.name)
        for spec in CostOptimizer({})._initialize_models_legacy().values()
    ] + [
        (
            fallback.optimization_result.primary_model.provider,
            fallback.optimization_result.primary_model.name,
        )
    ]

    for provider_name, model in emitted:
        provider = router.registry.providers[provider_name]
        asyncio.run(provider.ensure_configuration_loaded())
        assert provider.supports_model(model), (provider_name, model)


class _Failing(_Unprobeable):
    def __init__(self, error_type: str | None = None) -> None:
        super().__init__()
        self.error_type = error_type
        self.calls = 0

    async def generate(self, request: ModelRequest, model_name: str | None) -> Any:
        from src.ai_brain.providers.base_provider import ModelResponse

        self.calls += 1
        return ModelResponse(
            request_id=request.request_id,
            provider="p",
            success=False,
            error_message="503",
            error_type=self.error_type,
        )


def _router_with_provider(provider: Any) -> Any:
    from src.ai_brain.providers.model_router import ModelRouter

    router = ModelRouter({"providers": {}}, component_registry=object())  # type: ignore[arg-type]
    router.registry.providers["p"] = provider
    return router


class _ProbeRaises(_Unprobeable):
    async def health_check(self) -> Any:
        raise RuntimeError("down")


async def test_provider_whose_probe_raises_is_observed_unhealthy() -> None:
    router = _router_with_provider(_ProbeRaises())

    status = await router._get_provider_health("p")

    assert not status.healthy
    assert router._is_observed_unhealthy("p")


async def test_failed_requests_stop_routing_to_the_provider_for_the_interval() -> None:
    provider = _Failing("generation_error")
    router = _router_with_provider(provider)

    for _ in range(3):
        await router.route_and_generate(ModelRequest(prompt="x"))

    assert provider.calls == 1
    assert router._is_observed_unhealthy("p")


async def test_request_specific_rejections_do_not_mark_the_provider() -> None:
    provider = _Failing("model_error")
    router = _router_with_provider(provider)

    for _ in range(3):
        await router.route_and_generate(ModelRequest(prompt="x"))

    assert provider.calls == 3
    assert not router._is_observed_unhealthy("p")


async def test_a_success_after_the_interval_clears_the_mark() -> None:
    provider = _Failing("generation_error")
    router = _router_with_provider(provider)
    await router.route_and_generate(ModelRequest(prompt="x"))
    router.registry.last_health_check["p"] -= timedelta(
        seconds=router.health_check_interval + 1
    )

    ok = _Unprobeable()
    router.registry.providers["p"] = ok  # type: ignore[assignment]
    response = await router._try_provider(ModelRequest(prompt="x"), "p", "m")

    assert response.success
    assert not router._is_observed_unhealthy("p")


def _plan_route(monkeypatch: pytest.MonkeyPatch, api_key: str) -> Any:
    from src.agents import llm_worker_base

    monkeypatch.setattr(
        type(settings),
        "get_ai_brain_config",
        lambda _self: {"providers": {"gemini": {"api_key": api_key}}},
    )
    monkeypatch.setattr("src.ai_brain.providers.ModelRouter", lambda *a, **k: object())
    worker = llm_worker_base.LLMWorkerAgentBase.__new__(
        llm_worker_base.LLMWorkerAgentBase
    )
    worker.config = {}
    return worker._build_plan_route(
        {"primary": {"provider": "gemini", "model": "gemini-pro"}}
    )


def test_plan_router_cache_is_keyed_by_config_without_holding_the_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.agents import llm_worker_base

    first = _plan_route(monkeypatch, "secret-key-one")
    second = _plan_route(monkeypatch, "secret-key-two")

    assert first.router is not second.router
    assert not any(
        "secret-key" in part
        for key in llm_worker_base._PLAN_ROUTERS
        for part in map(str, key)
    )


async def test_router_status_is_healthy_when_every_provider_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from src.api.services.masr_routing_service import MASRRoutingService

    async def adaptive() -> dict[str, Any]:
        return {}

    monkeypatch.setattr(settings, "GEMINI_ENABLED", True)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "k")
    service = MASRRoutingService.__new__(MASRRoutingService)
    service.start_time = datetime.now(UTC)
    service.performance_metrics = {}
    service.bridge = SimpleNamespace()
    service.router = SimpleNamespace(get_adaptive_status=adaptive)

    status = await service.get_router_status()

    assert status.status == "healthy"
