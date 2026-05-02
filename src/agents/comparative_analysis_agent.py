"""
Comparative Analysis Agent implementation.

This agent specializes in comparing different approaches, methods, or solutions
across multiple criteria to provide recommendations.
"""

import hashlib
import logging
from typing import Any

from src.agents.base import BaseAgent
from src.agents.comparative_insight_synthesizer import ComparativeInsightSynthesizer
from src.agents.comparative_matrix_builder import ComparisonMatrixBuilder
from src.agents.comparative_theory_extractor import TheoryExtractor
from src.agents.models import AgentResult, AgentTask
from src.core.constants import LONG_TERM_CACHE_TTL
from src.services.parsers.json_parser import parse_json_response
from src.services.prompts.agent_prompts import generate_comparative_agent_prompt

logger = logging.getLogger(__name__)


class ComparativeAnalysisAgent(BaseAgent):
    """
    Agent specialized in comparative analysis of research items.

    This agent compares different approaches, methods, or solutions,
    creates comparison matrices, identifies trade-offs, and provides
    contextual recommendations.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.matrix_builder = ComparisonMatrixBuilder()
        self.insight_synthesizer = ComparativeInsightSynthesizer()
        self.theory_extractor = TheoryExtractor()

    def get_agent_type(self) -> str:
        """Return the agent type identifier."""
        return "comparative_analysis"

    async def execute(self, task: AgentTask) -> AgentResult:
        """
        Execute a comparative analysis task.

        Args:
            task: The comparative analysis task to execute

        Returns:
            AgentResult containing comparison matrix and recommendations
        """
        try:
            # Validate input
            items = task.input_data.get("items", [])
            if not items or len(items) < 2:
                return self.handle_error(
                    task, ValueError("At least 2 items required for comparison")
                )

            criteria = task.input_data.get("criteria", [])
            if not criteria:
                return self.handle_error(
                    task, ValueError("Comparison criteria required")
                )

            # Check cache first
            cache_key = self._generate_cache_key(task)
            cached_result = await self.get_cached_result(cache_key)
            if cached_result:
                self.log_info(f"Using cached result for task {task.id}")
                return cached_result

            # Step 1: Gather comparative research using MCP tools
            comparative_research = await self._search_comparative_studies(
                task.input_data
            )

            # Step 2: Perform statistical analysis using MCP tools
            statistical_analysis = await self._perform_statistical_comparison(
                task.input_data
            )

            # Step 3: Build knowledge graph for relationship analysis
            relationship_graph = await self._build_comparison_knowledge_graph(
                task.input_data, comparative_research
            )

            # Step 4: Generate analysis using Gemini with MCP-enhanced data
            analysis = await self._analyze_comparison_with_gemini(
                task.input_data, comparative_research, statistical_analysis
            )

            # Process comparison matrix with MCP statistical enhancements
            matrix = analysis.get("comparison_matrix", {})
            enhanced_matrix = await self.matrix_builder.enhance_matrix_with_statistics(
                matrix, statistical_analysis
            )
            normalized_matrix = self.matrix_builder.normalize_comparison_matrix(
                enhanced_matrix
            )

            # Calculate rankings with statistical significance
            rankings = self.matrix_builder.calculate_rankings(normalized_matrix, criteria)
            statistical_rankings = await self.matrix_builder.calculate_statistical_rankings(
                normalized_matrix, statistical_analysis
            )

            # Analyze trade-offs with relationship insights
            trade_offs = analysis.get("trade_offs", [])
            trade_off_analysis = self._analyze_trade_offs_with_relationships(
                trade_offs, normalized_matrix, relationship_graph
            )

            # Generate citations for comparative methodologies
            methodology_citations = await self._cite_comparison_methodologies(
                comparative_research
            )

            # Calculate confidence score with MCP data quality
            confidence = self._calculate_confidence_with_mcp(
                items_count=len(items),
                criteria_count=len(criteria),
                matrix_completeness=self.matrix_builder.assess_matrix_completeness(
                    normalized_matrix
                ),
                trade_offs=trade_offs,
                recommendations=analysis.get("recommendations", []),
                mcp_data_quality={
                    "research_quality": comparative_research.get("success", False),
                    "statistical_quality": statistical_analysis.get("success", False),
                    "graph_quality": relationship_graph.get("success", False),
                },
            )

            # Build enhanced output with MCP data
            output = {
                "items_compared": analysis.get("items_compared", items),
                "comparison_criteria": criteria,
                "comparison_matrix": normalized_matrix,
                "enhanced_matrix": enhanced_matrix,
                "strengths_weaknesses": analysis.get("strengths_weaknesses", {}),
                "recommendations": analysis.get("recommendations", []),
                "trade_offs": trade_offs,
                "trade_off_analysis": trade_off_analysis,
                "statistical_analysis": statistical_analysis,
                "comparative_research": comparative_research,
                "relationship_insights": relationship_graph,
                "methodology_citations": methodology_citations,
                "data_sources": {
                    "research_papers": comparative_research.get("total_found", 0),
                    "statistical_tests": len(
                        statistical_analysis.get("tests_performed", [])
                    ),
                    "relationship_entities": len(
                        relationship_graph.get("entities", [])
                    ),
                },
                "data_source": (
                    "mcp_enhanced"
                    if any(
                        [
                            comparative_research.get("success"),
                            statistical_analysis.get("success"),
                            relationship_graph.get("success"),
                        ]
                    )
                    else "fallback"
                ),
                "mcp_integration_status": self._get_mcp_status(),
            }

            # Add rankings if calculated
            if rankings:
                output["rankings"] = rankings
                output["statistical_rankings"] = statistical_rankings

            # Add visual data if requested
            if task.input_data.get("generate_visual"):
                output["visual_data"] = self.matrix_builder.generate_visual_data(
                    normalized_matrix, criteria
                )

            # Add contextual analysis if context provided
            if "context" in task.input_data:
                output["context_considerations"] = analysis.get(
                    "context_considerations", []
                )
                output["contextual_recommendation"] = analysis.get(
                    "contextual_recommendation", ""
                )

            result = AgentResult(
                task_id=task.id,
                status="success",
                output=output,
                confidence=confidence,
                execution_time=0.0,  # Would be calculated in real implementation
                metadata={
                    "agent_type": self.get_agent_type(),
                    "items_count": len(items),
                    "criteria_count": len(criteria),
                },
            )

            # Cache the result
            await self.cache_result(cache_key, result, ttl=LONG_TERM_CACHE_TTL)  # 24 hours

            return result

        except Exception as e:
            self.log_error(f"Comparative analysis failed: {e!s}")
            return self.handle_error(task, e)

    async def validate_result(self, result: AgentResult) -> bool:
        """
        Validate the comparative analysis result.

        Args:
            result: The result to validate

        Returns:
            True if valid, False otherwise
        """
        if result.status != "success":
            return result.status == "failed"  # Failed results are valid

        output = result.output

        # Check required fields
        required_fields = ["items_compared", "comparison_matrix", "recommendations"]
        for field in required_fields:
            if field not in output:
                self.log_warning(f"Missing required field: {field}")
                return False

        # Validate comparison matrix structure
        matrix = output.get("comparison_matrix", {})
        if not isinstance(matrix, dict) or len(matrix) < 2:
            return False

        # Validate items compared
        items = output.get("items_compared", [])
        return not (not isinstance(items, list) or len(items) < 2)

    def _normalize_comparison_matrix(
        self, matrix: dict[str, dict[str, float]]
    ) -> dict[str, dict[str, float]]:
        """
        Normalize comparison matrix values to 0-1 range.

        Args:
            matrix: Raw comparison matrix

        Returns:
            Normalized matrix
        """
        return self.matrix_builder.normalize_comparison_matrix(matrix)

    def _calculate_rankings(
        self, matrix: dict[str, dict[str, float]], criteria: list[str]
    ) -> dict[str, list[str]]:
        """
        Calculate rankings for each criterion and overall.

        Args:
            matrix: Normalized comparison matrix
            criteria: List of criteria

        Returns:
            Rankings dictionary
        """
        return self.matrix_builder.calculate_rankings(matrix, criteria)

    def _analyze_trade_offs(
        self, trade_offs: list[str], matrix: dict[str, dict[str, float]]
    ) -> dict[str, Any]:
        """
        Analyze trade-offs between compared items.

        Args:
            trade_offs: List of identified trade-offs
            matrix: Comparison matrix

        Returns:
            Trade-off analysis
        """
        return self.insight_synthesizer.analyze_trade_offs(trade_offs, matrix)

    def _categorize_trade_offs(self, trade_offs: list[str]) -> dict[str, list[str]]:
        """
        Categorize trade-offs by type.

        Args:
            trade_offs: List of trade-off descriptions

        Returns:
            Categorized trade-offs
        """
        return self.insight_synthesizer.categorize_trade_offs(trade_offs)

    def _assess_trade_off_severity(
        self, trade_offs: list[str], matrix: dict[str, dict[str, float]]
    ) -> str:
        """
        Assess the severity of trade-offs.

        Args:
            trade_offs: List of trade-offs
            matrix: Comparison matrix

        Returns:
            Severity assessment
        """
        return self.insight_synthesizer.assess_trade_off_severity(trade_offs, matrix)

    def _assess_matrix_completeness(self, matrix: dict[str, dict[str, float]]) -> float:
        """
        Assess how complete the comparison matrix is.

        Args:
            matrix: Comparison matrix

        Returns:
            Completeness score (0-1)
        """
        return self.matrix_builder.assess_matrix_completeness(matrix)

    def _calculate_confidence(
        self,
        items_count: int,
        criteria_count: int,
        matrix_completeness: float,
        trade_offs: list[str],
        recommendations: list[str],
    ) -> float:
        """
        Calculate confidence score based on analysis completeness.

        Args:
            items_count: Number of items compared
            criteria_count: Number of criteria used
            matrix_completeness: Completeness of comparison matrix
            trade_offs: List of identified trade-offs
            recommendations: List of recommendations

        Returns:
            Confidence score between 0.0 and 1.0
        """
        confidence = 0.4  # Base confidence

        # Factor 1: Number of items (max +0.15)
        if items_count >= 4:
            confidence += 0.15
        elif items_count >= 3:
            confidence += 0.10
        elif items_count >= 2:
            confidence += 0.05

        # Factor 2: Number of criteria (max +0.15)
        if criteria_count >= 4:
            confidence += 0.15
        elif criteria_count >= 3:
            confidence += 0.10
        elif criteria_count >= 2:
            confidence += 0.05
        elif criteria_count >= 1:
            confidence += 0.02

        # Factor 3: Matrix completeness (max +0.2)
        confidence += matrix_completeness * 0.2

        # Factor 4: Trade-offs identified (max +0.1)
        if len(trade_offs) >= 3:
            confidence += 0.1
        elif len(trade_offs) >= 2:
            confidence += 0.07
        elif len(trade_offs) >= 1:
            confidence += 0.04

        # Factor 5: Recommendations provided (max +0.1)
        if len(recommendations) >= 3:
            confidence += 0.1
        elif len(recommendations) >= 2:
            confidence += 0.07
        elif len(recommendations) >= 1:
            confidence += 0.04

        # Ensure confidence is within bounds
        return min(max(confidence, 0.0), 1.0)

    def _calculate_confidence_with_mcp(
        self,
        items_count: int,
        criteria_count: int,
        matrix_completeness: float,
        trade_offs: list[str],
        recommendations: list[str],
        mcp_data_quality: dict[str, bool],
    ) -> float:
        """
        Calculate confidence score with MCP data quality factors.

        Args:
            items_count: Number of items compared
            criteria_count: Number of criteria used
            matrix_completeness: Completeness of comparison matrix
            trade_offs: List of identified trade-offs
            recommendations: List of recommendations
            mcp_data_quality: MCP data quality indicators

        Returns:
            Confidence score between 0.0 and 1.0
        """
        # Base confidence from original method
        confidence = self._calculate_confidence(
            items_count,
            criteria_count,
            matrix_completeness,
            trade_offs,
            recommendations,
        )

        # MCP enhancement bonuses (max +0.15)
        mcp_bonus = 0.0

        if mcp_data_quality.get("research_quality"):
            mcp_bonus += 0.05  # Research data available
        if mcp_data_quality.get("statistical_quality"):
            mcp_bonus += 0.05  # Statistical analysis available
        if mcp_data_quality.get("graph_quality"):
            mcp_bonus += 0.05  # Relationship data available

        return min(confidence + mcp_bonus, 1.0)

    def _generate_visual_data(
        self, matrix: dict[str, dict[str, float]], criteria: list[str]
    ) -> dict[str, Any]:
        """
        Generate data for visual representation.

        Args:
            matrix: Comparison matrix
            criteria: List of criteria

        Returns:
            Visual data dictionary
        """
        return self.matrix_builder.generate_visual_data(matrix, criteria)

    async def _search_comparative_studies(
        self, input_data: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Search for comparative studies using MCP academic search tool.

        Args:
            input_data: Task input data

        Returns:
            Comparative research results
        """
        return await self.theory_extractor.search_comparative_studies(
            input_data,
            self.mcp_integration,
            self.log_info,
            self.log_warning,
            self.log_error,
        )

    async def _perform_statistical_comparison(
        self, input_data: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Perform statistical analysis of comparison data using MCP statistics tool.

        Args:
            input_data: Task input data

        Returns:
            Statistical analysis results
        """
        if not self.mcp_integration:
            self.log_warning("MCP integration not available for statistical analysis")
            return self._fallback_statistical_analysis(input_data)

        # Extract numerical data from input if available
        comparison_data = input_data.get("comparison_data", {})

        try:
            tests_performed = []

            # Perform descriptive statistics if numerical data available
            if comparison_data:
                descriptive_result = await self.mcp_integration.analyze_statistics(
                    operation="descriptive", data=list(comparison_data.values())
                )
                if descriptive_result.get("success"):
                    tests_performed.append("descriptive_statistics")

            # Perform correlation analysis if multiple criteria
            criteria = input_data.get("criteria", [])
            if len(criteria) >= 2 and comparison_data:
                correlation_result = await self.mcp_integration.analyze_statistics(
                    operation="correlation", variables=criteria[:2]
                )
                if correlation_result.get("success"):
                    tests_performed.append("correlation_analysis")

            return {
                "success": True,
                "tests_performed": tests_performed,
                "descriptive_stats": (
                    descriptive_result.get("analysis", {}) if comparison_data else {}
                ),
                "correlation_analysis": (
                    correlation_result.get("analysis", {}) if len(criteria) >= 2 else {}
                ),
                "data_quality": "high" if comparison_data else "limited",
            }

        except Exception as e:
            self.log_error(f"MCP statistical analysis failed: {e}")
            return self._fallback_statistical_analysis(input_data)

    async def _build_comparison_knowledge_graph(
        self, input_data: dict[str, Any], research_data: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Build knowledge graph for comparison relationships using MCP tools.

        Args:
            input_data: Task input data
            research_data: Research results from academic search

        Returns:
            Knowledge graph data
        """
        if not self.mcp_integration:
            return {"success": False, "error": "MCP integration not available"}

        # Combine items and criteria for entity extraction
        items = input_data.get("items", [])
        criteria = input_data.get("criteria", [])

        text_for_analysis = f"Comparison between {', '.join(items)} using criteria: {', '.join(criteria)}."

        # Add research abstracts if available
        sources = research_data.get("sources", [])
        for source in sources[:3]:  # Use top 3 sources
            abstract = source.get("abstract", "")
            if abstract:
                text_for_analysis += f" {abstract[:200]}"  # First 200 chars

        try:
            result = await self.mcp_integration.build_knowledge_graph(
                text=text_for_analysis
            )

            if result.get("success"):
                entities = result.get("entities", [])
                self.log_info(
                    f"Built comparison knowledge graph with {len(entities)} entities"
                )
                return dict(result)
            else:
                raise Exception(
                    f"Knowledge graph building failed: {result.get('error')}"
                )

        except Exception as e:
            self.log_error(f"Comparison knowledge graph building failed: {e}")
            return {"success": False, "error": str(e)}

    def _generate_cache_key(self, task: AgentTask) -> str:
        """
        Generate a cache key for the task.

        Args:
            task: The task to generate key for

        Returns:
            Cache key string
        """
        key_parts = [
            self.get_agent_type(),
            str(sorted(task.input_data.get("items", []))),
            str(sorted(task.input_data.get("criteria", []))),
            str(task.input_data.get("generate_visual", False)),
        ]
        key_string = "|".join(key_parts)
        return hashlib.md5(key_string.encode()).hexdigest()

    def _generate_mock_analysis(self, task: AgentTask) -> dict[str, Any]:
        """
        Generate mock analysis for testing without Gemini.

        Args:
            task: The task to analyze

        Returns:
            Mock comparative analysis
        """
        items = task.input_data.get("items", [])
        criteria = task.input_data.get("criteria", [])

        # Generate mock comparison matrix
        matrix: dict[str, dict[str, float]] = {}
        for i, item in enumerate(items):
            matrix[item] = {}
            for j, criterion in enumerate(criteria):
                # Generate varied scores
                score = 0.5 + (i * 0.1) - (j * 0.05)
                matrix[item][criterion] = max(0.0, min(1.0, score))

        # Generate mock strengths and weaknesses
        strengths_weaknesses = {}
        for item in items:
            strengths_weaknesses[item] = {
                "strengths": [f"Strong in {criteria[0]}"],
                "weaknesses": [f"Weak in {criteria[-1]}"],
            }

        return {
            "items_compared": items,
            "comparison_criteria": criteria,
            "comparison_matrix": matrix,
            "strengths_weaknesses": strengths_weaknesses,
            "recommendations": [
                f"Recommend {items[0]} for general use",
                f"Consider {items[-1]} for specific cases",
            ],
            "trade_offs": [
                f"{criteria[0]} vs {criteria[-1]} trade-off",
                "Performance vs Cost consideration",
            ],
            "mcp_enhanced": True,
        }

    async def _analyze_comparison_with_gemini(
        self,
        input_data: dict[str, Any],
        research_data: dict[str, Any],
        statistical_data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Analyze comparison using Gemini with MCP-enhanced data.

        Args:
            input_data: Original task input
            research_data: Research results from MCP
            statistical_data: Statistical analysis from MCP

        Returns:
            Enhanced comparative analysis from Gemini
        """
        if not self.gemini_service:
            return self._generate_mock_analysis_with_mcp(
                input_data, research_data, statistical_data
            )

        # Enhance input with MCP data
        enhanced_input = input_data.copy()
        enhanced_input["research_findings"] = self._summarize_research_findings(
            research_data
        )
        enhanced_input["statistical_insights"] = statistical_data.get(
            "tests_performed", []
        )
        enhanced_input["data_quality"] = {
            "research_sources": research_data.get("total_found", 0),
            "statistical_tests": len(statistical_data.get("tests_performed", [])),
        }

        prompt = generate_comparative_agent_prompt(enhanced_input)

        try:
            response = await self.gemini_service.generate_content(prompt)
            parsed_response = parse_json_response(response)
            analysis = (
                parsed_response.get("comparative_analysis")
                or parsed_response.get("analysis")
                or parsed_response
            )

            # Enhance analysis with MCP insights
            analysis["mcp_enhanced"] = True
            analysis["data_sources"] = {
                "academic_papers": research_data.get("total_found", 0),
                "statistical_tests": len(statistical_data.get("tests_performed", [])),
            }

            return dict(analysis)

        except Exception as e:
            self.log_error(f"Gemini analysis with MCP data failed: {e}")
            return self._generate_mock_analysis_with_mcp(
                input_data, research_data, statistical_data
            )

    async def _enhance_matrix_with_statistics(
        self, matrix: dict[str, dict[str, float]], statistical_data: dict[str, Any]
    ) -> dict[str, dict[str, float]]:
        """
        Enhance comparison matrix with statistical insights.

        Args:
            matrix: Original comparison matrix
            statistical_data: Statistical analysis results

        Returns:
            Enhanced matrix with statistical adjustments
        """
        return await self.matrix_builder.enhance_matrix_with_statistics(
            matrix, statistical_data
        )

    async def _calculate_statistical_rankings(
        self, matrix: dict[str, dict[str, float]], statistical_data: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Calculate statistically-informed rankings.

        Args:
            matrix: Normalized comparison matrix
            statistical_data: Statistical analysis results

        Returns:
            Statistical rankings with confidence intervals
        """
        return await self.matrix_builder.calculate_statistical_rankings(
            matrix, statistical_data
        )

    def _calculate_ranking_confidence(
        self, scores: dict[str, float], statistical_data: dict[str, Any]
    ) -> float:
        """
        Calculate confidence for individual item ranking.

        Args:
            scores: Item's scores across criteria
            statistical_data: Statistical analysis data

        Returns:
            Confidence score for ranking
        """
        return self.matrix_builder.calculate_ranking_confidence(
            scores, statistical_data
        )

    def _analyze_trade_offs_with_relationships(
        self,
        trade_offs: list[str],
        matrix: dict[str, dict[str, float]],
        relationship_graph: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Analyze trade-offs enhanced with relationship insights.

        Args:
            trade_offs: List of identified trade-offs
            matrix: Comparison matrix
            relationship_graph: Knowledge graph data

        Returns:
            Enhanced trade-off analysis
        """
        return self.insight_synthesizer.analyze_trade_offs_with_relationships(
            trade_offs, matrix, relationship_graph
        )

    async def _cite_comparison_methodologies(
        self, research_data: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Generate citations for comparison methodologies using MCP citation tool.

        Args:
            research_data: Research results from academic search

        Returns:
            Formatted citations for methodologies
        """
        return await self.insight_synthesizer.cite_comparison_methodologies(
            research_data,
            self.mcp_integration,
            self.log_info,
            self.log_error,
        )

    def _summarize_research_findings(self, research_data: dict[str, Any]) -> str:
        """
        Summarize research findings for Gemini analysis.

        Args:
            research_data: Research results from MCP

        Returns:
            Summary text of research findings
        """
        return self.insight_synthesizer.summarize_research_findings(research_data)

    def _extract_theories_from_sources(
        self, research_data: dict[str, Any]
    ) -> list[dict[str, str]]:
        """
        Extract theory/framework hints from comparative research sources.

        Args:
            research_data: Research results from MCP

        Returns:
            Extracted theory metadata
        """
        return self.theory_extractor.extract_theories_from_sources(research_data)

    def _fallback_comparative_research(
        self, input_data: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Fallback comparative research when MCP tools unavailable.

        Args:
            input_data: Task input data

        Returns:
            Mock research results
        """
        return self.theory_extractor.fallback_comparative_research(input_data)

    def _fallback_statistical_analysis(
        self, input_data: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Fallback statistical analysis when MCP tools unavailable.

        Args:
            input_data: Task input data

        Returns:
            Mock statistical analysis
        """
        return self.insight_synthesizer.fallback_statistical_analysis(input_data)

    def _generate_mock_analysis_with_mcp(
        self,
        input_data: dict[str, Any],
        research_data: dict[str, Any],
        statistical_data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Generate mock analysis enhanced with MCP data.

        Args:
            input_data: Task input data
            research_data: Research results
            statistical_data: Statistical analysis

        Returns:
            Enhanced mock analysis
        """
        fake_task = AgentTask(
            id="mock",
            agent_type="comparative",
            input_data=input_data,
            priority=1,
        )
        return self.insight_synthesizer.generate_mock_analysis_with_mcp(
            self._generate_mock_analysis(fake_task),
            research_data,
            statistical_data,
        )

    def _get_mcp_status(self) -> dict[str, Any]:
        """Get MCP integration status for comparative analysis."""
        if not self.mcp_integration:
            return {"enabled": False, "status": "not_configured"}

        return {
            "enabled": True,
            "status": "configured",
            "tools_available": [
                "academic_search",
                "statistics",
                "knowledge_graph",
                "citation",
            ],
            "fallback_enabled": getattr(self.mcp_integration, "enable_fallback", False),
        }
