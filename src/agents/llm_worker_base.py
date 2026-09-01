"""Shared base for prompt-driven LLM worker agents.

A lightweight ``BaseAgent`` scaffold for domain worker agents that reason over
the query (and any prior-stage context) with the Gemini service. Subclasses set
``agent_type`` and implement ``_build_prompt``. No external data sources.

Workers may request an internal tool only with the exact JSON envelope described
by :data:`TOOL_CALL_RESPONSE_INSTRUCTIONS`. Anything else is ordinary model
text and is returned unchanged. A successful tool call gets one follow-up model
turn for a final answer; a follow-up tool call is refused at the hard two-turn
limit. Tool failures are returned as structured failures without a fabricated
model answer.
"""

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from src.agents.base import BaseAgent
from src.agents.models import AgentResult, AgentTask
from src.agents.tools.base_tool import ToolResult
from src.agents.tools.mediation import ToolCallIdentity
from src.agents.tools.registry import ToolRegistry
from src.core.capabilities import CAPABILITY_GRANTS_CONTEXT_KEY

if TYPE_CHECKING:
    from src.ai_brain.memory import ProceduralMemoryManager


MAX_TOOL_MODEL_TURNS: Final[int] = 2
"""Maximum model turns for one worker execution that enters the tool loop."""

TOOL_CALL_RESPONSE_INSTRUCTIONS: Final[str] = """Tool-call response contract:
To request a listed tool, respond with exactly one JSON object of this shape:
{"tool_call":{"name":"tool_name","arguments":{}}}
The top-level object must contain only ``tool_call``. The ``tool_call`` object
must contain only ``name`` and ``arguments``; ``name`` is a non-empty string and
``arguments`` is a JSON object. Do not use markdown fences or surrounding text.
If no tool is needed, respond with ordinary text instead of JSON. Only one tool
call is allowed; after a tool result, provide the final answer as ordinary text.
"""
"""The prompt-visible, strict response envelope accepted by the worker."""


@dataclass(frozen=True, slots=True)
class _ToolCall:
    """A validated model-requested tool call."""

    name: str
    arguments: dict[str, Any]


class _MalformedToolCallError(ValueError):
    """A response claimed the tool-call envelope but did not match it."""


def _parse_tool_call_response(content: str) -> _ToolCall | None:
    """Parse only the strict tool-call envelope; leave other text untouched."""
    try:
        decoded = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(decoded, dict) or "tool_call" not in decoded:
        return None
    if set(decoded) != {"tool_call"}:
        raise _MalformedToolCallError(
            "the top-level object must contain only 'tool_call'"
        )

    tool_call = decoded["tool_call"]
    if not isinstance(tool_call, dict) or set(tool_call) != {"name", "arguments"}:
        raise _MalformedToolCallError(
            "tool_call must contain only 'name' and 'arguments'"
        )

    name = tool_call["name"]
    if not isinstance(name, str) or not name or name != name.strip():
        raise _MalformedToolCallError(
            "tool_call.name must be a non-empty, whitespace-normalized string"
        )

    arguments = tool_call["arguments"]
    if not isinstance(arguments, dict):
        raise _MalformedToolCallError("tool_call.arguments must be a JSON object")

    return _ToolCall(name=name, arguments=dict(arguments))


def _tool_result_payload(result: ToolResult) -> dict[str, Any]:
    """Project a registry result into an honest model/worker-facing object."""
    if result.success:
        return {"success": True, "value": result.value}
    return {
        "success": False,
        "error": result.error or "Tool execution failed",
    }


class PlanProviderUnavailableError(RuntimeError):
    """A plan-backed generation call failed with no substitution permitted.

    Raised only when the task carries a plan-authoritative
    ``provider_model_policy`` (attached solely by
    ``ExecutionPlanTopologyExecutor`` for v1 plans, which admission already
    restricts to ``FallbackMode.FAIL_CLOSED`` with no fallbacks). The
    presence of the policy is itself the fail-closed signal: on failure the
    caller must not substitute Gemini or any other provider.
    """


