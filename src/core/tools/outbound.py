"""Pinned outbound host constants and guarded HTTPS fetch helpers.

Lives in ``src.core.tools`` with no dependency on ``src.mcp.tools`` or
``src.agents`` so capability issuance, tool specs, and tool implementations
share one source of truth without cross-layer eager imports.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from types import MappingProxyType
from typing import Any, Final

import httpx

PUBMED_HOST: Final[str] = "eutils.ncbi.nlm.nih.gov"
ARXIV_HOST: Final[str] = "export.arxiv.org"
ARXIV_PDF_HOST: Final[str] = "arxiv.org"
PMC_HOST: Final[str] = "pmc.ncbi.nlm.nih.gov"
SEMANTIC_SCHOLAR_HOST: Final[str] = "api.semanticscholar.org"
CROSSREF_HOST: Final[str] = "api.crossref.org"
DOI_HOST: Final[str] = "doi.org"
OPENALEX_HOST: Final[str] = "api.openalex.org"

ACADEMIC_SEARCH_DATABASE_HOSTS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "pubmed": PUBMED_HOST,
        "arxiv": ARXIV_HOST,
        "semantic_scholar": SEMANTIC_SCHOLAR_HOST,
    }
)
ACADEMIC_SEARCH_OUTBOUND_HOSTS: Final[tuple[str, ...]] = tuple(
    sorted(ACADEMIC_SEARCH_DATABASE_HOSTS.values())
)
CITATION_OUTBOUND_HOSTS: Final[tuple[str, ...]] = (CROSSREF_HOST,)
SOURCE_FETCH_OUTBOUND_HOSTS: Final[tuple[str, ...]] = tuple(
    sorted(
        {
            ARXIV_PDF_HOST,
            ARXIV_HOST,
            PUBMED_HOST,
            PMC_HOST,
            SEMANTIC_SCHOLAR_HOST,
            CROSSREF_HOST,
            DOI_HOST,
            OPENALEX_HOST,
        }
    )
)

OUTBOUND_TOOL_HOSTS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "mcp.academic_search": ACADEMIC_SEARCH_OUTBOUND_HOSTS,
        "mcp.format_citations": CITATION_OUTBOUND_HOSTS,
        "source.fetch": SOURCE_FETCH_OUTBOUND_HOSTS,
    }
)
"""Pinned outbound host sets for tools classified as ``EXTERNAL_READ``."""


class OutboundHostNotAllowedError(PermissionError):
    """Raised when an outbound HTTP call targets an undeclared or unapproved host."""


def _validate_host_collection(hosts: Collection[str], *, name: str) -> set[str]:
    if isinstance(hosts, str | bytes):
        raise TypeError(
            f"{name} must be a collection of hostnames, not {type(hosts).__name__}"
        )
    return {host.lower() for host in hosts}


def require_allowed_outbound_host(
    url: str,
    *,
    declared_hosts: Collection[str],
    allowed_hosts: Collection[str] | None,
) -> None:
    """Refuse any outbound request that is not a plain HTTPS call to an allowed host.

    Uses ``httpx.URL`` (the same parser as ``httpx.AsyncClient``) to enforce
    ``https`` scheme, no userinfo, no non-default port, and exact membership in
    ``declared_hosts``. When ``allowed_hosts`` is provided (every call mediated
    through ``ToolBoundary`` passes a concrete tuple), the host must also appear
    in ``allowed_hosts``; ``allowed_hosts=None`` is reserved for direct in-process
    tool invocations where only ``declared_hosts`` applies.
    """
    declared = _validate_host_collection(declared_hosts, name="declared_hosts")
    approved = (
        None
        if allowed_hosts is None
        else _validate_host_collection(allowed_hosts, name="allowed_hosts")
    )
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as exc:
        raise OutboundHostNotAllowedError(f"invalid outbound URL {url!r}") from exc

    if parsed.scheme != "https":
        raise OutboundHostNotAllowedError(
            f"outbound URL scheme {parsed.scheme!r} is not 'https'"
        )
    if parsed.userinfo:
        raise OutboundHostNotAllowedError("outbound URL must not contain userinfo")
    if parsed.port is not None:
        raise OutboundHostNotAllowedError(
            f"outbound URL must not specify non-default port {parsed.port}"
        )
    hostname = parsed.host.lower()
    if not hostname or hostname not in declared:
        raise OutboundHostNotAllowedError(
            f"outbound host {hostname!r} is not in the tool's declared host set"
        )
    if approved is not None and hostname not in approved:
        raise OutboundHostNotAllowedError(
            f"outbound host {hostname!r} is not in the authority's allowed hosts"
        )


async def checked_get(
    client: Any,
    url: str,
    *,
    declared_hosts: Collection[str],
    allowed_hosts: Collection[str] | None,
    params: Mapping[str, Any] | None = None,
) -> Any:
    """Validate ``url`` against host allowlists and issue a non-redirecting GET."""
    require_allowed_outbound_host(
        url,
        declared_hosts=declared_hosts,
        allowed_hosts=allowed_hosts,
    )
    kwargs: dict[str, Any] = {}
    if params is not None:
        kwargs["params"] = params
    if isinstance(client, httpx.AsyncClient):
        kwargs["follow_redirects"] = False
    return await client.get(url, **kwargs)


__all__ = [
    "ACADEMIC_SEARCH_DATABASE_HOSTS",
    "ACADEMIC_SEARCH_OUTBOUND_HOSTS",
    "ARXIV_HOST",
    "ARXIV_PDF_HOST",
    "CITATION_OUTBOUND_HOSTS",
    "CROSSREF_HOST",
    "DOI_HOST",
    "OPENALEX_HOST",
    "OUTBOUND_TOOL_HOSTS",
    "PMC_HOST",
    "PUBMED_HOST",
    "SEMANTIC_SCHOLAR_HOST",
    "SOURCE_FETCH_OUTBOUND_HOSTS",
    "OutboundHostNotAllowedError",
    "checked_get",
    "require_allowed_outbound_host",
]
