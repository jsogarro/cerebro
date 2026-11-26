"""Handlers in ai_brain providers/router/config catch only the errors the try body
can raise; anything else propagates instead of becoming a default or an error string."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import redis.exceptions

from src.ai_brain.config.integration_service import ConfigurationIntegrationService
from src.ai_brain.config.model_config_manager import ModelConfigManager
from src.ai_brain.providers.base_provider import (
    ModelRequest,
    ModelResponse,
    ProviderHealthStatus,
)
from src.ai_brain.providers.deepseek_provider import DeepSeekProvider
from src.ai_brain.providers.gemini_provider import GeminiProvider
from src.ai_brain.providers.llama_provider import LlamaProvider
from src.ai_brain.providers.model_router import ModelRouter
from src.ai_brain.providers.openrouter_provider import OpenRouterProvider
from src.ai_brain.router.adaptive_state_store import (
    RedisAdaptiveStateStore,
    StateLoadStatus,
)
from src.ai_brain.router.cost_optimizer import CostOptimizer

pytestmark = pytest.mark.asyncio


def _request() -> ModelRequest:
    return ModelRequest(prompt="hello", max_tokens=50)


# --- providers: generate() ----------------------------------------------------


def _openrouter() -> OpenRouterProvider:
    return OpenRouterProvider({"api_key": "k", "validate_slugs_on_startup": False})


@pytest.mark.parametrize(
    "build",
    [
        lambda: DeepSeekProvider({"api_key": "k"}),
        lambda: LlamaProvider({}),
        _openrouter,
    ],
    ids=["deepseek", "llama", "openrouter"],
)
async def test_generate_turns_network_errors_into_error_response_only(build):
    provider = build()
    provider._ensure_model_available = AsyncMock()
    provider._make_api_request = AsyncMock(side_effect=httpx.ConnectError("down"))
    response = await provider.generate(_request())
    assert response.success is False
    assert response.error_type == "generation_error"

    provider._make_api_request = AsyncMock(side_effect=AttributeError("bug"))
    with pytest.raises(AttributeError):
        await provider.generate(_request())


async def test_llama_api_status_error_is_still_an_error_response():
    provider = LlamaProvider({})
    provider._ensure_model_available = AsyncMock()
    provider.client = MagicMock()
    provider.client.post = AsyncMock(
        return_value=MagicMock(status_code=500, aread=AsyncMock(return_value=b"boom"))
    )
    response = await provider.generate(_request())
    assert response.success is False
    assert "boom" in response.error_message


async def test_deepseek_api_status_error_is_still_an_error_response():
    provider = DeepSeekProvider({"api_key": "k"})
    provider.client = MagicMock()
    provider.client.post = AsyncMock(
        return_value=MagicMock(status_code=500, aread=AsyncMock(return_value=b"boom"))
    )
    response = await provider.generate(_request())
    assert response.success is False
    assert "boom" in response.error_message


# --- providers: stream() ------------------------------------------------------


async def test_deepseek_stream_propagates_failure_instead_of_yielding_error_text():
    provider = DeepSeekProvider({"api_key": "k"})
    await provider.ensure_configuration_loaded()
    provider.client = MagicMock()
    provider.client.stream = MagicMock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(httpx.ConnectError):
        async for _ in provider.stream(_request()):
            pass


async def test_llama_stream_propagates_failure_instead_of_yielding_error_text():
    provider = LlamaProvider({})
    await provider.ensure_configuration_loaded()
    provider._ensure_model_available = AsyncMock()
    provider.client = MagicMock()
    provider.client.stream = MagicMock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(httpx.ConnectError):
        async for _ in provider.stream(_request()):
            pass


async def test_gemini_stream_and_health_check_propagate_unexpected_errors():
    provider = GeminiProvider({"api_key": "k"})
    provider.generate = AsyncMock(side_effect=AttributeError("bug"))
    with pytest.raises(AttributeError):
        async for _ in provider.stream(_request()):
            pass
    with pytest.raises(AttributeError):
        await provider.health_check()


# --- providers: health_check() ------------------------------------------------


async def test_deepseek_and_openrouter_health_check_propagate_unexpected_errors():
    for provider in (DeepSeekProvider({"api_key": "k"}), _openrouter()):
        provider.generate = AsyncMock(side_effect=AttributeError("bug"))
        with pytest.raises(AttributeError):
            await provider.health_check()


async def test_llama_health_check_reports_unreachable_service_but_not_bugs():
    provider = LlamaProvider({})
    provider.client = MagicMock()
    provider.client.get = AsyncMock(side_effect=httpx.ConnectError("down"))
    status = await provider.health_check()
    assert status.healthy is False
    assert status.api_status == "error"

    provider.client.get = AsyncMock(return_value=MagicMock(status_code=503))
    status = await provider.health_check()
    assert status.healthy is False
    assert "Ollama service not available" in status.last_error

    provider.client.get = AsyncMock(side_effect=AttributeError("bug"))
    with pytest.raises(AttributeError):
        await provider.health_check()


async def test_base_provider_health_check_propagates_unexpected_errors():
    provider = LlamaProvider({})
    provider.generate = AsyncMock(side_effect=AttributeError("bug"))
    # BaseProvider.health_check is what GeminiProvider-style subclasses inherit
    from src.ai_brain.providers.base_provider import BaseProvider

    with pytest.raises(AttributeError):
        await BaseProvider.health_check(provider)


# --- providers: configuration load -------------------------------------------


async def test_provider_config_load_falls_back_on_config_errors_only():
    manager = MagicMock()
    provider = DeepSeekProvider({"api_key": "k"}, model_config_manager=manager)

    manager.get_provider_configuration = AsyncMock(side_effect=ValueError("bad yaml"))
    await provider.load_configuration()
    assert provider.supported_models  # legacy fallback

    manager.get_provider_configuration = AsyncMock(side_effect=AttributeError("bug"))
    with pytest.raises(AttributeError):
        await provider.load_configuration()


# --- model router -------------------------------------------------------------


def _router_with(provider) -> ModelRouter:
    router = ModelRouter({}, component_registry=MagicMock())
    router.registry.providers["p"] = provider
    router.registry.health_status["p"] = ProviderHealthStatus(
        provider_name="p", healthy=True
    )
    router.registry.last_health_check["p"] = datetime.now()
    return router


def _decision(provider: str = "p") -> dict:
    return {"primary_model": {"provider": provider, "name": "m"}}


async def test_router_returns_failure_response_for_routing_errors_only():
    router = _router_with(MagicMock())

    unknown = await router.route_and_generate(_request(), _decision("missing"))
    assert unknown.success is False

    router.registry.health_status["p"].healthy = False
    unhealthy = await router.route_and_generate(_request(), _decision())
    assert unhealthy.success is False
    assert "unhealthy" in unhealthy.error_message

    router.registry.health_status.clear()
    router.registry.providers.clear()
    nothing = await router.route_and_generate(_request())
    assert "No healthy providers" in nothing.error_message


async def test_router_propagates_unexpected_errors_from_a_provider():
    provider = MagicMock()
    provider.generate = AsyncMock(side_effect=AttributeError("bug"))
    router = _router_with(provider)
    with pytest.raises(AttributeError):
        await router.route_and_generate(_request(), _decision())


async def test_router_fallback_loop_skips_routing_errors_but_not_bugs():
    primary = MagicMock()
    primary.generate = AsyncMock(
        return_value=ModelResponse(request_id="r", success=False, error_message="x")
    )
    router = _router_with(primary)
    decision = _decision() | {
        "fallback_models": [SimpleNamespace(provider="gone", name=None)]
    }
    result = await router.route_and_generate(_request(), decision)
    assert result.success is False
    assert result.error_message == "All providers failed"


async def test_router_stream_propagates_failure_instead_of_yielding_error_text():
    provider = MagicMock()

    async def broken(*_a, **_k):
        raise httpx.ConnectError("down")
        yield  # pragma: no cover

    provider.stream = broken
    router = _router_with(provider)
    with pytest.raises(httpx.ConnectError):
        async for _ in router.stream_response(_request(), _decision()):
            pass


async def test_router_health_probe_handles_network_failure_but_not_bugs():
    provider = MagicMock()
    router = _router_with(provider)
    router.registry.last_health_check.clear()

    provider.health_check = AsyncMock(side_effect=httpx.ConnectError("down"))
    status = await router._get_provider_health("p")
    assert status.healthy is False

    router.registry.last_health_check.clear()
    provider.health_check = AsyncMock(side_effect=AttributeError("bug"))
    with pytest.raises(AttributeError):
        await router._get_provider_health("p")


async def test_router_close_ignores_io_errors_but_not_bugs():
    provider = MagicMock()
    router = _router_with(provider)
    provider.close = AsyncMock(side_effect=OSError("socket"))
    await router.close()

    provider.close = AsyncMock(side_effect=AttributeError("bug"))
    with pytest.raises(AttributeError):
        await router.close()


async def test_router_skips_providers_whose_config_is_invalid_only():
    registry = MagicMock()
    registry.resolve.side_effect = ValueError("bad config")
    router = ModelRouter(
        {"providers": {"deepseek": {"enabled": True}}}, component_registry=registry
    )
    assert router.registry.providers == {}

    registry.resolve.side_effect = AttributeError("bug")
    with pytest.raises(AttributeError):
        ModelRouter(
            {"providers": {"deepseek": {"enabled": True}}}, component_registry=registry
        )


# --- cost optimizer -----------------------------------------------------------


async def test_cost_optimizer_falls_back_to_legacy_on_config_errors_only():
    manager = MagicMock()
    optimizer = CostOptimizer({}, model_config_manager=manager)

    manager.get_enabled_models = AsyncMock(side_effect=FileNotFoundError("base.yaml"))
    assert await optimizer.load_models() is False
    assert optimizer.models

    manager.get_enabled_models = AsyncMock(side_effect=AttributeError("bug"))
    with pytest.raises(AttributeError):
        await optimizer.load_models()


# --- adaptive state store -----------------------------------------------------


async def test_redis_state_store_reports_client_errors_but_not_bugs():
    client = MagicMock()
    store = RedisAdaptiveStateStore(client)

    client.get = AsyncMock(side_effect=redis.exceptions.ConnectionError("down"))
    assert (await store.load()).status == StateLoadStatus.ERROR

    client.get = AsyncMock(side_effect=AttributeError("bug"))
    with pytest.raises(AttributeError):
        await store.load()


# --- config -------------------------------------------------------------------


async def test_validate_configuration_returns_false_for_bad_files_only(tmp_path):
    manager = ModelConfigManager({"config_dir": str(tmp_path)})
    assert await manager.validate_configuration() is False  # missing file

    (tmp_path / "base.yaml").write_text("not: [valid")
    assert await manager.validate_configuration() is False  # malformed yaml

    with (
        patch.object(manager, "_load_yaml_file", AsyncMock(side_effect=AttributeError)),
        pytest.raises(AttributeError),
    ):
        await manager.validate_configuration()


async def test_integration_service_provider_probe_reports_http_errors_only():
    service = ConfigurationIntegrationService({})
    provider_config = MagicMock(api_endpoint="http://x", health_check_endpoint="")

    with patch("httpx.AsyncClient.get", AsyncMock(side_effect=httpx.ConnectError("d"))):
        result = await service._validate_provider_connectivity("p", provider_config)
    assert result["reachable"] is False

    with (
        patch("httpx.AsyncClient.get", AsyncMock(side_effect=AttributeError("bug"))),
        pytest.raises(AttributeError),
    ):
        await service._validate_provider_connectivity("p", provider_config)


async def test_integration_service_validate_reports_config_errors_only():
    service = ConfigurationIntegrationService({})
    # no manager initialised -> RuntimeError is a validation result, not a crash
    result = await service.validate_system_configuration()
    assert result["valid"] is False

    service.get_current_configuration = AsyncMock(side_effect=AttributeError("bug"))
    with pytest.raises(AttributeError):
        await service.validate_system_configuration()


async def test_integration_service_config_change_handler_propagates_bugs():
    service = ConfigurationIntegrationService({})
    service._update_components = AsyncMock(side_effect=AttributeError("bug"))
    event = MagicMock(change_type="modified")
    with pytest.raises(AttributeError):
        await service._on_configuration_change(event)
