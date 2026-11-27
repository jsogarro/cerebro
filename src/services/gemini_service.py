"""
Gemini service for AI-powered research capabilities.

This service integrates with Google's Gemini API following functional programming principles.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import httpx
from langchain_google_genai import ChatGoogleGenerativeAI
from redis import asyncio as aioredis
from redis.exceptions import RedisError
from structlog import get_logger

from src.core.observability import LLMCallMetrics, record_llm_call
from src.core.tools.circuit import CircuitBreaker
from src.services.gemini_config import GeminiConfig
from src.services.gemini_limiter import RateLimiter
from src.utils.serialization import deserialize_from_cache, serialize_for_cache

logger = get_logger()


class GeminiCircuitOpenError(RuntimeError):
    """Recent calls kept failing, so this one was not attempted."""


@dataclass(frozen=True)
class GeminiResult:
    """One Gemini generation with the metadata the provider reported.

    A token count the provider did not report is ``None``, never zero.
    """

    content: str
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


_PROVIDER_SIDE_HTTP = frozenset({408, 429})


def _is_provider_side(error: BaseException) -> bool:
    """Whether a failure says the provider is unhealthy, not that the request was bad.

    Walks the cause chain because the SDK wrappers re-raise. Unclassified
    errors are the caller's: one tenant's oversized or malformed prompt must
    not open the breaker every other tenant shares.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, TimeoutError | OSError | httpx.TransportError):
            return True
        code = getattr(current, "code", None)
        if isinstance(code, int) and not isinstance(code, bool):
            return code >= 500 or code in _PROVIDER_SIDE_HTTP
        current = current.__cause__ or current.__context__
    return False


