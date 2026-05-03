"""
Gemini service for AI-powered research capabilities.

This service integrates with Google's Gemini API following functional programming principles.
"""

from __future__ import annotations

import hashlib
from typing import Any

from langchain_google_genai import ChatGoogleGenerativeAI
from redis import asyncio as aioredis
from structlog import get_logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.services.gemini_config import GeminiConfig
from src.services.gemini_limiter import RateLimiter
from src.utils.serialization import deserialize_from_cache, serialize_for_cache

logger = get_logger()


class GeminiService:
    """
    Service for interacting with Google's Gemini API.

    This service follows functional programming principles:
    - All data transformations are pure functions
    - Immutable configuration
    - Side effects isolated in specific methods
    """

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str = "gemini-2.0-flash",
        config: GeminiConfig | None = None,
        cache_client: aioredis.Redis[Any] | None = None,
        **kwargs: Any,
    ):
        """
        Initialize Gemini service.

        Args:
            api_key: Gemini API key (reads from env if not provided)
            model_name: Gemini model to use
            config: Complete configuration object
            cache_client: Redis client for caching
            **kwargs: Additional configuration overrides
        """
        # Use provided config or create from environment
        if config:
            self.config = config
        elif api_key:
            self.config = GeminiConfig(api_key=api_key, model_name=model_name, **kwargs)
        else:
            self.config = GeminiConfig.from_env()

        # Store individual attributes for compatibility
        self.api_key = self.config.api_key
        self.model_name = self.config.model_name

        # Initialize model with LangChain ChatGoogleGenerativeAI
        self.model = ChatGoogleGenerativeAI(
            model=self.config.model_name,
            google_api_key=self.config.api_key,
            temperature=self.config.temperature,
            top_p=self.config.top_p,
            top_k=self.config.top_k,
            max_output_tokens=self.config.max_output_tokens,
        )

        # Initialize rate limiter
        self.rate_limiter = RateLimiter(
            rate_limit=kwargs.get("rate_limit", self.config.rate_limit),
            rate_period=kwargs.get("rate_period", self.config.rate_period),
            max_concurrent=kwargs.get("max_concurrent", self.config.max_concurrent),
        )

        # Initialize cache client
        self.cache_client = cache_client
        self._cache_enabled = self.config.cache_enabled and cache_client is not None

        logger.info("gemini_service_initialized", model=self.config.model_name)

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=5, max=30),
    )
    async def _generate_content(self, prompt: str) -> str:
        """
        Generate content from Gemini with retry logic.

        This method handles the side effect of calling the API.
        """
        async with self.rate_limiter.acquire():
            try:
                result = await self.model.ainvoke(prompt)
                return str(result.content)
            except Exception as e:
                logger.error("gemini_api_error", error=str(e))
                raise

    async def generate_content(self, prompt: str) -> str:
        """
        Public interface to generate content from Gemini.

        Used by all agent implementations to call the LLM.

        Args:
            prompt: The prompt to send to Gemini.

        Returns:
            Generated text response.
        """
        return await self._generate_content(prompt)

    async def generate_structured_content(
        self, prompt: str, schema: type[Any], max_retries: int = 2
    ) -> Any:
        """
        Generate structured content from Gemini using Pydantic schema validation.

        Args:
            prompt: The prompt to send to Gemini.
            schema: Pydantic model class for output validation.
            max_retries: Number of retry attempts on parse failure (default: 2).

        Returns:
            Validated Pydantic model instance.

        Raises:
            Exception: If parsing fails after all retries.
        """
        from langchain_core.output_parsers import PydanticOutputParser

        # Create parser for the schema
        parser = PydanticOutputParser(pydantic_object=schema)

        # Enhance prompt with format instructions
        format_instructions = parser.get_format_instructions()
        enhanced_prompt = f"{prompt}\n\n{format_instructions}"

        # Try to generate and parse with retries
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                # Generate content using existing retry logic
                response = await self._generate_content(enhanced_prompt)

                # Parse and validate response
                parsed = parser.parse(response)
                logger.debug(
                    "structured_output_parsed",
                    attempt=attempt + 1,
                )
                return parsed

            except Exception as e:
                last_error = e
                logger.warning(
                    "structured_output_parse_failed",
                    attempt=attempt + 1,
                    max_attempts=max_retries + 1,
                    error=str(e),
                )
                if attempt < max_retries:
                    continue
                else:
                    logger.error(
                        "structured_output_parse_exhausted",
                        error=str(last_error),
                    )
                    raise

        # Should never reach here, but for type safety
        raise last_error if last_error else Exception("Unexpected parse failure")

    def _generate_cache_key(self, prefix: str, data: Any) -> str:
        """
        Generate cache key for data.

        Pure function that generates deterministic cache keys.
        """
        # Create deterministic string representation
        if isinstance(data, dict):
            data_str = serialize_for_cache(data).decode("utf-8")
        else:
            data_str = str(data)

        # Generate hash
        hash_obj = hashlib.sha256(data_str.encode())
        return f"gemini:{prefix}:{hash_obj.hexdigest()}"

    async def _get_cached_response(self, cache_key: str) -> dict[str, Any] | None:
        """
        Get cached response if available.

        Side effect: Redis read operation.
        """
        if not self._cache_enabled:
            return None

        try:
            if self.cache_client:
                cached = await self.cache_client.get(cache_key)
                if cached:
                    logger.debug("gemini_cache_hit", cache_key=cache_key)
                    result = deserialize_from_cache(cached)
                    if isinstance(result, dict):
                        return result
        except Exception as e:
            logger.warning("gemini_cache_read_failed", error=str(e))

        return None

    async def _set_cached_response(self, cache_key: str, data: dict[str, Any]) -> None:
        """
        Set cached response.

        Side effect: Redis write operation.
        """
        if not self._cache_enabled:
            return

        try:
            if self.cache_client:
                await self.cache_client.set(
                    cache_key, serialize_for_cache(data).decode("utf-8"), ex=self.config.cache_ttl
                )
                logger.debug("gemini_cache_write_completed", cache_key=cache_key)
        except Exception as e:
            logger.warning("gemini_cache_write_failed", error=str(e))

    def _parse_json_response(self, response: str) -> dict[str, Any]:
        """
        Parse JSON from Gemini response using the parsers module.

        Pure function that extracts and parses JSON.
        """
        from src.services.parsers.json_parser import parse_json_response

        return parse_json_response(response)

    async def generate_research_plan(self, query: Any) -> dict[str, Any]:
        """
        Generate a research plan from a query.

        Transforms query into research plan through Gemini.
        """
        # Generate cache key
        cache_key = self._generate_cache_key("research_plan", query)

        # Check cache
        cached = await self._get_cached_response(cache_key)
        if cached:
            return cached

        # Create prompt
        from src.services.prompts.research_prompts import (
            generate_query_decomposition_prompt,
        )

        prompt = generate_query_decomposition_prompt(query)

        # Generate response
        response = await self._generate_content(prompt)

        # Parse response
        result = self._parse_json_response(response)

        # Cache result
        await self._set_cached_response(cache_key, result)

        return result

    async def analyze_literature(self, sources: list[str]) -> dict[str, Any]:
        """
        Analyze literature sources.

        Transforms source list into literature analysis.
        """
        # Generate cache key
        cache_key = self._generate_cache_key("literature", sources)

        # Check cache
        cached = await self._get_cached_response(cache_key)
        if cached:
            return cached

        # Create prompt
        from src.services.prompts.research_prompts import (
            generate_literature_review_prompt,
        )

        prompt = generate_literature_review_prompt(sources)

        # Generate response
        response = await self._generate_content(prompt)

        # Parse response
        result = self._parse_json_response(response)

        # Cache result
        await self._set_cached_response(cache_key, result)

        return result

    async def synthesize_findings(
        self, findings: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        Synthesize research findings.

        Transforms findings list into synthesis.
        """
        # Generate cache key
        cache_key = self._generate_cache_key("synthesis", findings)

        # Check cache
        cached = await self._get_cached_response(cache_key)
        if cached:
            return cached

        # Create prompt
        from src.services.prompts.research_prompts import generate_synthesis_prompt

        prompt = generate_synthesis_prompt(findings)

        # Generate response
        response = await self._generate_content(prompt)

        # Parse response
        result = self._parse_json_response(response)

        # Cache result
        await self._set_cached_response(cache_key, result)

        return result

    async def generate_citations(self, sources: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Generate formatted citations.

        Transforms source data into formatted citations.
        """
        # Generate cache key
        cache_key = self._generate_cache_key("citations", sources)

        # Check cache
        cached = await self._get_cached_response(cache_key)
        if cached:
            return cached

        # Create prompt
        from src.services.prompts.agent_prompts import generate_citation_agent_prompt

        prompt = generate_citation_agent_prompt(sources)

        # Generate response
        response = await self._generate_content(prompt)

        # Parse response
        result = self._parse_json_response(response)

        # Cache result
        await self._set_cached_response(cache_key, result)

        return result

    async def validate_hypothesis(
        self, hypothesis: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Validate a research hypothesis.

        Transforms hypothesis and data into validation result.
        """
        # Generate cache key
        cache_data = {"hypothesis": hypothesis, "data": data}
        cache_key = self._generate_cache_key("validation", cache_data)

        # Check cache
        cached = await self._get_cached_response(cache_key)
        if cached:
            return cached

        # Create prompt
        from src.services.prompts.validation_prompts import (
            generate_hypothesis_validation_prompt,
        )

        prompt = generate_hypothesis_validation_prompt(hypothesis, data)

        # Generate response
        response = await self._generate_content(prompt)

        # Parse response
        result = self._parse_json_response(response)

        # Cache result
        await self._set_cached_response(cache_key, result)

        return result
