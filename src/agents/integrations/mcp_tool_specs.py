"""The four MCP operations, declared as tools the boundary can enforce.

Packet 4-Char found that these four operations reached the network through
`MCPIntegration` with no deadline, no capability check, no provenance, and
sanitization on exactly one of them. Declaring them here is what makes the
boundary able to enforce those things: a ``ToolSpec`` cannot be constructed
without a timeout and a sensitivity, and everything else follows from being
registered.

**Sanitization moved inside the handler, deliberately.** It used to run in
`MCPIntegration` after the call returned, on academic search only. Running it
in the handler means the sanitized value is what gets validated, hashed,
redacted, recorded, and published — so a poisoned title cannot be persisted and
then read back by something downstream that trusts the record. The cost is that
the durable ``ToolInvocation`` holds sanitized rather than raw content;
preserving raw bytes with a hash is what packet 4E's source snapshots are for,
and this record is not a substitute for one.

**Output models are permissive on purpose, and that is a real limit.** Each
declares the fields the integration reads and accepts unknown extras, because
these tools have no published schema and tightening one here would turn an
upstream field addition into a hard failure. What the models genuinely enforce
is JSON-representability, which is the failure mode that matters for something
about to be persisted. They do not constrain the *shape* of a source or a
citation, so ``INVALID_OUTPUT`` on this path means "unserializable", not
"malformed".

**Input models are closed, and the asymmetry is the point.** They used to share
the permissive base, which meant an undeclared field passed validation, landed
in ``model_extra``, and — for ``analyze_statistics`` — was forwarded to the
outbound client as a keyword argument, while being absent from the record, from
``input_sha256``, and from the approval binding ``input_sha256`` carries. Two
calls differing only in smuggled material produced the same digest and the same
idempotency key, so one could be served the other's recorded output.

The forward-compatibility argument that justifies permissive *outputs* does not
transfer to *inputs*: an upstream server adds response fields, it does not
invent request parameters this repository then has to accept sight-unseen. The
input parameters are published — ``statistics_analyzer`` names ``operation``,
``data``, ``group1``, ``group2``, ``x``, ``y``, ``plot_type`` in its metadata —
so they are declared here rather than admitted anonymously. An undeclared field
is now ``INVALID_INPUT``: refused before the network, and recorded with the
offending field visible.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from src.core.acquisition.fetch_tool import (
    SOURCE_FETCH_TOOL,
    SourceFetcher,
    hardened_source_fetch_tool,
)
from src.core.acquisition.snapshots import FilesystemSnapshotStore, SnapshotStore
from src.core.config import settings
from src.core.contracts.capabilities import SensitivityClass
from src.core.tools import ToolCallContext, ToolSpec
from src.core.tools.outbound import (
    ACADEMIC_SEARCH_DATABASE_HOSTS,
    ACADEMIC_SEARCH_OUTBOUND_HOSTS,
    CITATION_OUTBOUND_HOSTS,
    SOURCE_FETCH_OUTBOUND_HOSTS,
)
from src.mcp.academic_filters import AcademicSearchFilters
from src.security.content_sanitizer import ContentSanitizer

TOOL_ACADEMIC_SEARCH: Final[str] = "mcp.academic_search"
TOOL_FORMAT_CITATIONS: Final[str] = "mcp.format_citations"
TOOL_ANALYZE_STATISTICS: Final[str] = "mcp.analyze_statistics"
TOOL_BUILD_KNOWLEDGE_GRAPH: Final[str] = "mcp.build_knowledge_graph"
TOOL_SOURCE_FETCH: Final[str] = SOURCE_FETCH_TOOL
TOOL_DATETIME_INFO: Final[str] = "datetime_info"
TOOL_UNIT_CONVERSION: Final[str] = "unit_conversion"

TOOL_VERSION: Final[str] = "1.0.0"

MCP_OUTBOUND_SENSITIVITY: Final[SensitivityClass] = SensitivityClass.EXTERNAL_READ
"""Outbound bibliographic tools transmit query or DOI text outside the tenant boundary.

