"""
Query Complexity Analyzer for MASR

Analyzes incoming queries to determine complexity score, required capabilities,
and optimal routing strategy based on multiple factors including:
- Query length and linguistic complexity
- Domain requirements and cross-domain analysis needs
- Required reasoning depth and analytical complexity
- Time constraints and priority levels
- Expected output format and quality requirements
"""

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ComplexityLevel(Enum):
    """Query complexity levels for routing decisions."""

    SIMPLE = "simple"  # 0.0 - 0.3: Single agent, fast model
    MODERATE = "moderate"  # 0.3 - 0.7: Multi-agent, mixed models
    COMPLEX = "complex"  # 0.7 - 1.0: Hierarchical, premium models


class QueryDomain(Enum):
    """Supported query domains for specialized routing."""

    RESEARCH = "research"
    CONTENT = "content"
    ANALYTICS = "analytics"
    SERVICE = "service"
    GENERAL = "general"
    MULTIMODAL = "multimodal"


@dataclass
class ComplexityFactors:
    """Individual complexity factors with weights."""

    linguistic_complexity: float = 0.0  # Word choice, sentence structure
    reasoning_depth: float = 0.0  # Required analytical thinking
    domain_breadth: float = 0.0  # Cross-domain requirements
    data_requirements: float = 0.0  # External data needed
    output_complexity: float = 0.0  # Expected output sophistication
    time_sensitivity: float = 0.0  # Urgency and latency requirements
    quality_requirements: float = 0.0  # Accuracy and validation needs


@dataclass
class ComplexityAnalysis:
    """Complete analysis of query complexity."""

    score: float  # Overall complexity (0.0 - 1.0)
    level: ComplexityLevel  # Categorized complexity level
    factors: ComplexityFactors  # Detailed factor breakdown
    domains: list[QueryDomain]  # Identified domains
    subtask_count: int = 1  # Estimated subtasks needed
    uncertainty: float = 0.0  # Confidence in analysis
    reasoning_types: list[str] = field(default_factory=list)
    recommended_agents: dict[str, int] = field(default_factory=dict)
    estimated_tokens: int = 1000  # Estimated token usage
    priority_level: str = "normal"  # Priority: low, normal, high, critical


