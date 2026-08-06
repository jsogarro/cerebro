"""
MCP Tools for research platform.

This module contains specialized tools for academic research,
citation management, statistical analysis, and knowledge graphs.

Exports are resolved lazily. Importing them eagerly coupled three
network-calling tools to `scipy`, which only `StatisticsTool` uses: because
Python executes a package's ``__init__.py`` before any of its submodules,
``from src.mcp.tools.citation_tool import CitationTool`` required `scipy` even
though `citation_tool.py` never imports it. The consequence was not a slow
import — it was that the tests covering those three tools could not run in an
environment without the optional `[stats]` extra, which is the environment the
gate runs in.

The same shape as `src/mcp/__init__.py`, which already defers `MCPServer` for
its own optional `fastmcp` dependency.
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
