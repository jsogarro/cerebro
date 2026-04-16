"""
Citation Tool for MCP.

Provides citation formatting and DOI resolution capabilities.
"""

import logging
from typing import Any

import httpx

from src.mcp.base import BaseMCPTool, ToolMetadata, ToolParameter

logger = logging.getLogger(__name__)


class CitationTool(BaseMCPTool):
    """
    MCP tool for citation formatting and management.

    Supports APA, MLA, Chicago citation styles and DOI resolution.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        """Initialize citation tool."""
        super().__init__(config)
        self.client = httpx.AsyncClient(timeout=30.0)
        self.crossref_base = "https://api.crossref.org/works"

    def _build_metadata(self) -> ToolMetadata:
        """Build tool metadata."""
        return ToolMetadata(
            name="citation_formatter",
            description="Format citations and resolve DOIs",
            version="1.0.0",
            parameters=[
                ToolParameter(
                    name="sources",
                    type="array",
                    description="List of sources to format",
                    required=False,
                ),
                ToolParameter(
                    name="style",
                    type="string",
                    description="Citation style (APA, MLA, Chicago)",
                    required=False,
                    default="APA",
                ),
                ToolParameter(
                    name="doi",
                    type="string",
                    description="DOI to resolve",
                    required=False,
                ),
                ToolParameter(
                    name="format",
                    type="string",
                    description="Output format (text, bibtex)",
                    required=False,
                    default="text",
                ),
            ],
            tags=["citation", "formatting", "doi", "bibliography"],
        )

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        """
        Execute citation operation.

        Args:
            **kwargs: Citation parameters

        Returns:
            Formatted citations or resolved DOI
        """
        try:
            # Handle DOI resolution
            if "doi" in kwargs:
                return await self._resolve_doi(
                    kwargs["doi"], kwargs.get("style", "APA")
                )

            # Handle citation formatting
            sources = kwargs.get("sources", [])
            style = kwargs.get("style", "APA")
            output_format = kwargs.get("format", "text")

            if not sources:
                return {"success": False, "error": "No sources provided"}

            if output_format == "bibtex":
                return self._format_bibtex(sources)
            else:
                return self._format_citations(sources, style)

        except Exception as e:
            logger.error(f"Citation operation failed: {e!s}")
            return {"success": False, "error": str(e)}

    def _format_citations(self, sources: list[dict[str, Any]], style: str) -> dict[str, Any]:
        """Format citations in specified style."""
        citations = []

        for source in sources:
            if style == "APA":
                citation = self._format_apa(source)
            elif style == "MLA":
                citation = self._format_mla(source)
            elif style == "Chicago":
                citation = self._format_chicago(source)
            else:
                citation = self._format_apa(source)  # Default to APA

            citations.append(citation)

        return {
            "success": True,
            "citations": citations,
            "style": style,
            "count": len(citations),
        }

    def _format_apa(self, source: dict[str, Any]) -> str:
        """Format citation in APA style."""
        authors = source.get("authors", [])
        year = source.get("year", "n.d.")
        title = source.get("title", "Untitled")
        journal = source.get("journal", "")
        volume = source.get("volume", "")
        pages = source.get("pages", "")

        # Format authors
        if authors:
            if len(authors) == 1:
                author_str = authors[0]
            elif len(authors) == 2:
                author_str = f"{authors[0]} & {authors[1]}"
            else:
                author_str = f"{authors[0]} et al."
        else:
            author_str = "Unknown"

        # Build citation
        citation = f"{author_str} ({year}). {title}."
        if journal:
            citation += f" {journal}"
            if volume:
                citation += f", {volume}"
            if pages:
                citation += f", {pages}"
        citation += "."

        return citation

    def _format_mla(self, source: dict[str, Any]) -> str:
        """Format citation in MLA style."""
        authors = source.get("authors", [])
        title = source.get("title", "Untitled")
        publisher = source.get("publisher", "")
        year = source.get("year", "")

        # Format authors
        if authors:
            author_str = authors[0] if len(authors) == 1 else f"{authors[0]}, et al"
        else:
            author_str = "Unknown"

        # Build citation
        citation = f'{author_str}. "{title}."'
        if publisher:
            citation += f" {publisher},"
        if year:
            citation += f" {year}"
        citation += "."

        return citation

    def _format_chicago(self, source: dict[str, Any]) -> str:
        """Format citation in Chicago style."""
        authors = source.get("authors", [])
        title = source.get("title", "Untitled")
        year = source.get("year", "")

        # Format authors
        if authors:
            author_str = ", ".join(authors)
        else:
            author_str = "Unknown"

        # Build citation
        citation = f'{author_str}. "{title}."'
        if year:
            citation += f" ({year})"
        citation += "."

        return citation

    def _format_bibtex(self, sources: list[dict[str, Any]]) -> dict[str, Any]:
        """Format sources as BibTeX."""
        bibtex_entries = []

        for i, source in enumerate(sources):
            entry_type = "@article" if source.get("journal") else "@book"
            key = f"ref{i+1}"

            entry = f"{entry_type}{{{key},\n"
            if source.get("title"):
                entry += f'  title = "{source["title"]}",\n'
            if source.get("authors"):
                entry += f'  author = "{" and ".join(source["authors"])}",\n'
            if source.get("year"):
                entry += f'  year = {source["year"]},\n'
            if source.get("journal"):
                entry += f'  journal = "{source["journal"]}",\n'
            if source.get("volume"):
                entry += f'  volume = {source["volume"]},\n'
            if source.get("pages"):
                entry += f'  pages = "{source["pages"]}",\n'
            entry += "}"

            bibtex_entries.append(entry)

        return {
            "success": True,
            "bibtex": "\n\n".join(bibtex_entries),
            "count": len(bibtex_entries),
        }

    async def _resolve_doi(self, doi: str, style: str) -> dict[str, Any]:
        """Resolve DOI and format citation."""
        try:
            url = f"{self.crossref_base}/{doi}"
            response = await self.client.get(url)

            if response.status_code != 200:
                return {"success": False, "error": f"DOI not found: {doi}"}

            data = response.json()
            message = data.get("message", {})

            # Extract metadata
            source = {
                "title": message.get("title", [""])[0],
                "authors": [
                    f"{a.get('family', '')}, {a.get('given', '')}"
                    for a in message.get("author", [])
                ],
                "year": message.get("published-print", {}).get("date-parts", [[None]])[
                    0
                ][0],
                "journal": message.get("container-title", [""])[0],
                "volume": message.get("volume", ""),
                "pages": message.get("page", ""),
                "doi": doi,
            }

            # Format citation
            if style == "APA":
                citation = self._format_apa(source)
            elif style == "MLA":
                citation = self._format_mla(source)
            else:
                citation = self._format_chicago(source)

            return {
                "success": True,
                "citation": citation,
                "metadata": source,
                "doi": doi,
            }

        except Exception as e:
            logger.error(f"DOI resolution failed: {e!s}")
            return {"success": False, "error": str(e)}

    def __del__(self) -> None:
        """Cleanup HTTP client."""
        # Note: We can't use async in __del__, so client cleanup
        # should be handled explicitly in production code
