"""Research tools for academic search, citations, statistics, and knowledge graphs.

Exports are resolved lazily so importing the package or a network-calling tool
submodule does not eagerly import ``scipy`` from ``statistics_tool``.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.mcp.tools.academic_search_tool import AcademicSearchTool
    from src.mcp.tools.citation_tool import CitationTool
    from src.mcp.tools.knowledge_graph_tool import KnowledgeGraphTool
    from src.mcp.tools.statistics_tool import StatisticsTool

_MODULES = {
    "AcademicSearchTool": "academic_search_tool",
    "CitationTool": "citation_tool",
    "KnowledgeGraphTool": "knowledge_graph_tool",
    "StatisticsTool": "statistics_tool",
}


def __getattr__(name: str) -> Any:
    module_name = _MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = __import__(f"src.mcp.tools.{module_name}", fromlist=[name])
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(__all__)


__all__ = [
    "AcademicSearchTool",
    "CitationTool",
    "KnowledgeGraphTool",
    "StatisticsTool",
]
