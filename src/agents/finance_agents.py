"""Finance domain worker agents.

LLM-reasoning agents that operate on figures/assumptions/theses supplied in the
query text — no market-data feeds or datasets required. Each agent produces a
focused analysis using the shared Gemini service.
"""

from typing import Any

from structlog import get_logger

from src.agents.base import BaseAgent
from src.agents.models import AgentResult, AgentTask

logger = get_logger()


class _FinanceAgentBase(BaseAgent):
    """Shared execute/validate scaffolding for finance worker agents."""

    agent_type: str = "finance"

    def get_agent_type(self) -> str:
        return self.agent_type

    def _build_prompt(self, query: str, task: AgentTask) -> str:
        """Return the agent-specific prompt. Overridden by each agent."""
        raise NotImplementedError

    def _precompute(self, task: AgentTask) -> dict[str, Any] | None:
        """Optional hook for deterministic precomputation.

        When a subclass returns a dict, the execute method will:
        1. Append a formatted "Precomputed exact values:" block to the prompt
        2. Merge the dict into output["computed"]

        Returns None by default (no precomputation).
        """
        return None

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

    async def execute(self, task: AgentTask) -> AgentResult:
        query = str(task.input_data.get("query", "")).strip()
        if not query:
            return self.handle_error(task, ValueError("Query cannot be empty"))

        # Run optional precomputation hook
        computed = self._precompute(task)

        # Build base prompt
        prompt = self._build_prompt(query, task)

        # Append precomputed values to prompt if present
        if computed:
            import json

            computed_block = "\n\nPrecomputed exact values:\n" + json.dumps(
                computed, indent=2
            )
            prompt += computed_block

        gemini = self.gemini_service or self._ensure_gemini_service()
        if gemini is None:
            # Deterministic, dependency-free fallback so the agent still returns
            # a well-formed result when no LLM is available (e.g. unit tests).
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

        result = AgentResult(
            task_id=task.id,
            status="success",
            output=output,
            confidence=confidence,
            execution_time=0.0,
            metadata=self.build_execution_metadata(agent_type=self.agent_type),
        )
        return result

    async def validate_result(self, result: AgentResult) -> bool:
        return (
            result.status == "success"
            and bool(result.output)
            and bool(result.output.get("content"))
        )


class FinancialAnalysisAgent(_FinanceAgentBase):
    """Analyzes financial-statement figures supplied in the query."""

    agent_type = "financial_analysis"

    def _precompute(self, task: AgentTask) -> dict[str, Any] | None:
        """Compute exact financial ratios when structured parameters provided."""
        params = task.input_data.get("parameters", {})
        values = params.get("values")
        if not isinstance(values, dict) or not values:
            return None

        try:
            from src.agents.tools.finance_math import financial_ratios

            result = financial_ratios(values)
            return {"ratios": result.model_dump()}
        except Exception:
            # Silently fail on bad/missing inputs
            return None

    def _build_prompt(self, query: str, task: AgentTask) -> str:
        return (
            "You are a financial analyst. Using ONLY the figures and facts "
            "provided below (do not invent data), analyze the entity's financial "
            "health. Where the necessary inputs are present, compute and interpret "
            "key ratios (e.g. current ratio, quick ratio, debt-to-equity, gross/net "
            "margin, ROE, ROA, interest coverage). Identify strengths, weaknesses, "
            "and red flags, and give an overall health assessment. If a figure is "
            "missing, say so rather than guessing.\n\n"
            f"Request and provided figures:\n{query}"
        )


class ValuationAgent(_FinanceAgentBase):
    """Produces a valuation from assumptions supplied in the query."""

    agent_type = "valuation"

    def _precompute(self, task: AgentTask) -> dict[str, Any] | None:
        """Compute exact DCF when structured parameters provided."""
        params = task.input_data.get("parameters", {})

        # Check for explicit operation dispatch
        operation = params.get("operation")
        if operation == "dcf":
            try:
                from src.agents.tools.finance_math import dcf_gordon_growth

                result = dcf_gordon_growth(
                    params["fcf_next"], params["growth_rate"], params["discount_rate"]
                )
                return {"dcf": result.model_dump()}
            except (KeyError, ValueError, TypeError):
                return None

        # Auto-detect DCF inputs
        if all(k in params for k in ("fcf_next", "growth_rate", "discount_rate")):
            try:
                from src.agents.tools.finance_math import dcf_gordon_growth

                result = dcf_gordon_growth(
                    params["fcf_next"], params["growth_rate"], params["discount_rate"]
                )
                return {"dcf": result.model_dump()}
            except (ValueError, TypeError):
                return None

        return None

    def _build_prompt(self, query: str, task: AgentTask) -> str:
        return (
            "You are a valuation analyst. Using ONLY the assumptions provided "
            "below (do not fabricate inputs), produce a valuation. Prefer a "
            "discounted-cash-flow approach when cash flows, a growth rate, and a "
            "discount rate are given (show the mechanics and the resulting present "
            "value); use a comparables/multiples approach when multiples are "
            "provided instead. State the method, the key assumptions used, the "
            "resulting value, and a brief sensitivity note on the most important "
            "drivers. If required inputs are missing, state what is needed.\n\n"
            f"Request and provided assumptions:\n{query}"
        )


class RiskAssessmentAgent(_FinanceAgentBase):
    """Qualitatively assesses the risks of a thesis or portfolio in the query."""

    agent_type = "risk_assessment"

    def _build_prompt(self, query: str, task: AgentTask) -> str:
        return (
            "You are a risk analyst. For the investment thesis, position, or "
            "portfolio described below, identify the material risks (e.g. market, "
            "credit, liquidity, concentration, operational, regulatory, and "
            "key-assumption risks). For each, note likelihood and potential impact "
            "qualitatively, suggest mitigations, and give an overall risk rating "
            "(low / moderate / high) with justification. Base the assessment only "
            "on what is described.\n\n"
            f"Described thesis/portfolio:\n{query}"
        )


__all__ = [
    "FinancialAnalysisAgent",
    "RiskAssessmentAgent",
    "ValuationAgent",
]
