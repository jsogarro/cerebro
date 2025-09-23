"""
Agent factory for generating mock agent responses and test data.
"""

import json
import random
import uuid
from typing import Any

from faker import Faker

fake = Faker()


class MockAgentResponseFactory:
    """Factory for creating mock agent responses."""

    @staticmethod
    def create_literature_review_response(
        num_papers: int = 20, include_error: bool = False
    ) -> dict[str, Any]:
        """Create a mock literature review agent response."""
        if include_error:
            return {
                "status": "error",
                "error": "Failed to retrieve academic papers",
                "error_code": "RETRIEVAL_ERROR",
            }

        papers = []
        for i in range(num_papers):
            paper = {
                "title": fake.catch_phrase(),
                "authors": [fake.name() for _ in range(random.randint(1, 4))],
                "year": random.randint(2015, 2024),
                "journal": fake.company(),
                "doi": f"10.{random.randint(1000, 9999)}/{fake.word()}",
                "abstract": fake.paragraph(nb_sentences=5),
                "citations": random.randint(0, 500),
                "relevance_score": round(random.uniform(0.5, 1.0), 2),
            }
            papers.append(paper)

        return {
            "status": "success",
            "papers_reviewed": papers,
            "total_papers": num_papers,
            "key_themes": [fake.word() for _ in range(5)],
            "research_gaps": [fake.sentence() for _ in range(3)],
            "summary": fake.paragraph(nb_sentences=10),
            "recommendations": [fake.sentence() for _ in range(3)],
            "confidence_score": round(random.uniform(0.7, 1.0), 2),
            "metadata": {
                "search_queries": [fake.sentence() for _ in range(3)],
                "databases_searched": [
                    "Google Scholar",
                    "PubMed",
                    "ArXiv",
                    "IEEE Xplore",
                ],
                "execution_time": round(random.uniform(2.0, 10.0), 2),
            },
        }

    @staticmethod
    def create_comparative_analysis_response(
        num_theories: int = 5, include_error: bool = False
    ) -> dict[str, Any]:
        """Create a mock comparative analysis agent response."""
        if include_error:
            return {
                "status": "error",
                "error": "Insufficient data for comparison",
                "error_code": "INSUFFICIENT_DATA",
            }

        theories = []
        for i in range(num_theories):
            theory = {
                "name": f"Theory {fake.word().capitalize()}",
                "proponents": [fake.name() for _ in range(2)],
                "year_introduced": random.randint(1950, 2024),
                "key_concepts": [fake.word() for _ in range(3)],
                "strengths": [fake.sentence() for _ in range(2)],
                "weaknesses": [fake.sentence() for _ in range(2)],
                "applications": [fake.sentence() for _ in range(2)],
            }
            theories.append(theory)

        comparison_matrix = {}
        criteria = [
            "Empirical Support",
            "Practical Application",
            "Theoretical Rigor",
            "Scope",
        ]
        for theory in theories:
            comparison_matrix[theory["name"]] = {
                criterion: round(random.uniform(0.5, 1.0), 2) for criterion in criteria
            }

        return {
            "status": "success",
            "theories_analyzed": theories,
            "comparison_matrix": comparison_matrix,
            "similarities": [fake.sentence() for _ in range(3)],
            "differences": [fake.sentence() for _ in range(3)],
            "synthesis": fake.paragraph(nb_sentences=8),
            "recommendations": [fake.sentence() for _ in range(3)],
            "confidence_score": round(random.uniform(0.7, 1.0), 2),
            "metadata": {
                "comparison_criteria": criteria,
                "analysis_method": "Multi-criteria decision analysis",
                "execution_time": round(random.uniform(3.0, 8.0), 2),
            },
        }

    @staticmethod
    def create_methodology_response(include_error: bool = False) -> dict[str, Any]:
        """Create a mock methodology agent response."""
        if include_error:
            return {
                "status": "error",
                "error": "Unable to determine appropriate methodology",
                "error_code": "METHODOLOGY_ERROR",
            }

        methods = [
            {
                "name": "Quantitative Analysis",
                "description": fake.paragraph(nb_sentences=3),
                "strengths": [fake.sentence() for _ in range(2)],
                "limitations": [fake.sentence() for _ in range(2)],
                "data_requirements": [fake.sentence() for _ in range(3)],
                "suitability_score": round(random.uniform(0.6, 1.0), 2),
            },
            {
                "name": "Qualitative Research",
                "description": fake.paragraph(nb_sentences=3),
                "strengths": [fake.sentence() for _ in range(2)],
                "limitations": [fake.sentence() for _ in range(2)],
                "data_requirements": [fake.sentence() for _ in range(3)],
                "suitability_score": round(random.uniform(0.6, 1.0), 2),
            },
            {
                "name": "Mixed Methods",
                "description": fake.paragraph(nb_sentences=3),
                "strengths": [fake.sentence() for _ in range(2)],
                "limitations": [fake.sentence() for _ in range(2)],
                "data_requirements": [fake.sentence() for _ in range(3)],
                "suitability_score": round(random.uniform(0.7, 1.0), 2),
            },
        ]

        return {
            "status": "success",
            "recommended_methods": methods,
            "primary_recommendation": methods[2]["name"],  # Mixed methods
            "research_design": {
                "type": "Sequential Explanatory Design",
                "phases": [
                    {
                        "phase": 1,
                        "name": "Quantitative Data Collection",
                        "duration": "4 weeks",
                        "activities": [fake.sentence() for _ in range(3)],
                    },
                    {
                        "phase": 2,
                        "name": "Qualitative Data Collection",
                        "duration": "3 weeks",
                        "activities": [fake.sentence() for _ in range(3)],
                    },
                    {
                        "phase": 3,
                        "name": "Integration and Analysis",
                        "duration": "2 weeks",
                        "activities": [fake.sentence() for _ in range(3)],
                    },
                ],
            },
            "potential_biases": [fake.sentence() for _ in range(3)],
            "mitigation_strategies": [fake.sentence() for _ in range(3)],
            "ethical_considerations": [fake.sentence() for _ in range(3)],
            "confidence_score": round(random.uniform(0.7, 1.0), 2),
            "metadata": {
                "frameworks_considered": [
                    "Positivist",
                    "Interpretivist",
                    "Critical Realist",
                ],
                "execution_time": round(random.uniform(2.0, 6.0), 2),
            },
        }

    @staticmethod
    def create_synthesis_response(include_error: bool = False) -> dict[str, Any]:
        """Create a mock synthesis agent response."""
        if include_error:
            return {
                "status": "error",
                "error": "Insufficient input data for synthesis",
                "error_code": "SYNTHESIS_ERROR",
            }

        return {
            "status": "success",
            "executive_summary": fake.paragraph(nb_sentences=8),
            "integrated_findings": {
                "main_themes": [
                    {
                        "theme": fake.word().capitalize(),
                        "description": fake.paragraph(nb_sentences=3),
                        "supporting_evidence": [fake.sentence() for _ in range(2)],
                        "confidence": round(random.uniform(0.7, 1.0), 2),
                    }
                    for _ in range(4)
                ],
                "cross_cutting_issues": [fake.sentence() for _ in range(3)],
                "emergent_patterns": [fake.sentence() for _ in range(3)],
            },
            "coherent_narrative": fake.text(max_nb_chars=2000),
            "key_insights": [
                {
                    "insight": fake.sentence(),
                    "implications": fake.paragraph(nb_sentences=2),
                    "certainty_level": random.choice(["High", "Medium", "Low"]),
                }
                for _ in range(5)
            ],
            "conclusions": [fake.paragraph(nb_sentences=3) for _ in range(3)],
            "future_research_directions": [fake.sentence() for _ in range(4)],
            "limitations": [fake.sentence() for _ in range(3)],
            "confidence_score": round(random.uniform(0.75, 0.95), 2),
            "metadata": {
                "sources_integrated": random.randint(20, 50),
                "synthesis_method": "Thematic analysis with narrative synthesis",
                "execution_time": round(random.uniform(5.0, 15.0), 2),
            },
        }

    @staticmethod
    def create_citation_response(
        num_citations: int = 30, include_error: bool = False
    ) -> dict[str, Any]:
        """Create a mock citation agent response."""
        if include_error:
            return {
                "status": "error",
                "error": "Citation formatting service unavailable",
                "error_code": "CITATION_ERROR",
            }

        citation_styles = ["APA", "MLA", "Chicago", "Harvard", "IEEE"]

        citations = []
        for i in range(num_citations):
            citation = {
                "id": str(uuid.uuid4()),
                "type": random.choice(
                    ["journal", "book", "conference", "web", "thesis"]
                ),
                "title": fake.catch_phrase(),
                "authors": [fake.name() for _ in range(random.randint(1, 3))],
                "year": random.randint(2015, 2024),
                "source": (
                    fake.company()
                    if random.choice([True, False])
                    else fake.catch_phrase()
                ),
                "doi": (
                    f"10.{random.randint(1000, 9999)}/{fake.word()}"
                    if random.choice([True, False])
                    else None
                ),
                "url": fake.url() if random.choice([True, False]) else None,
                "accessed_date": fake.date_between(
                    start_date="-30d", end_date="today"
                ).isoformat(),
                "formatted_citations": {
                    style: f"Formatted citation in {style} style"
                    for style in citation_styles
                },
                "verification_status": random.choice(
                    ["verified", "unverified", "partial"]
                ),
            }
            citations.append(citation)

        return {
            "status": "success",
            "citations": citations,
            "total_citations": num_citations,
            "verified_citations": sum(
                1 for c in citations if c["verification_status"] == "verified"
            ),
            "citation_styles_available": citation_styles,
            "bibliography": {
                style: [
                    f"Bibliography entry {i+1} in {style} format"
                    for i in range(num_citations)
                ]
                for style in citation_styles
            },
            "duplicate_check": {
                "duplicates_found": random.randint(0, 3),
                "duplicates_removed": random.randint(0, 2),
            },
            "confidence_score": round(random.uniform(0.85, 1.0), 2),
            "metadata": {
                "verification_sources": ["CrossRef", "Google Scholar", "PubMed"],
                "formatting_standard": "Latest edition guidelines",
                "execution_time": round(random.uniform(3.0, 8.0), 2),
            },
        }

    @staticmethod
    def create_batch_responses(
        agents: list[str], include_errors: bool = False
    ) -> dict[str, dict[str, Any]]:
        """Create responses for multiple agents."""
        factory = MockAgentResponseFactory()
        responses = {}

        agent_methods = {
            "literature_review": factory.create_literature_review_response,
            "comparative_analysis": factory.create_comparative_analysis_response,
            "methodology": factory.create_methodology_response,
            "synthesis": factory.create_synthesis_response,
            "citation": factory.create_citation_response,
        }

        for agent in agents:
            if agent in agent_methods:
                # Randomly include errors if requested
                include_error = include_errors and random.random() < 0.2
                responses[agent] = agent_methods[agent](include_error=include_error)
            else:
                responses[agent] = {
                    "status": "error",
                    "error": f"Unknown agent: {agent}",
                    "error_code": "UNKNOWN_AGENT",
                }

        return responses


