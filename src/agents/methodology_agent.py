"""
Methodology Agent implementation.

This agent specializes in recommending and evaluating research methodologies.
"""

import hashlib
import logging
from typing import Any

from src.agents.base import BaseAgent
from src.agents.models import AgentResult, AgentTask
from src.core.constants import LONG_TERM_CACHE_TTL

logger = logging.getLogger(__name__)


class MethodologyAgent(BaseAgent):
    """
    Agent specialized in research methodology design and evaluation.

    This agent recommends appropriate research methods, identifies biases,
    and ensures methodological rigor.
    """

    def get_agent_type(self) -> str:
        """Return the agent type identifier."""
        return "methodology"

    async def execute(self, task: AgentTask) -> AgentResult:
        """
        Execute a methodology analysis task.

        Args:
            task: The methodology task to execute

        Returns:
            AgentResult containing methodology recommendations
        """
        try:
            # Validate input
            research_question = task.input_data.get("research_question", "").strip()
            if not research_question:
                return self.handle_error(task, ValueError("Research question required"))

            # Check cache first
            cache_key = self._generate_cache_key(task)
            cached_result = await self.get_cached_result(cache_key)
            if cached_result:
                self.log_info(f"Using cached result for task {task.id}")
                return cached_result

            # Generate methodology using structured output
            if self.gemini_service:
                from src.agents.schemas import MethodologySchema

                prompt = self._build_prompt(task.input_data)
                schema_result = await self.gemini_service.generate_structured_content(
                    prompt, MethodologySchema
                )
                # Convert Pydantic model to dict for compatibility
                analysis = schema_result.model_dump()
            else:
                # Fallback for testing without Gemini
                analysis = self._generate_mock_analysis(task)

            # Calculate confidence score
            confidence = self._calculate_confidence(analysis)

            # Build output
            output = {
                "research_design": analysis.get("research_design", ""),
                "data_collection_methods": analysis.get("data_collection_methods", []),
                "sampling_strategy": analysis.get("sampling_strategy", ""),
                "analysis_approaches": analysis.get("analysis_approaches", []),
                "validity_measures": analysis.get("validity_measures", []),
                "ethical_considerations": analysis.get("ethical_considerations", []),
                "limitations": analysis.get("limitations", []),
                "timeline": analysis.get("timeline", ""),
                "quality_indicators": analysis.get("quality_indicators", []),
            }

            result = AgentResult(
                task_id=task.id,
                status="success",
                output=output,
                confidence=confidence,
                execution_time=0.0,
                metadata=self.build_execution_metadata(
                    methods_count=len(output["data_collection_methods"]),
                ),
            )

            # Cache the result
            await self.cache_result(cache_key, result, ttl=LONG_TERM_CACHE_TTL)

            return result

        except Exception as e:
            self.log_error(f"Methodology analysis failed: {e!s}")
            return self.handle_error(task, e)

    async def validate_result(self, result: AgentResult) -> bool:
        """
        Validate the methodology result.

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
            "research_design",
            "data_collection_methods",
            "analysis_approaches",
        ]
        for field in required_fields:
            if field not in output:
                self.log_warning(f"Missing required field: {field}")
                return False

        return True

    def _calculate_confidence(self, analysis: dict[str, Any]) -> float:
        """Calculate confidence score based on methodology completeness."""
        confidence = 0.5

        # Check key components
        if analysis.get("research_design"):
            confidence += 0.1
        if len(analysis.get("data_collection_methods", [])) > 0:
            confidence += 0.15
        if len(analysis.get("validity_measures", [])) > 0:
            confidence += 0.15
        if len(analysis.get("ethical_considerations", [])) > 0:
            confidence += 0.1

        return min(confidence, 1.0)

    def _generate_cache_key(self, task: AgentTask) -> str:
        """Generate a cache key for the task."""
        key_parts = [
            self.get_agent_type(),
            task.input_data.get("research_question", ""),
            task.input_data.get("research_type", ""),
            task.input_data.get("scope", ""),
        ]
        key_string = "|".join(key_parts)
        return hashlib.md5(key_string.encode()).hexdigest()

    def _build_prompt(self, input_data: dict[str, Any]) -> str:
        """Build prompt for Gemini."""
        return f"""Design a research methodology for: {input_data.get('research_question')}
        Type: {input_data.get('research_type', 'mixed')}
        Scope: {input_data.get('scope', 'general')}
        
        Provide methodology recommendations in JSON format."""

    def _generate_mock_analysis(self, task: AgentTask) -> dict[str, Any]:
        """Generate mock analysis for testing."""
        return {
            "research_design": "Mixed methods approach",
            "data_collection_methods": ["Surveys", "Interviews"],
            "sampling_strategy": "Random sampling",
            "analysis_approaches": ["Statistical analysis", "Thematic analysis"],
            "validity_measures": ["Triangulation"],
            "ethical_considerations": ["Informed consent"],
            "limitations": ["Sample size"],
            "timeline": "3 months",
            "quality_indicators": ["Reliability", "Validity"],
        }
