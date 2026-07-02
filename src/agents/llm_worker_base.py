"""Shared base for prompt-driven LLM worker agents.

A lightweight ``BaseAgent`` scaffold for domain worker agents that reason over
the query (and any prior-stage context) with the Gemini service. Subclasses set
``agent_type`` and implement ``_build_prompt``. No external data sources.
"""

from typing import TYPE_CHECKING, Any

from src.agents.base import BaseAgent
from src.agents.models import AgentResult, AgentTask

if TYPE_CHECKING:
    from src.ai_brain.memory import ProceduralMemoryManager


class LLMWorkerAgentBase(BaseAgent):
    """Execute/validate scaffolding for prompt-driven LLM worker agents."""

    agent_type: str = "llm_worker"
    # Procedural memory manager (injected externally, None = no memory)
    procedural_memory: "ProceduralMemoryManager | None" = None

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

        # Append precomputed values to prompt if present
        if computed:
            import json

            computed_block = "\n\nPrecomputed exact values:\n" + json.dumps(
                computed, indent=2
            )
            prompt += computed_block

        gemini = self._ensure_gemini_service()
        if gemini is None:
            content = (
                f"[{self.agent_type}] No language model configured; unable to "
                f"produce a full {self.agent_type.replace('_', ' ')}."
            )
            confidence = 0.3
        else:
            try:
                content = await gemini.generate_content(prompt)
                confidence = 0.85 if content and content.strip() else 0.3
            except Exception as exc:
                self.log_error(f"{self.agent_type} generation failed: {exc}")
                return self.handle_error(task, exc)

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