class QueryComplexityAnalyzer:
    """
    Analyzes queries to determine optimal routing and resource allocation.

    Uses multiple heuristics and pattern matching to assess complexity
    and provide routing recommendations for the MASR system.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        """Initialize analyzer with configuration."""
        self.config = config or {}

        # Complexity weights for different factors
        self.weights = self.config.get(
            "complexity_weights",
            {
                "linguistic": 0.15,
                "reasoning": 0.25,
                "domain": 0.20,
                "data": 0.15,
                "output": 0.15,
                "time": 0.05,
                "quality": 0.05,
            },
        )

        # Domain keywords for classification
        self.domain_patterns = {
            QueryDomain.RESEARCH: [
                r"\b(?:research|study|analysis|literature|academic|paper|citation)\b",
                r"\b(?:methodology|hypothesis|evidence|peer.?review)\b",
                r"\b(?:compare|contrast|synthesize|meta.?analysis)\b",
            ],
            QueryDomain.CONTENT: [
                r"\b(?:create|write|generate|compose|draft|edit)\b",
                r"\b(?:article|blog|story|script|copy|content)\b",
                r"\b(?:style|tone|audience|brand|message)\b",
            ],
            QueryDomain.ANALYTICS: [
                r"\b(?:analyze|data|statistics|metrics|trends|insights)\b",
                r"\b(?:dashboard|visualization|chart|graph|report)\b",
                r"\b(?:performance|kpi|roi|revenue|growth)\b",
            ],
            QueryDomain.SERVICE: [
                r"\b(?:help|support|assist|customer|user|client)\b",
                r"\b(?:problem|issue|troubleshoot|resolve|fix)\b",
                r"\b(?:guide|tutorial|instructions|how.?to)\b",
            ],
            QueryDomain.MULTIMODAL: [
                r"\b(?:image|video|audio|visual|multimedia)\b",
                r"\b(?:picture|photo|diagram|chart|infographic)\b",
                r"\b(?:voice|speech|sound|music|design)\b",
            ],
        }

        # Reasoning complexity patterns
        self.reasoning_patterns = {
            "logical": r"\b(?:because|therefore|thus|hence|consequently|if.then)\b",
            "comparative": r"\b(?:compare|versus|vs|against|than|relative|contrast)\b",
            "analytical": r"\b(?:analyze|break.down|examine|investigate|explore)\b",
            "synthetic": r"\b(?:combine|integrate|synthesize|merge|unify|compile)\b",
            "evaluative": r"\b(?:evaluate|assess|judge|critique|review|rate)\b",
            "causal": r"\b(?:cause|effect|impact|influence|result|lead.to)\b",
        }

        # Priority indicators
        self.priority_patterns = {
            "critical": r"\b(?:urgent|critical|emergency|asap|immediately|now)\b",
            "high": r"\b(?:important|priority|soon|quickly|fast|rapid)\b",
            "low": r"\b(?:when.possible|eventually|low.priority|nice.to.have)\b",
        }

    async def analyze(
        self, query: str, context: dict[str, Any] | None = None
    ) -> ComplexityAnalysis:
        """
        Analyze query complexity and provide routing recommendations.

        Args:
            query: The input query to analyze
            context: Additional context (user history, session info, etc.)

        Returns:
            ComplexityAnalysis with routing recommendations
        """
        logger.info(f"Analyzing query complexity: {query[:100]}...")

        # Clean and prepare query
        cleaned_query = self._clean_query(query)

        # Analyze individual complexity factors
        factors = await self._analyze_factors(cleaned_query, context)

        # Calculate overall complexity score
        score = self._calculate_complexity_score(factors)

        # Determine complexity level
        level = self._determine_complexity_level(score)

        # Identify domains
        domains = self._identify_domains(cleaned_query)

        # Estimate subtasks and reasoning types
        subtask_count = self._estimate_subtasks(cleaned_query, domains)
        reasoning_types = self._identify_reasoning_types(cleaned_query)

        # Recommend agents based on analysis
        recommended_agents = self._recommend_agents(level, domains, reasoning_types)

        # Estimate token usage
        estimated_tokens = self._estimate_token_usage(
            cleaned_query, level, subtask_count
        )

        # Determine priority
        priority_level = self._determine_priority(cleaned_query, context)

        # Calculate uncertainty
        uncertainty = self._calculate_uncertainty(factors, domains)

        analysis = ComplexityAnalysis(
            score=score,
            level=level,
            factors=factors,
            domains=domains,
            subtask_count=subtask_count,
            uncertainty=uncertainty,
            reasoning_types=reasoning_types,
            recommended_agents=recommended_agents,
            estimated_tokens=estimated_tokens,
            priority_level=priority_level,
        )

        logger.info(
            f"Analysis complete: {level.value} complexity ({score:.2f}), "
            f"{len(domains)} domains, {subtask_count} subtasks"
        )

        return analysis

    def _clean_query(self, query: str) -> str:
        """Clean and normalize query text."""
        # Convert to lowercase for pattern matching
        cleaned = query.lower().strip()

        # Remove extra whitespace
        cleaned = re.sub(r"\s+", " ", cleaned)

        return cleaned

    async def _analyze_factors(
        self, query: str, context: dict[str, Any] | None = None
    ) -> ComplexityFactors:
        """Analyze individual complexity factors."""

        # Linguistic complexity based on word choice and structure
        linguistic = self._analyze_linguistic_complexity(query)

        # Reasoning depth based on analytical requirements
        reasoning = self._analyze_reasoning_depth(query)

        # Domain breadth based on cross-domain requirements
        domain = self._analyze_domain_breadth(query)

        # Data requirements based on external data needs
        data = self._analyze_data_requirements(query, context)

        # Output complexity based on expected sophistication
        output = self._analyze_output_complexity(query)

        # Time sensitivity from context and keywords
        time = self._analyze_time_sensitivity(query, context)

        # Quality requirements from domain and context
        quality = self._analyze_quality_requirements(query, context)

        return ComplexityFactors(
            linguistic_complexity=linguistic,
            reasoning_depth=reasoning,
            domain_breadth=domain,
            data_requirements=data,
            output_complexity=output,
            time_sensitivity=time,
            quality_requirements=quality,
        )

    def _analyze_linguistic_complexity(self, query: str) -> float:
        """Analyze linguistic complexity of the query."""
        complexity = 0.0

        # Query length factor
        word_count = len(query.split())
        if word_count > 100:
            complexity += 0.3
        elif word_count > 50:
            complexity += 0.2
        elif word_count > 20:
            complexity += 0.1

        # Sentence complexity
        sentence_count = len([s for s in query.split(".") if s.strip()])
        if sentence_count > 5:
            complexity += 0.2

        # Advanced vocabulary indicators
        advanced_words = [
            r"\b(?:methodology|epistemology|ontology|paradigm|heuristic)\b",
            r"\b(?:multifaceted|comprehensive|intricate|sophisticated)\b",
            r"\b(?:synthesize|aggregate|interpolate|extrapolate)\b",
        ]

        for pattern in advanced_words:
            if re.search(pattern, query):
                complexity += 0.1

        return min(complexity, 1.0)

    def _analyze_reasoning_depth(self, query: str) -> float:
        """Analyze required reasoning depth."""
        depth = 0.0

        # Look for reasoning indicators
        reasoning_indicators = [
            (r"\b(?:why|how|explain|understand|reason)\b", 0.3),
            (r"\b(?:analyze|evaluate|assess|critique)\b", 0.4),
            (r"\b(?:compare|contrast|relate|connect)\b", 0.3),
            (r"\b(?:predict|forecast|estimate|project)\b", 0.4),
            (r"\b(?:optimize|improve|enhance|refine)\b", 0.4),
            (r"\b(?:synthesize|integrate|combine|merge)\b", 0.5),
        ]

        for pattern, weight in reasoning_indicators:
            if re.search(pattern, query):
                depth += weight

        # Multi-step reasoning indicators
        if re.search(r"\b(?:first|then|next|finally|step.by.step)\b", query):
            depth += 0.2

        # Conditional reasoning
        if re.search(r"\b(?:if|unless|provided|assuming|given)\b", query):
            depth += 0.2

        return min(depth, 1.0)

    def _analyze_domain_breadth(self, query: str) -> float:
        """Analyze cross-domain requirements."""
        domains_found = self._identify_domains(query)

        # More domains = higher complexity
        domain_complexity = len(domains_found) * 0.2

        # Cross-domain indicators
        cross_domain_patterns = [
            r"\b(?:interdisciplinary|cross.domain|multi.domain)\b",
            r"\b(?:intersection|overlap|relationship.between)\b",
            r"\b(?:integrate|combine|merge|unify)\b",
        ]

        for pattern in cross_domain_patterns:
            if re.search(pattern, query):
                domain_complexity += 0.3

        return min(domain_complexity, 1.0)

    def _analyze_data_requirements(
        self, query: str, context: dict[str, Any] | None = None
    ) -> float:
        """Analyze external data requirements."""
        data_complexity = 0.0

        # Data source indicators
        data_patterns = [
            (r"\b(?:data|database|dataset|statistics|metrics)\b", 0.3),
            (r"\b(?:research|literature|papers|studies)\b", 0.4),
            (r"\b(?:real.time|current|latest|recent)\b", 0.3),
            (r"\b(?:historical|trend|time.series|longitudinal)\b", 0.4),
            (r"\b(?:api|integration|external|third.party)\b", 0.5),
        ]

        for pattern, weight in data_patterns:
            if re.search(pattern, query):
                data_complexity += weight

        return min(data_complexity, 1.0)

    def _analyze_output_complexity(self, query: str) -> float:
        """Analyze expected output complexity."""
        output_complexity = 0.0

        # Output format indicators
        output_patterns = [
            (r"\b(?:report|document|presentation|summary)\b", 0.3),
            (r"\b(?:detailed|comprehensive|thorough|extensive)\b", 0.3),
            (r"\b(?:chart|graph|visualization|diagram)\b", 0.4),
            (r"\b(?:code|implementation|solution|algorithm)\b", 0.4),
            (r"\b(?:recommendations|strategy|plan|roadmap)\b", 0.4),
        ]

        for pattern, weight in output_patterns:
            if re.search(pattern, query):
                output_complexity += weight

        return min(output_complexity, 1.0)

    def _analyze_time_sensitivity(
        self, query: str, context: dict[str, Any] | None = None
    ) -> float:
        """Analyze time sensitivity requirements."""
        time_sensitivity = 0.0

        # Priority patterns
        for priority, pattern in self.priority_patterns.items():
            if re.search(pattern, query):
                if priority == "critical":
                    time_sensitivity = 1.0
                elif priority == "high":
                    time_sensitivity = 0.7
                elif priority == "low":
                    time_sensitivity = 0.1

        # Context-based timing
        if context and context.get("deadline"):
            _deadline = context["deadline"]
            # Would implement deadline parsing logic here
            time_sensitivity = max(time_sensitivity, 0.5)

        return time_sensitivity

    def _analyze_quality_requirements(
        self, query: str, context: dict[str, Any] | None = None
    ) -> float:
        """Analyze quality and validation requirements."""
        quality = 0.0

        # Quality indicators
        quality_patterns = [
            (r"\b(?:accurate|precise|exact|verified)\b", 0.4),
            (r"\b(?:peer.reviewed|scholarly|academic)\b", 0.5),
            (r"\b(?:validate|verify|check|confirm)\b", 0.3),
            (r"\b(?:high.quality|professional|publication)\b", 0.4),
        ]

        for pattern, weight in quality_patterns:
            if re.search(pattern, query):
                quality += weight

        return min(quality, 1.0)

    def _calculate_complexity_score(self, factors: ComplexityFactors) -> float:
        """Calculate overall complexity score from individual factors."""
        score = (
            factors.linguistic_complexity * self.weights["linguistic"]
            + factors.reasoning_depth * self.weights["reasoning"]
            + factors.domain_breadth * self.weights["domain"]
            + factors.data_requirements * self.weights["data"]
            + factors.output_complexity * self.weights["output"]
            + factors.time_sensitivity * self.weights["time"]
            + factors.quality_requirements * self.weights["quality"]
        )

        return float(min(score, 1.0))

    def _determine_complexity_level(self, score: float) -> ComplexityLevel:
        """Determine complexity level from score."""
        if score < 0.3:
            return ComplexityLevel.SIMPLE
        elif score < 0.7:
            return ComplexityLevel.MODERATE
        else:
            return ComplexityLevel.COMPLEX

    def _identify_domains(self, query: str) -> list[QueryDomain]:
        """Identify relevant domains for the query."""
        domains = []

        for domain, patterns in self.domain_patterns.items():
            for pattern in patterns:
                if re.search(pattern, query):
                    if domain not in domains:
                        domains.append(domain)
                    break

        # Default to general if no specific domains found
        if not domains:
            domains = [QueryDomain.GENERAL]

        return domains

    def _identify_reasoning_types(self, query: str) -> list[str]:
        """Identify types of reasoning required."""
        reasoning_types = []

        for reasoning_type, pattern in self.reasoning_patterns.items():
            if re.search(pattern, query):
                reasoning_types.append(reasoning_type)

        return reasoning_types

    def _estimate_subtasks(self, query: str, domains: list[QueryDomain]) -> int:
        """Estimate number of subtasks required."""
        # Base subtasks
        subtask_count = 1

        # Add for multiple domains
        subtask_count += max(0, len(domains) - 1)

        # Add for complexity indicators
        if re.search(r"\b(?:first|then|next|step|phase)\b", query):
            subtask_count += 2

        if re.search(r"\b(?:compare|contrast|analyze|synthesize)\b", query):
            subtask_count += 1

        return min(subtask_count, 10)  # Cap at reasonable maximum

    def _recommend_agents(
        self,
        level: ComplexityLevel,
        domains: list[QueryDomain],
        reasoning_types: list[str],
    ) -> dict[str, int]:
        """Recommend agent allocation based on analysis."""
        agents = {}

        # Base agent recommendation by complexity
        if level == ComplexityLevel.SIMPLE:
            agents["primary"] = 1

        elif level == ComplexityLevel.MODERATE:
            agents["primary"] = 1
            agents["support"] = 2
            agents["validator"] = 1

        else:  # COMPLEX
            agents["supervisor"] = 1
            agents["specialist"] = len(domains)
            agents["validator"] = 2
            agents["synthesizer"] = 1

        # Add domain-specific agents
        for domain in domains:
            if domain == QueryDomain.RESEARCH:
                agents["literature_agent"] = agents.get("literature_agent", 0) + 1
                agents["citation_agent"] = agents.get("citation_agent", 0) + 1
            elif domain == QueryDomain.ANALYTICS:
                agents["analytics_agent"] = agents.get("analytics_agent", 0) + 1
            elif domain == QueryDomain.CONTENT:
                agents["content_agent"] = agents.get("content_agent", 0) + 1

        return agents

    def _estimate_token_usage(
        self, query: str, level: ComplexityLevel, subtask_count: int
    ) -> int:
        """Estimate token usage for the query."""
        # Base token estimate
        base_tokens = len(query.split()) * 1.3  # Account for tokenization

        # Multiply by complexity and subtask factors
        complexity_multiplier = {
            ComplexityLevel.SIMPLE: 50,
            ComplexityLevel.MODERATE: 200,
            ComplexityLevel.COMPLEX: 500,
        }

        estimated_tokens = int(
            base_tokens + (complexity_multiplier[level] * subtask_count)
        )

        return estimated_tokens

    def _determine_priority(self, query: str, context: dict[str, Any] | None = None) -> str:
        """Determine query priority level."""
        for priority, pattern in self.priority_patterns.items():
            if re.search(pattern, query):
                return priority

        # Check context for priority
        if context and context.get("priority"):
            return str(context["priority"])

        return "normal"

    def _calculate_uncertainty(
        self, factors: ComplexityFactors, domains: list[QueryDomain]
    ) -> float:
        """Calculate uncertainty in the analysis."""
        uncertainty = 0.0

        # Higher uncertainty for edge cases
        if factors.reasoning_depth > 0.8:
            uncertainty += 0.2

        if len(domains) > 3:
            uncertainty += 0.2

        if QueryDomain.GENERAL in domains and len(domains) == 1:
            uncertainty += 0.3  # Couldn't classify domain well

        return min(uncertainty, 1.0)


__all__ = [
    "ComplexityAnalysis",
    "ComplexityFactors",
    "ComplexityLevel",
    "QueryComplexityAnalyzer",
    "QueryDomain",
]
