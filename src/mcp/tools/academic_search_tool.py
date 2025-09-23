"""
Academic Search Tool for MCP.

Provides search capabilities across multiple academic databases.
"""

import logging
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from src.mcp.base import BaseMCPTool, ToolMetadata, ToolParameter

logger = logging.getLogger(__name__)


class AcademicSearchTool(BaseMCPTool):
    """
    MCP tool for searching academic databases.

    Supports PubMed, arXiv, and Semantic Scholar searches.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        """Initialize academic search tool."""
        super().__init__(config)
        self.client = httpx.AsyncClient(timeout=30.0)

        # API endpoints
        self.pubmed_base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
        self.arxiv_base = "http://export.arxiv.org/api/query"
        self.semantic_scholar_base = "https://api.semanticscholar.org/graph/v1"

    def _build_metadata(self) -> ToolMetadata:
        """Build tool metadata."""
        return ToolMetadata(
            name="search_academic",
            description="Search academic databases for research papers",
            version="1.0.0",
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="Search query",
                    required=True,
                ),
                ToolParameter(
                    name="databases",
                    type="array",
                    description="List of databases to search (pubmed, arxiv, semantic_scholar)",
                    required=False,
                    default=["arxiv"],
                ),
                ToolParameter(
                    name="max_results",
                    type="integer",
                    description="Maximum number of results",
                    required=False,
                    default=10,
                ),
                ToolParameter(
                    name="filters",
                    type="object",
                    description="Additional filters (year_start, year_end, categories)",
                    required=False,
                    default={},
                ),
            ],
            examples=[
                {
                    "query": "machine learning healthcare",
                    "databases": ["pubmed", "arxiv"],
                    "max_results": 20,
                }
            ],
            tags=["academic", "search", "research", "papers"],
        )

    async def execute(self, **kwargs) -> dict[str, Any]:
        """
        Execute academic search.

        Args:
            **kwargs: Search parameters

        Returns:
            Search results
        """
        try:
            query = kwargs.get("query", "")
            databases = kwargs.get("databases", ["arxiv"])
            max_results = kwargs.get("max_results", 10)
            filters = kwargs.get("filters", {})

            if not query:
                return {"success": False, "error": "Query is required"}

            # Validate databases
            valid_databases = ["pubmed", "arxiv", "semantic_scholar"]
            invalid_dbs = [db for db in databases if db not in valid_databases]
            if invalid_dbs:
                return {"success": False, "error": f"Invalid databases: {invalid_dbs}"}

            # Search each database
            all_results = []
            sources_used = []

            for db in databases:
                if db == "pubmed":
                    results = await self._search_pubmed(query, max_results, filters)
                elif db == "arxiv":
                    results = await self._search_arxiv(query, max_results, filters)
                elif db == "semantic_scholar":
                    results = await self._search_semantic_scholar(
                        query, max_results, filters
                    )
                else:
                    continue

                all_results.extend(results)
                sources_used.append(db)

            # Return aggregated results
            if len(databases) == 1:
                return {
                    "success": True,
                    "results": all_results,
                    "source": databases[0],
                    "query": query,
                    "total": len(all_results),
                }
            else:
                return {
                    "success": True,
                    "results": all_results,
                    "sources": sources_used,
                    "query": query,
                    "total": len(all_results),
                }

        except Exception as e:
            logger.error(f"Academic search failed: {e!s}")
            return {"success": False, "error": str(e)}

    async def _search_pubmed(
        self, query: str, max_results: int, filters: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """
        Search PubMed database.

        Args:
            query: Search query
            max_results: Maximum results
            filters: Search filters

        Returns:
            List of results
        """
        try:
            # Build query with filters
            search_query = query
            if filters.get("year_start"):
                search_query += f" AND {filters['year_start']}[pdat]"
            if filters.get("year_end"):
                search_query += f" : {filters['year_end']}[pdat]"

            # Search for IDs
            search_url = f"{self.pubmed_base}/esearch.fcgi"
            search_params = {
                "db": "pubmed",
                "term": search_query,
                "retmode": "json",
                "retmax": max_results,
            }

            response = await self.client.get(search_url, params=search_params)
            if response.status_code != 200:
                logger.error(f"PubMed search failed: {response.status_code}")
                return []

            search_data = response.json()
            id_list = search_data.get("esearchresult", {}).get("idlist", [])

            if not id_list:
                return []

            # Fetch summaries
            summary_url = f"{self.pubmed_base}/esummary.fcgi"
            summary_params = {
                "db": "pubmed",
                "id": ",".join(id_list),
                "retmode": "json",
            }

            response = await self.client.get(summary_url, params=summary_params)
            if response.status_code != 200:
                return []

            summary_data = response.json()
            results = []

            for pmid in id_list:
                if pmid in summary_data.get("result", {}):
                    article = summary_data["result"][pmid]
                    results.append(
                        {
                            "id": pmid,
                            "title": article.get("title", ""),
                            "authors": [
                                author.get("name", "")
                                for author in article.get("authors", [])
                            ],
                            "abstract": article.get("abstract", ""),
                            "year": (
                                article.get("pubdate", "").split()[0]
                                if article.get("pubdate")
                                else ""
                            ),
                            "journal": article.get("source", ""),
                            "doi": article.get("doi", ""),
                            "source": "pubmed",
                        }
                    )

            return results

        except Exception as e:
            logger.error(f"PubMed search error: {e!s}")
            return []

    async def _search_arxiv(
        self, query: str, max_results: int, filters: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """
        Search arXiv database.

        Args:
            query: Search query
            max_results: Maximum results
            filters: Search filters

        Returns:
            List of results
        """
        try:
            # Build query parameters
            params = {
                "search_query": f"all:{query}",
                "start": 0,
                "max_results": max_results,
                "sortBy": "relevance",
                "sortOrder": "descending",
            }

            # Add category filter
            if filters.get("categories"):
                cat_query = " OR ".join([f"cat:{cat}" for cat in filters["categories"]])
                params["search_query"] += f" AND ({cat_query})"

            response = await self.client.get(self.arxiv_base, params=params)
            if response.status_code != 200:
                logger.error(f"arXiv search failed: {response.status_code}")
                return []

            # Parse XML response
            root = ET.fromstring(response.text)
            ns = {"atom": "http://www.w3.org/2005/Atom"}

            results = []
            for entry in root.findall("atom:entry", ns):
                # Extract authors
                authors = []
                for author in entry.findall("atom:author", ns):
                    name = author.find("atom:name", ns)
                    if name is not None:
                        authors.append(name.text)

                # Extract ID
                entry_id = entry.find("atom:id", ns)
                arxiv_id = entry_id.text.split("/")[-1] if entry_id is not None else ""

                # Extract publication date
                published = entry.find("atom:published", ns)
                year = published.text[:4] if published is not None else ""

                results.append(
                    {
                        "id": arxiv_id,
                        "title": (
                            entry.find("atom:title", ns).text.strip()
                            if entry.find("atom:title", ns) is not None
                            else ""
                        ),
                        "authors": authors,
                        "abstract": (
                            entry.find("atom:summary", ns).text.strip()
                            if entry.find("atom:summary", ns) is not None
                            else ""
                        ),
                        "year": year,
                        "url": entry_id.text if entry_id is not None else "",
                        "source": "arxiv",
                    }
                )

            return results

        except Exception as e:
            logger.error(f"arXiv search error: {e!s}")
            return []

    async def _search_semantic_scholar(
        self, query: str, max_results: int, filters: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """
        Search Semantic Scholar database.

        Args:
            query: Search query
            max_results: Maximum results
            filters: Search filters

        Returns:
            List of results
        """
        try:
            # Search papers
            search_url = f"{self.semantic_scholar_base}/paper/search"
            params = {
                "query": query,
                "limit": max_results,
                "fields": "paperId,title,abstract,authors,year,venue,citationCount",
            }

            # Add year filter
            if filters.get("year_start") or filters.get("year_end"):
                year_filter = ""
                if filters.get("year_start"):
                    year_filter = f"{filters['year_start']}-"
                if filters.get("year_end"):
                    year_filter += str(filters["year_end"])
                else:
                    year_filter += "2024"
                params["year"] = year_filter

            response = await self.client.get(search_url, params=params)
            if response.status_code != 200:
                logger.error(f"Semantic Scholar search failed: {response.status_code}")
                return []

            data = response.json()
            results = []

            for paper in data.get("data", []):
                results.append(
                    {
                        "id": paper.get("paperId", ""),
                        "title": paper.get("title", ""),
                        "authors": [
                            author.get("name", "")
                            for author in paper.get("authors", [])
                        ],
                        "abstract": paper.get("abstract", ""),
                        "year": str(paper.get("year", "")),
                        "venue": paper.get("venue", ""),
                        "citations": paper.get("citationCount", 0),
                        "source": "semantic_scholar",
                    }
                )

            return results

        except Exception as e:
            logger.error(f"Semantic Scholar search error: {e!s}")
            return []

    def __del__(self):
        """Cleanup HTTP client."""
        # Note: We can't use async in __del__, so client cleanup
        # should be handled explicitly in production code
