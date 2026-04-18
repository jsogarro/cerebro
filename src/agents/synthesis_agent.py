"""
Synthesis Agent implementation.

This agent specializes in integrating outputs from multiple agents into coherent insights.
"""

import hashlib
import logging
from typing import Any

from src.agents.base import BaseAgent
from src.agents.models import AgentResult, AgentTask
from src.core.constants import LONG_TERM_CACHE_TTL

logger = logging.getLogger(__name__)


class SynthesisAgent(BaseAgent):
    """
    Agent specialized in synthesizing outputs from multiple research agents.

    This agent integrates findings, resolves conflicts, and creates
    comprehensive narratives from diverse inputs.
    """

    def get_agent_type(self) -> str:
        """Return the agent type identifier."""
        return "synthesis"

    async def execute(self, task: AgentTask) -> AgentResult:
        """
        Execute a synthesis task.

        Args:
            task: The synthesis task to execute

        Returns:
            AgentResult containing integrated findings
        """
        try:
            # Validate input
            agent_outputs = task.input_data.get("agent_outputs", {})
            if not agent_outputs:
                return self.handle_error(
                    task, ValueError("Agent outputs required for synthesis")
                )

            # Check cache first
            cache_key = self._generate_cache_key(task)
            cached_result = await self.get_cached_result(cache_key)
            if cached_result:
                self.log_info(f"Using cached result for task {task.id}")
                return cached_result

            # Generate synthesis using structured output
            if self.gemini_service:
                from src.agents.schemas import SynthesisSchema

                prompt = self._build_prompt(agent_outputs)
                schema_result = await self.gemini_service.generate_structured_content(
                    prompt, SynthesisSchema
                )
                # Convert Pydantic model to dict for compatibility
                synthesis = schema_result.model_dump()
            else:
                # Fallback for testing without Gemini
                synthesis = self._generate_mock_synthesis(agent_outputs)

            # Calculate confidence score
            confidence = self._calculate_confidence(synthesis, agent_outputs)

            # Build output
            output = {
                "integrated_findings": synthesis.get("integrated_findings", []),
                "cross_agent_patterns": synthesis.get("cross_agent_patterns", []),
                "conflict_resolutions": synthesis.get("conflict_resolutions", []),
                "meta_insights": synthesis.get("meta_insights", []),
                "comprehensive_narrative": synthesis.get("comprehensive_narrative", ""),
                "confidence_assessment": synthesis.get("confidence_assessment", ""),
            }

            result = AgentResult(
                task_id=task.id,
                status="success",
                output=output,
                confidence=confidence,
                execution_time=0.0,
                metadata={
                    "agent_type": self.get_agent_type(),
                    "agents_synthesized": len(agent_outputs),
                },
            )

            # Cache the result
            await self.cache_result(cache_key, result, ttl=LONG_TERM_CACHE_TTL)

            return result

        except Exception as e:
            self.log_error(f"Synthesis failed: {e!s}")
            return self.handle_error(task, e)

    async def validate_result(self, result: AgentResult) -> bool:
        """
        Validate the synthesis result.

        Args:
            result: The result to validate

        Returns:
            True if valid, False otherwise
        """
        if result.status != "success":
            return result.status == "failed"

        output = result.output

        # Check required fields
        required_fields = [
            "integrated_findings",
            "comprehensive_narrative",
            "meta_insights",
        ]
        for field in required_fields:
            if field not in output:
                self.log_warning(f"Missing required field: {field}")
                return False

        return True

    def _calculate_confidence(
        self, synthesis: dict[str, Any], agent_outputs: dict[str, Any]
    ) -> float:
        """Calculate confidence score based on synthesis quality."""
        confidence = 0.5

        # Factor in number of agents synthesized
        if len(agent_outputs) >= 3:
            confidence += 0.2
        elif len(agent_outputs) >= 2:
            confidence += 0.1

        # Check synthesis completeness
        if len(synthesis.get("integrated_findings", [])) > 0:
            confidence += 0.15
        if len(synthesis.get("meta_insights", [])) > 0:
            confidence += 0.15

        return min(confidence, 1.0)

    def _generate_cache_key(self, task: AgentTask) -> str:
        """Generate a cache key for the task."""
        agent_outputs = task.input_data.get("agent_outputs", {})
        key_parts = [self.get_agent_type(), str(sorted(agent_outputs.keys()))]
        key_string = "|".join(key_parts)
        return hashlib.md5(key_string.encode()).hexdigest()

    def _build_prompt(self, agent_outputs: dict[str, Any]) -> str:
        """Build prompt for Gemini."""
        # Format agent outputs clearly for synthesis
        formatted_outputs = []
        for agent_type, output_data in agent_outputs.items():
            formatted_outputs.append(f"\n=== {agent_type.upper()} ===")
            if isinstance(output_data, dict):
                for key, value in output_data.items():
                    formatted_outputs.append(f"{key}: {value}")
            else:
                formatted_outputs.append(str(output_data))

        outputs_text = "\n".join(formatted_outputs)

        return f"""Synthesize the following outputs from multiple research agents:

{outputs_text}

Your task:
1. Integrate findings from all agents into coherent integrated_findings
2. Identify cross_agent_patterns across different agent outputs
3. Resolve any conflict_resolutions between agent findings
4. Extract meta_insights (higher-order insights from the synthesis)
5. Create a comprehensive_narrative combining all outputs
6. Provide a confidence_assessment of the synthesis quality

Return your synthesis as structured JSON."""

    def _generate_mock_synthesis(self, agent_outputs: dict[str, Any]) -> dict[str, Any]:
        """Generate mock synthesis for testing."""
        return {
            "integrated_findings": [
                f"Integrated finding from {len(agent_outputs)} agents",
                "Cross-validated conclusion",
            ],
            "cross_agent_patterns": ["Pattern identified across agents"],
            "conflict_resolutions": ["Resolved conflicting findings"],
            "meta_insights": ["Higher-order insight from synthesis"],
            "comprehensive_narrative": "Complete synthesis narrative combining all agent outputs...",
            "confidence_assessment": "High confidence based on convergent findings",
        }
