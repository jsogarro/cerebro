"""
Test suite for Gemini service following TDD principles.

These tests verify the Gemini integration service functionality.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from redis import asyncio as aioredis

from src.models.research_project import ResearchDepth, ResearchQuery


class TestGeminiService:
    """Test the main Gemini service."""

    @pytest.fixture
    async def redis_client(self):
        """Mock Redis client for testing."""
        mock_redis = AsyncMock(spec=aioredis.Redis)
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.expire = AsyncMock(return_value=True)
        return mock_redis

    @pytest.fixture
    def mock_gemini_model(self):
        """Mock Gemini model for testing."""
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps(
            {
                "phases": ["literature_review", "analysis", "synthesis"],
                "estimated_time": 3600,
                "methodology": "systematic_review",
            }
        )
        mock_model.generate_content_async = AsyncMock(return_value=mock_response)
        return mock_model

    async def test_service_initialization(self):
        """Test that Gemini service initializes correctly."""
        from src.services.gemini_service import GeminiService

        service = GeminiService(api_key="test-key", model_name="gemini-pro")
        assert service.api_key == "test-key"
        assert service.model_name == "gemini-pro"
        assert service.rate_limiter is not None

    async def test_service_initialization_from_env(self, monkeypatch):
        """Test that service reads API key from environment."""
        monkeypatch.setenv("GEMINI_API_KEY", "env-test-key")

        from src.services.gemini_service import GeminiService

        service = GeminiService()
        assert service.api_key == "env-test-key"

    async def test_generate_research_plan(self, mock_gemini_model, redis_client):
        """Test research plan generation."""
        from src.services.gemini_service import GeminiService

        service = GeminiService(api_key="test-key")
        service.model = mock_gemini_model
        service.cache_client = redis_client

        query = ResearchQuery(
            text="Impact of AI on healthcare",
            domains=["AI", "Healthcare"],
            depth_level=ResearchDepth.COMPREHENSIVE,
        )

        result = await service.generate_research_plan(query)

        assert result is not None
        assert "phases" in result
        assert "methodology" in result
        assert mock_gemini_model.generate_content_async.called

    async def test_retry_on_api_failure(self, mock_gemini_model):
        """Test that service retries on API failures."""
        from src.services.gemini_service import GeminiService

        service = GeminiService(api_key="test-key")

        # Configure mock to fail twice then succeed
        success_response = MagicMock()
        success_response.text = json.dumps(
            {"phases": ["literature_review"], "methodology": "test"}
        )
        mock_gemini_model.generate_content_async.side_effect = [
            Exception("API Error"),
            Exception("API Error"),
            success_response,
        ]
        service.model = mock_gemini_model

        query = ResearchQuery(
            text="Test query", domains=["Test"], depth_level=ResearchDepth.SURVEY
        )

        result = await service.generate_research_plan(query)

        assert result is not None
        assert mock_gemini_model.generate_content_async.call_count == 3

    async def test_rate_limiting(self, mock_gemini_model, redis_client):
        """Test that rate limiting works correctly."""
        from src.services.gemini_service import GeminiService

        service = GeminiService(
            api_key="test-key", rate_limit=2, rate_period=1  # 2 requests per second
        )
        service.model = mock_gemini_model
        service.cache_client = redis_client  # Add cache client to avoid cache errors

        # Make unique queries to avoid caching
        queries = [
            ResearchQuery(
                text=f"Test query {i}",
                domains=["Test"],
                depth_level=ResearchDepth.SURVEY,
            )
            for i in range(5)
        ]

        # Fire multiple requests rapidly
        start_time = asyncio.get_event_loop().time()
        tasks = [service.generate_research_plan(q) for q in queries]
        await asyncio.gather(*tasks)
        end_time = asyncio.get_event_loop().time()

        # Should take at least 2 seconds for 5 requests with limit of 2/sec
        assert (end_time - start_time) >= 1.5  # Allow small tolerance

    async def test_cache_hit(self, mock_gemini_model, redis_client):
        """Test that cached responses are returned."""
        from src.services.gemini_service import GeminiService

        # Configure redis mock to return cached value
        cached_response = json.dumps({"cached": True, "data": "test"})
        redis_client.get = AsyncMock(return_value=cached_response.encode())

        service = GeminiService(api_key="test-key", cache_client=redis_client)
        service.model = mock_gemini_model

        query = ResearchQuery(
            text="Test query", domains=["Test"], depth_level=ResearchDepth.SURVEY
        )

        result = await service.generate_research_plan(query)

        # Check that cache was accessed
        assert redis_client.get.called
        assert result["cached"] is True
        # Model should not be called since we got cached result
        assert not mock_gemini_model.generate_content_async.called

    async def test_analyze_literature(self, mock_gemini_model):
        """Test literature analysis functionality."""
        from src.services.gemini_service import GeminiService

        mock_response = MagicMock()
        mock_response.text = json.dumps(
            {
                "key_findings": ["Finding 1", "Finding 2"],
                "gaps": ["Gap 1"],
                "summary": "Analysis summary",
            }
        )
        mock_gemini_model.generate_content_async = AsyncMock(return_value=mock_response)

        service = GeminiService(api_key="test-key")
        service.model = mock_gemini_model

        sources = ["Source 1", "Source 2", "Source 3"]
        result = await service.analyze_literature(sources)

        assert "key_findings" in result
        assert "gaps" in result
        assert len(result["key_findings"]) == 2

    async def test_synthesize_findings(self, mock_gemini_model):
        """Test findings synthesis."""
        from src.services.gemini_service import GeminiService

        mock_response = MagicMock()
        mock_response.text = json.dumps(
            {
                "synthesis": "Comprehensive synthesis",
                "conclusions": ["Conclusion 1", "Conclusion 2"],
                "recommendations": ["Recommendation 1"],
            }
        )
        mock_gemini_model.generate_content_async = AsyncMock(return_value=mock_response)

        service = GeminiService(api_key="test-key")
        service.model = mock_gemini_model

        findings = [
            {"finding": "Finding 1", "source": "Source 1"},
            {"finding": "Finding 2", "source": "Source 2"},
        ]

        result = await service.synthesize_findings(findings)

        assert "synthesis" in result
        assert "conclusions" in result
        assert len(result["conclusions"]) == 2

    async def test_generate_citations(self, mock_gemini_model):
        """Test citation generation."""
        from src.services.gemini_service import GeminiService

        mock_response = MagicMock()
        mock_response.text = json.dumps(
            {
                "citations": [
                    {
                        "apa": "Author, A. (2024). Title. Journal.",
                        "mla": 'Author, A. "Title." Journal (2024).',
                        "chicago": 'Author, A. "Title." Journal (2024).',
                    }
                ]
            }
        )
        mock_gemini_model.generate_content_async = AsyncMock(return_value=mock_response)

        service = GeminiService(api_key="test-key")
        service.model = mock_gemini_model

        sources = [{"title": "Test Paper", "author": "Test Author", "year": 2024}]
        result = await service.generate_citations(sources)

        assert "citations" in result
        assert len(result["citations"]) == 1
        assert "apa" in result["citations"][0]

    async def test_validate_hypothesis(self, mock_gemini_model):
        """Test hypothesis validation."""
        from src.services.gemini_service import GeminiService

        mock_response = MagicMock()
        mock_response.text = json.dumps(
            {
                "validation": "supported",
                "confidence": 0.85,
                "evidence": ["Evidence 1", "Evidence 2"],
                "counter_evidence": ["Counter 1"],
            }
        )
        mock_gemini_model.generate_content_async = AsyncMock(return_value=mock_response)

        service = GeminiService(api_key="test-key")
        service.model = mock_gemini_model

        hypothesis = "AI improves diagnostic accuracy"
        data = {"studies": 10, "positive_results": 8}

        result = await service.validate_hypothesis(hypothesis, data)

        assert result["validation"] == "supported"
        assert result["confidence"] == 0.85
        assert len(result["evidence"]) == 2

    async def test_error_handling_invalid_response(self, mock_gemini_model):
        """Test handling of invalid Gemini responses."""
        from src.services.gemini_service import GeminiService

        mock_response = MagicMock()
        mock_response.text = "Invalid JSON response"
        mock_gemini_model.generate_content_async = AsyncMock(return_value=mock_response)

        service = GeminiService(api_key="test-key")
        service.model = mock_gemini_model

        query = ResearchQuery(
            text="Test query", domains=["Test"], depth_level=ResearchDepth.SURVEY
        )

        with pytest.raises(ValueError) as exc_info:
            await service.generate_research_plan(query)

        assert "Invalid" in str(exc_info.value)

    async def test_concurrent_requests(self, mock_gemini_model):
        """Test handling of concurrent requests."""
        from src.services.gemini_service import GeminiService

        service = GeminiService(api_key="test-key", max_concurrent=3)
        service.model = mock_gemini_model

        queries = [
            ResearchQuery(
                text=f"Query {i}", domains=["Test"], depth_level=ResearchDepth.SURVEY
            )
            for i in range(10)
        ]

        # All requests should complete without error
        results = await asyncio.gather(
            *[service.generate_research_plan(q) for q in queries]
        )

        assert len(results) == 10
        assert all(r is not None for r in results)
