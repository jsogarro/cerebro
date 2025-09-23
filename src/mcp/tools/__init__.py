"""
MCP Tools for research platform.

This module contains specialized tools for academic research,
citation management, statistical analysis, and knowledge graphs.
"""

from src.mcp.tools.academic_search_tool import AcademicSearchTool
from src.mcp.tools.citation_tool import CitationTool
from src.mcp.tools.knowledge_graph_tool import KnowledgeGraphTool
from src.mcp.tools.statistics_tool import StatisticsTool

__all__ = [
    "AcademicSearchTool",
    "CitationTool",
    "KnowledgeGraphTool",
    "StatisticsTool",
]
