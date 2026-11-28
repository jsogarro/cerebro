"""
Literature Review Agent implementation.

This agent specializes in conducting comprehensive literature reviews,
searching academic sources, and identifying research gaps.
"""

import hashlib
from typing import Any, Final

from structlog import get_logger

from src.agents.llm_worker_base import LLMWorkerAgentBase
from src.agents.models import AgentResult, AgentTask
from src.agents.tools.mediation import ToolCallIdentity
from src.core.acquisition.search_result import (
    DEFAULT_MAX_PROMPT_SOURCES,
    BoundedSelectionResult,
    SelectionReason,
    select_bounded_sources,
    source_ref,
)
from src.core.budget import (
    BUDGET_LEDGER_CONTEXT_KEY,
    MIN_WORKER_CALL_TOKENS,
    BudgetExceededError,
    estimate_prompt_tokens,
)
from src.core.constants import LONG_TERM_CACHE_TTL
from src.core.observability import record_literature_selection
from src.models.research_project import ResearchDepth
from src.services.parsers.json_parser import parse_json_response
from src.services.prompts.agent_prompts import generate_literature_agent_prompt

logger = get_logger()

DEFAULT_CONTEXT_WINDOW_TOKENS: Final[int] = 100_000
DEFAULT_OUTPUT_TOKEN_RESERVE: Final[int] = MIN_WORKER_CALL_TOKENS


def _source_ref(source: dict[str, Any]) -> str | None:
    """A stable identity for a source with no recorded evidence."""
    return source_ref(source)


class LiteratureAnalysisError(Exception):
    """The model produced nothing usable; the message is the failure reason."""


