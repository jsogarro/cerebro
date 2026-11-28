"""
Academic Search Tool for MCP.

Provides search capabilities across multiple academic databases.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any, Final
from xml.etree import ElementTree as ET

import httpx
from structlog import get_logger

from src.core.tools.outbound import (
    ACADEMIC_SEARCH_DATABASE_HOSTS,
    ACADEMIC_SEARCH_OUTBOUND_HOSTS,
    ARXIV_HOST,
    PUBMED_HOST,
    SEMANTIC_SCHOLAR_HOST,
    OutboundHostNotAllowedError,
    checked_get,
    require_allowed_outbound_host,
)
from src.mcp.academic_filters import AcademicSearchFilters

logger = get_logger()


def _year(value: Any) -> int | None:
    """The four-digit year a provider reported, or ``None`` when it did not."""

    match = re.match(r"\s*(\d{4})", str(value or ""))
    return int(match.group(1)) if match else None


def _compact(record: dict[str, Any]) -> dict[str, Any]:
    """Drop unknown identity fields so absence is absence, not an empty string."""

    return {k: v for k, v in record.items() if v is not None}


class AcademicSearchTool:
    """Search PubMed, arXiv, and Semantic Scholar."""

    DATABASE_HOSTS: Final[Mapping[str, str]] = ACADEMIC_SEARCH_DATABASE_HOSTS
    OUTBOUND_HOSTS: Final[tuple[str, ...]] = ACADEMIC_SEARCH_OUTBOUND_HOSTS

    @classmethod
    def target_hosts_for_databases(cls, databases: Sequence[str]) -> tuple[str, ...]:
        """Return the sorted pinned outbound hosts for the requested databases."""
        if not databases:
            return cls.OUTBOUND_HOSTS
        return tuple(
            sorted(
                {cls.DATABASE_HOSTS[db] for db in databases if db in cls.DATABASE_HOSTS}
            )
        )

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.client = httpx.AsyncClient(timeout=30.0, follow_redirects=False)

        self.pubmed_base = f"https://{PUBMED_HOST}/entrez/eutils"
        self.arxiv_base = f"https://{ARXIV_HOST}/api/query"
        self.semantic_scholar_base = f"https://{SEMANTIC_SCHOLAR_HOST}/graph/v1"

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        query = kwargs.get("query", "")
        databases = kwargs.get("databases", ["arxiv"])
        max_results = kwargs.get("max_results", 10)
        allowed_hosts: Sequence[str] | None = kwargs.get("allowed_hosts")

        if not query:
            return {"success": False, "error": "Query is required"}

        invalid_dbs = [db for db in databases if db not in self.DATABASE_HOSTS]
        if invalid_dbs:
            return {"success": False, "error": f"Invalid databases: {invalid_dbs}"}

        try:
            filters = AcademicSearchFilters.model_validate(
                kwargs.get("filters") or {}
            ).require_supported_by(databases)
        except ValueError as error:
            return {"success": False, "error": f"Invalid filters: {error}"}

        all_results = []
        sources_used = []

        for db in databases:
            if db == "pubmed":
                results = await self._search_pubmed(
                    query, max_results, filters, allowed_hosts=allowed_hosts
                )
            elif db == "arxiv":
                results = await self._search_arxiv(
                    query, max_results, filters, allowed_hosts=allowed_hosts
                )
            elif db == "semantic_scholar":
                results = await self._search_semantic_scholar(
                    query, max_results, filters, allowed_hosts=allowed_hosts
                )
            else:
                continue

            all_results.extend(results)
            sources_used.append(db)

        # Providers' own date handling is approximate (arXiv filters on
        # submission date, PubMed on several), so the bound is re-checked here.
        kept: list[dict[str, Any]] = []
        excluded = {"out_of_range": 0, "unknown_year": 0}
        for r in all_results:
            if filters.admits(r.get("year")):
                kept.append(r)
            else:
                excluded[
                    "unknown_year" if r.get("year") is None else "out_of_range"
                ] += 1
        payload: dict[str, Any] = {
            "success": True,
            "results": kept,
            "query": query,
            "total": len(kept),
            "filters_applied": filters.applied(),
            "excluded": excluded,
        }
        if len(databases) == 1:
            payload["source"] = databases[0]
        else:
            payload["sources"] = sources_used
        return payload

    async def _search_pubmed(
        self,
        query: str,
        max_results: int,
        filters: AcademicSearchFilters,
        *,
        allowed_hosts: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Search PubMed database.

        Args:
            query: Search query
            max_results: Maximum results
            filters: Validated search filters
            allowed_hosts: Authority-approved outbound hosts, or ``None`` for direct calls

        Returns:
            List of results
        """
        search_url = f"{self.pubmed_base}/esearch.fcgi"
        summary_url = f"{self.pubmed_base}/esummary.fcgi"
        try:
            search_query = query
            if filters.bounded:
                # PubMed date ranges need both ends; 1000/3000 are open-ended.
                start = filters.year_start or 1000
                end = filters.year_end or 3000
                search_query += f" AND {start}:{end}[pdat]"

            # Search for IDs
            search_params: dict[str, str | int] = {
                "db": "pubmed",
                "term": search_query,
                "retmode": "json",
                "retmax": max_results,
            }

            response = await checked_get(
                self.client,
                search_url,
                declared_hosts=(PUBMED_HOST,),
                allowed_hosts=allowed_hosts,
                params=search_params,
            )
            if response.status_code != 200:
                logger.error(f"PubMed search failed: {response.status_code}")
                return []

            search_data = response.json()
            id_list = search_data.get("esearchresult", {}).get("idlist", [])

            if not id_list:
                return []

            # Fetch summaries
            summary_params = {
                "db": "pubmed",
                "id": ",".join(id_list),
                "retmode": "json",
            }

            response = await checked_get(
                self.client,
                summary_url,
                declared_hosts=(PUBMED_HOST,),
                allowed_hosts=allowed_hosts,
                params=summary_params,
            )
            if response.status_code != 200:
                return []

            summary_data = response.json()
            results = []

            for pmid in id_list:
                if pmid in summary_data.get("result", {}):
                    article = summary_data["result"][pmid]
                    doi = next(
                        (
                            i.get("value")
                            for i in article.get("articleids", [])
                            if i.get("idtype") == "doi"
                        ),
                        None,
                    )
                    results.append(
                        _compact(
                            {
                                "id": pmid,
                                "title": article.get("title", ""),
                                "authors": [
                                    author.get("name", "")
                                    for author in article.get("authors", [])
                                ],
                                "abstract": article.get("abstract", ""),
                                "year": _year(article.get("pubdate")),
                                "journal": article.get("source", ""),
                                "doi": doi,
                                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                                "source": "pubmed",
                            }
                        )
                    )

            return results

        except (httpx.HTTPError, ValueError) as e:
            logger.error(f"PubMed search error: {e!s}")
            return []

    async def _search_arxiv(
        self,
        query: str,
        max_results: int,
        filters: AcademicSearchFilters,
        *,
        allowed_hosts: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Search arXiv database.

        Args:
            query: Search query
            max_results: Maximum results
            filters: Validated search filters
            allowed_hosts: Authority-approved outbound hosts, or ``None`` for direct calls

        Returns:
            List of results
        """
        try:
            search_query = f"all:{query}"

            if filters.categories:
                cat_query = " OR ".join(f"cat:{cat}" for cat in filters.categories)
                search_query += f" AND ({cat_query})"
            if filters.bounded:
                start = filters.year_start or 1900
                end = filters.year_end or 9999
                search_query += f" AND submittedDate:[{start}01010000 TO {end}12312359]"

            params: dict[str, str | int] = {
                "search_query": search_query,
                "start": 0,
                "max_results": max_results,
                "sortBy": "relevance",
                "sortOrder": "descending",
            }

            response = await checked_get(
                self.client,
                self.arxiv_base,
                declared_hosts=(ARXIV_HOST,),
                allowed_hosts=allowed_hosts,
                params=params,
            )
            if response.status_code != 200:
                logger.error(f"arXiv search failed: {response.status_code}")
                return []

            root = ET.fromstring(response.text)
            ns = {
                "atom": "http://www.w3.org/2005/Atom",
                "arxiv": "http://arxiv.org/schemas/atom",
            }

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
                arxiv_id = (
                    entry_id.text.split("/")[-1]
                    if entry_id is not None and entry_id.text is not None
                    else ""
                )

                # Extract publication date
                published = entry.find("atom:published", ns)
                year = _year(published.text if published is not None else None)

                title_elem = entry.find("atom:title", ns)
                title_text = (
                    title_elem.text.strip()
                    if title_elem is not None and title_elem.text is not None
                    else ""
                )

                summary_elem = entry.find("atom:summary", ns)
                abstract_text = (
                    summary_elem.text.strip()
                    if summary_elem is not None and summary_elem.text is not None
                    else ""
                )

                url_text = (
                    entry_id.text.replace("http://", "https://", 1)
                    if entry_id is not None and entry_id.text is not None
                    else ""
                )
                doi_elem = entry.find("arxiv:doi", ns)

                results.append(
                    _compact(
                        {
                            "id": arxiv_id,
                            "title": title_text,
                            "authors": authors,
                            "abstract": abstract_text,
                            "year": year,
                            "url": url_text,
                            "doi": doi_elem.text if doi_elem is not None else None,
                            "source": "arxiv",
                        }
                    )
                )

            return results

        except (httpx.HTTPError, ET.ParseError) as e:
            logger.error(f"arXiv search error: {e!s}")
            return []

    async def _search_semantic_scholar(
        self,
        query: str,
        max_results: int,
        filters: AcademicSearchFilters,
        *,
        allowed_hosts: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Search Semantic Scholar database.

        Args:
            query: Search query
            max_results: Maximum results
            filters: Validated search filters
            allowed_hosts: Authority-approved outbound hosts, or ``None`` for direct calls

        Returns:
            List of results
        """
        search_url = f"{self.semantic_scholar_base}/paper/search"
        try:
            # Search papers
            params: dict[str, str | int] = {
                "query": query,
                "limit": max_results,
                "fields": (
                    "paperId,title,abstract,authors,year,venue,citationCount,url,"
                    "externalIds"
                ),
            }

            if filters.bounded:
                # "2020-2022", "2020-" and "-2022" are the provider's range forms.
                params["year"] = f"{filters.year_start or ''}-{filters.year_end or ''}"
                if filters.year_start == filters.year_end:
                    params["year"] = str(filters.year_start)

            response = await checked_get(
                self.client,
                search_url,
                declared_hosts=(SEMANTIC_SCHOLAR_HOST,),
                allowed_hosts=allowed_hosts,
                params=params,
            )
            if response.status_code != 200:
                logger.error(f"Semantic Scholar search failed: {response.status_code}")
                return []

            data = response.json()
            results = []

            for paper in data.get("data", []):
                results.append(
                    _compact(
                        {
                            "id": paper.get("paperId", ""),
                            "title": paper.get("title", ""),
                            "authors": [
                                author.get("name", "")
                                for author in paper.get("authors", [])
                            ],
                            "abstract": paper.get("abstract", ""),
                            "year": _year(paper.get("year")),
                            "venue": paper.get("venue", ""),
                            "citations": paper.get("citationCount", 0),
                            "url": paper.get("url"),
                            "doi": (paper.get("externalIds") or {}).get("DOI"),
                            "source": "semantic_scholar",
                        }
                    )
                )

            return results

        except (httpx.HTTPError, ValueError) as e:
            logger.error(f"Semantic Scholar search error: {e!s}")
            return []

    def __del__(self) -> None:
        """Cleanup HTTP client."""
        # Note: We can't use async in __del__, so client cleanup
        # should be handled explicitly in production code


__all__ = [
    "ACADEMIC_SEARCH_DATABASE_HOSTS",
    "ACADEMIC_SEARCH_OUTBOUND_HOSTS",
    "ARXIV_HOST",
    "PUBMED_HOST",
    "SEMANTIC_SCHOLAR_HOST",
    "AcademicSearchTool",
    "OutboundHostNotAllowedError",
    "require_allowed_outbound_host",
]