class MockGeminiResponseFactory:
    """Factory for creating mock Gemini API responses."""

    @staticmethod
    def create_response(
        prompt_type: str = "general", include_error: bool = False
    ) -> str:
        """Create a mock Gemini response based on prompt type."""
        if include_error:
            raise Exception("Gemini API error: Rate limit exceeded")

        responses = {
            "general": fake.paragraph(nb_sentences=5),
            "analysis": json.dumps(
                {
                    "analysis": fake.paragraph(nb_sentences=8),
                    "key_points": [fake.sentence() for _ in range(3)],
                    "confidence": round(random.uniform(0.7, 1.0), 2),
                }
            ),
            "summary": fake.paragraph(nb_sentences=3),
            "question": fake.sentence() + "?",
            "explanation": fake.text(max_nb_chars=500),
        }

        return responses.get(prompt_type, fake.paragraph(nb_sentences=5))

    @staticmethod
    def create_structured_response(schema: dict[str, Any]) -> dict[str, Any]:
        """Create a structured response based on a schema."""
        result = {}

        for key, value_type in schema.items():
            if value_type == "string":
                result[key] = fake.sentence()
            elif value_type == "number":
                result[key] = round(random.uniform(0, 100), 2)
            elif value_type == "boolean":
                result[key] = random.choice([True, False])
            elif value_type == "array":
                result[key] = [fake.word() for _ in range(3)]
            elif value_type == "object":
                result[key] = {"nested": fake.word()}
            else:
                result[key] = None

        return result