class LiteratureReviewAgent(LLMWorkerAgentBase):
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
            query = task.input_data.get("query", "").strip()
            if not query:
                return self.handle_error(task, ValueError("Query cannot be empty"))

            cache_key = self._generate_cache_key(task)
            cached_result = await self.get_cached_result(cache_key)
            if cached_result:
                self.log_info(f"Using cached result for task {task.id}")
                return cached_result

            academic_sources = await self._search_academic_sources(
                task.input_data, task
            )
            # Only sources the tool boundary retrieved count, and when
            # evidence is recorded only those that became evidence. Anything
            # less is a failed run, never model-named or canned findings.
            if not (
                academic_sources.get("data_source") == "mcp_tools"
                and academic_sources.get("sources")
            ):
                return self._failed_result(
                    task,
                    academic_sources,
                    "INSUFFICIENT_EVIDENCE",
                    str(
                        academic_sources.get("error_code")
                        or academic_sources.get("tool_outcome")
                        or "no_sources_retrieved"
                    ),
                )
            selection = self._select_bounded_sources(
                task.input_data, academic_sources, task
            )
            diagnostics = selection.diagnostics.as_dict()
            selected = selection.eligible_sources
            if not selected:
                reason = (
                    "no_source_in_year_range"
                    if (
                        diagnostics["reason_counts"].get(
                            SelectionReason.OUT_OF_YEAR_RANGE.value, 0
                        )
                        or diagnostics["reason_counts"].get(
                            SelectionReason.UNKNOWN_YEAR.value, 0
                        )
                    )
                    and not diagnostics["reason_counts"].get(
                        SelectionReason.NOT_RECORDED.value, 0
                    )
                    else "no_source_became_evidence"
                )
                return self._failed_result(
                    task,
                    academic_sources,
                    "INSUFFICIENT_EVIDENCE",
                    reason,
                    selection_diagnostics=diagnostics,
                )
            if not selection.prompt_entries:
                return self._failed_result(
                    task,
                    academic_sources,
                    "INSUFFICIENT_EVIDENCE",
                    "context_budget_exhausted",
                    selection_diagnostics=diagnostics,
                )
            # Everything below sees only what the analysis was allowed to use.
            grounded = {**academic_sources, "sources": selected}
            try:
                literature_analysis_dict = await self._analyze_literature_with_gemini(
                    task.input_data, academic_sources, task, selection=selection
                )
            except LiteratureAnalysisError as error:
                return self._failed_result(
                    task,
                    grounded,
                    "ANALYSIS_FAILED",
                    str(error),
                    selection_diagnostics=diagnostics,
                )
            from src.agents.schemas import LiteratureAnalysisSchema

            literature_analysis = LiteratureAnalysisSchema(
                sources=[],
                key_findings=literature_analysis_dict.get("key_findings", []),
                research_gaps=literature_analysis_dict.get("research_gaps", []),
                methodologies_used=literature_analysis_dict.get(
                    "methodologies_used", []
                ),
                quality_assessment=literature_analysis_dict.get(
                    "quality_assessment", ""
                ),
            )

            # Step 3: Build knowledge graph of relationships
            knowledge_graph = await self._build_literature_knowledge_graph(
                grounded, literature_analysis.model_dump(), task
            )

            # Step 4: Format citations properly
            formatted_citations = await self._format_source_citations(grounded, task)

            ranked_sources = selected

            # Analyze research gaps with enhanced analysis
            gaps = literature_analysis.research_gaps
            gap_analysis = self._analyze_research_gaps(
                gaps, ranked_sources, knowledge_graph
            )

            confidence = self._calculate_confidence(
                sources=ranked_sources,
                quality=literature_analysis.quality_assessment,
                findings=literature_analysis.key_findings,
                mcp_data_quality=academic_sources.get("success", False),
            )

            output = {
                "sources_found": ranked_sources,
                "key_findings": literature_analysis.key_findings,
                "research_gaps": gaps,
                "gap_analysis": gap_analysis,
                "methodologies_used": literature_analysis.methodologies_used,
                "quality_assessment": literature_analysis.quality_assessment,
                "search_strategy": academic_sources.get(
                    "search_strategy", "Multi-database academic search"
                ),
                "total_sources": len(ranked_sources),
                "databases_searched": academic_sources.get("databases_searched", []),
                "formatted_citations": formatted_citations,
                "knowledge_graph": knowledge_graph,
                "analysis_status": "grounded",
                "evidence_status": "recorded"
                if "recorded" in (academic_sources.get("source_evidence") or {})
                else "not_recorded",
                "source_evidence": academic_sources.get("source_evidence", {}),
                "selection_diagnostics": diagnostics,
                # Reported by the tool boundary, not inferred here. The
                # expression this replaces read `success` alone, and every
                # fallback set `success: True` -- so a fabricated stand-in was
                # labelled `mcp_tools`. The flag is now trustworthy, and taking
                # the label from its source removes the inference entirely.
                "data_source": academic_sources["data_source"],
                "mcp_integration_status": self._get_mcp_status(),
            }

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
                metadata=self.build_execution_metadata(
                    sources_count=len(ranked_sources),
                    gaps_identified=len(gaps),
                    selection_truncated=selection.diagnostics.truncated_count,
                    selection_omitted=selection.diagnostics.omitted_count,
                ),
            )

            # Cache the result
            await self.cache_result(
                cache_key, result, ttl=LONG_TERM_CACHE_TTL
            )  # 24 hours

            return result

        except BudgetExceededError:
            raise
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

        sources = output.get("sources_found", [])
        if not isinstance(sources, list):
            return False

        # At least some findings should be present
        findings = output.get("key_findings", [])
        if not isinstance(findings, list) or len(findings) == 0:
            return False

        # Research gaps should be identified
        gaps = output.get("research_gaps", [])
        return not (not isinstance(gaps, list) or len(gaps) == 0)

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

        if knowledge_graph and knowledge_graph.get("success"):
            entities = knowledge_graph.get("entities", [])
            analysis["knowledge_graph_insights"] = {
                "entities_identified": len(entities),
                "entity_types": list({e.get("type", "unknown") for e in entities}),
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
            str(task.input_data.get("year_start") or ""),
            str(task.input_data.get("year_end") or ""),
            # Another tenant's evidence must never answer this tenant's query.
            ToolCallIdentity.from_agent_task(task).organization_id or "",
        ]
        key_string = "|".join(key_parts)
        return hashlib.md5(key_string.encode()).hexdigest()

    async def _search_academic_sources(
        self, input_data: dict[str, Any], task: AgentTask | None = None
    ) -> dict[str, Any]:
        """
        Search academic sources using MCP tools.

        Args:
            input_data: Task input data
            task: Optional AgentTask for routing context

        Returns:
            Academic search results
        """
        if not self.mcp_integration:
            return self._no_evidence_search("search_unavailable")

        query = input_data.get("query", "")
        domains = input_data.get("domains", [])
        max_sources = input_data.get("max_sources", 20)

        # Determine databases based on domains
        databases = self._determine_databases(domains)

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
                identity=ToolCallIdentity.from_agent_task(task),
            )

        except OSError as e:
            self.log_error(f"MCP academic search failed: {e}")
            return self._no_evidence_search("search_unreachable")

        if not isinstance(result, dict):
            return self._no_evidence_search("malformed_search_result")
        self.log_info(
            f"Academic search completed: {result.get('total_found', 0)} sources found"
        )
        return result

    @staticmethod
    def _no_evidence_search(reason: str) -> dict[str, Any]:
        return {
            "success": False,
            "sources": [],
            "total_found": 0,
            "databases_searched": [],
            "search_strategy": "No source search available",
            "data_source": "no_evidence",
            "error_code": reason,
        }

    def _resolve_context_window(self, task: AgentTask | None) -> int:
        configured = self.config.get("context_window")
        if isinstance(configured, int) and configured > 0:
            return configured

        route = self._select_generation_route(task)
        if route is not None:
            primary = route.routing_decision.get("primary_model") or {}
            provider_name = primary.get("provider")
            model_name = primary.get("name") or ""
            try:
                provider = route.router.registry.providers.get(provider_name)
                if provider is not None and hasattr(
                    provider, "_get_model_context_window"
                ):
                    window = int(provider._get_model_context_window(model_name))
                    if window > 0:
                        return window
            except (AttributeError, KeyError, TypeError, ValueError):
                pass

        for candidate in (
            getattr(self.gemini_service, "context_window", None),
            getattr(
                getattr(self.gemini_service, "config", None), "context_window", None
            ),
        ):
            if isinstance(candidate, int) and candidate > 0:
                return candidate

        return DEFAULT_CONTEXT_WINDOW_TOKENS

    def _resolve_output_reserve(self) -> int:
        configured = self.config.get("output_token_reserve")
        if isinstance(configured, int) and configured > 0:
            return configured
        return DEFAULT_OUTPUT_TOKEN_RESERVE

    def _resolve_evidence_budget(
        self, input_data: dict[str, Any], task: AgentTask | None
    ) -> tuple[int, int, int, int, int | None]:
        """Compute ``(context_window, output_reserve, instruction_tokens, evidence_budget, budget_cap)``."""
        context_window = self._resolve_context_window(task)
        output_reserve = self._resolve_output_reserve()
        # Render with a sentinel space so the <RETRIEVED_SOURCES> wrapper is counted too.
        wrapper_prompt = self._with_brief(
            generate_literature_agent_prompt({**input_data, "found_sources": " "}),
            task,
        )
        instruction_tokens = estimate_prompt_tokens(wrapper_prompt)

        ledger = None
        if task is not None:
            ctx = self._invocation_context(task)
            ledger = (
                ctx.ledger
                if ctx is not None
                else task.context.get(BUDGET_LEDGER_CONTEXT_KEY)
            )
        remaining = getattr(ledger, "remaining_tokens", None)
        effective_window = context_window
        budget_cap: int | None = None
        # Leave an exhausted ledger (<= fixed overhead) to BudgetExceededError
        # instead of relabelling it as INSUFFICIENT_EVIDENCE.
        if (
            isinstance(remaining, int)
            and remaining > instruction_tokens + output_reserve
            and remaining < context_window
        ):
            effective_window = remaining
            budget_cap = remaining

        evidence_budget = max(0, effective_window - output_reserve - instruction_tokens)
        return (
            context_window,
            output_reserve,
            instruction_tokens,
            evidence_budget,
            budget_cap,
        )

    def _select_bounded_sources(
        self,
        input_data: dict[str, Any],
        academic_sources: dict[str, Any],
        task: AgentTask | None,
    ) -> BoundedSelectionResult:
        (
            context_window,
            output_reserve,
            instruction_tokens,
            evidence_budget,
            budget_cap,
        ) = self._resolve_evidence_budget(input_data, task)
        max_prompt_sources = int(
            self.config.get("max_prompt_sources", DEFAULT_MAX_PROMPT_SOURCES)
        )
        result = select_bounded_sources(
            academic_sources.get("sources") or [],
            query=str(input_data.get("query") or ""),
            evidence_budget_tokens=evidence_budget,
            source_evidence=academic_sources.get("source_evidence"),
            max_prompt_sources=max_prompt_sources,
            year_start=input_data.get("year_start"),
            year_end=input_data.get("year_end"),
            context_window_tokens=context_window,
            budget_cap_tokens=budget_cap,
            output_reserve_tokens=output_reserve,
            instruction_tokens=instruction_tokens,
        )
        diag = result.diagnostics
        record_literature_selection(
            retrieved_count=diag.retrieved_count,
            eligible_count=diag.eligible_count,
            deduped_count=diag.deduped_count,
            selected_count=diag.selected_count,
            truncated_count=diag.truncated_count,
            omitted_count=diag.omitted_count,
            context_window_tokens=diag.context_window_tokens,
            budget_cap_tokens=diag.budget_cap_tokens,
            output_reserve_tokens=diag.output_reserve_tokens,
            instruction_tokens=diag.instruction_tokens,
            evidence_budget_tokens=diag.evidence_budget_tokens,
            evidence_tokens_used=diag.evidence_tokens_used,
            reason_counts=diag.reason_counts,
        )
        return result

    async def _analyze_literature_with_gemini(
        self,
        input_data: dict[str, Any],
        academic_sources: dict[str, Any],
        task: AgentTask,
        *,
        selection: BoundedSelectionResult | None = None,
    ) -> dict[str, Any]:
        """Analyze literature using Gemini with bounded, grounded academic sources."""
        if selection is None:
            selection = self._select_bounded_sources(input_data, academic_sources, task)
        enhanced_input = input_data.copy()
        enhanced_input["found_sources"] = selection.rendered_sources

        prompt = generate_literature_agent_prompt(enhanced_input)

        content, _confidence = await self._generate_with_routing(prompt, task)
        if content is None:
            raise LiteratureAnalysisError("no_model_output")
        try:
            parsed_response = parse_json_response(content)
        except (ValueError, TypeError) as e:
            raise LiteratureAnalysisError("unparseable_model_output") from e
        result = parsed_response.get("literature_analysis")
        if not isinstance(result, dict):
            raise LiteratureAnalysisError("unparseable_model_output")
        return dict(result)

    def _select_sources(self, academic_sources: dict[str, Any]) -> list[dict[str, Any]]:
        """Eligible, deduplicated sources tagged with the ref each is cited by."""
        return select_bounded_sources(
            academic_sources.get("sources") or [],
            query="",
            evidence_budget_tokens=DEFAULT_CONTEXT_WINDOW_TOKENS,
            source_evidence=academic_sources.get("source_evidence"),
        ).eligible_sources

    def _failed_result(
        self,
        task: AgentTask,
        academic_sources: dict[str, Any],
        error_code: str,
        reason: str,
        *,
        selection_diagnostics: dict[str, Any] | None = None,
    ) -> AgentResult:
        """A failed result that carries what was retrieved and why it stopped."""
        self.log_warning(
            "literature_review_failed", error_code=error_code, reason=reason
        )
        retrieved = (
            []
            if error_code == "INSUFFICIENT_EVIDENCE"
            else academic_sources.get("sources", [])
        )
        output: dict[str, Any] = {
            "error": f"{error_code}: {reason}",
            "error_code": error_code,
            "reason": reason,
            "analysis_status": "no_evidence"
            if error_code == "INSUFFICIENT_EVIDENCE"
            else "analysis_failed",
            "data_source": "no_evidence"
            if error_code == "INSUFFICIENT_EVIDENCE"
            else academic_sources.get("data_source"),
            "sources_found": retrieved,
            "total_sources": len(retrieved),
            "key_findings": [],
            "research_gaps": [],
            "methodologies_used": [],
            "source_evidence": academic_sources.get("source_evidence", {}),
        }
        if selection_diagnostics is not None:
            output["selection_diagnostics"] = selection_diagnostics
        return AgentResult(
            task_id=task.id,
            status="failed",
            output=output,
            confidence=0.0,
            execution_time=0.0,
            metadata=self.build_execution_metadata(task_type=task.agent_type),
        )

    async def _build_literature_knowledge_graph(
        self,
        academic_sources: dict[str, Any],
        literature_analysis: dict[str, Any],
        task: AgentTask | None = None,
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

        key_findings = literature_analysis.get("key_findings", [])
        text_for_analysis += " ".join(key_findings)

        result = await self.mcp_integration.build_knowledge_graph(
            text=text_for_analysis, identity=ToolCallIdentity.from_agent_task(task)
        )
        self.log_info(
            f"Knowledge graph built with {len(result.get('entities', []))} entities"
        )
        return dict(result)

    async def _format_source_citations(
        self, academic_sources: dict[str, Any], task: AgentTask | None = None
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

        result = await self.mcp_integration.format_citations(
            sources=citation_sources,
            style="APA",
            identity=ToolCallIdentity.from_agent_task(task),
        )
        self.log_info(f"Formatted {len(citation_sources)} citations")
        return dict(result)

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

    def _prepare_sources_for_analysis(
        self,
        sources: list[dict[str, Any]],
        *,
        query: str = "",
        evidence_budget_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS,
    ) -> str:
        """Render bounded, escaped source blocks with digests and byte locators."""
        if not sources:
            return "No sources found."
        return select_bounded_sources(
            sources,
            query=query,
            evidence_budget_tokens=evidence_budget_tokens,
        ).rendered_sources

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
