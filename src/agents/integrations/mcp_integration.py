"""MCP Integration for Agents.

The integration layer between agents and MCP tools. Every operation runs
through the tool boundary (:mod:`src.core.tools`); the specifications it is
registered under are in :mod:`src.agents.integrations.mcp_tool_specs`.

Packet 4-Char characterized what this replaces, and the shape of the result is
a direct answer to each finding:

- ``data_source`` was computed as ``"mcp_tools" if success else "fallback"``,
  checking only ``success`` and never ``fallback`` — and every fallback set
  ``success: True``. A fabricated stand-in therefore reached the user labelled
  as a real tool result. **A degraded result now reports ``success: False``.**
  The flag is projected from a boundary outcome, and only the boundary can mint
  a successful one, so the mislabelling is not corrected by a better
  conditional — it is unrepresentable.
- One flaky call and a sustained outage produced the identical value. They are
  now distinct ``tool_outcome`` states carried to the caller.
- Sanitization ran on academic search alone. It now runs inside every handler,
  on the whole payload.
- No public method threaded a run/task/attempt id. Every operation takes an
  optional :class:`~src.agents.tools.mediation.ToolCallIdentity`, and a call
  made without one is recorded and reported as uncorrelatable rather than
  given a plausible identifier.
- ``initialize()`` constructed ``MCPClient()`` before its own try/except, so a
  construction failure escaped the fallback, the breaker, and the sanitizer
  entirely — for every caller that does not inject a client, which is every
  caller in production. Construction now happens inside the mediated handler,
  where a failure is an ordinary recorded tool error.

**What a terminal degraded result still contains.** With ``enable_fallback`` on, the
payload carries stand-in content so a partially-working pipeline keeps moving.
Every level of it says so: ``success`` is ``False``, ``degraded`` is ``True``,
``data_source`` is ``"fallback"``, ``fallback`` is ``True``, and each
fabricated source carries ``source: "fallback"``. Deleting the fabrication
outright is a separate decision from routing, and it is not this module's to
make; what routing guarantees is that no consumer can mistake it for measured
data by reading the flag it already reads.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from structlog import get_logger

from src.agents.integrations.mcp_tool_specs import (
    DEFAULT_MCP_TIMEOUT_SECONDS,
    MCP_SENSITIVITY,
    TOOL_ACADEMIC_SEARCH,
    TOOL_ANALYZE_STATISTICS,
    TOOL_BUILD_KNOWLEDGE_GRAPH,
    TOOL_FORMAT_CITATIONS,
    build_specs,
)
from src.agents.tools.mediation import (
    InMemoryToolAuditStore,
    SelfIssuedPolicy,
    ToolCallIdentity,
    build_tool_boundary,
    plain,
    self_issued_grant,
)
from src.core.contracts.capabilities import CapabilityGrant
from src.core.contracts.trust import TrustClassification
from src.core.tools import (
    ToolAuditStore,
    ToolBoundary,
    ToolOutcome,
    ToolOutcomeStatus,
)
from src.security.content_sanitizer import ContentSanitizer

if TYPE_CHECKING:
    from src.mcp.client import MCPClient

logger = get_logger()

DEFAULT_MCP_INPUT_TRUST = TrustClassification.USER_SUPPLIED
"""The trust label a caller that does not state one is taken to be supplying.

A *default*, not a constant. Every operation accepts `input_trust`, so an agent
forwarding text a model rewrote from a retrieved source can declare
`derived_untrusted` and be refused by the ceiling below. Without that
parameter the ceiling could never be exceeded and the check would be
decorative.
"""

MCP_MAX_INPUT_TRUST = TrustClassification.EXTERNAL_UNTRUSTED
"""The ceiling declared for every MCP operation.

