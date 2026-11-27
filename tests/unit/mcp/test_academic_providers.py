"""Academic providers: identity survives, filters reach the request, bad filters never dispatch.

The HTTP client is replaced by a recorder, so these assert what the adapters
*send* and what they *keep* from the provider's body; nothing touches a network.
"""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from src.agents.integrations.mcp_integration import MCPIntegration
from src.agents.integrations.mcp_tool_specs import AcademicSearchInput
from src.agents.tools.mediation import ToolCallIdentity
from src.mcp.tools.academic_search_tool import AcademicSearchTool

ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
{entries}
</feed>"""


def _entry(arxiv_id: str, published: str | None, doi: str | None = None) -> str:
    date = f"<published>{published}</published>" if published else ""
    doi_tag = f"<arxiv:doi>{doi}</arxiv:doi>" if doi else ""
    return (
        f"<entry><id>http://arxiv.org/abs/{arxiv_id}</id><title>T {arxiv_id}</title>"
        f"<summary>S {arxiv_id}</summary><author><name>A</name></author>"
        f"{date}{doi_tag}</entry>"
    )


class _Response:
    def __init__(self, body: Any) -> None:
        self.status_code = 200
        self._body = body

    @property
    def text(self) -> str:
        return self._body

    def json(self) -> Any:
        return self._body


class _Http:
    """Records every request and answers from a queue of canned bodies."""

    def __init__(self, *bodies: Any) -> None:
        self._bodies = list(bodies)
        self.requests: list[tuple[str, dict[str, Any]]] = []

    async def get(self, url: str, params: dict[str, Any] | None = None) -> _Response:
        self.requests.append((url, dict(params or {})))
        return _Response(self._bodies.pop(0))


def _tool(*bodies: Any) -> tuple[AcademicSearchTool, _Http]:
    tool = AcademicSearchTool()
    http = _Http(*bodies)
    tool.client = http  # type: ignore[assignment]
    return tool, http


async def test_arxiv_uses_https_and_keeps_identity() -> None:
    tool, http = _tool(
        ATOM.format(entries=_entry("2301.00001v2", "2023-05-01T00:00:00Z", "10.1/x"))
    )

    result = await tool.execute(query="gnn", databases=["arxiv"])

    url, _ = http.requests[0]
    assert url.startswith("https://export.arxiv.org/")
    (source,) = result["results"]
    assert source["url"] == "https://arxiv.org/abs/2301.00001v2"
    assert source["doi"] == "10.1/x"
    assert source["id"] == "2301.00001v2"
    assert source["year"] == 2023


async def test_arxiv_year_bounds_are_sent_and_backstopped() -> None:
    tool, http = _tool(
        ATOM.format(
            entries=_entry("in", "2022-03-01T00:00:00Z")
            + _entry("early", "2019-03-01T00:00:00Z")
            + _entry("undated", None)
        )
    )

    result = await tool.execute(
        query="gnn", databases=["arxiv"], filters={"year_start": 2020, "year_end": 2022}
    )

    query = http.requests[0][1]["search_query"]
    assert "submittedDate:[202001010000 TO 202212312359]" in query
    assert [r["id"] for r in result["results"]] == ["in"]
    assert result["excluded"] == {"out_of_range": 1, "unknown_year": 1}
    assert result["filters_applied"] == {"year_start": 2020, "year_end": 2022}


async def test_unbounded_search_keeps_undated_sources() -> None:
    tool, _ = _tool(ATOM.format(entries=_entry("undated", None)))

    result = await tool.execute(query="gnn", databases=["arxiv"])

    assert [r["id"] for r in result["results"]] == ["undated"]
    assert result["excluded"] == {"out_of_range": 0, "unknown_year": 0}


async def test_semantic_scholar_keeps_url_doi_and_requests_them() -> None:
    tool, http = _tool(
        {
            "data": [
                {
                    "paperId": "abc",
                    "title": "T",
                    "abstract": "S",
                    "authors": [{"name": "A"}],
                    "year": 2021,
                    "url": "https://www.semanticscholar.org/paper/abc",
                    "externalIds": {"DOI": "10.2/y", "ArXiv": "2101.1"},
                },
                {"paperId": "nodate", "title": "U", "abstract": "V", "year": None},
            ]
        }
    )

    result = await tool.execute(
        query="gnn", databases=["semantic_scholar"], filters={"year_start": 2020}
    )

    params = http.requests[0][1]
    assert "externalIds" in params["fields"]
    assert "url" in params["fields"].split(",")
    assert params["year"] == "2020-"  # open-ended, not capped at a fixed year
    (source,) = result["results"]
    assert source["url"] == "https://www.semanticscholar.org/paper/abc"
    assert source["doi"] == "10.2/y"
    assert source["year"] == 2021
    assert result["excluded"]["unknown_year"] == 1


async def test_pubmed_keeps_pmid_url_doi_and_bounds_dates() -> None:
    tool, http = _tool(
        {"esearchresult": {"idlist": ["99"]}},
        {
            "result": {
                "99": {
                    "title": "T",
                    "pubdate": "2022 Jan 5",
                    "articleids": [
                        {"idtype": "pubmed", "value": "99"},
                        {"idtype": "doi", "value": "10.3/z"},
                    ],
                }
            }
        },
    )

    result = await tool.execute(
        query="gnn",
        databases=["pubmed"],
        filters={"year_start": 2020, "year_end": 2022},
    )

    assert "2020:2022[pdat]" in http.requests[0][1]["term"]
    (source,) = result["results"]
    assert source["url"] == "https://pubmed.ncbi.nlm.nih.gov/99/"
    assert source["doi"] == "10.3/z"
    assert source["year"] == 2022


@pytest.mark.parametrize(
    ("databases", "filters"),
    [
        (["pubmed"], {"year_start": 2023, "year_end": 2020}),
        (["pubmed"], {"year_start": "soon"}),
        (["pubmed"], {"year_start": True}),
        (["pubmed"], {"language": "en"}),
        (["arxiv"], {"categories": ["cs.LG) OR (all:x"]}),
        (["pubmed"], {"categories": ["cs.LG"]}),  # categories are arxiv-only
    ],
)
async def test_invalid_filters_are_refused_before_any_request(
    databases: list[str], filters: dict[str, Any]
) -> None:
    tool, http = _tool()

    result = await tool.execute(query="gnn", databases=databases, filters=filters)

    assert result["success"] is False
    assert http.requests == []


def test_the_input_model_refuses_unsupported_filters() -> None:
    with pytest.raises(ValidationError):
        AcademicSearchInput(query="q", databases=["arxiv"], filters={"language": "en"})
    with pytest.raises(ValidationError):
        AcademicSearchInput(
            query="q", databases=["pubmed"], filters={"categories": ["cs.LG"]}
        )
    AcademicSearchInput(
        query="q", databases=["arxiv"], filters={"categories": ["cs.LG"]}
    )


class _Client:
    """Stands in for ``MCPClient`` and answers from the real tool + fake HTTP."""

    def __init__(self, tool: AcademicSearchTool) -> None:
        self._tool = tool
        self.calls: list[dict[str, Any]] = []

    async def health_check(self) -> dict[str, str]:
        return {"client": "healthy"}

    async def search_academic(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return await self._tool.execute(**kwargs)


_IDENTITY = ToolCallIdentity("run-1", "task-1", "attempt-1", None)


async def test_filters_travel_from_the_integration_to_the_provider_request() -> None:
    tool, http = _tool(ATOM.format(entries=_entry("in", "2022-03-01T00:00:00Z")))
    client = _Client(tool)

    result = await MCPIntegration(mcp_client=client).search_academic_sources(
        "gnn", filters={"year_start": 2022, "year_end": 2022}, identity=_IDENTITY
    )

    assert client.calls[0]["filters"] == {"year_start": 2022, "year_end": 2022}
    assert "202201010000 TO 202212312359" in http.requests[0][1]["search_query"]
    assert result["success"] is True
    assert result["excluded"] == {"out_of_range": 0, "unknown_year": 0}
    assert json.dumps(result["sources"][0]["year"]) == "2022"


async def test_an_invalid_filter_never_reaches_the_client_or_the_network() -> None:
    tool, http = _tool()
    client = _Client(tool)

    result = await MCPIntegration(mcp_client=client).search_academic_sources(
        "gnn", filters={"year_start": 2024, "year_end": 2020}, identity=_IDENTITY
    )

    assert result["success"] is False
    assert client.calls == []
    assert http.requests == []


async def test_the_client_forwards_filters_to_the_tool() -> None:
    from unittest.mock import AsyncMock

    from src.mcp.client import MCPClient

    client = MCPClient.__new__(MCPClient)
    client.server = AsyncMock()

    await client.search_academic("q", ["arxiv"], 5, {"year_start": 2020})

    client.server.execute_tool.assert_awaited_once_with(
        "search_academic",
        query="q",
        databases=["arxiv"],
        max_results=5,
        filters={"year_start": 2020},
    )
