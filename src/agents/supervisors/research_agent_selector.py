"""Research worker definition helpers for agent selection."""

from ..citation_agent import CitationAgent
from ..comparative_analysis_agent import ComparativeAnalysisAgent
from ..literature_review_agent import LiteratureReviewAgent
from ..methodology_agent import MethodologyAgent
from ..synthesis_agent import SynthesisAgent
from .base_supervisor import WorkerDefinition


class ResearchAgentSelector:
    """Builds research worker definitions used by supervisor allocation."""

    def build_worker_definitions(self) -> dict[str, WorkerDefinition]:
        """Build the legacy research worker definition catalog."""
        return {
            "literature_review": WorkerDefinition(
                worker_type="literature_review",
                agent_class=LiteratureReviewAgent,
                specialization="Academic source analysis and systematic reviews",
                capabilities=["database_search", "source_evaluation", "gap_analysis"],
                required_for=["research", "literature_analysis"],
                optimal_for=["academic_research", "systematic_review"],
                avg_execution_time_ms=45000,
                reliability_score=0.95,
                quality_score=0.90,
            ),
            "methodology": WorkerDefinition(
                worker_type="methodology",
                agent_class=MethodologyAgent,
                specialization="Research design and methodological validation",
                capabilities=[
                    "research_design",
                    "validity_assessment",
                    "bias_detection",
                ],
                required_for=["research", "methodology_design"],
                optimal_for=["experimental_design", "validation_studies"],
                avg_execution_time_ms=30000,
                reliability_score=0.92,
                quality_score=0.88,
            ),
            "comparative_analysis": WorkerDefinition(
                worker_type="comparative_analysis",
                agent_class=ComparativeAnalysisAgent,
                specialization="Multi-perspective analysis and comparison",
                capabilities=[
                    "framework_comparison",
                    "strength_weakness_analysis",
                    "evidence_synthesis",
                ],
                required_for=["comparative_studies", "multi_perspective_analysis"],
                optimal_for=["theory_comparison", "approach_evaluation"],
                avg_execution_time_ms=35000,
                reliability_score=0.90,
                quality_score=0.87,
            ),
            "synthesis": WorkerDefinition(
                worker_type="synthesis",
                agent_class=SynthesisAgent,
                specialization="Cross-agent integration and narrative synthesis",
                capabilities=[
                    "integration",
                    "narrative_building",
                    "pattern_identification",
                ],
                required_for=["research", "synthesis"],
                optimal_for=["comprehensive_analysis", "meta_analysis"],
                avg_execution_time_ms=40000,
                reliability_score=0.93,
                quality_score=0.91,
            ),
            "citation": WorkerDefinition(
                worker_type="citation",
                agent_class=CitationAgent,
                specialization="Source verification and citation formatting",
                capabilities=[
                    "citation_formatting",
                    "source_verification",
                    "plagiarism_check",
                ],
                required_for=["research", "academic_writing"],
                optimal_for=["publication_prep", "academic_validation"],
                avg_execution_time_ms=20000,
                reliability_score=0.98,
                quality_score=0.85,
            ),
        }
