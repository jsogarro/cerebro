"""Analytics domain worker agents (LLM-reasoning; no external data)."""

from src.agents.llm_worker_base import LLMWorkerAgentBase
from src.agents.models import AgentTask


def _prior(task: AgentTask) -> str:
    prior = task.input_data.get("previous_result") or task.input_data.get("background")
    return f"\n\nPrior stage output:\n{prior}" if prior else ""


class DataAnalysisAgent(LLMWorkerAgentBase):
    """Analyzes the data/metrics described in the query."""

    agent_type = "data_analysis"

    def _build_prompt(self, query: str, task: AgentTask) -> str:
        return (
            "You are a data analyst. Using ONLY the data, metrics, or "
            "observations provided below (do not invent numbers), describe what "
            "the data shows: notable patterns, comparisons, outliers, and "
            "caveats. If key data is missing, state what would be needed.\n\n"
            f"Request and provided data:\n{query}"
        )


class StatisticalModelingAgent(LLMWorkerAgentBase):
    """Reasons about appropriate statistical methods/models for the problem."""

    agent_type = "statistical_modeling"

    def _build_prompt(self, query: str, task: AgentTask) -> str:
        return (
            "You are a statistician. For the problem and any data described "
            "below, recommend appropriate statistical methods or models, state "
            "their assumptions, outline how you would apply and validate them, "
            "and interpret what the results would mean. Where inputs are given, "
            "reason through them explicitly.\n\n"
            f"Request/context:\n{query}{_prior(task)}"
        )


class InsightSynthesisAgent(LLMWorkerAgentBase):
    """Synthesizes analysis into decision-ready insights and recommendations."""

    agent_type = "insight_synthesis"

    def _build_prompt(self, query: str, task: AgentTask) -> str:
        return (
            "You are an analytics lead. Synthesize the analysis below into "
            "decision-ready insights: the key takeaways, what they imply, "
            "recommended actions, and the main risks/uncertainties. Be concise "
            "and prioritized.\n\n"
            f"Request/context:\n{query}{_prior(task)}"
        )


__all__ = [
    "DataAnalysisAgent",
    "InsightSynthesisAgent",
    "StatisticalModelingAgent",
]
