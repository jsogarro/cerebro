"""Cross-cutting verification (critic) agent.

Reviews another agent's output — or any content supplied in the query — for
factual errors, unsupported claims, logical inconsistencies, arithmetic
mistakes, and likely hallucinations. Returns a verdict plus specific issues.
LLM-reasoning only; no external data sources.
"""

from src.agents.llm_worker_base import LLMWorkerAgentBase
from src.agents.models import AgentTask


class VerificationAgent(LLMWorkerAgentBase):
    """Adversarially reviews content for errors and unsupported claims."""

    agent_type = "verification"

    def _build_prompt(self, query: str, task: AgentTask) -> str:
        # The content to review is whatever a prior stage produced, falling back
        # to the query itself (e.g. when called directly on pasted text).
        content = (
            task.input_data.get("content")
            or task.input_data.get("previous_result")
            or task.input_data.get("background")
            or query
        )
        return (
            "You are a rigorous fact-checker and critic. Review the content "
            "below for problems: factual errors, unsupported or overstated "
            "claims, internal contradictions, logical fallacies, arithmetic or "
            "unit mistakes, and statements that look like hallucinations. Only "
            "flag genuine issues — do not invent problems.\n\n"
            "Respond with:\n"
            "VERDICT: one of PASS (no material issues) or REVISE.\n"
            "ISSUES: a numbered list; for each, quote the problem, give its "
            "severity (low/medium/high), and a suggested correction. If there "
            "are no issues, write 'None'.\n\n"
            f"Content to review:\n{content}"
        )


__all__ = ["VerificationAgent"]