``AcademicSearchTool`` sends search queries and filters to PubMed
(``eutils.ncbi.nlm.nih.gov``), arXiv (``export.arxiv.org``), and Semantic
Scholar (``api.semanticscholar.org``). ``CitationTool`` resolves DOIs against
Crossref (``api.crossref.org``). None mutates third-party state, but run query
text leaves the tenant boundary to those pinned hosts, so grants must
explicitly allowlist every target host.
"""

MCP_LOCAL_SENSITIVITY: Final[SensitivityClass] = SensitivityClass.READ_ONLY
"""Pure local compute tools (``StatisticsTool``, ``KnowledgeGraphTool``,
``DatetimeTool``, ``UnitConversionTool``).

These operations execute in-process with no outbound network access.
"""

MCP_TOOL_SENSITIVITIES: Final[Mapping[str, SensitivityClass]] = MappingProxyType(
    {
        TOOL_ACADEMIC_SEARCH: MCP_OUTBOUND_SENSITIVITY,
        TOOL_FORMAT_CITATIONS: MCP_OUTBOUND_SENSITIVITY,
        TOOL_ANALYZE_STATISTICS: MCP_LOCAL_SENSITIVITY,
        TOOL_BUILD_KNOWLEDGE_GRAPH: MCP_LOCAL_SENSITIVITY,
        TOOL_SOURCE_FETCH: MCP_OUTBOUND_SENSITIVITY,
        TOOL_DATETIME_INFO: MCP_LOCAL_SENSITIVITY,
        TOOL_UNIT_CONVERSION: MCP_LOCAL_SENSITIVITY,
    }
)

MCP_TOOL_OUTBOUND_HOSTS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        TOOL_ACADEMIC_SEARCH: ACADEMIC_SEARCH_OUTBOUND_HOSTS,
        TOOL_FORMAT_CITATIONS: CITATION_OUTBOUND_HOSTS,
        TOOL_ANALYZE_STATISTICS: (),
        TOOL_BUILD_KNOWLEDGE_GRAPH: (),
        TOOL_SOURCE_FETCH: SOURCE_FETCH_OUTBOUND_HOSTS,
        TOOL_DATETIME_INFO: (),
        TOOL_UNIT_CONVERSION: (),
    }
)

DEFAULT_MCP_TIMEOUT_SECONDS: Final[float] = 45.0
"""Chosen against the tools' own 30s httpx client timeout, with headroom.

`AcademicSearchTool` queries several databases behind one call, each under a
30-second client timeout, so a deadline at or below 30s would cut off calls
that were going to succeed. This bounds the operation the integration never
bounded at all; it is not tight, and a deployment that wants it tighter sets
``tool_timeout_seconds`` in the integration config.
"""

ClientProvider = Callable[[], Awaitable[Any]]
"""Returns the research tool bundle (or an injected test double), constructing it lazily.

