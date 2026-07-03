"""Shared base for prompt-driven LLM worker agents.

A lightweight ``BaseAgent`` scaffold for domain worker agents that reason over
the query (and any prior-stage context) with the Gemini service. Subclasses set
``agent_type`` and implement ``_build_prompt``. No external data sources.
"""

from typing import TYPE_CHECKING, Any

from src.agents.base import BaseAgent
from src.agents.models import AgentResult, AgentTask
from src.agents.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from src.ai_brain.memory import ProceduralMemoryManager


class LLMWorkerAgentBase(BaseAgent):
    """Execute/validate scaffolding for prompt-driven LLM worker agents."""

    agent_type: str = "llm_worker"
    # Procedural memory manager (injected externally, None = no memory)
    procedural_memory: "ProceduralMemoryManager | None" = None
    # Tool registry (built on first access if not injected)
    _tool_registry: ToolRegistry | None = None

    def get_agent_type(self) -> str:
        return self.agent_type

    def _build_prompt(self, query: str, task: AgentTask) -> str:
        """Return the agent-specific prompt. Overridden by each agent."""
        raise NotImplementedError

    def _ensure_gemini_service(self) -> Any:
        """Lazily obtain a Gemini service when one was not injected.

        Supervisors inject a shared service; the Bypass single-agent path does
        not, so build one on demand (cached on the instance). Returns None if a
        service cannot be constructed, in which case a deterministic fallback is
        used instead of failing.
        """
        if self.gemini_service is not None:
            return self.gemini_service
        try:
            from src.core.config import settings
            from src.services.gemini_service import GeminiService

            self.gemini_service = GeminiService(api_key=settings.GEMINI_API_KEY)
        except Exception as exc:  # pragma: no cover - env-dependent
            self.log_warning(f"gemini_service_unavailable: {exc}")
            self.gemini_service = None
        return self.gemini_service

    def _precompute(self, task: AgentTask) -> dict[str, Any] | None:
        """Optional hook for deterministic precomputation.

        When a subclass returns a dict, the execute method will:
        1. Append a formatted "Precomputed exact values:" block to the prompt
        2. Merge the dict into output["computed"]

        Returns None by default (no precomputation).
        """
        return None

    def _register_tools(self, registry: ToolRegistry) -> None:
        """Optional hook for registering agent-specific tools.

        Subclasses override this to register tools they want available.
        Default: no tools registered.

        Example:
            def _register_tools(self, registry: ToolRegistry) -> None:
                from src.agents.tools import ArithmeticTool
                registry.register(ArithmeticTool())
        """
        pass

    def _get_tool_registry(self) -> ToolRegistry:
        """Get or create the tool registry for this agent.

        Lazy initialization: builds registry on first access and caches it.
        Subclasses call _register_tools() to declare their tools.
        """
        if self._tool_registry is None:
            from src.agents.tools.registry import ToolRegistry

            self._tool_registry = ToolRegistry()
            self._register_tools(self._tool_registry)
        return self._tool_registry

    async def _get_procedural_context(self, task: AgentTask) -> str | None:
        """Query procedural memory for relevant past approaches.

        Returns a formatted context block with top successful procedures, or None
        if memory is unavailable/disabled/empty.
        """
        try:
            from src.core.config import settings

            if not settings.MEMORY_INFORMED_ROUTING_ENABLED:
                return None
            if self.procedural_memory is None:
                return None

            from src.ai_brain.memory.procedural_memory import (
                ProcedureQuery,
                ProcedureType,
            )

            # Query for relevant procedures
            query_domains = task.input_data.get("domains", [])
            domain = query_domains[0] if query_domains else None

            procedure_query = ProcedureQuery(
                procedure_type=ProcedureType.STRATEGY,
                domain=domain,
                agent_type=self.agent_type,
                min_confidence=0.6,
                min_success_rate=0.7,
                limit=settings.MEMORY_PROMPT_MAX_PROCEDURES,
            )

            procedures = await self.procedural_memory.retrieve_procedures(
                procedure_query
            )

            if not procedures:
                return None

            # Format as a concise context block
            lines = ["## Past Successful Approaches"]
            for i, proc in enumerate(procedures, 1):
                success_rate = proc.success_count / max(proc.usage_count, 1)
                lines.append(
                    f"{i}. {proc.name} (confidence: {proc.confidence:.2f}, "
                    f"success: {success_rate:.1%}, used {proc.usage_count}x)"
                )
                if proc.parameters:
                    # Truncate if too long
                    params_str = str(proc.parameters)
                    if len(params_str) > 100:
                        params_str = params_str[:97] + "..."
                    lines.append(f"   Parameters: {params_str}")

            return "\n".join(lines)

        except Exception as e:
            # Resilient: log at debug and return None (no memory context)
            self.log_warning(f"procedural_context_failed (graceful fallback): {e}")
            return None

    async def _generate_with_routing(
        self, prompt: str, task: AgentTask
    ) -> tuple[str | None, float]:
        """Generate content via ModelRouter or GeminiService with graceful fallback.

        When MULTI_PROVIDER_ROUTING_ENABLED=True and OPENROUTER_API_KEY is set,
        routes through ModelRouter/OpenRouterProvider. Otherwise falls back to
        GeminiService (preserves current behavior).

        Args:
            prompt: The prompt to generate content for
            task: AgentTask context (for metadata/tier information)

        Returns:
            Tuple of (content, confidence_score). Content is None on failure.
        """
        from src.core.config import settings

        # Check if multi-provider routing is enabled
        if (
            not settings.MULTI_PROVIDER_ROUTING_ENABLED
            or not settings.OPENROUTER_API_KEY
        ):
            # Fallback to GeminiService (current behavior)
            return await self._generate_with_gemini(prompt)

        # Route through ModelRouter with OpenRouter
        try:
            from src.ai_brain.providers import ModelRequest, ModelRouter

            # Build ModelRequest
            request = ModelRequest(
                prompt=prompt,
                max_tokens=2000,
                temperature=0.7,
                complexity_score=task.input_data.get("complexity_score", 0.5),
                metadata={"tier": self._determine_tier(task)},
            )

            # Initialize ModelRouter (lazy, cached on instance)
            if not hasattr(self, "_model_router"):
                router_config = {
                    "providers": {
                        "openrouter": {
                            "enabled": True,
                            "api_key": settings.OPENROUTER_API_KEY,
                            "endpoint": settings.OPENROUTER_ENDPOINT,
                            "tier_mapping": settings.OPENROUTER_TIER_MAPPING,
                        }
                    },
                    "enable_fallback": True,
                    "max_retries": 3,
                }
                self._model_router = ModelRouter(router_config)

            # Route and generate via OpenRouter
            response = await self._model_router.route_and_generate(
                request,
                routing_decision={
                    "primary_model": {"provider": "openrouter", "name": None},
                    "fallback_models": [],
                },
            )

            if response.success:
                return response.content, response.confidence_score

            # OpenRouter failed, log and fall back to Gemini
            self.log_warning(
                f"OpenRouter generation failed: {response.error_message}, "
                "falling back to GeminiService"
            )

        except Exception as exc:
            self.log_warning(
                f"ModelRouter routing failed: {exc}, falling back to GeminiService"
            )

        # Fallback to GeminiService on any error
        return await self._generate_with_gemini(prompt)

    async def _generate_with_gemini(self, prompt: str) -> tuple[str | None, float]:
        """Generate content using GeminiService (current default path).

        Args:
            prompt: The prompt to generate content for

        Returns:
            Tuple of (content, confidence_score). Content is None on failure.
        """
        gemini = self._ensure_gemini_service()
        if gemini is None:
            content = (
                f"[{self.agent_type}] No language model configured; unable to "
                f"produce a full {self.agent_type.replace('_', ' ')}."
            )
            return content, 0.3

        try:
            content = await gemini.generate_content(prompt)
            confidence = 0.85 if content and content.strip() else 0.3
            return content, confidence
        except Exception as exc:
            self.log_error(f"{self.agent_type} generation failed: {exc}")
            return None, 0.0

    async def _generate_structured_with_routing(
        self, prompt: str, schema: type[Any], task: AgentTask | None = None
    ) -> Any:
        """Generate structured content via ModelRouter or GeminiService with graceful fallback.

        When MULTI_PROVIDER_ROUTING_ENABLED=True and OPENROUTER_API_KEY is set,
        routes through ModelRouter/OpenRouterProvider using JSON mode. Otherwise
        falls back to GeminiService (preserves current behavior).

        Args:
            prompt: The prompt to generate content for
            schema: Pydantic model class for output validation
            task: Optional AgentTask context (for metadata/tier information)

        Returns:
            Validated Pydantic model instance

        Raises:
            Exception: If generation and fallback both fail
        """
        from src.core.config import settings

        # Check if multi-provider routing is enabled
        if (
            not settings.MULTI_PROVIDER_ROUTING_ENABLED
            or not settings.OPENROUTER_API_KEY
        ):
            # Fallback to GeminiService (current behavior)
            gemini = self._ensure_gemini_service()
            if gemini is None:
                raise ValueError("No language model configured")
            return await gemini.generate_structured_content(prompt, schema)

        # Route through ModelRouter with OpenRouter (JSON mode)
        try:
            from src.ai_brain.providers import ModelRequest, ModelRouter

            # Embed JSON schema in the prompt (broadest model support)
            schema_instructions = self._build_json_schema_instructions(schema)
            enhanced_prompt = f"{prompt}\n\n{schema_instructions}"

            # Build ModelRequest with JSON response format in metadata
            metadata = {
                "tier": self._determine_tier(task) if task else "balanced",
                "response_format": {
                    "type": "json_object"
                },  # OpenAI-compatible JSON mode
            }

            request = ModelRequest(
                prompt=enhanced_prompt,
                max_tokens=2000,
                temperature=0.7,
                complexity_score=task.input_data.get("complexity_score", 0.5)
                if task
                else 0.5,
                metadata=metadata,
            )

            # Initialize ModelRouter (lazy, cached on instance)
            if not hasattr(self, "_model_router"):
                router_config = {
                    "providers": {
                        "openrouter": {
                            "enabled": True,
                            "api_key": settings.OPENROUTER_API_KEY,
                            "endpoint": settings.OPENROUTER_ENDPOINT,
                            "tier_mapping": settings.OPENROUTER_TIER_MAPPING,
                        }
                    },
                    "enable_fallback": True,
                    "max_retries": 3,
                }
                self._model_router = ModelRouter(router_config)

            # Route and generate via OpenRouter
            response = await self._model_router.route_and_generate(
                request,
                routing_decision={
                    "primary_model": {"provider": "openrouter", "name": None},
                    "fallback_models": [],
                },
            )

            if response.success:
                # Parse and validate JSON response with Pydantic schema
                import json

                try:
                    parsed_data = json.loads(response.content)
                    validated = schema.model_validate(parsed_data)
                    return validated
                except (json.JSONDecodeError, ValueError) as parse_err:
                    self.log_warning(
                        f"OpenRouter JSON parse/validation failed: {parse_err}, "
                        "falling back to GeminiService"
                    )

            else:
                # OpenRouter failed, log and fall back to Gemini
                self.log_warning(
                    f"OpenRouter generation failed: {response.error_message}, "
                    "falling back to GeminiService"
                )

        except Exception as exc:
            self.log_warning(
                f"ModelRouter structured routing failed: {exc}, "
                "falling back to GeminiService"
            )

        # Fallback to GeminiService on any error
        gemini = self._ensure_gemini_service()
        if gemini is None:
            raise ValueError("No language model configured and OpenRouter failed")
        return await gemini.generate_structured_content(prompt, schema)

    def _build_json_schema_instructions(self, schema: type[Any]) -> str:
        """Build JSON schema instructions for the prompt.

        Embeds the JSON schema description in the prompt to guide model output.
        Uses Pydantic's schema generation for broadest model compatibility.

        Args:
            schema: Pydantic model class

        Returns:
            Formatted schema instructions string
        """
        try:
            schema_dict = schema.model_json_schema()
            import json

            schema_json = json.dumps(schema_dict, indent=2)
            return (
                f"Respond with valid JSON matching this schema:\n{schema_json}\n\n"
                "Your response must be valid JSON that can be parsed and validated "
                "against this schema."
            )
        except Exception as e:
            # Fallback to basic instructions if schema generation fails
            self.log_warning(f"Schema generation failed: {e}, using basic instructions")
            return "Respond with valid JSON."

    def _determine_tier(self, task: AgentTask) -> str:
        """Determine routing tier based on task complexity.

        Maps complexity score to tier (simple/balanced/complex) for OpenRouter
        model selection.

        Args:
            task: AgentTask context

        Returns:
            Tier string: "simple", "balanced", or "complex"
        """
        complexity_score = task.input_data.get("complexity_score", 0.5)

        if complexity_score < 0.3:
            return "simple"
        elif complexity_score < 0.7:
            return "balanced"
        else:
            return "complex"

    async def execute(self, task: AgentTask) -> AgentResult:
        query = str(task.input_data.get("query", "")).strip()
        if not query:
            return self.handle_error(task, ValueError("Query cannot be empty"))

        # Run optional precomputation hook
        computed = self._precompute(task)

        # Build base prompt
        prompt = self._build_prompt(query, task)

        # Append procedural memory context if available (feature-flagged)
        procedural_context = await self._get_procedural_context(task)
        if procedural_context:
            prompt += f"\n\n{procedural_context}\n"

        # Append tool availability context if tools are registered
        registry = self._get_tool_registry()
        if len(registry) > 0:
            import json

            tool_list = registry.list_tools()
            tools_block = "\n\nAvailable tools:\n" + json.dumps(tool_list, indent=2)
            prompt += tools_block

        # Append precomputed values to prompt if present
        if computed:
            import json

            computed_block = "\n\nPrecomputed exact values:\n" + json.dumps(
                computed, indent=2
            )
            prompt += computed_block

        # Route through ModelRouter if multi-provider routing is enabled,
        # otherwise fall back to GeminiService (current behavior)
        content, confidence = await self._generate_with_routing(prompt, task)

        if content is None:
            return self.handle_error(task, ValueError("Generation failed"))

        # Build output with optional computed field
        output: dict[str, Any] = {
            "content": content,
            "analysis": content,
            "agent_type": self.agent_type,
        }
        if computed:
            output["computed"] = computed

        return AgentResult(
            task_id=task.id,
            status="success",
            output=output,
            confidence=confidence,
            execution_time=0.0,
            metadata=self.build_execution_metadata(agent_type=self.agent_type),
        )

    async def validate_result(self, result: AgentResult) -> bool:
        return (
            result.status == "success"
            and bool(result.output)
            and bool(result.output.get("content"))
        )


__all__ = ["LLMWorkerAgentBase"]
