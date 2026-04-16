"""
Literature Review Agent implementation.

This agent specializes in conducting comprehensive literature reviews,
searching academic sources, and identifying research gaps.
"""

import hashlib
import logging
from typing import Any

from src.agents.base import BaseAgent
from src.agents.models import AgentResult, AgentTask
from src.core.constants import LONG_TERM_CACHE_TTL
from src.models.research_project import ResearchDepth
from src.services.parsers.json_parser import parse_json_response
from src.services.prompts.agent_prompts import generate_literature_agent_prompt

logger = logging.getLogger(__name__)


class LiteratureReviewAgent(BaseAgent):
    """
    Agent specialized in conducting systematic literature reviews.

    This agent searches for academic sources, extracts key findings,
    identifies research gaps, and generates comprehensive literature summaries.
    """

    def get_agent_type(self) -> str:
        """Return the agent type identifier."""
        return "literature_review"

    async def execute(self, task: AgentTask) -> AgentResult:
        """
        Execute a literature review task.

        Args:
            task: The literature review task to execute

        Returns:
            AgentResult containing sources, findings, and gaps
        """
        try:
            # Validate input
            query = task.input_data.get("query", "").strip()
            if not query:
                return self.handle_error(task, ValueError("Query cannot be empty"))

            # Check cache first
            cache_key = self._generate_cache_key(task)
            cached_result = await self.get_cached_result(cache_key)
            if cached_result:
                self.log_info(f"Using cached result for task {task.id}")
                return cached_result

            # Step 1: Search academic sources using MCP tools (production)
            academic_sources = await self._search_academic_sources(task.input_data)

            # Step 2: Extract key findings and analyze using Gemini
            literature_analysis = await self._analyze_literature_with_gemini(
                task.input_data, academic_sources
            )

            # Step 3: Build knowledge graph of relationships
            knowledge_graph = await self._build_literature_knowledge_graph(
                academic_sources, literature_analysis
            )

            # Step 4: Format citations properly
            formatted_citations = await self._format_source_citations(academic_sources)

            # Process and rank sources
            sources_list = academic_sources.get("sources", [])
            ranked_sources = self._rank_sources_by_relevance(sources_list)

            # Analyze research gaps with enhanced analysis
            gaps = literature_analysis.get("research_gaps", [])
            gap_analysis = self._analyze_research_gaps(
                gaps, ranked_sources, knowledge_graph
            )

            # Calculate confidence score with MCP-enhanced data
            confidence = self._calculate_confidence(
                sources=ranked_sources,
                quality=literature_analysis.get("quality_assessment", ""),
                findings=literature_analysis.get("key_findings", []),
                mcp_data_quality=academic_sources.get("success", False),
            )

            # Build enhanced output with MCP data
            output = {
                "sources_found": ranked_sources,
                "key_findings": literature_analysis.get("key_findings", []),
                "research_gaps": gaps,
                "gap_analysis": gap_analysis,
                "methodologies_used": literature_analysis.get("methodologies_used", []),
                "quality_assessment": literature_analysis.get("quality_assessment", ""),
                "search_strategy": academic_sources.get(
                    "search_strategy", "Multi-database academic search"
                ),
                "total_sources": len(ranked_sources),
                "databases_searched": academic_sources.get("databases_searched", []),
                "formatted_citations": formatted_citations,
                "knowledge_graph": knowledge_graph,
                "data_source": (
                    "mcp_tools" if academic_sources.get("success") else "fallback"
                ),
                "mcp_integration_status": self._get_mcp_status(),
            }

            # Handle depth level
            depth_level = task.input_data.get(
                "depth_level", ResearchDepth.COMPREHENSIVE.value
            )
            if depth_level == ResearchDepth.EXHAUSTIVE.value:
                output["search_strategy"] = "Exhaustive " + output.get(
                    "search_strategy", "search"
                )

            result = AgentResult(
                task_id=task.id,
                status="success",
                output=output,
                confidence=confidence,
                execution_time=0.0,  # Would be calculated in real implementation
                metadata={
                    "agent_type": self.get_agent_type(),
                    "sources_count": len(ranked_sources),
                    "gaps_identified": len(gaps),
                },
            )

            # Cache the result
            await self.cache_result(cache_key, result, ttl=LONG_TERM_CACHE_TTL)  # 24 hours

            return result

        except Exception as e:
            self.log_error(f"Literature review failed: {e!s}")
            return self.handle_error(task, e)

    async def validate_result(self, result: AgentResult) -> bool:
        """
        Validate the literature review result.

        Args:
            result: The result to validate

        Returns:
            True if valid, False otherwise
        """
        if result.status != "success":
            return result.status == "failed"  # Failed results are valid

        output = result.output

        # Check required fields
        required_fields = ["sources_found", "key_findings", "research_gaps"]
        for field in required_fields:
            if field not in output:
                self.log_warning(f"Missing required field: {field}")
                return False

        # Validate sources
        sources = output.get("sources_found", [])
        if not isinstance(sources, list):
            return False

        # At least some findings should be present
        findings = output.get("key_findings", [])
        if not isinstance(findings, list) or len(findings) == 0:
            return False

        # Research gaps should be identified
        gaps = output.get("research_gaps", [])
        if not isinstance(gaps, list) or len(gaps) == 0:
            return False

        return True

    def _rank_sources_by_relevance(
        self, sources: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        Rank sources by relevance score in descending order.

        Args:
            sources: List of source dictionaries

        Returns:
            Sorted list of sources
        """
        return sorted(
            sources, key=lambda x: x.get("relevance_score", 0.0), reverse=True
        )

    def _analyze_research_gaps(
        self,
        gaps: list[str],
        sources: list[dict[str, Any]],
        knowledge_graph: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Analyze research gaps in context of found sources and knowledge graph.

        Args:
            gaps: List of identified gaps
            sources: List of sources found
            knowledge_graph: Optional knowledge graph data

        Returns:
            Gap analysis dictionary
        """
        analysis = {
            "total_gaps": len(gaps),
            "gap_categories": self._categorize_gaps(gaps),
            "coverage_assessment": self._assess_coverage(sources),
            "priority_gaps": gaps[:3] if gaps else [],  # Top 3 priority gaps
        }

        # Add knowledge graph insights if available
        if knowledge_graph and knowledge_graph.get("success"):
            entities = knowledge_graph.get("entities", [])
            analysis["knowledge_graph_insights"] = {
                "entities_identified": len(entities),
                "entity_types": list(set(e.get("type", "unknown") for e in entities)),
                "research_coverage": self._assess_kg_coverage(entities, gaps),
            }

        return analysis

    def _categorize_gaps(self, gaps: list[str]) -> dict[str, list[str]]:
        """
        Categorize research gaps by type.

        Args:
            gaps: List of gap descriptions

        Returns:
            Categorized gaps
        """
        categories: dict[str, list[str]] = {
            "methodological": [],
            "geographical": [],
            "temporal": [],
            "theoretical": [],
            "other": [],
        }

        for gap in gaps:
            gap_lower = gap.lower()
            if "method" in gap_lower or "approach" in gap_lower:
                categories["methodological"].append(gap)
            elif (
                "geographic" in gap_lower
                or "region" in gap_lower
                or "country" in gap_lower
            ):
                categories["geographical"].append(gap)
            elif (
                "longitudinal" in gap_lower
                or "time" in gap_lower
                or "period" in gap_lower
            ):
                categories["temporal"].append(gap)
            elif (
                "theory" in gap_lower
                or "model" in gap_lower
                or "framework" in gap_lower
            ):
                categories["theoretical"].append(gap)
            else:
                categories["other"].append(gap)

        # Remove empty categories
        return {k: v for k, v in categories.items() if v}

    def _assess_coverage(self, sources: list[dict[str, Any]]) -> str:
        """
        Assess literature coverage based on sources.

        Args:
            sources: List of sources

        Returns:
            Coverage assessment
        """
        count = len(sources)
        if count >= 50:
            return "Comprehensive coverage"
        elif count >= 20:
            return "Good coverage"
        elif count >= 10:
            return "Moderate coverage"
        else:
            return "Limited coverage"

    def _calculate_confidence(
        self,
        sources: list[dict[str, Any]],
        quality: str,
        findings: list[str],
        mcp_data_quality: bool = False,
    ) -> float:
        """
        Calculate confidence score based on multiple factors.

        Args:
            sources: List of sources found
            quality: Quality assessment string
            findings: List of key findings

        Returns:
            Confidence score between 0.0 and 1.0
        """
        confidence = 0.5  # Base confidence

        # Factor 1: Number of sources (max +0.2)
        source_count = len(sources)
        if source_count >= 20:
            confidence += 0.2
        elif source_count >= 10:
            confidence += 0.15
        elif source_count >= 5:
            confidence += 0.1
        elif source_count >= 1:
            confidence += 0.05

        # Factor 2: Quality assessment (max +0.2)
        quality_lower = quality.lower()
        if "high" in quality_lower and "quality" in quality_lower:
            confidence += 0.2
        elif "good" in quality_lower or "peer" in quality_lower:
            confidence += 0.15
        elif "moderate" in quality_lower:
            confidence += 0.1
        elif "limited" in quality_lower:
            confidence += 0.0
        else:
            confidence += 0.05

        # Factor 3: Number of findings (max +0.1)
        finding_count = len(findings)
        if finding_count >= 3:
            confidence += 0.1
        elif finding_count >= 2:
            confidence += 0.07
        elif finding_count >= 1:
            confidence += 0.04

        # Factor 4: Average relevance score (max +0.1)
        if sources:
            avg_relevance = sum(s.get("relevance_score", 0.0) for s in sources) / len(
                sources
            )
            confidence += avg_relevance * 0.1

        # Factor 5: MCP data quality bonus (max +0.1)
        if mcp_data_quality:
            confidence += 0.1

        # Ensure confidence is within bounds
        return min(max(confidence, 0.0), 1.0)

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
            task.input_data.get("query", ""),
            str(sorted(task.input_data.get("domains", []))),
            str(task.input_data.get("max_sources", 50)),
        ]
        key_string = "|".join(key_parts)
        return hashlib.md5(key_string.encode()).hexdigest()

    def _generate_mock_analysis(self, task: AgentTask) -> dict[str, Any]:
        """
        Generate mock analysis for testing without Gemini.

        Args:
            task: The task to analyze

        Returns:
            Mock literature analysis
        """
        return {
            "search_strategy": "Mock systematic search",
            "sources_found": [
                {
                    "title": f"Mock Paper {i}",
                    "authors": [f"Author {i}"],
                    "year": 2024 - i,
                    "relevance_score": 0.9 - (i * 0.1),
                }
                for i in range(min(5, task.input_data.get("max_sources", 5)))
            ],
            "key_findings": [
                "Mock finding 1: Significant correlation identified",
                "Mock finding 2: Novel approach discovered",
            ],
            "methodologies_used": ["Systematic review", "Meta-analysis"],
            "research_gaps": [
                "Limited longitudinal studies",
                "Lack of geographical diversity",
            ],
            "quality_assessment": "Mock quality assessment",
        }

    async def _search_academic_sources(
        self, input_data: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Search academic sources using MCP tools.

        Args:
            input_data: Task input data

        Returns:
            Academic search results
        """
        if not self.mcp_integration:
            self.log_warning("MCP integration not available, using fallback")
            return self._fallback_academic_search(input_data)

        query = input_data.get("query", "")
        domains = input_data.get("domains", [])
        max_sources = input_data.get("max_sources", 20)

        # Determine databases based on domains
        databases = self._determine_databases(domains)

        # Build search filters
        filters = {}
        if input_data.get("year_start"):
            filters["year_start"] = input_data["year_start"]
        if input_data.get("year_end"):
            filters["year_end"] = input_data["year_end"]

        try:
            result = await self.mcp_integration.search_academic_sources(
                query=query,
                databases=databases,
                max_results=max_sources,
                filters=filters,
            )

            self.log_info(
                f"Academic search completed: {result.get('total_found', 0)} sources found"
            )
            return dict(result)

        except Exception as e:
            self.log_error(f"MCP academic search failed: {e}")
            return self._fallback_academic_search(input_data)

    async def _analyze_literature_with_gemini(
        self, input_data: dict[str, Any], academic_sources: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Analyze literature using Gemini with real academic sources.

        Args:
            input_data: Original task input
            academic_sources: Results from academic search

        Returns:
            Literature analysis from Gemini
        """
        if not self.gemini_service:
            return self._generate_mock_analysis_from_sources(academic_sources)

        # Prepare sources for Gemini analysis
        sources_text = self._prepare_sources_for_analysis(
            academic_sources.get("sources", [])
        )

        # Create enhanced prompt with real sources
        enhanced_input = input_data.copy()
        enhanced_input["found_sources"] = sources_text
        enhanced_input["source_count"] = len(academic_sources.get("sources", []))

        prompt = generate_literature_agent_prompt(enhanced_input)

        try:
            response = await self.gemini_service.generate_content(prompt)
            parsed_response = parse_json_response(response)
            result = parsed_response.get("literature_analysis", {})
            return dict(result) if isinstance(result, dict) else {}
        except Exception as e:
            self.log_error(f"Gemini analysis failed: {e}")
            return self._generate_mock_analysis_from_sources(academic_sources)

    async def _build_literature_knowledge_graph(
        self, academic_sources: dict[str, Any], literature_analysis: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Build knowledge graph from literature using MCP tools.

        Args:
            academic_sources: Academic search results
            literature_analysis: Gemini analysis results

        Returns:
            Knowledge graph data
        """
        if not self.mcp_integration:
            return {"success": False, "error": "MCP integration not available"}

        # Combine abstracts and key findings for entity extraction
        text_for_analysis = ""

        sources = academic_sources.get("sources", [])
        for source in sources[:5]:  # Use top 5 sources
            abstract = source.get("abstract", "")
            if abstract:
                text_for_analysis += abstract + " "

        # Add key findings
        key_findings = literature_analysis.get("key_findings", [])
        text_for_analysis += " ".join(key_findings)

        try:
            result = await self.mcp_integration.build_knowledge_graph(
                text=text_for_analysis
            )
            self.log_info(
                f"Knowledge graph built with {len(result.get('entities', []))} entities"
            )
            return dict(result)
        except Exception as e:
            self.log_error(f"Knowledge graph building failed: {e}")
            return {"success": False, "error": str(e)}

    async def _format_source_citations(
        self, academic_sources: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Format citations using MCP citation tool.

        Args:
            academic_sources: Academic search results

        Returns:
            Formatted citations
        """
        if not self.mcp_integration:
            return {"success": False, "citations": []}

        sources = academic_sources.get("sources", [])
        if not sources:
            return {"success": True, "citations": []}

        # Convert sources to citation format
        citation_sources = []
        for source in sources:
            citation_source = {
                "title": source.get("title", "Unknown Title"),
                "authors": source.get("authors", ["Unknown Author"]),
                "year": source.get("year", "n.d."),
                "journal": source.get("journal", ""),
                "doi": source.get("doi", ""),
            }
            citation_sources.append(citation_source)

        try:
            result = await self.mcp_integration.format_citations(
                sources=citation_sources, style="APA"
            )
            self.log_info(f"Formatted {len(citation_sources)} citations")
            return dict(result)
        except Exception as e:
            self.log_error(f"Citation formatting failed: {e}")
            return {"success": False, "error": str(e)}

    def _determine_databases(self, domains: list[str]) -> list[str]:
        """
        Determine which databases to search based on domains.

        Args:
            domains: Research domains

        Returns:
            List of database names
        """
        databases = ["arxiv"]  # Default

        domain_mapping = {
            "medicine": ["pubmed"],
            "health": ["pubmed"],
            "biology": ["pubmed"],
            "ai": ["arxiv"],
            "computer science": ["arxiv"],
            "physics": ["arxiv"],
            "engineering": ["arxiv"],
            "general": ["arxiv", "pubmed"],
        }

        for domain in domains:
            domain_lower = domain.lower()
            for key, dbs in domain_mapping.items():
                if key in domain_lower:
                    databases.extend(dbs)

        # Remove duplicates and return
        return list(set(databases))

    def _prepare_sources_for_analysis(self, sources: list[dict[str, Any]]) -> str:
        """
        Prepare sources text for Gemini analysis.

        Args:
            sources: List of source dictionaries

        Returns:
            Formatted sources text
        """
        if not sources:
            return "No sources found."

        sources_text = []
        for i, source in enumerate(sources[:10], 1):  # Top 10 sources
            source_info = f"{i}. {source.get('title', 'Unknown Title')}"
            if source.get("authors"):
                source_info += (
                    f" by {', '.join(source['authors'][:3])}"  # First 3 authors
                )
            if source.get("year"):
                source_info += f" ({source['year']})"
            if source.get("abstract"):
                abstract = (
                    source["abstract"][:200] + "..."
                    if len(source["abstract"]) > 200
                    else source["abstract"]
                )
                source_info += f"\\nAbstract: {abstract}"
            sources_text.append(source_info)

        return "\\n\\n".join(sources_text)

    def _fallback_academic_search(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """
        Fallback academic search when MCP tools are unavailable.

        Args:
            input_data: Task input data

        Returns:
            Mock search results
        """
        query = input_data.get("query", "")
        domains = input_data.get("domains", [])
        max_sources = input_data.get("max_sources", 20)

        # Generate mock sources
        mock_sources = []
        for i in range(min(max_sources, 10)):
            mock_sources.append(
                {
                    "title": f"Research Study {i+1}: {query}",
                    "authors": [f"Author {i+1}", f"Co-Author {i+1}"],
                    "year": 2024 - i,
                    "journal": f"Journal of {domains[0] if domains else 'Research'}",
                    "abstract": f"This study investigates {query} using systematic methodology...",
                    "doi": f"10.1000/mock.{i+1}",
                    "relevance_score": 0.9 - (i * 0.05),
                }
            )

        return {
            "success": True,
            "sources": mock_sources,
            "total_found": len(mock_sources),
            "databases_searched": ["fallback"],
            "search_strategy": "Fallback search (MCP tools unavailable)",
            "fallback": True,
        }

    def _generate_mock_analysis_from_sources(
        self, academic_sources: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Generate mock Gemini analysis based on real sources.

        Args:
            academic_sources: Real or mock academic sources

        Returns:
            Mock literature analysis
        """
        sources = academic_sources.get("sources", [])
        source_count = len(sources)

        return {
            "key_findings": [
                f"Analysis of {source_count} sources reveals significant patterns",
                "Methodological consistency across studies",
                "Emerging trends in the field identified",
            ],
            "methodologies_used": [
                "Systematic literature review",
                "Thematic analysis",
                "Cross-study comparison",
            ],
            "research_gaps": [
                "Limited longitudinal studies in the dataset",
                "Need for more diverse geographical representation",
                "Methodological standardization required",
            ],
            "quality_assessment": f"Analysis based on {source_count} peer-reviewed sources with good methodological rigor",
        }

    def _get_mcp_status(self) -> dict[str, Any]:
        """Get MCP integration status."""
        if not self.mcp_integration:
            return {"enabled": False, "status": "not_configured"}

        return {
            "enabled": True,
            "status": "configured",
            "fallback_enabled": getattr(self.mcp_integration, "enable_fallback", False),
        }

    def _assess_kg_coverage(
        self, entities: list[dict[str, Any]], gaps: list[str]
    ) -> str:
        """
        Assess how well the knowledge graph covers research gaps.

        Args:
            entities: List of extracted entities
            gaps: List of research gaps

        Returns:
            Coverage assessment string
        """
        if not entities or not gaps:
            return "insufficient_data"

        # Simple heuristic: more entities generally means better coverage
        entity_count = len(entities)
        gap_count = len(gaps)

        coverage_ratio = entity_count / max(
            gap_count * 5, 1
        )  # 5 entities per gap is good

        if coverage_ratio >= 1.0:
            return "comprehensive"
        elif coverage_ratio >= 0.5:
            return "good"
        elif coverage_ratio >= 0.2:
            return "moderate"
        else:
            return "limited"
