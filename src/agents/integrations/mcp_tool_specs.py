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

from collections.abc import Awaitable, Callable
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from src.core.contracts.capabilities import SensitivityClass
from src.core.tools import ToolCallContext, ToolSpec
from src.security.content_sanitizer import ContentSanitizer

TOOL_ACADEMIC_SEARCH: Final[str] = "mcp.academic_search"
TOOL_FORMAT_CITATIONS: Final[str] = "mcp.format_citations"
TOOL_ANALYZE_STATISTICS: Final[str] = "mcp.analyze_statistics"
TOOL_BUILD_KNOWLEDGE_GRAPH: Final[str] = "mcp.build_knowledge_graph"

TOOL_VERSION: Final[str] = "1.0.0"

MCP_SENSITIVITY: Final[SensitivityClass] = SensitivityClass.READ_ONLY
"""These four operations read public bibliographic sources and compute.

None of them writes to a third-party system or sends run content outside the
tenant boundary, so none is ``EXTERNAL_WRITE`` or ``EXFILTRATION``. That is
load-bearing for the interim self-issued grant — see
``src/agents/tools/mediation.py``. A tool added here that *does* write must
declare it, and the self-issued grant will then refuse to authorize it.
"""

DEFAULT_MCP_TIMEOUT_SECONDS: Final[float] = 45.0
"""Chosen against the tools' own 30s httpx client timeout, with headroom.

`AcademicSearchTool` queries several databases behind one call, each under a
30-second client timeout, so a deadline at or below 30s would cut off calls
that were going to succeed. This bounds the operation the integration never
bounded at all; it is not tight, and a deployment that wants it tighter sets
``tool_timeout_seconds`` in the integration config.
"""

ClientProvider = Callable[[], Awaitable[Any]]
"""Returns the MCP client, constructing it if needed.

Called *inside* the handler, which is the point. 4-Char's most severe finding
was that `initialize()` constructed the client before its own try/except, so a
construction failure escaped past the circuit breaker, the fallback, and the
sanitizer — the whole apparatus was unreachable for every caller that does not
inject a client, which is every caller in production. Constructing inside the
handler makes that failure an ordinary mediated tool error: recorded, counted
by the breaker, and returned as a typed degraded state.
"""


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


class AcademicSearchOutput(_PermissiveOutput):
    sources: list[dict[str, JsonValue]] = Field(default_factory=list)
    total_found: int = 0
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


def build_specs(
    *,
    client: ClientProvider,
    sanitizer: ContentSanitizer,
    timeout_seconds: float = DEFAULT_MCP_TIMEOUT_SECONDS,
) -> tuple[ToolSpec, ...]:
    """Return the four specifications, bound to one client and sanitizer."""

    async def academic_search(
        params: BaseModel, _context: ToolCallContext
    ) -> dict[str, JsonValue]:
        assert isinstance(params, AcademicSearchInput)
        connected = await client()
        result = _require_success(
            await connected.search_academic(
                query=params.query,
                databases=params.databases,
                max_results=params.max_results,
            ),
            operation="academic search",
        )
        sources = sanitize(sanitizer, result.get("results", []))
        databases = list(params.databases)
        return {
            "sources": sources,
            "total_found": len(sources),
            "databases_searched": databases,
            "search_strategy": (
                f"Multi-database search across {', '.join(databases)}"
                if databases
                else "Search"
            ),
        }

    async def format_citations(
        params: BaseModel, _context: ToolCallContext
    ) -> dict[str, JsonValue]:
        assert isinstance(params, FormatCitationsInput)
        connected = await client()
        result = _require_success(
            await connected.format_citations(
                sources=params.sources, style=params.style
            ),
            operation="citation formatting",
        )
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
        result = _require_success(
            await connected.analyze_statistics(
                operation=params.operation, data=params.data, **optional
            ),
            operation="statistical analysis",
        )
        return {
            "analysis": sanitize(sanitizer, result.get("result", {})),
            "operation": params.operation,
            "data_points": len(params.data) if params.data else 0,
        }

    async def build_knowledge_graph(
        params: BaseModel, _context: ToolCallContext
    ) -> dict[str, JsonValue]:
        assert isinstance(params, BuildKnowledgeGraphInput)
        connected = await client()
        result = _require_success(
            await connected.build_knowledge_graph(
                text=params.text,
                entities=params.entities,
                relationships=params.relationships,
            ),
            operation="knowledge graph building",
        )
        return {
            "graph": sanitize(sanitizer, result.get("graph", {})),
            "entities": sanitize(sanitizer, result.get("entities", [])),
            "relationships": sanitize(sanitizer, result.get("relationships", [])),
        }

    return (
        ToolSpec(
            name=TOOL_ACADEMIC_SEARCH,
            version=TOOL_VERSION,
            sensitivity=MCP_SENSITIVITY,
            input_model=AcademicSearchInput,
            output_model=AcademicSearchOutput,
            timeout_seconds=timeout_seconds,
            handler=academic_search,
        ),
        ToolSpec(
            name=TOOL_FORMAT_CITATIONS,
            version=TOOL_VERSION,
            sensitivity=MCP_SENSITIVITY,
            input_model=FormatCitationsInput,
            output_model=FormatCitationsOutput,
            timeout_seconds=timeout_seconds,
            handler=format_citations,
        ),
        ToolSpec(
            name=TOOL_ANALYZE_STATISTICS,
            version=TOOL_VERSION,
            sensitivity=MCP_SENSITIVITY,
            input_model=AnalyzeStatisticsInput,
            output_model=AnalyzeStatisticsOutput,
            timeout_seconds=timeout_seconds,
            handler=analyze_statistics,
        ),
        ToolSpec(
            name=TOOL_BUILD_KNOWLEDGE_GRAPH,
            version=TOOL_VERSION,
            sensitivity=MCP_SENSITIVITY,
            input_model=BuildKnowledgeGraphInput,
            output_model=BuildKnowledgeGraphOutput,
            timeout_seconds=timeout_seconds,
            handler=build_knowledge_graph,
        ),
    )


__all__ = [
    "DEFAULT_MCP_TIMEOUT_SECONDS",
    "MCP_SENSITIVITY",
    "TOOL_ACADEMIC_SEARCH",
    "TOOL_ANALYZE_STATISTICS",
    "TOOL_BUILD_KNOWLEDGE_GRAPH",
    "TOOL_FORMAT_CITATIONS",
    "TOOL_VERSION",
    "AcademicSearchInput",
    "AcademicSearchOutput",
    "AnalyzeStatisticsInput",
    "AnalyzeStatisticsOutput",
    "BuildKnowledgeGraphInput",
    "BuildKnowledgeGraphOutput",
    "ClientProvider",
    "FormatCitationsInput",
    "FormatCitationsOutput",
    "MCPToolError",
    "build_specs",
    "sanitize",
]
