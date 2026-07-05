"""
Delegation Contract Validation

Based on Anthropic research on multi-agent coordination, this module enforces
the 4-field delegation contract to prevent agent drift and ensure clear task specs.

Required fields (per Anthropic Engineering blog post):
1. objective: Clear goal for the subagent
2. output_format: Specified structure (e.g., "JSON with fields X, Y")
3. tool_guidance: Which tools/sources to use
4. task_boundaries: Explicit scope limits

Missing any field → increased risk of agent drift, off-task behavior, or
misaligned output formats.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from src.agents.models import AgentTask


from structlog import get_logger

logger = get_logger()


@dataclass
class DelegationContract:
    """Validated delegation contract per Anthropic research.

    Enforces the 4-field schema that prevents drift in multi-agent systems.
    """

    objective: str  # REQUIRED: Clear goal for the subagent
    output_format: str  # REQUIRED: Specified structure
    tool_guidance: str  # REQUIRED: Which tools/sources to use
    task_boundaries: str  # REQUIRED: Explicit scope limits

    def validate(self, mode: Literal["strict", "lenient"] = "lenient") -> list[str]:
        """Return list of missing/invalid fields.

        Args:
            mode: "strict" raises errors, "lenient" auto-fills with warnings

        Returns:
            List of validation error messages (empty = valid)
        """
        errors = []

        if not self.objective or len(self.objective.strip()) < 10:
            errors.append(
                "objective must be ≥10 chars (too vague); agents need clear goals"
            )

        if not self.output_format or len(self.output_format.strip()) < 5:
            errors.append(
                "output_format required (agent won't know how to structure response)"
            )

        if not self.tool_guidance or len(self.tool_guidance.strip()) < 5:
            errors.append(
                "tool_guidance required (agent may use wrong sources or tools)"
            )

        if not self.task_boundaries or len(self.task_boundaries.strip()) < 5:
            errors.append("task_boundaries required (scope creep risk)")

        return errors

    @classmethod
    def from_agent_task(cls, agent_task: "AgentTask") -> "DelegationContract":
        """Extract delegation contract from AgentTask.

        Args:
            agent_task: The AgentTask to extract contract fields from

        Returns:
            DelegationContract instance (may have missing fields)
        """
        # Extract from input_data and context
        input_data = agent_task.input_data or {}
        context = agent_task.context or {}

        return cls(
            objective=input_data.get("objective", context.get("objective", "")),
            output_format=input_data.get(
                "output_format", context.get("output_format", "")
            ),
            tool_guidance=input_data.get(
                "tool_guidance", context.get("tool_guidance", "")
            ),
            task_boundaries=input_data.get(
                "task_boundaries", context.get("task_boundaries", "")
            ),
        )

    def auto_fill_defaults(self, agent_type: str, query: str) -> "DelegationContract":
        """Auto-fill missing fields with sensible defaults (lenient mode).

        Args:
            agent_type: The agent type (e.g., "research", "content")
            query: The original query text

        Returns:
            New DelegationContract with defaults filled in
        """
        return DelegationContract(
            objective=self.objective or f"Answer the following query: {query[:100]}...",
            output_format=self.output_format
            or "Markdown summary with clear sections and bullet points",
            tool_guidance=self.tool_guidance
            or "Use available tools as needed; prioritize authoritative sources",
            task_boundaries=self.task_boundaries
            or f"Focus on {agent_type} domain; stay within query scope",
        )


__all__ = ["DelegationContract"]