class MockMCPToolFactory:
    """Factory for creating mock MCP tool responses."""

    @staticmethod
    def create_academic_search_response(
        query: str, max_results: int = 10
    ) -> dict[str, Any]:
        """Create mock academic search tool response."""
        results = []
        for i in range(max_results):
            result = {
                "title": fake.catch_phrase(),
                "authors": [fake.name() for _ in range(random.randint(1, 3))],
                "year": random.randint(2015, 2024),
                "abstract": fake.paragraph(nb_sentences=5),
                "source": fake.company(),
                "url": fake.url(),
                "relevance_score": round(random.uniform(0.5, 1.0), 2),
            }
            results.append(result)

        return {
            "query": query,
            "total_results": random.randint(max_results, max_results * 10),
            "returned_results": max_results,
            "results": results,
        }

    @staticmethod
    def create_citation_tool_response(
        citation_data: dict[str, Any], style: str = "APA"
    ) -> dict[str, Any]:
        """Create mock citation formatting tool response."""
        return {
            "original_data": citation_data,
            "formatted_citation": f"Formatted in {style}: {citation_data.get('title', 'Unknown')}",
            "style": style,
            "warnings": [] if random.random() > 0.3 else ["Missing publication date"],
        }

    @staticmethod
    def create_statistics_tool_response(
        data: list[float], analysis_type: str = "descriptive"
    ) -> dict[str, Any]:
        """Create mock statistics tool response."""
        import statistics

        return {
            "analysis_type": analysis_type,
            "results": {
                "mean": statistics.mean(data) if data else 0,
                "median": statistics.median(data) if data else 0,
                "std_dev": statistics.stdev(data) if len(data) > 1 else 0,
                "min": min(data) if data else 0,
                "max": max(data) if data else 0,
                "count": len(data),
            },
            "interpretation": fake.paragraph(nb_sentences=3),
        }
