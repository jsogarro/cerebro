"""
Query analysis node for decomposing and understanding research queries.

This node analyzes the user's research query to extract key concepts,
identify research domains, and determine the appropriate research approach.
"""

import logging
from typing import Any

from src.core.pii_redactor import redact_pii
from src.orchestration.state import ResearchState
from src.services.gemini_service import GeminiService

logger = logging.getLogger(__name__)


async def query_analysis_node(state: ResearchState) -> ResearchState:
    """
    Analyze the research query to extract key information.

    This node:
    1. Extracts key concepts and entities from the query
    2. Identifies relevant research domains
    3. Determines query complexity and scope
    4. Suggests appropriate research methodology

    Args:
        state: Current workflow state

    Returns:
        Updated state with query analysis results
    """
    logger.info("Analyzing query: %s", redact_pii(state.query))

    try:
        # Extract key concepts from query
        concepts = extract_key_concepts(state.query)

        # Identify research domains if not provided
        if not state.domains:
            state.domains = identify_domains(state.query, concepts)

        # Determine query complexity
        complexity = assess_query_complexity(state.query, concepts)

        # Suggest research approach
        research_approach = determine_research_approach(complexity, state.domains)

        # Update state context with analysis results
        state.context["query_analysis"] = {
            "key_concepts": concepts,
            "domains": state.domains,
            "complexity": complexity,
            "research_approach": research_approach,
            "query_type": classify_query_type(state.query),
            "scope": determine_scope(state.query),
        }

        # If we have Gemini integration, use it for deeper analysis
        if hasattr(state, "_gemini_service"):
            enhanced_analysis = await analyze_with_gemini(
                state.query, state._gemini_service
            )
            state.context["query_analysis"].update(enhanced_analysis)

        logger.info(
            f"Query analysis complete. Identified {len(concepts)} key concepts in {len(state.domains)} domains"
        )

    except Exception as e:
        logger.error(f"Error in query analysis: {e}")
        state.validation_errors.append(f"Query analysis failed: {e!s}")

    return state


def extract_key_concepts(query: str) -> list[str]:
    """
    Extract key concepts and entities from the research query.

    Args:
        query: Research query text

    Returns:
        List of key concepts
    """
    # Simple keyword extraction (in production, use NLP libraries)
    # Remove common words and extract significant terms
    stop_words = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "up",
        "about",
        "into",
        "through",
        "during",
        "how",
        "what",
        "when",
        "where",
        "why",
        "which",
        "who",
        "whom",
        "whose",
        "is",
        "are",
        "was",
        "were",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
    }

    # Tokenize and filter
    words = query.lower().split()
    concepts = [
        word.strip(".,!?;:")
        for word in words
        if word.lower() not in stop_words and len(word) > 2
    ]

    # Remove duplicates while preserving order
    seen = set()
    unique_concepts = []
    for concept in concepts:
        if concept not in seen:
            seen.add(concept)
            unique_concepts.append(concept)

    return unique_concepts


def identify_domains(query: str, concepts: list[str]) -> list[str]:
    """
    Identify research domains based on query and concepts.

    Args:
        query: Research query
        concepts: Extracted concepts

    Returns:
        List of relevant domains
    """
    # Domain keywords mapping
    domain_keywords = {
        "AI": [
            "artificial intelligence",
            "ai",
            "machine learning",
            "deep learning",
            "neural",
            "algorithm",
            "model",
            "training",
            "classification",
        ],
        "Healthcare": [
            "health",
            "medical",
            "disease",
            "treatment",
            "patient",
            "clinical",
            "therapy",
            "diagnosis",
            "medicine",
            "hospital",
        ],
        "Technology": [
            "technology",
            "software",
            "hardware",
            "computer",
            "system",
            "digital",
            "internet",
            "web",
            "application",
            "platform",
        ],
        "Science": [
            "science",
            "research",
            "experiment",
            "hypothesis",
            "theory",
            "analysis",
            "data",
            "study",
            "observation",
            "evidence",
        ],
        "Economics": [
            "economic",
            "economy",
            "market",
            "finance",
            "business",
            "trade",
            "investment",
            "money",
            "cost",
            "price",
        ],
        "Environment": [
            "environment",
            "climate",
            "sustainability",
            "green",
            "ecology",
            "pollution",
            "conservation",
            "renewable",
        ],
        "Education": [
            "education",
            "learning",
            "teaching",
            "student",
            "school",
            "university",
            "curriculum",
            "pedagogy",
            "academic",
        ],
        "Psychology": [
            "psychology",
            "behavior",
            "cognitive",
            "mental",
            "emotion",
            "personality",
            "perception",
            "motivation",
            "consciousness",
        ],
        "Social": [
            "social",
            "society",
            "community",
            "culture",
            "relationship",
            "communication",
            "interaction",
            "network",
            "group",
        ],
    }

    query_lower = query.lower()
    all_text = query_lower + " " + " ".join(concepts).lower()

    identified_domains = []

    for domain, keywords in domain_keywords.items():
        if any(keyword in all_text for keyword in keywords):
            identified_domains.append(domain)

    # Default to Science if no specific domain identified
    if not identified_domains:
        identified_domains = ["Science"]

    return identified_domains[:3]  # Limit to top 3 domains


