"""
Query Decomposer for Multi-Domain Queries

Decomposes complex queries that span multiple domains into domain-specific
sub-queries. This enables intelligent routing where a single query like
"Analyze AI impact on healthcare costs and write a report" can be broken
into research + analytics + content sub-queries.

Extracted from the legacy orchestration subsystem and integrated into
the active MASR/query-analysis path.
"""

from typing import Any


class QueryDecomposition:
    """Results of query decomposition."""

    def __init__(
        self,
        detected_domains: list[str],
        domain_relevance: dict[str, float],
        domain_subqueries: dict[str, str],
        cross_domain_dependencies: list[tuple[str, str]],
        coordination_complexity: int,
    ):
        """
        Initialize decomposition results.

        Args:
            detected_domains: List of domains detected in the query
            domain_relevance: Relevance score (0-1) for each domain
            domain_subqueries: Domain-specific sub-query for each domain
            cross_domain_dependencies: Pairs of (source, target) dependencies
            coordination_complexity: Measure of cross-domain complexity
        """
        self.detected_domains = detected_domains
        self.domain_relevance = domain_relevance
        self.domain_subqueries = domain_subqueries
        self.cross_domain_dependencies = cross_domain_dependencies
        self.coordination_complexity = coordination_complexity

    @property
    def is_multi_domain(self) -> bool:
        """Check if query spans multiple domains."""
        return len(self.detected_domains) > 1

    @property
    def primary_domain(self) -> str | None:
        """Get the primary (highest relevance) domain."""
        if not self.domain_relevance:
            return None
        return max(self.domain_relevance.items(), key=lambda x: x[1])[0]


class QueryDecomposer:
    """Decomposes complex queries into domain-specific sub-queries."""

    def __init__(self, config: dict[str, Any] | None = None):
        """Initialize query decomposer."""
        self.config = config or {}

        # Domain detection patterns (simplified for demo)
        self.domain_patterns = {
            "research": ["analyze", "study", "research", "literature", "findings"],
            "content": ["write", "create", "generate", "content", "article"],
            "analytics": ["data", "statistics", "metrics", "analysis", "trends"],
            "service": ["support", "help", "customer", "assistance", "service"],
            "finance": ["valuation", "dcf", "balance sheet", "portfolio", "invest"],
        }

    def decompose_query(self, query: str) -> QueryDecomposition:
        """
        Decompose complex query into domain-specific components.

        Args:
            query: Original complex query

        Returns:
            QueryDecomposition with domain assignments and sub-queries
        """

        query_lower = query.lower()

        # Detect domains
        detected_domains = []
        domain_relevance = {}

        for domain, patterns in self.domain_patterns.items():
            matches = sum(1 for pattern in patterns if pattern in query_lower)
            if matches > 0:
                detected_domains.append(domain)
                domain_relevance[domain] = matches / len(patterns)

        # Create domain-specific sub-queries (simplified)
        domain_subqueries = {}
        for domain in detected_domains:
            if domain == "research":
                domain_subqueries[domain] = f"Research and analyze: {query}"
            elif domain == "content":
                domain_subqueries[domain] = f"Create content for: {query}"
            elif domain == "analytics":
                domain_subqueries[domain] = (
                    f"Analyze data and trends related to: {query}"
                )
            elif domain == "service":
                domain_subqueries[domain] = f"Provide service guidance for: {query}"
            elif domain == "finance":
                domain_subqueries[domain] = f"Financial analysis for: {query}"

        # Determine dependencies
        dependencies = []
        if "research" in detected_domains and "content" in detected_domains:
            dependencies.append(("research", "content"))  # Content depends on research

        return QueryDecomposition(
            detected_domains=detected_domains,
            domain_relevance=domain_relevance,
            domain_subqueries=domain_subqueries,
            cross_domain_dependencies=dependencies,
            coordination_complexity=len(detected_domains) * len(dependencies),
        )


__all__ = ["QueryDecomposer", "QueryDecomposition"]
