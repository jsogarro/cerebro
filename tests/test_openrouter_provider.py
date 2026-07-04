"""Tests for OpenRouterProvider multi-provider routing integration."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.ai_brain.providers.base_provider import ModelRequest
from src.ai_brain.providers.openrouter_provider import OpenRouterProvider


@pytest.fixture
def openrouter_config():
    """Minimal OpenRouter provider configuration."""
    return {
        "api_key": "test-openrouter-key",
        "endpoint": "https://openrouter.ai/api/v1/chat/completions",
        "tier_mapping": {
            "simple": "deepseek/deepseek-chat",
            "balanced": "anthropic/claude-sonnet-4.6",
            "complex": "anthropic/claude-sonnet-4.6",
        },
    }


@pytest.fixture
def openrouter_provider(openrouter_config):
    """Create OpenRouterProvider instance for testing."""
    return OpenRouterProvider(config=openrouter_config)


@pytest.fixture
def mock_openrouter_response():
    """Mock successful OpenRouter API response."""
    return {
        "choices": [
            {
                "message": {
                    "content": "Test response from OpenRouter",
                    "role": "assistant",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 8,
            "total_tokens": 18,
        },
        "model": "anthropic/claude-sonnet-4.6",
    }


class TestOpenRouterProviderInitialization:
    """Test OpenRouterProvider initialization and configuration."""

    def test_provider_name(self, openrouter_provider):
        """Provider name should be 'openrouter'."""
        assert openrouter_provider.provider_name == "openrouter"

    def test_api_configuration(self, openrouter_provider):
        """API endpoint and key should be configured from config."""
        assert (
            openrouter_provider.api_endpoint
            == "https://openrouter.ai/api/v1/chat/completions"
        )
        assert openrouter_provider.api_key == "test-openrouter-key"

    def test_tier_mapping_default(self, openrouter_provider):
        """Tier mapping should match provided configuration."""
        assert openrouter_provider.tier_mapping["simple"] == "deepseek/deepseek-chat"
        assert (
            openrouter_provider.tier_mapping["balanced"]
            == "anthropic/claude-sonnet-4.6"
        )
        assert (
            openrouter_provider.tier_mapping["complex"] == "anthropic/claude-sonnet-4.6"
        )

    def test_supported_capabilities(self, openrouter_provider):
        """Provider should support expected capabilities."""
        from src.ai_brain.providers.base_provider import ModelCapability

        caps = openrouter_provider._get_supported_capabilities_legacy()
        assert ModelCapability.TEXT_GENERATION in caps
        assert ModelCapability.CHAT in caps
        assert ModelCapability.REASONING in caps
        assert ModelCapability.ANALYSIS in caps

    def test_no_api_key_warning(self):
        """Should complete initialization even when API key is missing."""
        import asyncio

        config = {"api_key": None}
        provider = OpenRouterProvider(config=config)
        asyncio.run(provider.load_configuration())

        # Provider should initialize with None API key (logs warning but doesn't fail)
        assert provider.api_key is None
        assert provider.provider_name == "openrouter"


class TestModelSelection:
    """Test tier-based model selection logic."""

    def test_select_simple_tier(self, openrouter_provider):
        """Simple tier should map to cost-efficient model."""
        model = openrouter_provider._select_model_from_tier("simple")
        assert model == "deepseek/deepseek-chat"

    def test_select_balanced_tier(self, openrouter_provider):
        """Balanced tier should map to mid-tier quality model."""
        model = openrouter_provider._select_model_from_tier("balanced")
        assert model == "anthropic/claude-sonnet-4.6"

    def test_select_complex_tier(self, openrouter_provider):
        """Complex tier should map to quality-focused model."""
        model = openrouter_provider._select_model_from_tier("complex")
        assert model == "anthropic/claude-sonnet-4.6"

    def test_select_invalid_tier_defaults_to_balanced(self, openrouter_provider):
        """Invalid tier should default to balanced."""
        model = openrouter_provider._select_model_from_tier("invalid_tier")
        assert model == "anthropic/claude-sonnet-4.6"

    def test_select_none_tier_defaults_to_balanced(self, openrouter_provider):
        """None tier should default to balanced."""
        model = openrouter_provider._select_model_from_tier(None)
        assert model == "anthropic/claude-sonnet-4.6"


class TestRequestPayloadBuilding:
    """Test OpenAI-compatible request payload construction."""

    def test_build_payload_from_prompt(self, openrouter_provider):
        """Should convert prompt to messages format."""
        request = ModelRequest(
            prompt="What is 2+2?",
            max_tokens=100,
            temperature=0.7,
            top_p=0.9,
        )

        payload = openrouter_provider._build_request_payload(
            request, "anthropic/claude-sonnet-4.6"
        )

        assert payload["model"] == "anthropic/claude-sonnet-4.6"
        assert len(payload["messages"]) == 1
        assert payload["messages"][0]["role"] == "user"
        assert payload["messages"][0]["content"] == "What is 2+2?"
        assert payload["max_tokens"] == 100
        assert payload["temperature"] == 0.7
        assert payload["top_p"] == 0.9

    def test_build_payload_with_system_prompt(self, openrouter_provider):
        """Should include system prompt as first message."""
        request = ModelRequest(
            prompt="What is 2+2?",
            system_prompt="You are a helpful math tutor.",
            max_tokens=100,
        )

        payload = openrouter_provider._build_request_payload(
            request, "anthropic/claude-sonnet-4.6"
        )

        assert len(payload["messages"]) == 2
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][0]["content"] == "You are a helpful math tutor."
        assert payload["messages"][1]["role"] == "user"

    def test_build_payload_from_messages(self, openrouter_provider):
        """Should use messages directly when provided."""
        request = ModelRequest(
            messages=[
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
                {"role": "user", "content": "How are you?"},
            ],
            max_tokens=100,
        )

        payload = openrouter_provider._build_request_payload(
            request, "anthropic/claude-sonnet-4.6"
        )

        assert len(payload["messages"]) == 3
        assert payload["messages"][0]["content"] == "Hello"
        assert payload["messages"][2]["content"] == "How are you?"

    def test_build_payload_with_top_k(self, openrouter_provider):
        """Should include top_k when specified."""
        request = ModelRequest(prompt="Test", top_k=40)

        payload = openrouter_provider._build_request_payload(
            request, "anthropic/claude-sonnet-4.6"
        )

        assert payload["top_k"] == 40


@pytest.mark.asyncio
class TestGeneration:
    """Test content generation via OpenRouter API."""

    async def test_generate_success(
        self, openrouter_provider, mock_openrouter_response
    ):
        """Successful generation should return ModelResponse with content."""
        request = ModelRequest(prompt="What is 2+2?", max_tokens=100)

        # Mock HTTP client
        mock_response = MagicMock()
        mock_response.json.return_value = mock_openrouter_response
        mock_response.raise_for_status = MagicMock()

        with patch.object(openrouter_provider, "client", create=True) as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)

            response = await openrouter_provider.generate(request)

            assert response.success is True
            assert response.content == "Test response from OpenRouter"
            assert response.provider == "openrouter"
            assert response.prompt_tokens == 10
            assert response.completion_tokens == 8
            assert response.total_tokens == 18

    async def test_generate_with_explicit_model(
        self, openrouter_provider, mock_openrouter_response
    ):
        """Should use explicitly specified model."""
        request = ModelRequest(prompt="Test", max_tokens=100)

        mock_response = MagicMock()
        mock_response.json.return_value = mock_openrouter_response
        mock_response.raise_for_status = MagicMock()

        with patch.object(openrouter_provider, "client", create=True) as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)

            _response = await openrouter_provider.generate(
                request, model_name="deepseek/deepseek-chat"
            )

            # Verify the request payload used the explicit model
            call_args = mock_client.post.call_args
            payload = call_args.kwargs["json"]
            assert payload["model"] == "deepseek/deepseek-chat"

    async def test_generate_with_tier_in_metadata(
        self, openrouter_provider, mock_openrouter_response
    ):
        """Should select model based on tier in request metadata."""
        request = ModelRequest(
            prompt="Complex analysis task",
            max_tokens=200,
            metadata={"tier": "complex"},
        )

        mock_response = MagicMock()
        mock_response.json.return_value = mock_openrouter_response
        mock_response.raise_for_status = MagicMock()

        with patch.object(openrouter_provider, "client", create=True) as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)

            _response = await openrouter_provider.generate(request)

            # Verify complex tier selected Sonnet model
            call_args = mock_client.post.call_args
            payload = call_args.kwargs["json"]
            assert payload["model"] == "anthropic/claude-sonnet-4.6"

    async def test_generate_api_error_handling(self, openrouter_provider):
        """API errors should return error response."""
        request = ModelRequest(prompt="Test", max_tokens=100)

        with patch.object(openrouter_provider, "client", create=True) as mock_client:
            mock_client.post = AsyncMock(side_effect=Exception("API connection failed"))

            response = await openrouter_provider.generate(request)

            assert response.success is False
            assert response.error_type == "generation_error"
            assert "API connection failed" in response.error_message

    async def test_generate_empty_choices_error(self, openrouter_provider):
        """Empty choices array should return error response."""
        request = ModelRequest(prompt="Test", max_tokens=100)

        mock_response = MagicMock()
        mock_response.json.return_value = {"choices": [], "usage": {}}
        mock_response.raise_for_status = MagicMock()

        with patch.object(openrouter_provider, "client", create=True) as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)

            response = await openrouter_provider.generate(request)

            assert response.success is False
            assert response.error_type == "empty_response"

    async def test_generate_invalid_request(self, openrouter_provider):
        """Invalid request should return validation error."""
        # Empty prompt and no messages
        request = ModelRequest(prompt="", max_tokens=100)

        response = await openrouter_provider.generate(request)

        assert response.success is False
        assert response.error_type == "validation_error"


@pytest.mark.asyncio
class TestHealthCheck:
    """Test provider health check functionality."""

    async def test_health_check_success(
        self, openrouter_provider, mock_openrouter_response
    ):
        """Successful health check should mark provider as healthy."""
        # Mock response with "4" in content (correct answer to 2+2 test)
        health_response = mock_openrouter_response.copy()
        health_response["choices"][0]["message"]["content"] = "4"

        mock_response = MagicMock()
        mock_response.json.return_value = health_response
        mock_response.raise_for_status = MagicMock()

        with patch.object(openrouter_provider, "client", create=True) as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)

            health_status = await openrouter_provider.health_check()

            assert health_status.healthy is True
            assert health_status.api_status == "operational"

    async def test_health_check_degraded(
        self, openrouter_provider, mock_openrouter_response
    ):
        """Incorrect response should mark as degraded."""
        # Mock response without "4" in content
        health_response = mock_openrouter_response.copy()
        health_response["choices"][0]["message"]["content"] = "Hello"

        mock_response = MagicMock()
        mock_response.json.return_value = health_response
        mock_response.raise_for_status = MagicMock()

        with patch.object(openrouter_provider, "client", create=True) as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)

            health_status = await openrouter_provider.health_check()

            assert health_status.healthy is True  # Request succeeded
            assert health_status.api_status == "degraded"  # But answer was wrong

    async def test_health_check_failure(self, openrouter_provider):
        """Failed health check should mark provider as unhealthy."""
        with patch.object(openrouter_provider, "client", create=True) as mock_client:
            mock_client.post = AsyncMock(side_effect=Exception("Network error"))

            health_status = await openrouter_provider.health_check()

            assert health_status.healthy is False
            assert health_status.api_status == "error"
            assert "Network error" in health_status.last_error


@pytest.mark.asyncio
class TestStreaming:
    """Test streaming functionality (fallback implementation)."""

    async def test_stream_falls_back_to_generate(
        self, openrouter_provider, mock_openrouter_response
    ):
        """Stream should fall back to generate and yield full content."""
        request = ModelRequest(prompt="Test", max_tokens=100)

        mock_response = MagicMock()
        mock_response.json.return_value = mock_openrouter_response
        mock_response.raise_for_status = MagicMock()

        with patch.object(openrouter_provider, "client", create=True) as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)

            chunks = []
            async for chunk in openrouter_provider.stream(request):
                chunks.append(chunk)

            assert len(chunks) == 1
            assert chunks[0] == "Test response from OpenRouter"


class TestValidation:
    """Test request validation logic."""

    @pytest.mark.asyncio
    async def test_validate_request_valid(self, openrouter_provider):
        """Valid request should pass validation."""
        request = ModelRequest(prompt="Test query", max_tokens=100)
        assert await openrouter_provider.validate_request(request) is True

    @pytest.mark.asyncio
    async def test_validate_request_max_tokens_exceeded(self, openrouter_provider):
        """Request exceeding max_tokens limit should fail."""
        request = ModelRequest(prompt="Test", max_tokens=10000)
        assert await openrouter_provider.validate_request(request) is False

    @pytest.mark.asyncio
    async def test_validate_request_empty_prompt(self, openrouter_provider):
        """Request with empty prompt and no messages should fail."""
        request = ModelRequest(prompt="", max_tokens=100)
        assert await openrouter_provider.validate_request(request) is False


@pytest.mark.asyncio
class TestResourceCleanup:
    """Test proper resource cleanup."""

    async def test_close_client(self, openrouter_provider):
        """Close should clean up HTTP client."""
        # Create mock client
        mock_client = AsyncMock()
        openrouter_provider.client = mock_client

        await openrouter_provider.close()

        mock_client.aclose.assert_called_once()
        assert openrouter_provider.client is None


@pytest.mark.asyncio
class TestModelSlugValidation:
    """Test model slug validation against OpenRouter catalog."""

    @pytest.fixture
    def mock_catalog_response_valid(self):
        """Mock catalog response where all tier_mapping slugs are valid."""
        return {
            "data": [
                {"id": "deepseek/deepseek-chat", "name": "DeepSeek Chat"},
                {"id": "anthropic/claude-sonnet-4.6", "name": "Claude Sonnet 4.6"},
                {"id": "google/gemini-pro-1.5", "name": "Gemini Pro 1.5"},
            ]
        }

    @pytest.fixture
    def mock_catalog_response_stale(self):
        """Mock catalog response where one tier_mapping slug is stale."""
        return {
            "data": [
                {"id": "deepseek/deepseek-chat", "name": "DeepSeek Chat"},
                # Missing: anthropic/claude-sonnet-4.6 (stale slug)
                {"id": "anthropic/claude-sonnet-4.7", "name": "Claude Sonnet 4.7"},
                {"id": "google/gemini-pro-1.5", "name": "Gemini Pro 1.5"},
            ]
        }

    async def test_validate_model_slugs_all_valid(
        self, openrouter_provider, mock_catalog_response_valid
    ):
        """All tier_mapping slugs present in catalog should pass validation."""
        mock_response = MagicMock()
        mock_response.json.return_value = mock_catalog_response_valid
        mock_response.raise_for_status = MagicMock()

        # Mock the client
        openrouter_provider.client = AsyncMock()
        openrouter_provider.client.get = AsyncMock(return_value=mock_response)

        result = await openrouter_provider.validate_model_slugs()

        # All slugs should be valid
        assert len(result.valid_slugs) == 3
        assert "simple" in result.valid_slugs
        assert "balanced" in result.valid_slugs
        assert "complex" in result.valid_slugs
        assert result.valid_slugs["simple"] == "deepseek/deepseek-chat"
        assert result.valid_slugs["balanced"] == "anthropic/claude-sonnet-4.6"

        # No invalid slugs
        assert len(result.invalid_slugs) == 0
        assert result.validation_error is None

    async def test_validate_model_slugs_stale_detected(
        self, openrouter_provider, mock_catalog_response_stale
    ):
        """Stale slugs should be detected and reported in invalid_slugs."""
        mock_response = MagicMock()
        mock_response.json.return_value = mock_catalog_response_stale
        mock_response.raise_for_status = MagicMock()

        openrouter_provider.client = AsyncMock()
        openrouter_provider.client.get = AsyncMock(return_value=mock_response)

        result = await openrouter_provider.validate_model_slugs()

        # Only simple tier should be valid
        assert len(result.valid_slugs) == 1
        assert result.valid_slugs["simple"] == "deepseek/deepseek-chat"

        # Balanced and complex should be invalid (both point to same stale slug)
        assert len(result.invalid_slugs) == 2
        assert "balanced" in result.invalid_slugs
        assert "complex" in result.invalid_slugs
        assert result.invalid_slugs["balanced"] == "anthropic/claude-sonnet-4.6"
        assert result.invalid_slugs["complex"] == "anthropic/claude-sonnet-4.6"

        assert result.validation_error is None

    async def test_validate_model_slugs_network_error(self, openrouter_provider):
        """Network errors should be non-fatal and return validation_error."""
        import httpx

        openrouter_provider.client = AsyncMock()
        openrouter_provider.client.get = AsyncMock(
            side_effect=httpx.HTTPError("Connection failed")
        )

        result = await openrouter_provider.validate_model_slugs()

        # Should have validation_error set
        assert result.validation_error is not None
        assert "Network error" in result.validation_error

        # No slugs validated (error path)
        assert len(result.valid_slugs) == 0
        assert len(result.invalid_slugs) == 0

    async def test_validate_model_slugs_unexpected_error(self, openrouter_provider):
        """Unexpected errors should be caught and logged."""
        openrouter_provider.client = AsyncMock()
        openrouter_provider.client.get = AsyncMock(
            side_effect=Exception("Unexpected error")
        )

        result = await openrouter_provider.validate_model_slugs()

        # Should have validation_error set
        assert result.validation_error is not None
        assert "Unexpected validation error" in result.validation_error

        # No slugs validated
        assert len(result.valid_slugs) == 0
        assert len(result.invalid_slugs) == 0

    async def test_load_configuration_calls_validation_when_enabled(
        self, openrouter_config, mock_catalog_response_valid
    ):
        """load_configuration should call validate_model_slugs when enabled."""
        # Enable validation explicitly
        openrouter_config["validate_slugs_on_startup"] = True

        provider = OpenRouterProvider(config=openrouter_config)

        # Mock the catalog response
        mock_response = MagicMock()
        mock_response.json.return_value = mock_catalog_response_valid
        mock_response.raise_for_status = MagicMock()

        # Mock client and catalog fetch
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            await provider.load_configuration()

        # Validation should have been called (catalog GET)
        mock_client.get.assert_called_once()
        call_args = mock_client.get.call_args
        assert "/models" in call_args.args[0]

        # No stale_slugs recorded (all valid)
        assert len(provider.stale_slugs) == 0

    async def test_load_configuration_skips_validation_when_disabled(
        self, openrouter_config
    ):
        """load_configuration should skip validation when disabled."""
        # Disable validation explicitly
        openrouter_config["validate_slugs_on_startup"] = False

        provider = OpenRouterProvider(config=openrouter_config)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock()  # Should NOT be called
        mock_client.aclose = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            await provider.load_configuration()

        # Validation should NOT have been called
        mock_client.get.assert_not_called()

    async def test_load_configuration_logs_error_on_stale_slugs(
        self, openrouter_config, mock_catalog_response_stale
    ):
        """load_configuration should log ERROR when stale slugs detected."""
        provider = OpenRouterProvider(config=openrouter_config)

        # Mock catalog response with stale slug
        mock_response = MagicMock()
        mock_response.json.return_value = mock_catalog_response_stale
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            await provider.load_configuration()

        # Should have recorded stale_slugs
        assert len(provider.stale_slugs) == 2
        assert "balanced" in provider.stale_slugs
        assert "complex" in provider.stale_slugs
        assert provider.stale_slugs["balanced"] == "anthropic/claude-sonnet-4.6"
        assert provider.stale_slugs["complex"] == "anthropic/claude-sonnet-4.6"

    async def test_load_configuration_skips_validation_when_no_api_key(
        self, openrouter_config
    ):
        """Validation should be skipped if no API key configured."""
        openrouter_config["api_key"] = None
        openrouter_config["validate_slugs_on_startup"] = True

        provider = OpenRouterProvider(config=openrouter_config)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock()  # Should NOT be called
        mock_client.aclose = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            await provider.load_configuration()

        # Validation skipped (no API key)
        mock_client.get.assert_not_called()

    async def test_health_check_surfaces_stale_slugs(
        self, openrouter_provider, mock_openrouter_response
    ):
        """health_check should surface stale_slugs in metadata."""
        # Simulate stale slugs detected during load_configuration
        openrouter_provider.stale_slugs = {
            "balanced": "anthropic/claude-sonnet-4.6",
            "complex": "anthropic/claude-sonnet-4.6",
        }

        # Mock successful health check response
        health_response = mock_openrouter_response.copy()
        health_response["choices"][0]["message"]["content"] = "4"

        mock_response = MagicMock()
        mock_response.json.return_value = health_response
        mock_response.raise_for_status = MagicMock()

        with patch.object(openrouter_provider, "client", create=True) as mock_client:
            mock_client.post = AsyncMock(return_value=mock_response)

            health_status = await openrouter_provider.health_check()

        # Health check should succeed but be degraded due to stale_slugs
        assert health_status.healthy is True
        assert health_status.api_status == "degraded"

        # Stale slugs should be in metadata
        assert hasattr(health_status, "metadata")
        assert "stale_slugs" in health_status.metadata
        assert (
            health_status.metadata["stale_slugs"]["balanced"]
            == "anthropic/claude-sonnet-4.6"
        )