def _reported_count(usage: Any, key: str) -> int | None:
    value = usage.get(key) if isinstance(usage, Mapping) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _result_from(message: Any) -> GeminiResult:
    usage = getattr(message, "usage_metadata", None)
    metadata = getattr(message, "response_metadata", None)
    finish = metadata.get("finish_reason") if isinstance(metadata, Mapping) else None
    return GeminiResult(
        content=str(message.content),
        finish_reason=finish if isinstance(finish, str) else None,
        prompt_tokens=_reported_count(usage, "input_tokens"),
        completion_tokens=_reported_count(usage, "output_tokens"),
        total_tokens=_reported_count(usage, "total_tokens"),
    )


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
        model_name: str | None = None,
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
            overrides = dict(kwargs)
            if model_name is not None:
                overrides["model_name"] = model_name
            self.config = replace(GeminiConfig.from_env(api_key=api_key), **overrides)
        else:
            self.config = GeminiConfig.from_env()

        # Store individual attributes for compatibility
        self.api_key = self.config.api_key
        self.model_name = self.config.model_name

        self.model = ChatGoogleGenerativeAI(
            model=self.config.model_name,
            google_api_key=self.config.api_key,
            temperature=self.config.temperature,
            top_p=self.config.top_p,
            top_k=self.config.top_k,
            max_output_tokens=self.config.max_output_tokens,
            # The SDK default is 6 resends, each billed under one reservation.
            max_retries=1,
        )

        self.rate_limiter = RateLimiter(
            rate_limit=kwargs.get("rate_limit", self.config.rate_limit),
            rate_period=kwargs.get("rate_period", self.config.rate_period),
            max_concurrent=kwargs.get("max_concurrent", self.config.max_concurrent),
        )

        # One breaker per service: a provider that keeps failing is not re-called
        # until its cooldown ends, and the refusal is its own error, not a value.
        self.circuit = CircuitBreaker()

        self.cache_client = cache_client
        self._cache_enabled = self.config.cache_enabled and cache_client is not None

        logger.info("gemini_service_initialized", model=self.config.model_name)

    async def generate_content_result(
        self, prompt: str, *, record_metrics: bool = True
    ) -> GeminiResult:
        """One Gemini call, one attempt, with the metadata the provider reported.

        Retrying is the caller's decision: a hidden retry here would spend again
        under a reservation that covers a single call. ``record_metrics=False``
        is for a caller that records the call itself (the provider adapter).
        """
        if not self.circuit.allows(datetime.now(UTC)):
            raise GeminiCircuitOpenError("gemini circuit is open")
        epoch = self.circuit.epoch
        started = time.monotonic()
        try:
            async with self.rate_limiter.acquire():
                message = await self.model.ainvoke(prompt)
            result = _result_from(message)
        except BaseException as exc:
            if isinstance(exc, Exception) and _is_provider_side(exc):
                self.circuit.record_failure(datetime.now(UTC), epoch=epoch)
            else:
                # Cancelled or caller-caused: no evidence about the provider,
                # but a half-open trial slot must still go back.
                self.circuit.record_abandoned()
            logger.error("gemini_api_error", error=str(exc))
            if record_metrics and isinstance(exc, Exception):
                self._record_call(None, started, success=False)
            raise
        self.circuit.record_success(epoch=epoch)
        if record_metrics:
            self._record_call(result, started, success=True)
        return result

    def _record_call(
        self, result: GeminiResult | None, started: float, *, success: bool
    ) -> None:
        # Cost is not priced here, so it is recorded as 0.0 on the counter and
        # never presented as a measurement; budgets price calls at the provider.
        record_llm_call(
            LLMCallMetrics(
                provider="gemini",
                model=self.model_name,
                prompt_tokens=(result.prompt_tokens or 0) if result else 0,
                completion_tokens=(result.completion_tokens or 0) if result else 0,
                latency_ms=int((time.monotonic() - started) * 1000),
                cost_usd=0.0,
                success=success,
            )
        )

    async def _generate_content(self, prompt: str) -> str:
        return (await self.generate_content_result(prompt)).content

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
        self, prompt: str, schema: type[Any], max_retries: int = 0
    ) -> Any:
        """
        Generate structured content from Gemini using Pydantic schema validation.

        Args:
            prompt: The prompt to send to Gemini.
            schema: Pydantic model class for output validation.
            max_retries: Extra calls allowed after a parse failure; each is a new
                billed request, so the default is none.

        Returns:
            Validated Pydantic model instance.

        Raises:
            Exception: If parsing fails after all retries.
        """
        from langchain_core.output_parsers import PydanticOutputParser

        parser = PydanticOutputParser(pydantic_object=schema)

        # Enhance prompt with format instructions
        format_instructions = parser.get_format_instructions()
        enhanced_prompt = f"{prompt}\n\n{format_instructions}"

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                # One call per attempt; a re-ask is the caller's explicit choice
                response = await self._generate_content(enhanced_prompt)

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
        except (RedisError, OSError, ValueError) as e:
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
                    cache_key,
                    serialize_for_cache(data).decode("utf-8"),
                    ex=self.config.cache_ttl,
                )
                logger.debug("gemini_cache_write_completed", cache_key=cache_key)
        except (RedisError, OSError, TypeError) as e:
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

        cached = await self._get_cached_response(cache_key)
        if cached:
            return cached

        from src.services.prompts.research_prompts import (
            generate_query_decomposition_prompt,
        )

        prompt = generate_query_decomposition_prompt(query)

        # Generate response
        response = await self._generate_content(prompt)

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

        cached = await self._get_cached_response(cache_key)
        if cached:
            return cached

        from src.services.prompts.research_prompts import (
            generate_literature_review_prompt,
        )

        prompt = generate_literature_review_prompt(sources)

        # Generate response
        response = await self._generate_content(prompt)

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

        cached = await self._get_cached_response(cache_key)
        if cached:
            return cached

        from src.services.prompts.research_prompts import generate_synthesis_prompt

        prompt = generate_synthesis_prompt(findings)

        # Generate response
        response = await self._generate_content(prompt)

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

        cached = await self._get_cached_response(cache_key)
        if cached:
            return cached

        from src.services.prompts.agent_prompts import generate_citation_agent_prompt

        prompt = generate_citation_agent_prompt(sources)

        # Generate response
        response = await self._generate_content(prompt)

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

        cached = await self._get_cached_response(cache_key)
        if cached:
            return cached

        from src.services.prompts.validation_prompts import (
            generate_hypothesis_validation_prompt,
        )

        prompt = generate_hypothesis_validation_prompt(hypothesis, data)

        # Generate response
        response = await self._generate_content(prompt)

        result = self._parse_json_response(response)

        # Cache result
        await self._set_cached_response(cache_key, result)

        return result