def assess_query_complexity(query: str, concepts: list[str]) -> str:
    """
    Assess the complexity level of the research query.

    Args:
        query: Research query
        concepts: Key concepts

    Returns:
        Complexity level: "simple", "moderate", or "complex"
    """
    # Factors for complexity assessment
    word_count = len(query.split())
    concept_count = len(concepts)
    has_comparison = any(
        word in query.lower() for word in ["compare", "versus", "vs", "difference"]
    )
    has_multiple_aspects = any(
        word in query.lower() for word in ["and", "multiple", "various", "several"]
    )
    has_temporal = any(
        word in query.lower()
        for word in ["evolution", "history", "future", "trend", "over time"]
    )

    complexity_score = 0

    # Score based on various factors
    if word_count > 20:
        complexity_score += 2
    elif word_count > 10:
        complexity_score += 1

    if concept_count > 5:
        complexity_score += 2
    elif concept_count > 3:
        complexity_score += 1

    if has_comparison:
        complexity_score += 1

    if has_multiple_aspects:
        complexity_score += 1

    if has_temporal:
        complexity_score += 1

    # Determine complexity level
    if complexity_score >= 5:
        return "complex"
    elif complexity_score >= 3:
        return "moderate"
    else:
        return "simple"


def determine_research_approach(complexity: str, domains: list[str]) -> str:
    """
    Determine the appropriate research approach based on complexity and domains.

    Args:
        complexity: Query complexity level
        domains: Research domains

    Returns:
        Research approach type
    """
    if complexity == "complex":
        return "comprehensive"
    elif complexity == "moderate":
        if "Science" in domains or "Healthcare" in domains:
            return "systematic_review"
        else:
            return "comparative_analysis"
    else:
        return "focused_review"


def classify_query_type(query: str) -> str:
    """
    Classify the type of research query.

    Args:
        query: Research query

    Returns:
        Query type classification
    """
    query_lower = query.lower()

    if any(word in query_lower for word in ["how", "why", "what causes"]):
        return "explanatory"
    elif any(word in query_lower for word in ["compare", "difference", "versus"]):
        return "comparative"
    elif any(word in query_lower for word in ["effect", "impact", "influence"]):
        return "causal"
    elif any(word in query_lower for word in ["trend", "pattern", "correlation"]):
        return "analytical"
    elif any(word in query_lower for word in ["describe", "what is", "define"]):
        return "descriptive"
    else:
        return "exploratory"


def determine_scope(query: str) -> str:
    """
    Determine the scope of the research query.

    Args:
        query: Research query

    Returns:
        Scope classification: "narrow", "moderate", or "broad"
    """
    # Check for scope indicators
    broad_indicators = [
        "comprehensive",
        "all",
        "entire",
        "global",
        "general",
        "overall",
    ]
    narrow_indicators = ["specific", "particular", "single", "focused", "limited"]

    query_lower = query.lower()

    if any(indicator in query_lower for indicator in broad_indicators):
        return "broad"
    elif any(indicator in query_lower for indicator in narrow_indicators):
        return "narrow"
    else:
        return "moderate"


async def analyze_with_gemini(
    query: str, gemini_service: GeminiService
) -> dict[str, Any]:
    """
    Use Gemini for enhanced query analysis.

    Args:
        query: Research query
        gemini_service: Gemini service instance

    Returns:
        Enhanced analysis results
    """
    try:
        prompt = f"""
        Analyze the following research query and provide:
        1. Main research question
        2. Sub-questions that need to be answered
        3. Key hypotheses to explore
        4. Potential challenges in researching this topic
        5. Suggested data sources
        
        Query: {query}
        """

        response = await gemini_service._generate_content(prompt)

        # Parse response (simplified - actual implementation would parse structured output)
        return {"enhanced_analysis": response, "gemini_processed": True}

    except Exception as e:
        logger.warning(f"Gemini analysis failed: {e}")
        return {"gemini_processed": False}


__all__ = ["query_analysis_node"]
