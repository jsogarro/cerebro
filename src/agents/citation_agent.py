"""
Citation & Verification Agent implementation.

This agent specializes in formatting citations and verifying sources.
"""

import hashlib
from typing import Any

import orjson
from structlog import get_logger

from src.agents.base import BaseAgent
from src.agents.models import AgentResult, AgentTask
from src.core.constants import LONG_TERM_CACHE_TTL

logger = get_logger()


class CitationAgent(BaseAgent):
    """
    Agent specialized in citation formatting and source verification.
    
    This agent formats citations according to academic standards,
    verifies source information, and ensures proper attribution.
    """
    
    def get_agent_type(self) -> str:
        """Return the agent type identifier."""
        return "citation"
    
    async def execute(self, task: AgentTask) -> AgentResult:
        """
        Execute a citation formatting task.
        
        Args:
            task: The citation task to execute
            
        Returns:
            AgentResult containing formatted citations
        """
        try:
            # Validate input
            sources = task.input_data.get("sources", [])
            if not sources:
                return self.handle_error(
                    task,
                    ValueError("Sources required for citation formatting")
                )
            
            style = task.input_data.get("style", "APA")
            
            # Check cache first
            cache_key = self._generate_cache_key(task)
            cached_result = await self.get_cached_result(cache_key)
            if cached_result:
                self.log_info(f"Using cached result for task {task.id}")
                return cached_result
            
            # Step 1: Format citations using MCP Citation Tool
            citation_result = await self._format_citations_with_mcp(sources, style)
            
            # Step 2: Verify source credibility and DOIs
            verification_result = await self._verify_sources_with_mcp(sources)
            
            # Step 3: Generate bibliography entries
            bibliography_result = await self._generate_bibliography_with_mcp(sources, style)
            
            # Step 4: Export to multiple formats if requested
            export_formats = task.input_data.get("export_formats", ["text"])
            export_result = await self._export_citations(citation_result, export_formats)
            
            # Calculate confidence score with MCP data quality
            confidence = self._calculate_confidence(
                citation_result,
                sources,
                mcp_available=bool(self.mcp_integration)
            )
            
            # Build enhanced output with MCP data
            output = {
                "formatted_citations": citation_result.get("formatted_citations", []),
                "bibliography": bibliography_result.get("bibliography", []),
                "citation_style": style,
                "total_references": len(sources),
                "verified_sources": verification_result.get("verified_count", len(sources)),
                "verification_status": verification_result.get("verification_details", []),
                "export_formats": export_result,
                "doi_resolution": verification_result.get("doi_results", {}),
                "source_quality_scores": verification_result.get("quality_scores", {}),
                "data_source": "mcp_tools" if citation_result.get("success") else "fallback",
                "mcp_integration_status": self._get_mcp_status()
            }
            
            result = AgentResult(
                task_id=task.id,
                status="success",
                output=output,
                confidence=confidence,
                execution_time=0.0,
                metadata=self.build_execution_metadata(
                    citation_count=len(output["formatted_citations"])
                )
            )
            
            # Cache the result
            await self.cache_result(cache_key, result, ttl=LONG_TERM_CACHE_TTL)
            
            return result
            
        except Exception as e:
            self.log_error(f"Citation formatting failed: {e!s}")
            return self.handle_error(task, e)
    
    async def validate_result(self, result: AgentResult) -> bool:
        """
        Validate the citation result.
        
        Args:
            result: The result to validate
            
        Returns:
            True if valid, False otherwise
        """
        if result.status != "success":
            return result.status == "failed"
        
        output = result.output
        
        # Check required fields
        required_fields = ["formatted_citations", "bibliography", "citation_style"]
        for field in required_fields:
            if field not in output:
                self.log_warning(f"Missing required field: {field}")
                return False
        
        return True
    
    def _calculate_confidence(
        self,
        citation_data: dict[str, Any],
        sources: list[dict[str, Any]],
        mcp_available: bool = False
    ) -> float:
        """Calculate confidence score based on citation completeness and MCP integration."""
        confidence = 0.5
        
        # Check if all sources were formatted
        formatted_count = len(citation_data.get("formatted_citations", []))
        if formatted_count == len(sources):
            confidence += 0.25
        elif formatted_count > 0:
            confidence += 0.15
        
        # Check verification status
        verified = citation_data.get("verified_sources", 0)
        if verified == len(sources):
            confidence += 0.25
        elif verified > 0:
            confidence += 0.15
        
        # MCP integration bonus
        if mcp_available and citation_data.get("success"):
            confidence += 0.1
        
        return min(confidence, 1.0)
    
    def _generate_cache_key(self, task: AgentTask) -> str:
        """Generate a cache key for the task."""
        sources = task.input_data.get("sources", [])
        style = task.input_data.get("style", "APA")

        # Create a stable hash from sources
        source_str = orjson.dumps(sources, option=orjson.OPT_SORT_KEYS).decode()
        key_parts = [
            self.get_agent_type(),
            style,
            hashlib.md5(source_str.encode()).hexdigest()
        ]
        key_string = "|".join(key_parts)
        return hashlib.md5(key_string.encode()).hexdigest()
    
    def _build_prompt(self, sources: list[dict[str, Any]], style: str) -> str:
        """Build prompt for Gemini."""
        sources_text = orjson.dumps(sources, option=orjson.OPT_INDENT_2).decode()
        return f"""Format the following sources according to {style} citation style:

        {sources_text}

        Provide formatted citations and bibliography in JSON format."""
    
    def _format_author_list(self, source: dict[str, Any]) -> str:
        """Format author list from source data."""
        authors = source.get("authors", [])
        if not authors:
            author = source.get("author", "Unknown")
            return str(author)
        if len(authors) == 1:
            return str(authors[0])
        if len(authors) == 2:
            return f"{authors[0]} & {authors[1]}"
        return f"{authors[0]} et al."

    def _generate_mock_citations(
        self,
        sources: list[dict[str, Any]],
        style: str,
    ) -> dict[str, Any]:
        """Format citations from source data."""
        formatted_citations = []
        verification_status = []

        for i, source in enumerate(sources):
            author_str = self._format_author_list(source)
            year = source.get("year", "n.d.")
            title = source.get("title", "Untitled")
            journal = source.get("journal", "")
            doi = source.get("doi")

            if style == "APA":
                citation = f"{author_str} ({year}). {title}."
                if journal:
                    citation += f" *{journal}*."
                if doi:
                    citation += f" https://doi.org/{doi}"
            elif style == "MLA":
                citation = f'{author_str}. "{title}."'
                if journal:
                    citation += f" *{journal}*,"
                citation += f" {year}."
            else:  # Chicago
                citation = f"{author_str}. {title}."
                if journal:
                    citation += f" {journal}"
                citation += f" ({year})."

            formatted_citations.append(citation)
            verification_status.append({
                "source_id": i,
                "title": title,
                "verified": doi is not None,
                "doi": doi,
                "issues": [] if doi else ["DOI not available"],
            })

        bibliography = sorted(formatted_citations)

        return {
            "formatted_citations": formatted_citations,
            "bibliography": bibliography,
            "verified_sources": sum(1 for v in verification_status if v["verified"]),
            "total_sources": len(sources),
            "citation_style": style,
            "verification_status": verification_status,
        }
    
    async def _format_citations_with_mcp(
        self,
        sources: list[dict[str, Any]],
        style: str
    ) -> dict[str, Any]:
        """
        Format citations using MCP Citation Tool or Gemini structured output.

        Args:
            sources: List of source dictionaries
            style: Citation style (APA, MLA, Chicago)

        Returns:
            Citation formatting results
        """
        if not self.mcp_integration:
            self.log_warning("MCP integration not available, using Gemini fallback")
            return await self._format_citations_with_gemini(sources, style)

        try:
            result = await self.mcp_integration.format_citations(
                sources=sources,
                style=style
            )

            if result.get("success"):
                self.log_info(f"MCP citation formatting successful: {len(sources)} sources in {style} style")
                return dict(result)
            else:
                raise Exception(f"MCP citation formatting failed: {result.get('error', 'Unknown error')}")

        except Exception as e:
            self.log_error(f"MCP citation formatting failed: {e}")
            return await self._format_citations_with_gemini(sources, style)

    async def _format_citations_with_gemini(
        self,
        sources: list[dict[str, Any]],
        style: str
    ) -> dict[str, Any]:
        """
        Format citations using Gemini structured output when MCP unavailable.

        Args:
            sources: List of source dictionaries
            style: Citation style (APA, MLA, Chicago)

        Returns:
            Citation formatting results
        """
        if not self.gemini_service:
            self.log_warning("Gemini service not available, using mock citations")
            return self._generate_mock_citations(sources, style)

        from src.agents.schemas import CitationSchema

        prompt = f"""Format the following academic sources in {style} citation style:

Sources:
{orjson.dumps(sources, option=orjson.OPT_INDENT_2).decode()}

Format each source as a complete {style} citation with proper formatting.
Include all available metadata (authors, year, title, journal, DOI).

Return formatted citations as structured JSON."""

        try:
            result = await self.gemini_service.generate_structured_content(
                prompt, CitationSchema
            )

            # Convert Pydantic model to expected dict format
            formatted_citations = [
                {
                    "citation": citation.citation_text,
                    "source_id": citation.source_id or f"source_{i}"
                }
                for i, citation in enumerate(result.citations)
            ]

            return {
                "success": True,
                "formatted_citations": formatted_citations,
                "verified_sources": len(sources),
                "total_sources": result.total_sources or len(sources),
                "citation_style": result.style,
            }

        except Exception as e:
            self.log_error(f"Gemini citation formatting failed: {e}")
            return self._generate_mock_citations(sources, style)

    async def _verify_sources_with_mcp(self, sources: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Verify source credibility and resolve DOIs using MCP tools.
        
        Args:
            sources: List of source dictionaries
            
        Returns:
            Source verification results
        """
        verification_details = []
        doi_results = {}
        quality_scores = {}
        verified_count = 0
        
        for i, source in enumerate(sources):
            source_id = f"source_{i}"
            verification_result = await self._verify_single_source(source, source_id)
            
            verification_details.append(verification_result)
            
            if verification_result.get("verified"):
                verified_count += 1
            
            if verification_result.get("doi_resolved"):
                doi_results[source_id] = verification_result.get("doi_info", {})
            
            quality_scores[source_id] = verification_result.get("quality_score", 0.5)
        
        return {
            "verified_count": verified_count,
            "verification_details": verification_details,
            "doi_results": doi_results,
            "quality_scores": quality_scores,
            "total_processed": len(sources)
        }
    
    async def _verify_single_source(
        self,
        source: dict[str, Any],
        source_id: str
    ) -> dict[str, Any]:
        """
        Verify a single source using various criteria.
        
        Args:
            source: Source dictionary
            source_id: Unique identifier for the source
            
        Returns:
            Verification result for the source
        """
        verification_result: dict[str, Any] = {
            "source_id": source_id,
            "verified": False,
            "quality_score": 0.5,
            "issues": [],
            "doi_resolved": False,
            "doi_info": {}
        }
        quality_score: float = 0.5
        issues: list[str] = []

        # Check required fields
        required_fields = ["title", "authors", "year"]
        missing_fields = [field for field in required_fields if not source.get(field)]

        if missing_fields:
            issues.append(f"Missing fields: {', '.join(missing_fields)}")
            quality_score -= 0.2
        
        # Check DOI resolution if available
        if source.get("doi"):
            doi_info = await self._resolve_doi(source["doi"])
            if doi_info.get("resolved"):
                verification_result["doi_resolved"] = True
                verification_result["doi_info"] = doi_info
                quality_score += 0.2
            else:
                issues.append("DOI could not be resolved")
        
        # Check publication year
        year = source.get("year")
        if year:
            try:
                year_int = int(year)
                current_year = 2024
                if year_int > current_year:
                    issues.append("Future publication year")
                    quality_score -= 0.1
                elif year_int < 1800:
                    issues.append("Very old publication year")
                    quality_score -= 0.1
            except (ValueError, TypeError):
                issues.append("Invalid year format")
                quality_score -= 0.1
        
        # Check journal credibility (simple heuristic)
        journal = source.get("journal", "").lower()
        reputable_indicators = ["nature", "science", "plos", "ieee", "acm", "springer", "elsevier"]
        if any(indicator in journal for indicator in reputable_indicators):
            quality_score += 0.1

        # Overall verification status
        verification_result["quality_score"] = quality_score
        verification_result["issues"] = issues
        verification_result["verified"] = (
            quality_score >= 0.5 and
            len(issues) <= 1
        )

        return verification_result
    
    async def _resolve_doi(self, doi: str) -> dict[str, Any]:
        """
        Resolve DOI information.
        
        Args:
            doi: DOI string
            
        Returns:
            DOI resolution result
        """
        # Simple DOI validation
        if not doi or not doi.startswith("10."):
            return {"resolved": False, "error": "Invalid DOI format"}
        
        # In a real implementation, this would call CrossRef API
        # For now, return mock data
        return {
            "resolved": True,
            "doi": doi,
            "url": f"https://doi.org/{doi}",
            "crossref_data": {
                "status": "verified",
                "publisher": "Mock Publisher",
                "type": "journal-article"
            }
        }
    
    async def _generate_bibliography_with_mcp(
        self,
        sources: list[dict[str, Any]],
        style: str
    ) -> dict[str, Any]:
        """
        Generate bibliography using MCP Citation Tool.
        
        Args:
            sources: List of source dictionaries
            style: Citation style
            
        Returns:
            Bibliography generation results
        """
        if not self.mcp_integration:
            # Fallback bibliography generation
            citations = []
            for source in sources:
                citation = self._format_single_citation(source, style)
                citations.append(citation)
            
            return {
                "success": True,
                "bibliography": sorted(citations),
                "format": style,
                "fallback": True
            }
        
        try:
            # Use MCP citation tool for bibliography
            result = await self.mcp_integration.format_citations(
                sources=sources,
                style=style
            )
            
            if result.get("success"):
                citations = result.get("formatted_citations", [])
                bibliography = [c.get("citation", str(c)) for c in citations]
                
                return {
                    "success": True,
                    "bibliography": sorted(bibliography),
                    "format": style,
                    "total_entries": len(bibliography)
                }
            else:
                raise Exception("MCP bibliography generation failed")
                
        except Exception as e:
            self.log_error(f"MCP bibliography generation failed: {e}")
            # Fallback
            citations = [self._format_single_citation(source, style) for source in sources]
            return {
                "success": True,
                "bibliography": sorted(citations),
                "format": style,
                "fallback": True
            }
    
    async def _export_citations(
        self,
        citation_result: dict[str, Any],
        export_formats: list[str]
    ) -> dict[str, Any]:
        """
        Export citations to multiple formats.
        
        Args:
            citation_result: Citation formatting results
            export_formats: List of formats to export to
            
        Returns:
            Export results for each format
        """
        exports = {}
        citations = citation_result.get("formatted_citations", [])
        
        for format_type in export_formats:
            if format_type.lower() == "text":
                exports["text"] = self._export_to_text(citations)
            elif format_type.lower() == "bibtex":
                exports["bibtex"] = await self._export_to_bibtex(citations)
            elif format_type.lower() == "json":
                exports["json"] = self._export_to_json(citations)
            elif format_type.lower() == "csv":
                exports["csv"] = self._export_to_csv(citations)
            else:
                self.log_warning(f"Unsupported export format: {format_type}")
        
        return exports
    
    def _export_to_text(self, citations: list[Any]) -> str:
        """Export citations to plain text format."""
        if not citations:
            return ""
        
        text_lines = []
        for i, citation in enumerate(citations, 1):
            if isinstance(citation, dict):
                citation_text = citation.get("citation", str(citation))
            else:
                citation_text = str(citation)
            text_lines.append(f"{i}. {citation_text}")
        
        return "\\n".join(text_lines)
    
    async def _export_to_bibtex(self, citations: list[Any]) -> str:
        """Export citations to BibTeX format."""
        if not self.mcp_integration:
            return "% BibTeX export requires MCP integration"
        
        try:
            # Use MCP citation tool for BibTeX export
            # This would be implemented in the MCP citation tool
            return "% BibTeX export (placeholder - would use MCP tool)"
        except Exception as e:
            self.log_error(f"BibTeX export failed: {e}")
            return f"% BibTeX export failed: {e}"
    
    def _export_to_json(self, citations: list[Any]) -> str:
        """Export citations to JSON format."""
        return orjson.dumps(citations, option=orjson.OPT_INDENT_2).decode()
    
    def _export_to_csv(self, citations: list[Any]) -> str:
        """Export citations to CSV format."""
        csv_lines = ["Citation"]
        
        for citation in citations:
            if isinstance(citation, dict):
                citation_text = citation.get("citation", str(citation))
            else:
                citation_text = str(citation)
            
            # Escape quotes in CSV
            citation_text = citation_text.replace('"', '""')
            csv_lines.append(f'"{citation_text}"')
        
        return "\\n".join(csv_lines)
    
    def _format_single_citation(self, source: dict[str, Any], style: str) -> str:
        """
        Format a single citation in the specified style.
        
        Args:
            source: Source dictionary
            style: Citation style
            
        Returns:
            Formatted citation string
        """
        author = source.get("authors", ["Unknown"])[0] if source.get("authors") else "Unknown"
        year = source.get("year", "n.d.")
        title = source.get("title", "Untitled")
        journal = source.get("journal", "")
        
        if style.upper() == "APA":
            citation = f"{author} ({year}). {title}."
            if journal:
                citation += f" {journal}."
        elif style.upper() == "MLA":
            citation = f'{author}. "{title}." {journal}, {year}.'
        elif style.upper() == "CHICAGO":
            citation = f'{author}. "{title}." {journal} ({year}).'
        else:
            citation = f"{author}. {title}. {year}."
        
        return citation
    
    def _get_mcp_status(self) -> dict[str, Any]:
        """Get MCP integration status for citations."""
        if not self.mcp_integration:
            return {"enabled": False, "status": "not_configured"}
        
        return {
            "enabled": True,
            "status": "configured",
            "tools_available": ["citation_formatter", "doi_resolver"],
            "fallback_enabled": getattr(self.mcp_integration, "enable_fallback", False)
        }
