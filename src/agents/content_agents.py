"""Content domain worker agents (LLM-reasoning; no external data)."""

from src.agents.llm_worker_base import LLMWorkerAgentBase
from src.agents.models import AgentTask


def _prior(task: AgentTask) -> str:
    prior = task.input_data.get("previous_result") or task.input_data.get("background")
    return f"\n\nPrior stage output:\n{prior}" if prior else ""


class ContentPlanningAgent(LLMWorkerAgentBase):
    """Plans content strategy: audience, angle, and structure."""

    agent_type = "content_planning"

    def _build_prompt(self, query: str, task: AgentTask) -> str:
        return (
            "You are a content strategist. For the request below, produce a "
            "content plan: target audience, key angle/thesis, an outline of "
            "sections, and the main points to cover in each. Keep it concrete "
            "and ready to hand off to a writer.\n\n"
            f"Request:\n{query}"
        )


class DraftingAgent(LLMWorkerAgentBase):
    """Writes a full draft from the request and any plan."""

    agent_type = "drafting"

    def _build_prompt(self, query: str, task: AgentTask) -> str:
        return (
            "You are a writer. Write a clear, well-structured draft that "
            "fulfills the request, following the provided plan when present. "
            "Use headings where helpful and a natural, engaging voice.\n\n"
            f"Request:\n{query}{_prior(task)}"
        )


class EditingAgent(LLMWorkerAgentBase):
    """Edits a draft for clarity, correctness, and flow."""

    agent_type = "editing"

    def _build_prompt(self, query: str, task: AgentTask) -> str:
        return (
            "You are an editor. Improve the draft below for clarity, "
            "correctness, concision, and flow, preserving the author's intent. "
            "Return the improved version and a short note of the key changes.\n\n"
            f"Request/context:\n{query}{_prior(task)}"
        )


class OptimizationAgent(LLMWorkerAgentBase):
    """Optimizes content for readability and discoverability."""

    agent_type = "optimization"

    def _build_prompt(self, query: str, task: AgentTask) -> str:
        return (
            "You are a content optimizer. Improve the content below for "
            "readability and organic discoverability: suggest a strong title, "
            "relevant keywords/headings, meta description, and readability "
            "tweaks. Return the optimized content plus the suggestions.\n\n"
            f"Request/context:\n{query}{_prior(task)}"
        )


__all__ = [
    "ContentPlanningAgent",
    "DraftingAgent",
    "EditingAgent",
    "OptimizationAgent",
]