@dataclass(frozen=True)
class _GenerationRoute:
    """A resolved provider router plus the exact routing decision to use."""

    router: Any
    routing_decision: dict[str, Any]
    fail_closed: bool


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

    def _plan_provider_policy(self, task: AgentTask | None) -> dict[str, Any] | None:
        """Extract the plan-authoritative provider policy, if present.

        Only ``ExecutionPlanTopologyExecutor`` attaches this key, and only for
        plans the v1 admission gate already restricted to
        ``FallbackMode.FAIL_CLOSED`` with no fallbacks — so its mere presence
        means the plan's primary route is the only route this call may use.
        A task-less call (``task=None``) can never be plan-backed.
        """
        if task is None:
            return None
        policy = task.input_data.get("provider_model_policy")
        return policy if isinstance(policy, dict) else None

    def _build_plan_route(self, policy: dict[str, Any]) -> _GenerationRoute:
        """Build a router restricted to exactly the plan's primary provider."""
        from src.ai_brain.providers import ModelRouter
        from src.core.config import settings

        provider_name = policy["primary"]["provider"]
        model_name = policy["primary"]["model"]
        provider_configs = settings.get_ai_brain_config()["providers"]
        provider_config = {**provider_configs.get(provider_name, {}), "enabled": True}
        router = ModelRouter(
            {
                "providers": {provider_name: provider_config},
                "enable_fallback": False,
            },
            component_registry=self.config.get("component_registry"),
        )
        return _GenerationRoute(
            router=router,
            routing_decision={
                "primary_model": {"provider": provider_name, "name": model_name},
                "fallback_models": [],
            },
            fail_closed=True,
        )

    def _build_legacy_openrouter_route(self) -> _GenerationRoute | None:
        """Preserve today's flag-driven OpenRouter routing, unchanged."""
        from src.ai_brain.providers import ModelRouter
        from src.core.config import settings

        if (
            not settings.MULTI_PROVIDER_ROUTING_ENABLED
            or not settings.OPENROUTER_API_KEY
        ):
            return None

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
            self._model_router = ModelRouter(
                router_config,
                component_registry=self.config.get("component_registry"),
            )
        return _GenerationRoute(
            router=self._model_router,
            routing_decision={
                "primary_model": {"provider": "openrouter", "name": None},
                "fallback_models": [],
            },
            fail_closed=False,
        )

    def _select_generation_route(
        self, task: AgentTask | None
    ) -> _GenerationRoute | None:
        """Resolve which provider route to use for this call.

        A plan-backed task's policy always wins. Otherwise this preserves
        today's flag-driven OpenRouter routing (``None`` means "use
        GeminiService directly") — independent of whether ``task`` is given,
        matching the original method's task-optional contract.
        """
        policy = self._plan_provider_policy(task)
        if policy is not None:
            return self._build_plan_route(policy)
        return self._build_legacy_openrouter_route()

    async def _generate_with_routing(
        self, prompt: str, task: AgentTask
    ) -> tuple[str | None, float]:
        """Generate content via the resolved provider route, or GeminiService.

        A plan-backed task uses only the plan's primary provider/model and
        raises ``PlanProviderUnavailableError`` on failure rather than
        substituting another provider. Otherwise this preserves the existing
        ModelRouter/OpenRouter-with-Gemini-fallback behavior, unchanged.

        Args:
            prompt: The prompt to generate content for
            task: AgentTask context (for metadata/tier information)

        Returns:
            Tuple of (content, confidence_score). Content is None on failure.

        Raises:
            PlanProviderUnavailableError: the plan-backed primary provider
                failed and no fallback is permitted.
        """
        route = self._select_generation_route(task)
        if route is None:
            return await self._generate_with_gemini(prompt)

        try:
            from src.ai_brain.providers import ModelRequest

            request = ModelRequest(
                prompt=prompt,
                max_tokens=2000,
                temperature=0.7,
                complexity_score=task.input_data.get("complexity_score", 0.5),
                metadata={"tier": self._determine_tier(task)},
            )

            response = await route.router.route_and_generate(
                request, routing_decision=route.routing_decision
            )

            if response.success:
                return response.content, response.confidence_score

            if route.fail_closed:
                raise PlanProviderUnavailableError(
                    "plan-backed provider "
                    f"{route.routing_decision['primary_model']['provider']!r} "
                    f"failed: {response.error_message}"
                )

            self.log_warning(
                f"OpenRouter generation failed: {response.error_message}, "
                "falling back to GeminiService"
            )

        except PlanProviderUnavailableError:
            raise
        except Exception as exc:
            if route.fail_closed:
                raise PlanProviderUnavailableError(
                    f"plan-backed provider routing failed: {exc}"
                ) from exc
            self.log_warning(
                f"ModelRouter routing failed: {exc}, falling back to GeminiService"
            )

        # Fallback to GeminiService (legacy route only; plan routes raised above)
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
        self,
        prompt: str,
        schema: type[Any],
        task: AgentTask | None = None,
        max_tokens: int = 4000,
        tier: str | None = None,
    ) -> Any:
        """Generate structured content via ModelRouter or GeminiService with graceful fallback.

        Callers can check `self.last_structured_truncated` (reset on every
        call) for a programmatic signal that the returned object came from a
        truncated generation.

        Truncation semantics: if the first attempt parses as schema-valid but
        hit the token cap (finish_reason == "length") and the retry then fails,
        the truncated-but-valid first result IS returned (with a warning
        logged). Callers requiring complete content should treat that warning
        as a quality signal.

        A plan-backed task (``task.input_data["provider_model_policy"]``
        present) uses only the plan's primary provider/model and raises
        ``PlanProviderUnavailableError`` if it fails, instead of falling
        through to GeminiService. Otherwise this preserves the existing
        MULTI_PROVIDER_ROUTING_ENABLED/OPENROUTER_API_KEY-gated
        ModelRouter/OpenRouterProvider routing, unchanged.

        Args:
            prompt: The prompt to generate content for
            schema: Pydantic model class for output validation
            task: Optional AgentTask context (for metadata/tier information)
            max_tokens: Maximum tokens for generation (default 4000)
            tier: Optional explicit tier override ("simple"/"balanced"/"complex").
                  Precedence: explicit tier > task-derived > "balanced"

        Returns:
            Validated Pydantic model instance

        Raises:
            PlanProviderUnavailableError: the plan-backed primary provider
                failed and no fallback is permitted.
            Exception: If generation and fallback both fail (non-plan-backed)
        """
        self.last_structured_truncated = False

        route = self._select_generation_route(task)
        if route is None:
            gemini = self._ensure_gemini_service()
            if gemini is None:
                raise ValueError("No language model configured")
            return await gemini.generate_structured_content(prompt, schema)

        # Route through the resolved provider (JSON mode)
        try:
            from src.ai_brain.providers import ModelRequest

            # Embed JSON schema in the prompt (broadest model support)
            schema_instructions = self._build_json_schema_instructions(schema)
            enhanced_prompt = f"{prompt}\n\n{schema_instructions}"

            # Determine tier with precedence: explicit > task-derived > balanced
            if tier is not None:
                routing_tier = tier
            elif task is not None:
                routing_tier = self._determine_tier(task)
            else:
                routing_tier = "balanced"

            # Build ModelRequest with JSON response format in metadata
            metadata = {
                "tier": routing_tier,
                "response_format": {
                    "type": "json_object"
                },  # OpenAI-compatible JSON mode
            }

            request = ModelRequest(
                prompt=enhanced_prompt,
                max_tokens=max_tokens,
                temperature=0.7,
                complexity_score=task.input_data.get("complexity_score", 0.5)
                if task
                else 0.5,
                metadata=metadata,
            )

            # Route and generate with truncation-aware retry
            response = await route.router.route_and_generate(
                request, routing_decision=route.routing_decision
            )

            if response.success:
                # Parse and validate JSON response with Pydantic schema.
                # Models frequently wrap json_object output in markdown fences,
                # so use the fence-tolerant parser rather than bare json.loads.
                from src.services.parsers.json_parser import parse_json_response

                needs_retry = False
                retry_reason = None
                # Best-effort fallback: a schema-valid parse whose generation
                # hit the token cap (content may be truncated mid-field).
                truncated_but_valid = None

                try:
                    parsed_data = parse_json_response(response.content)
                    validated = schema.model_validate(parsed_data)

                    # Check if truncation occurred (finish_reason == "length")
                    if response.finish_reason == "length":
                        needs_retry = True
                        retry_reason = "finish_reason_length"
                        truncated_but_valid = validated
                        self.log_info(
                            "structured_generation_truncated",
                            finish_reason=response.finish_reason,
                            max_tokens=max_tokens,
                        )
                    else:
                        # Success: no truncation, valid parse
                        return validated

                except (ValueError, TypeError) as parse_err:
                    needs_retry = True
                    retry_reason = "parse_validation_failed"
                    self.log_warning(
                        f"OpenRouter JSON parse/validation failed: {parse_err}"
                    )

                # Retry ONCE with doubled max_tokens (capped at 8000) if needed
                if needs_retry and max_tokens < 8000:
                    retry_max_tokens = min(max_tokens * 2, 8000)
                    self.log_info(
                        "structured_generation_retry",
                        reason=retry_reason,
                        original_max_tokens=max_tokens,
                        retry_max_tokens=retry_max_tokens,
                    )

                    # Build retry request with increased token budget
                    retry_request = ModelRequest(
                        prompt=enhanced_prompt,
                        max_tokens=retry_max_tokens,
                        temperature=0.7,
                        complexity_score=task.input_data.get("complexity_score", 0.5)
                        if task
                        else 0.5,
                        metadata=metadata,
                    )

                    # Pin the retry to the model that produced the first
                    # attempt so the retry is a continuation, not a re-roll.
                    retry_response = await route.router.route_and_generate(
                        retry_request,
                        routing_decision={
                            "primary_model": {
                                "provider": route.routing_decision["primary_model"][
                                    "provider"
                                ],
                                "name": getattr(response, "model_name", None),
                            },
                            "fallback_models": [],
                        },
                    )

                    if retry_response.success:
                        try:
                            retry_parsed = parse_json_response(retry_response.content)
                            retry_validated = schema.model_validate(retry_parsed)
                            self.log_info(
                                "structured_generation_retry_success",
                                reason=retry_reason,
                            )
                            return retry_validated
                        except (ValueError, TypeError) as retry_parse_err:
                            if truncated_but_valid is not None:
                                self.log_warning(
                                    "Retry parse failed; returning the schema-valid "
                                    f"but truncated first attempt: {retry_parse_err}"
                                )
                                self.last_structured_truncated = True
                                return truncated_but_valid
                            self.log_warning(
                                f"Retry parse/validation also failed: {retry_parse_err}, "
                                "falling back to GeminiService"
                            )
                    else:
                        if truncated_but_valid is not None:
                            self.log_warning(
                                "Retry generation failed; returning the schema-valid "
                                f"but truncated first attempt: {retry_response.error_message}"
                            )
                            self.last_structured_truncated = True
                            return truncated_but_valid
                        self.log_warning(
                            f"Retry generation failed: {retry_response.error_message}, "
                            "falling back to GeminiService"
                        )
                elif needs_retry:
                    if truncated_but_valid is not None:
                        self.log_warning(
                            f"Truncation at max_tokens={max_tokens} >= 8000 cap; "
                            "returning the schema-valid but truncated result"
                        )
                        self.last_structured_truncated = True
                        return truncated_but_valid
                    # Already at or above 8000 token cap, skip retry
                    self.log_warning(
                        f"Truncation detected but max_tokens={max_tokens} >= 8000 cap, "
                        "falling back to GeminiService"
                    )
                # If we get here, both initial and retry (if attempted) failed
                # Fall through to Gemini fallback

            else:
                # OpenRouter failed, log and fall back to Gemini
                self.log_warning(
                    f"OpenRouter generation failed: {response.error_message}, "
                    "falling back to GeminiService"
                )

        except PlanProviderUnavailableError:
            raise
        except Exception as exc:
            if route.fail_closed:
                raise PlanProviderUnavailableError(
                    f"plan-backed structured generation failed: {exc}"
                ) from exc
            self.log_warning(
                f"ModelRouter structured routing failed: {exc}, "
                "falling back to GeminiService"
            )

        if route.fail_closed:
            raise PlanProviderUnavailableError(
                "plan-backed provider "
                f"{route.routing_decision['primary_model']['provider']!r} failed "
                "and no fallback is permitted"
            )

        # Fallback to GeminiService (legacy route only; plan routes raised above)
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

    def _tool_call_output(self, tool_call: _ToolCall) -> dict[str, Any]:
        """Return the stable representation of a validated tool call."""
        return {
            "name": tool_call.name,
            "arguments": tool_call.arguments,
        }

    def _tool_failure_result(
        self,
        task: AgentTask,
        computed: dict[str, Any] | None,
        *,
        code: str,
        message: str,
        tool_call: _ToolCall | None = None,
        tool_result: dict[str, Any] | None = None,
    ) -> AgentResult:
        """Build a failed result without presenting a final model answer."""
        output: dict[str, Any] = {
            "agent_type": self.agent_type,
            "error": message,
            "error_type": code,
            "tool_error": {"code": code, "message": message},
        }
        if tool_call is not None:
            output["tool_call"] = self._tool_call_output(tool_call)
        if tool_result is not None:
            output["tool_result"] = tool_result
        if computed:
            output["computed"] = computed

        return AgentResult(
            task_id=task.id,
            status="failed",
            output=output,
            confidence=0.0,
            execution_time=0.0,
            metadata=self.build_execution_metadata(agent_type=self.agent_type),
        )

    def _build_tool_follow_up_prompt(
        self,
        prompt: str,
        tool_call: _ToolCall,
        tool_result: dict[str, Any],
    ) -> str:
        """Give one successful tool result to the model for final narration."""
        result_json = json.dumps(
            {
                "tool_call": self._tool_call_output(tool_call),
                "tool_result": tool_result,
            },
            indent=2,
            default=str,
        )
        return (
            f"{prompt}\n\nTool result for the requested call:\n{result_json}\n\n"
            "Use this result when answering the original query. Respond with "
            "the final answer as ordinary text; do not request another tool."
        )

    async def _execute_model_tool_call(
        self,
        task: AgentTask,
        prompt: str,
        registry: ToolRegistry,
        tool_call: _ToolCall,
        computed: dict[str, Any] | None,
    ) -> AgentResult:
        """Execute one model call through the registry, then allow one final turn."""
        identity = ToolCallIdentity.from_agent_task(task)
        grants = task.context.get(CAPABILITY_GRANTS_CONTEXT_KEY, None)

        try:
            tool_result = await registry.execute(
                tool_call.name,
                tool_call.arguments,
                identity=identity,
                grants=grants,
            )
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            return self._tool_failure_result(
                task,
                computed,
                code="tool_execution_error",
                message=message,
                tool_call=tool_call,
                tool_result={"success": False, "error": message},
            )

        tool_result_payload = _tool_result_payload(tool_result)
        if not tool_result.success:
            return self._tool_failure_result(
                task,
                computed,
                code="tool_call_failed",
                message=tool_result_payload["error"],
                tool_call=tool_call,
                tool_result=tool_result_payload,
            )

        try:
            final_content, final_confidence = await self._generate_with_routing(
                self._build_tool_follow_up_prompt(
                    prompt, tool_call, tool_result_payload
                ),
                task,
            )
        except Exception as exc:
            message = f"Final generation failed after tool call: {exc}"
            return self._tool_failure_result(
                task,
                computed,
                code="final_generation_failed",
                message=message,
                tool_call=tool_call,
                tool_result=tool_result_payload,
            )

        if final_content is None:
            return self._tool_failure_result(
                task,
                computed,
                code="final_generation_failed",
                message="Final generation failed after tool call",
                tool_call=tool_call,
                tool_result=tool_result_payload,
            )

        try:
            second_tool_call = _parse_tool_call_response(final_content)
        except _MalformedToolCallError as exc:
            return self._tool_failure_result(
                task,
                computed,
                code="malformed_tool_call",
                message=str(exc),
                tool_call=tool_call,
                tool_result=tool_result_payload,
            )

        if second_tool_call is not None:
            return self._tool_failure_result(
                task,
                computed,
                code="tool_loop_limit",
                message=(
                    f"tool loop reached the hard limit of {MAX_TOOL_MODEL_TURNS} "
                    "model turns"
                ),
                tool_call=tool_call,
                tool_result=tool_result_payload,
            )

        output: dict[str, Any] = {
            "content": final_content,
            "analysis": final_content,
            "agent_type": self.agent_type,
            "tool_call": self._tool_call_output(tool_call),
            "tool_result": tool_result_payload,
        }
        if computed:
            output["computed"] = computed

        return AgentResult(
            task_id=task.id,
            status="success",
            output=output,
            confidence=final_confidence,
            execution_time=0.0,
            metadata=self.build_execution_metadata(agent_type=self.agent_type),
        )

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
            tool_list = registry.list_tools()
            tools_block = (
                "\n\nAvailable tools:\n"
                + json.dumps(tool_list, indent=2)
                + "\n\n"
                + TOOL_CALL_RESPONSE_INSTRUCTIONS
            )
            prompt += tools_block

        # Append precomputed values to prompt if present
        if computed:
            computed_block = "\n\nPrecomputed exact values:\n" + json.dumps(
                computed, indent=2
            )
            prompt += computed_block

        # Route through ModelRouter if multi-provider routing is enabled,
        # otherwise fall back to GeminiService (current behavior)
        content, confidence = await self._generate_with_routing(prompt, task)

        if content is None:
            return self.handle_error(task, ValueError("Generation failed"))

        try:
            tool_call = _parse_tool_call_response(content)
        except _MalformedToolCallError as exc:
            return self._tool_failure_result(
                task,
                computed,
                code="malformed_tool_call",
                message=str(exc),
            )

        if tool_call is not None:
            return await self._execute_model_tool_call(
                task,
                prompt,
                registry,
                tool_call,
                computed,
            )

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