`derived_untrusted` is model-rewritten text. Letting it reach these tools means
a model can put content it generated -- from a poisoned source it just read --
into an outbound query, or into a graph and a statistics summary that feed the
next prompt. That is the indirect-injection-to-exfiltration path this wave's
threat model names. Declared statically here, never read off the call: packet
4A demonstrated that a ceiling taken from the invocation allows all five trust
levels and cannot fail.
"""

MCP_OUTPUT_TRUST = TrustClassification.EXTERNAL_UNTRUSTED
"""Everything these tools return came from outside the tenant boundary.

Declared rather than derived. The boundary would otherwise propagate the input
label forward, and ``USER_SUPPLIED`` is a materially weaker statement about a
paper abstract fetched from arxiv than ``EXTERNAL_UNTRUSTED`` is.
"""


class MCPIntegration:
    """Integration layer between agents and MCP tools.

    Provides a production-ready interface for agents to access external tools,
    with every call mediated and every degraded outcome explicit.
    """

    def __init__(
        self,
        mcp_client: MCPClient | None = None,
        config: dict[str, Any] | None = None,
        enable_fallback: bool = True,
        *,
        identity: ToolCallIdentity | None = None,
        boundary: ToolBoundary | None = None,
        audit_store: ToolAuditStore | None = None,
        grants: Sequence[CapabilityGrant] | None = None,
    ):
        """Initialize MCP integration.

        Args:
            mcp_client: Optional pre-configured MCP client.
            config: Configuration dictionary.
            enable_fallback: Whether a degraded result carries stand-in content.
            identity: Default run/task/attempt correlation for calls that do
                not supply their own.
            boundary: An already-configured tool boundary. Supply one to share
                breaker state and provenance with the rest of a run.
            audit_store: Where invocations are recorded. Defaults to an
                in-memory store, which is not durable — see
                ``src/agents/tools/mediation.py``.
            grants: Capability grants from a real issuer. When omitted,
                non-durable callers retain the legacy self-issued behavior;
                durable callers receive no grants and are denied by the
                boundary.

        """
        self.config = config or {}
        self.enable_fallback = enable_fallback
        self._client = mcp_client
        self._initialized = False
        self._identity = identity
        self._grants = list(grants) if grants is not None else None

        # S3: Content sanitizer for prompt injection defense at external boundaries
        self._content_sanitizer = ContentSanitizer(enable_logging=True)

        self._boundary = boundary or build_tool_boundary(
            audit_store=audit_store or InMemoryToolAuditStore()
        )
        for spec in build_specs(
            client=self._connected_client,
            sanitizer=self._content_sanitizer,
            timeout_seconds=float(
                self.config.get("tool_timeout_seconds", DEFAULT_MCP_TIMEOUT_SECONDS)
            ),
        ):
            self._boundary.register(spec)

        logger.info("mcp_integration_initialized", fallback_enabled=enable_fallback)

    @property
    def boundary(self) -> ToolBoundary:
        """The mediator every MCP operation in this integration runs through."""

        return self._boundary

    async def initialize(self) -> None:
        """Construct and health-check the client, if that has not happened.

        Retained for callers that want to warm the client up front. It is no
        longer a precondition of any operation: each handler obtains the client
        itself, inside the boundary, so a construction failure is a mediated,
        recorded, breaker-counted outcome instead of an exception thrown past
        every protection this class offers.
        """

        try:
            await self._connected_client()
        except Exception as error:
            logger.error("mcp_client_initialization_failed", error=str(error))
            if not self.enable_fallback:
                raise

    async def _connected_client(self) -> MCPClient:
        """Return the client, building and health-checking it on first use."""

        if self._client is None:
            # Imported here, not at module scope: `MCPClient` reaches
            # `MCPServer` and therefore the optional `fastmcp` extra, and a
            # caller that injects its own client never needs either.
            from src.mcp.client import MCPClient
            from src.mcp.server import MCPServerConfig

            self._client = MCPClient(
                MCPServerConfig(
                    name=self.config.get("server_name", "research-mcp-server"),
                    port=self.config.get("server_port", 9000),
                    host=self.config.get("server_host", "localhost"),
                )
            )

        if not self._initialized:
            health = await self._client.health_check()
            if health.get("client") == "healthy":
                self._initialized = True
                logger.info("mcp_client_initialized")
            else:
                logger.warning("mcp_client_health_check_failed", health=health)
        return self._client

    # ------------------------------------------------------------------
    # operations
    # ------------------------------------------------------------------

    async def search_academic_sources(
        self,
        query: str,
        databases: list[str] | None = None,
        max_results: int = 20,
        filters: dict[str, Any] | None = None,
        *,
        identity: ToolCallIdentity | None = None,
        input_trust: TrustClassification = DEFAULT_MCP_INPUT_TRUST,
    ) -> dict[str, Any]:
        """Search academic databases through the boundary."""

        databases = databases or ["arxiv"]
        outcome, call_identity = await self._invoke(
            TOOL_ACADEMIC_SEARCH,
            {
                "query": query,
                "databases": databases,
                "max_results": max_results,
                "filters": filters or {},
            },
            identity=identity,
            input_trust=input_trust,
        )
        if outcome.succeeded:
            return self._succeeded(outcome, call_identity)
        if outcome.status is ToolOutcomeStatus.IN_PROGRESS:
            return self._in_progress(
                outcome,
                call_identity,
                empty={
                    "sources": [],
                    "total_found": 0,
                    "databases_searched": [],
                    "search_strategy": "",
                },
            )
        return self._degraded(
            outcome,
            call_identity,
            fallback=lambda: self._fallback_academic_search(
                query, databases, max_results
            ),
            empty={
                "sources": [],
                "total_found": 0,
                "databases_searched": databases,
                "search_strategy": "Unavailable",
            },
        )

    async def format_citations(
        self,
        sources: list[dict[str, Any]],
        style: str = "APA",
        *,
        identity: ToolCallIdentity | None = None,
        input_trust: TrustClassification = DEFAULT_MCP_INPUT_TRUST,
    ) -> dict[str, Any]:
        """Format citations through the boundary."""

        outcome, call_identity = await self._invoke(
            TOOL_FORMAT_CITATIONS,
            {"sources": sources, "style": style},
            identity=identity,
            input_trust=input_trust,
        )
        if outcome.succeeded:
            return self._succeeded(outcome, call_identity)
        if outcome.status is ToolOutcomeStatus.IN_PROGRESS:
            return self._in_progress(
                outcome,
                call_identity,
                empty={
                    "formatted_citations": [],
                    "style": "",
                    "total_sources": 0,
                },
            )
        return self._degraded(
            outcome,
            call_identity,
            fallback=lambda: self._fallback_citation_formatting(sources, style),
            empty={
                "formatted_citations": [],
                "style": style,
                "total_sources": len(sources),
            },
        )

    async def analyze_statistics(
        self,
        operation: str,
        data: list[Any] | None = None,
        *,
        identity: ToolCallIdentity | None = None,
        input_trust: TrustClassification = DEFAULT_MCP_INPUT_TRUST,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Perform statistical analysis through the boundary."""

        outcome, call_identity = await self._invoke(
            TOOL_ANALYZE_STATISTICS,
            {"operation": operation, "data": data, **kwargs},
            identity=identity,
            input_trust=input_trust,
        )
        if outcome.succeeded:
            return self._succeeded(outcome, call_identity)
        if outcome.status is ToolOutcomeStatus.IN_PROGRESS:
            return self._in_progress(
                outcome,
                call_identity,
                empty={"analysis": {}, "operation": "", "data_points": 0},
            )
        return self._degraded(
            outcome,
            call_identity,
            fallback=lambda: self._fallback_statistical_analysis(
                operation, data, **kwargs
            ),
            empty={
                "analysis": {},
                "operation": operation,
                "data_points": len(data) if data else 0,
            },
        )

    async def build_knowledge_graph(
        self,
        text: str | None = None,
        entities: list[Any] | None = None,
        relationships: list[Any] | None = None,
        *,
        identity: ToolCallIdentity | None = None,
        input_trust: TrustClassification = DEFAULT_MCP_INPUT_TRUST,
    ) -> dict[str, Any]:
        """Build a knowledge graph through the boundary."""

        outcome, call_identity = await self._invoke(
            TOOL_BUILD_KNOWLEDGE_GRAPH,
            {"text": text, "entities": entities, "relationships": relationships},
            identity=identity,
            input_trust=input_trust,
        )
        if outcome.succeeded:
            return self._succeeded(outcome, call_identity)
        if outcome.status is ToolOutcomeStatus.IN_PROGRESS:
            return self._in_progress(
                outcome,
                call_identity,
                empty={"graph": {}, "entities": [], "relationships": []},
            )
        return self._degraded(
            outcome,
            call_identity,
            fallback=lambda: self._fallback_knowledge_graph(
                text, entities, relationships
            ),
            empty={"graph": {}, "entities": [], "relationships": []},
        )

    async def get_tool_status(self) -> dict[str, Any]:
        """Report what the integration can currently reach."""

        try:
            connected = await self._connected_client()
            health = await connected.health_check()
            available_tools = connected.get_available_tools()
        except Exception as error:
            logger.error("mcp_tool_status_get_failed", error=str(error))
            return {
                "initialized": False,
                "error": str(error),
                "fallback_enabled": self.enable_fallback,
                "mediated": True,
            }

        return {
            "initialized": self._initialized,
            "client_health": health,
            "available_tools": available_tools,
            "mediated": True,
            "registered_tools": [
                TOOL_ACADEMIC_SEARCH,
                TOOL_FORMAT_CITATIONS,
                TOOL_ANALYZE_STATISTICS,
                TOOL_BUILD_KNOWLEDGE_GRAPH,
            ],
        }

    # ------------------------------------------------------------------
    # mediation
    # ------------------------------------------------------------------

    async def _invoke(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        identity: ToolCallIdentity | None,
        input_trust: TrustClassification,
    ) -> tuple[ToolOutcome, ToolCallIdentity]:
        """Run one mediated call and return its outcome with the identity used.

        The identity is returned rather than stashed on ``self``. An earlier
        version recorded it on the instance for the projection methods to read
        back, and that is correct only because no ``await`` separates the write
        from the read -- an invariant nothing holds up, in a class whose caller
        routinely runs several of these operations concurrently against one
        instance. Threading it through removes the invariant rather than
        relying on it.

        It was not a live race, and the test named for it does not catch one;
        see that test's docstring.
        """

        call_identity = (
            identity
            or self._identity
            or ToolCallIdentity.unbound(label=tool_name.replace(".", "-"))
        )
        now = datetime.now(UTC)
        spec = self._boundary.specification(tool_name)
        grants = self._grants
        if grants is None:
            grants = (
                []
                if call_identity.durable
                else [
                    self_issued_grant(
                        tool_name=spec.name,
                        policy=SelfIssuedPolicy(
                            sensitivity=MCP_SENSITIVITY,
                            max_input_trust=MCP_MAX_INPUT_TRUST,
                            tool_versions=(spec.version,),
                        ),
                        identity=call_identity,
                        now=now,
                    )
                ]
            )
        matching_grant = next(
            (grant for grant in grants if grant.tool_name == tool_name), None
        )
        outcome = await self._boundary.invoke(
            tool_name=tool_name,
            run_id=call_identity.run_id,
            task_id=call_identity.task_id,
            attempt_id=call_identity.attempt_id,
            organization_id=call_identity.organization_id,
            capability_scope=(
                matching_grant.capability_scope if matching_grant else tool_name
            ),
            arguments=arguments,
            input_trust=input_trust,
            output_trust=MCP_OUTPUT_TRUST,
            grants=grants,
        )
        return outcome, call_identity

    def _succeeded(
        self, outcome: ToolOutcome, identity: ToolCallIdentity
    ) -> dict[str, Any]:
        payload: dict[str, Any] = dict(plain(outcome.unwrap()))
        payload.update(
            {
                "success": True,
                "degraded": False,
                "fallback": False,
                "data_source": "mcp_tools",
                "tool_outcome": outcome.status.value,
                "tool_invocation_id": outcome.invocation.tool_invocation_id,
                "identity_bound": identity.bound,
            }
        )
        return payload

    def _degraded(
        self,
        outcome: ToolOutcome,
        identity: ToolCallIdentity,
        *,
        fallback: Any,
        empty: dict[str, Any],
    ) -> dict[str, Any]:
        """Return the projection for a terminal non-success outcome.

        ``success`` is ``False`` on every branch. That is the whole fix for
        4-Char's CRITICAL: the flag every downstream consumer already reads now
        answers the question it was always assumed to answer.
        """

        logger.warning(
            "mcp_tool_call_degraded",
            tool=outcome.invocation.tool_name,
            outcome=outcome.status.value,
            error_code=outcome.error_code,
            fallback_used=self.enable_fallback,
        )
        payload = dict(fallback()) if self.enable_fallback else dict(empty)
        payload.update(
            {
                "success": False,
                "degraded": True,
                "fallback": self.enable_fallback,
                "data_source": "fallback",
                "tool_outcome": outcome.status.value,
                "error_code": outcome.error_code,
                "detail": outcome.detail,
                "retry": outcome.retry.value,
                "tool_invocation_id": outcome.invocation.tool_invocation_id,
                "identity_bound": identity.bound,
            }
        )
        return payload

    def _in_progress(
        self,
        outcome: ToolOutcome,
        identity: ToolCallIdentity,
        *,
        empty: dict[str, Any],
    ) -> dict[str, Any]:
        """Project pending work without inventing an operation result.

        ``IN_PROGRESS`` is a committed reservation, not a terminal outage.
        It must never enter the fallback branch: a retry that is still owned by
        another invocation has no operation payload to project yet.
        """

        logger.info(
            "mcp_tool_call_in_progress",
            tool=outcome.invocation.tool_name,
            retry=outcome.retry.value,
            tool_invocation_id=outcome.invocation.tool_invocation_id,
        )
        payload = dict(empty)
        payload.update(
            {
                "success": False,
                "degraded": True,
                "fallback": False,
                "data_source": "in_progress",
                "tool_outcome": outcome.status.value,
                "error_code": outcome.error_code,
                "detail": outcome.detail,
                "reason": outcome.detail,
                "retry": outcome.retry.value,
                "tool_invocation_id": outcome.invocation.tool_invocation_id,
                "identity_bound": identity.bound,
            }
        )
        return payload

    # ------------------------------------------------------------------
    # stand-in content for a degraded result
    # ------------------------------------------------------------------

    def _fallback_academic_search(
        self,
        query: str,
        databases: list[str],
        max_results: int,
    ) -> dict[str, Any]:
        """Stand-in sources for a degraded search.

        Every source carries ``source: "fallback"``, and the envelope this is
        wrapped in reports ``success: False``. It is not a search result and
        nothing downstream may treat it as one.
        """
        logger.info(
            "mcp_academic_search_fallback_used",
            database_count=len(databases),
            max_results=max_results,
        )

        mock_sources = []
        for i in range(min(max_results, 5)):
            mock_sources.append(
                {
                    "title": f"Research Paper {i + 1}: {query}",
                    "authors": [f"Author {i + 1}"],
                    "year": 2024 - i,
                    "journal": f"Journal of {query.split(maxsplit=1)[0] if query.split() else 'Research'}",
                    "abstract": f"This paper investigates {query} using systematic methodology...",
                    "source": "fallback",
                    "relevance_score": 0.8 - (i * 0.1),
                },
            )

        return {
            "sources": mock_sources,
            "total_found": len(mock_sources),
            "databases_searched": databases,
            "search_strategy": "Fallback search (MCP tools unavailable)",
        }

    def _fallback_citation_formatting(
        self,
        sources: list[dict[str, Any]],
        style: str,
    ) -> dict[str, Any]:
        """Stand-in citations for a degraded format call."""
        logger.info(
            "mcp_citation_formatting_fallback_used",
            source_count=len(sources),
            style=style,
        )

        formatted_citations = []
        for source in sources:
            if style.upper() == "APA":
                citation = f"{source.get('authors', ['Unknown'])[0]} ({source.get('year', 'n.d.')}). {source.get('title', 'Untitled')}."
            else:
                citation = f"{source.get('title', 'Untitled')} by {source.get('authors', ['Unknown'])[0]} ({source.get('year', 'n.d.')})"

            formatted_citations.append(
                {"citation": citation, "style": style, "source": source},
            )

        return {
            "formatted_citations": formatted_citations,
            "style": style,
            "total_sources": len(sources),
        }

    def _fallback_statistical_analysis(
        self,
        operation: str,
        data: list[Any] | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Locally computed statistics for a degraded analysis call.

        Unlike the other three, this one is genuinely computed rather than
        invented — but it is still not what the caller asked for, and the
        envelope still reports ``success: False``.
        """
        logger.info(
            "mcp_statistical_analysis_fallback_used",
            operation=operation,
            data_points=len(data) if data else 0,
        )

        result: dict[str, Any] = {"operation": operation, "fallback": True}

        if operation == "descriptive" and data:
            import statistics

            result.update(
                {
                    "mean": statistics.mean(data),
                    "median": statistics.median(data),
                    "std_dev": statistics.stdev(data) if len(data) > 1 else 0,
                    "count": len(data),
                },
            )
        else:
            result["message"] = f"Basic {operation} analysis completed (fallback mode)"

        return {
            "analysis": result,
            "operation": operation,
            "data_points": len(data) if data else 0,
        }

    def _fallback_knowledge_graph(
        self,
        text: str | None,
        entities: list[Any] | None,
        relationships: list[Any] | None,
    ) -> dict[str, Any]:
        """Stand-in graph for a degraded build call."""
        logger.info(
            "mcp_knowledge_graph_fallback_used",
            entity_count=len(entities) if entities else 0,
            relationship_count=len(relationships) if relationships else 0,
        )

        extracted_entities = []
        if text:
            words = text.split()
            for i, word in enumerate(words):
                if word[0].isupper() and len(word) > 2:
                    extracted_entities.append(
                        {"id": str(i), "text": word, "type": "entity", "position": i},
                    )

        return {
            "graph": {"nodes": len(extracted_entities), "edges": 0},
            "entities": extracted_entities,
            "relationships": relationships or [],
        }


def create_mcp_integrated_agent(
    agent_class: type,
    config: dict[str, Any] | None = None,
) -> Any:
    """Factory function to create an agent with MCP integration.

    Args:
        agent_class: Agent class to instantiate
        config: Configuration including MCP settings

    Returns:
        Agent instance with MCP integration

    """
    config = config or {}

    mcp_config = config.get("mcp", {})
    mcp_integration = MCPIntegration(
        config=mcp_config,
        enable_fallback=mcp_config.get("enable_fallback", True),
    )

    agent_config = config.copy()
    agent_config["mcp_integration"] = mcp_integration

    return agent_class(config=agent_config)


__all__ = [
    "DEFAULT_MCP_INPUT_TRUST",
    "MCP_MAX_INPUT_TRUST",
    "MCP_OUTPUT_TRUST",
    "MCPIntegration",
    "create_mcp_integrated_agent",
]