Called *inside* the handler so a construction failure becomes an ordinary
mediated tool error: recorded, counted by the breaker, and returned as a typed
degraded state.
"""


class ResearchToolSet:
    """Direct in-process access to the four research tools, constructed lazily."""

    def __init__(
        self,
        *,
        academic_search: Any | None = None,
        citation: Any | None = None,
        statistics: Any | None = None,
        knowledge_graph: Any | None = None,
    ) -> None:
        self._academic_search = academic_search
        self._citation = citation
        self._statistics = statistics
        self._knowledge_graph = knowledge_graph

    @property
    def academic_search(self) -> Any:
        if self._academic_search is None:
            from src.mcp.tools.academic_search_tool import AcademicSearchTool

            self._academic_search = AcademicSearchTool()
        return self._academic_search

    @property
    def citation(self) -> Any:
        if self._citation is None:
            from src.mcp.tools.citation_tool import CitationTool

            self._citation = CitationTool()
        return self._citation

    @property
    def statistics(self) -> Any:
        if self._statistics is None:
            from src.mcp.tools.statistics_tool import StatisticsTool

            self._statistics = StatisticsTool()
        return self._statistics

    @property
    def knowledge_graph(self) -> Any:
        if self._knowledge_graph is None:
            from src.mcp.tools.knowledge_graph_tool import KnowledgeGraphTool

            self._knowledge_graph = KnowledgeGraphTool()
        return self._knowledge_graph


class _PermissiveOutput(BaseModel):
    """What comes back: unknown fields tolerated, JSON-representability forced."""

    model_config = ConfigDict(extra="allow")


class _ClosedInput(BaseModel):
    """What goes out: nothing this file did not declare.

    ``ToolSpec`` refuses a permissive input model at registration, so this base
    is what makes the four specifications constructible — the enforcement is
    not here, and a future model that forgets to inherit it is refused rather
    than silently reopened.
    """

    model_config = ConfigDict(extra="forbid")


class AcademicSearchInput(_ClosedInput):
    query: str
    databases: list[str] = Field(default_factory=list)
    max_results: int = 20
    filters: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _filters_are_supported(self) -> AcademicSearchInput:
        AcademicSearchFilters.model_validate(self.filters).require_supported_by(
            self.databases
        )
        return self


class AcademicSearchOutput(_PermissiveOutput):
    sources: list[dict[str, JsonValue]] = Field(default_factory=list)
    total_found: int = 0
    filters_applied: dict[str, JsonValue] = Field(default_factory=dict)
    excluded: dict[str, int] = Field(default_factory=dict)
    databases_searched: list[str] = Field(default_factory=list)
    search_strategy: str = ""


class FormatCitationsInput(_ClosedInput):
    sources: list[dict[str, JsonValue]] = Field(default_factory=list)
    style: str = "APA"


class FormatCitationsOutput(_PermissiveOutput):
    formatted_citations: list[JsonValue] = Field(default_factory=list)
    style: str = "APA"
    total_sources: int = 0


class AnalyzeStatisticsInput(_ClosedInput):
    """The parameter set ``statistics_analyzer`` publishes, declared.

    ``group1``/``group2`` serve ``t_test``, ``x``/``y`` serve ``correlation``,
    and ``plot_type`` serves ``plot``. They used to travel as ``model_extra``,
    which is how they reached the client without reaching the record.
    """

    operation: str
    data: list[JsonValue] | None = None
    group1: list[JsonValue] | None = None
    group2: list[JsonValue] | None = None
    x: list[JsonValue] | None = None
    y: list[JsonValue] | None = None
    plot_type: str | None = None


class AnalyzeStatisticsOutput(_PermissiveOutput):
    analysis: dict[str, JsonValue] = Field(default_factory=dict)
    operation: str = ""
    data_points: int = 0


class BuildKnowledgeGraphInput(_ClosedInput):
    text: str | None = None
    entities: list[JsonValue] | None = None
    relationships: list[JsonValue] | None = None


class BuildKnowledgeGraphOutput(_PermissiveOutput):
    graph: dict[str, JsonValue] = Field(default_factory=dict)
    entities: list[JsonValue] = Field(default_factory=list)
    relationships: list[JsonValue] = Field(default_factory=list)


class DatetimeInfoInput(_ClosedInput):
    operation: Literal["current", "day_of_week", "add_days", "diff_days"]
    date_iso: str | None = None
    days: int | None = None
    other_date_iso: str | None = None

    @field_validator("operation", mode="before")
    @classmethod
    def _normalize_operation(cls, value: Any) -> Any:
        return value.lower() if isinstance(value, str) else value


class DatetimeInfoOutput(BaseModel):
    operation: str
    current_datetime_utc: str | None = None
    date: str | None = None
    time: str | None = None
    day_of_week: str | None = None
    timestamp: float | None = None
    day_number: int | None = None
    original_date: str | None = None
    days_added: int | None = None
    result_date: str | None = None
    result_datetime_utc: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    difference_days: int | None = None
    difference_seconds: float | None = None


class UnitConversionInput(_ClosedInput):
    value: float
    from_unit: str = Field(min_length=1)
    to_unit: str = Field(min_length=1)


class UnitConversionOutput(BaseModel):
    value: float
    from_unit: str
    to_unit: str
    result: float


class MCPToolError(RuntimeError):
    """The MCP layer answered, and its answer was a failure.

    Distinct from a transport error so that "the tool said no" and "the tool
    could not be reached" are not the same event in a record.
    """


def sanitize(sanitizer: ContentSanitizer, value: Any) -> Any:
    """Neutralize instruction-like patterns in every string reachable in ``value``.

    Applied to whole payloads rather than to a chosen field list. 4-Char found
    the previous arrangement sanitized `title` and `abstract` on one operation;
    author names, journal names, entity labels, and statistical summaries all
    reach downstream prompts too, and picking which strings are "safe" is the
    decision that produced the gap.
    """

    if isinstance(value, str):
        return sanitizer.sanitize(value).sanitized_text
    if isinstance(value, dict):
        return {key: sanitize(sanitizer, item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(sanitizer, item) for item in value]
    return value


def _require_success(result: Any, *, operation: str) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise MCPToolError(
            f"{operation} returned {type(result).__name__}, not a result"
        )
    if not result.get("success"):
        raise MCPToolError(
            f"{operation} failed: {result.get('error', 'unknown error')}"
        )
    return result


def _academic_search_target_hosts(params: BaseModel) -> tuple[str, ...]:
    assert isinstance(params, AcademicSearchInput)
    if not params.databases:
        return ACADEMIC_SEARCH_OUTBOUND_HOSTS
    return tuple(
        sorted(
            {
                ACADEMIC_SEARCH_DATABASE_HOSTS[db]
                for db in params.databases
                if db in ACADEMIC_SEARCH_DATABASE_HOSTS
            }
        )
    )


def build_specs(
    *,
    client: ClientProvider,
    sanitizer: ContentSanitizer,
    timeout_seconds: float = DEFAULT_MCP_TIMEOUT_SECONDS,
    snapshot_store: SnapshotStore | None = None,
    source_fetcher: SourceFetcher | None = None,
) -> tuple[ToolSpec, ...]:
    """Return all mediated boundary specifications."""

    async def academic_search(
        params: BaseModel, context: ToolCallContext
    ) -> dict[str, JsonValue]:
        assert isinstance(params, AcademicSearchInput)
        connected = await client()
        if isinstance(connected, ResearchToolSet):
            raw = await connected.academic_search.execute(
                query=params.query,
                databases=params.databases,
                max_results=params.max_results,
                filters=params.filters,
                allowed_hosts=context.allowed_hosts,
            )
        else:
            raw = await connected.search_academic(
                query=params.query,
                databases=params.databases,
                max_results=params.max_results,
                filters=params.filters,
            )
        result = _require_success(raw, operation="academic search")
        sources = sanitize(sanitizer, result.get("results", []))
        databases = list(params.databases)
        return {
            "sources": sources,
            "total_found": len(sources),
            "filters_applied": result.get("filters_applied", {}),
            "excluded": result.get("excluded", {}),
            "databases_searched": databases,
            "search_strategy": (
                f"Multi-database search across {', '.join(databases)}"
                if databases
                else "Search"
            ),
        }

    async def format_citations(
        params: BaseModel, context: ToolCallContext
    ) -> dict[str, JsonValue]:
        assert isinstance(params, FormatCitationsInput)
        connected = await client()
        if isinstance(connected, ResearchToolSet):
            raw = await connected.citation.execute(
                sources=params.sources,
                style=params.style,
                allowed_hosts=context.allowed_hosts,
            )
        else:
            raw = await connected.format_citations(
                sources=params.sources, style=params.style
            )
        result = _require_success(raw, operation="citation formatting")
        return {
            "formatted_citations": sanitize(sanitizer, result.get("citations", [])),
            "style": params.style,
            "total_sources": len(params.sources),
        }

    async def analyze_statistics(
        params: BaseModel, _context: ToolCallContext
    ) -> dict[str, JsonValue]:
        assert isinstance(params, AnalyzeStatisticsInput)
        connected = await client()
        # Omitted rather than sent as ``None``: the upstream tool reads these
        # with ``kwargs.get(name, [])``, so passing an absent parameter
        # explicitly is not the same call as not passing it.
        optional = {
            name: value
            for name, value in (
                ("group1", params.group1),
                ("group2", params.group2),
                ("x", params.x),
                ("y", params.y),
                ("plot_type", params.plot_type),
            )
            if value is not None
        }
        if isinstance(connected, ResearchToolSet):
            raw = await connected.statistics.execute(
                operation=params.operation, data=params.data, **optional
            )
        else:
            raw = await connected.analyze_statistics(
                operation=params.operation, data=params.data, **optional
            )
        result = _require_success(raw, operation="statistical analysis")
        return {
            # The tool reports its figures at the top level, not under a
            # "result" key; reading that key made every real analysis empty.
            "analysis": sanitize(
                sanitizer, {k: v for k, v in result.items() if k != "success"}
            ),
            "operation": params.operation,
            "data_points": len(params.data) if params.data else 0,
        }

    async def build_knowledge_graph(
        params: BaseModel, _context: ToolCallContext
    ) -> dict[str, JsonValue]:
        assert isinstance(params, BuildKnowledgeGraphInput)
        connected = await client()
        if isinstance(connected, ResearchToolSet):
            kg_tool = connected.knowledge_graph
            entities = params.entities
            if params.text:
                extracted = await kg_tool.execute(
                    operation="extract_entities", text=params.text
                )
                if extracted.get("success") and extracted.get("entities"):
                    entities = [
                        {"id": str(i), "text": e["text"], "type": e["type"]}
                        for i, e in enumerate(extracted["entities"])
                    ]
            if not entities:
                raw: dict[str, Any] = {
                    "success": False,
                    "error": "No entities provided or extracted",
                }
            else:
                raw = await kg_tool.execute(
                    operation="build_graph",
                    entities=entities,
                    relationships=params.relationships or [],
                )
        else:
            raw = await connected.build_knowledge_graph(
                text=params.text,
                entities=params.entities,
                relationships=params.relationships,
            )
        result = _require_success(raw, operation="knowledge graph building")
        return {
            "graph": sanitize(sanitizer, result.get("graph", {})),
            "entities": sanitize(sanitizer, result.get("entities", [])),
            "relationships": sanitize(sanitizer, result.get("relationships", [])),
        }

    async def datetime_info(
        params: BaseModel, _context: ToolCallContext
    ) -> dict[str, JsonValue]:
        assert isinstance(params, DatetimeInfoInput)
        from src.agents.tools.datetime_tool import DatetimeParams, DatetimeTool

        tool = DatetimeTool()
        raw = await tool._execute_impl(
            DatetimeParams(
                operation=params.operation,
                date_iso=params.date_iso,
                days=params.days,
                other_date_iso=params.other_date_iso,
            )
        )
        return {"operation": params.operation, **raw}

    async def unit_conversion(
        params: BaseModel, _context: ToolCallContext
    ) -> dict[str, JsonValue]:
        assert isinstance(params, UnitConversionInput)
        from src.agents.tools.unit_conversion_tool import (
            UnitConversionParams,
            UnitConversionTool,
        )

        tool = UnitConversionTool()
        raw = await tool._execute_impl(
            UnitConversionParams(
                value=params.value,
                from_unit=params.from_unit,
                to_unit=params.to_unit,
            )
        )
        return dict(raw)

    active_store = snapshot_store or FilesystemSnapshotStore(
        root=Path(settings.SOURCE_SNAPSHOT_DIR)
    )

    return (
        ToolSpec(
            name=TOOL_ACADEMIC_SEARCH,
            version=TOOL_VERSION,
            sensitivity=MCP_TOOL_SENSITIVITIES[TOOL_ACADEMIC_SEARCH],
            input_model=AcademicSearchInput,
            output_model=AcademicSearchOutput,
            timeout_seconds=timeout_seconds,
            handler=academic_search,
            outbound_hosts=MCP_TOOL_OUTBOUND_HOSTS[TOOL_ACADEMIC_SEARCH],
            resolve_target_hosts=_academic_search_target_hosts,
        ),
        ToolSpec(
            name=TOOL_FORMAT_CITATIONS,
            version=TOOL_VERSION,
            sensitivity=MCP_TOOL_SENSITIVITIES[TOOL_FORMAT_CITATIONS],
            input_model=FormatCitationsInput,
            output_model=FormatCitationsOutput,
            timeout_seconds=timeout_seconds,
            handler=format_citations,
            outbound_hosts=MCP_TOOL_OUTBOUND_HOSTS[TOOL_FORMAT_CITATIONS],
        ),
        ToolSpec(
            name=TOOL_ANALYZE_STATISTICS,
            version=TOOL_VERSION,
            sensitivity=MCP_TOOL_SENSITIVITIES[TOOL_ANALYZE_STATISTICS],
            input_model=AnalyzeStatisticsInput,
            output_model=AnalyzeStatisticsOutput,
            timeout_seconds=timeout_seconds,
            handler=analyze_statistics,
            outbound_hosts=MCP_TOOL_OUTBOUND_HOSTS[TOOL_ANALYZE_STATISTICS],
        ),
        ToolSpec(
            name=TOOL_BUILD_KNOWLEDGE_GRAPH,
            version=TOOL_VERSION,
            sensitivity=MCP_TOOL_SENSITIVITIES[TOOL_BUILD_KNOWLEDGE_GRAPH],
            input_model=BuildKnowledgeGraphInput,
            output_model=BuildKnowledgeGraphOutput,
            timeout_seconds=timeout_seconds,
            handler=build_knowledge_graph,
            outbound_hosts=MCP_TOOL_OUTBOUND_HOSTS[TOOL_BUILD_KNOWLEDGE_GRAPH],
        ),
        hardened_source_fetch_tool(
            store=active_store,
            timeout_seconds=timeout_seconds,
            fetcher=source_fetcher,
            declared_hosts=MCP_TOOL_OUTBOUND_HOSTS[TOOL_SOURCE_FETCH],
            name=TOOL_SOURCE_FETCH,
            version=TOOL_VERSION,
        ),
        ToolSpec(
            name=TOOL_DATETIME_INFO,
            version=TOOL_VERSION,
            sensitivity=MCP_TOOL_SENSITIVITIES[TOOL_DATETIME_INFO],
            input_model=DatetimeInfoInput,
            output_model=DatetimeInfoOutput,
            timeout_seconds=timeout_seconds,
            handler=datetime_info,
            outbound_hosts=MCP_TOOL_OUTBOUND_HOSTS[TOOL_DATETIME_INFO],
        ),
        ToolSpec(
            name=TOOL_UNIT_CONVERSION,
            version=TOOL_VERSION,
            sensitivity=MCP_TOOL_SENSITIVITIES[TOOL_UNIT_CONVERSION],
            input_model=UnitConversionInput,
            output_model=UnitConversionOutput,
            timeout_seconds=timeout_seconds,
            handler=unit_conversion,
            outbound_hosts=MCP_TOOL_OUTBOUND_HOSTS[TOOL_UNIT_CONVERSION],
        ),
    )


__all__ = [
    "DEFAULT_MCP_TIMEOUT_SECONDS",
    "MCP_LOCAL_SENSITIVITY",
    "MCP_OUTBOUND_SENSITIVITY",
    "MCP_TOOL_OUTBOUND_HOSTS",
    "MCP_TOOL_SENSITIVITIES",
    "TOOL_ACADEMIC_SEARCH",
    "TOOL_ANALYZE_STATISTICS",
    "TOOL_BUILD_KNOWLEDGE_GRAPH",
    "TOOL_DATETIME_INFO",
    "TOOL_FORMAT_CITATIONS",
    "TOOL_SOURCE_FETCH",
    "TOOL_UNIT_CONVERSION",
    "TOOL_VERSION",
    "AcademicSearchInput",
    "AcademicSearchOutput",
    "AnalyzeStatisticsInput",
    "AnalyzeStatisticsOutput",
    "BuildKnowledgeGraphInput",
    "BuildKnowledgeGraphOutput",
    "ClientProvider",
    "DatetimeInfoInput",
    "DatetimeInfoOutput",
    "FormatCitationsInput",
    "FormatCitationsOutput",
    "MCPToolError",
    "ResearchToolSet",
    "UnitConversionInput",
    "UnitConversionOutput",
    "build_specs",
    "sanitize",
]
