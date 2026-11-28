"""The acquisition tool, SSRF-hardened HTTP fetcher, and PDF full-text extraction.

There is a genuine tension between two frozen rules, and this module is where
it is resolved. ``snapshot_digest`` must hash **verbatim, unredacted** bytes,
because evidence integrity means "this is what the source said" and scrubbing
would shift every locator offset. But the tool boundary **redacts everything it
persists, publishes, and returns**. A fetch tool that returned page content as
its output would therefore hand the acquisition service a redacted body — and
hashing that, or locating spans in it, would produce citations into text no
source ever contained.

The resolution is to keep the bytes out of the boundary record entirely. The
handler writes them into the quarantined snapshot store *inside itself*, and
returns only a **handle**: a digest, a storage URI, a length, a media type, and
whatever the source said about licensing. Every one of those is safe to redact,
persist, and publish, because none of them is content. The acquisition service
then reads the bytes back out of the store and re-checks them against the
digest the tool reported, so a handler that lies about what it stored is caught
by the next step rather than trusted.

When the fetched content is a PDF (``application/pdf``, or absent/generic binary
media type starting with ``%PDF-`` at offset 0), the handler extracts full text
through the sandboxed PDF extractor, stores the normalized text via
:func:`~src.core.acquisition.pdf_text.build_extracted_text_artifact` tagged as
``full_text``, and includes its digest, storage URI, and canonical locator on
the returned handle.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Collection, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, cast, final

import httpcore
import httpx
from pydantic import BaseModel, ConfigDict, Field

from src.core.acquisition.pdf_text import (
    ExtractedText,
    build_extracted_text_artifact,
)
from src.core.acquisition.resolution import locate
from src.core.contracts.capabilities import SensitivityClass
from src.core.contracts.redaction import SecretRef, snapshot_digest
from src.core.tools.outbound import (
    SOURCE_FETCH_OUTBOUND_HOSTS,
    OutboundHostNotAllowedError,
    require_allowed_outbound_host,
)
from src.core.tools.spec import ToolCallContext, ToolSpec

from .snapshots import SnapshotStore
from .sources import LicenseDeclaration, SourceLicense, SourceType

__all__ = [
    "DEFAULT_MAX_FETCH_BYTES",
    "DEFAULT_MAX_REDIRECTS",
    "SOURCE_FETCH_TOOL",
    "DnsResolver",
    "FetchedSource",
    "HardenedHttpFetcher",
    "PdfExtractor",
    "SSRFBlockedError",
    "SourceFetchInput",
    "SourceFetchOutput",
    "SourceFetcher",
    "hardened_source_fetch_tool",
    "is_disallowed_ip",
    "snapshot_fetch_tool",
    "validate_fetch_target",
]

SOURCE_FETCH_TOOL: Final[str] = "source.fetch"
"""The registered name of the acquisition tool."""

DEFAULT_MAX_REDIRECTS: Final[int] = 5
DEFAULT_MAX_FETCH_BYTES: Final[int] = 25 * 1024 * 1024
_REDIRECT_STATUS_CODES: Final[frozenset[int]] = frozenset({301, 302, 303, 307, 308})
_PDF_SNIFFABLE_MEDIA_TYPES: Final[frozenset[str]] = frozenset(
    {"", "application/octet-stream", "binary/octet-stream"}
)
_METADATA_NETWORKS: Final[tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]] = (
    ipaddress.ip_network("169.254.169.254/32"),
    ipaddress.ip_network("fd00:ec2::254/128"),
    ipaddress.ip_network("100.100.100.200/32"),
)


class SSRFBlockedError(OutboundHostNotAllowedError):
    """Raised when a fetch target violates SSRF, IP, redirect, or host policy."""


DnsResolver = Callable[[str, int], Awaitable[Sequence[str]]]
PdfExtractor = Callable[[bytes], Awaitable[tuple[ExtractedText, int]]]


def is_disallowed_ip(address: str) -> bool:
    """Return True if ``address`` is private, loopback, link-local, metadata, or non-global."""
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return True
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped
    if (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_reserved
        or parsed.is_unspecified
        or not parsed.is_global
    ):
        return True
    return any(parsed in network for network in _METADATA_NETWORKS)


async def _default_dns_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _is_ip_literal(host: str) -> bool:
    candidate = host.strip("[]")
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return True


async def validate_fetch_target(
    url: str,
    *,
    declared_hosts: Collection[str],
    allowed_hosts: Collection[str] | None,
    dns_resolver: DnsResolver,
) -> tuple[httpx.URL, tuple[str, ...]]:
    """Validate URL scheme, host allowlist, and resolved IPs before connecting."""
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as exc:
        raise SSRFBlockedError(f"invalid fetch URL {url!r}") from exc

    hostname = parsed.host.lower()
    if not hostname or _is_ip_literal(hostname):
        raise SSRFBlockedError(f"IP literal or empty host refused: {hostname!r}")

    try:
        require_allowed_outbound_host(
            url,
            declared_hosts=declared_hosts,
            allowed_hosts=allowed_hosts,
        )
    except OutboundHostNotAllowedError as exc:
        raise SSRFBlockedError(str(exc)) from exc

    try:
        resolved_ips = list(await dns_resolver(hostname, 443))
    except OSError as exc:
        raise SSRFBlockedError(f"DNS resolution failed for {hostname!r}") from exc

    if not resolved_ips:
        raise SSRFBlockedError(f"DNS returned no addresses for {hostname!r}")

    for ip_str in resolved_ips:
        if is_disallowed_ip(ip_str):
            raise SSRFBlockedError(
                f"host {hostname!r} resolved to disallowed IP {ip_str!r}"
            )

    return parsed, tuple(resolved_ips)


def _is_pdf_content(media_type: str, content: bytes) -> bool:
    mime = media_type.partition(";")[0].strip().lower()
    if mime == "application/pdf":
        return True
    if mime in _PDF_SNIFFABLE_MEDIA_TYPES:
        return content.startswith(b"%PDF-")
    return False


class _PinnedIpNetworkBackend(httpcore.AsyncNetworkBackend):
    """Dial only pre-validated IPs so TCP connect cannot perform a second DNS lookup."""

    def __init__(
        self,
        *,
        pinned_ips: dict[str, tuple[str, ...]],
        inner: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self._pinned_ips = pinned_ips
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        ips = self._pinned_ips.get(host.lower())
        if not ips:
            raise SSRFBlockedError(f"no validated IP pinned for host {host!r}")
        last_error: httpcore.ConnectError | None = None
        for ip_str in ips:
            if is_disallowed_ip(ip_str):
                raise SSRFBlockedError(
                    f"disallowed pinned IP {ip_str!r} for host {host!r}"
                )
            try:
                return await self._inner.connect_tcp(
                    host=ip_str,
                    port=port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except httpcore.ConnectError as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise SSRFBlockedError("unix domain sockets are forbidden for source.fetch")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class _PinnedIpAsyncTransport(httpx.AsyncHTTPTransport):
    """``AsyncHTTPTransport`` wired to ``_PinnedIpNetworkBackend`` without keepalive reuse."""

    def __init__(self, *, network_backend: httpcore.AsyncNetworkBackend) -> None:
        super().__init__(
            trust_env=False,
            limits=httpx.Limits(max_keepalive_connections=0),
        )
        # Disabling keepalive forces every redirect hop to dial the validated IP
        # recorded for that hop rather than reusing an earlier socket.
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(verify=True, trust_env=False),
            max_connections=10,
            max_keepalive_connections=0,
            http1=True,
            http2=False,
            network_backend=network_backend,
        )


async def _default_pdf_extractor(data: bytes) -> tuple[ExtractedText, int]:
    from src.services.document_ingestion import extract_pdf_sandboxed

    return await extract_pdf_sandboxed(data)


class SourceFetchInput(BaseModel):
    """What the acquisition tool is asked for.

    ``credential`` is ``SecretRef``-typed rather than a string, so a literal
    cannot type-check its way into a payload that is hashed, persisted, and
    published. The boundary's registration-time audit enforces the same rule on
    any tool declaring a credential-named field, and this is that rule's own
    tool obeying it.
    """

    model_config = ConfigDict(extra="forbid")

    source_uri: str = Field(min_length=1)
    source_type: SourceType
    credential: SecretRef | None = None


class SourceFetchOutput(BaseModel):
    """The handle a fetch returns. Deliberately carries no content.

    Everything here survives redaction intact and identifies the snapshot
    without reproducing it. ``content_sha256`` is the claim the acquisition
    service checks the store against; it is not taken on trust.
    """

    content_sha256: str = Field(min_length=64, max_length=64)
    storage_uri: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    byte_length: int = Field(gt=0)
    final_uri: str = Field(min_length=1)
    license_identifier: str | None = None
    license_declared_by: LicenseDeclaration
    license_statement: str | None = None
    content_kind: str | None = None
    extracted_text_sha256: str | None = Field(
        default=None, min_length=64, max_length=64
    )
    extracted_text_storage_uri: str | None = Field(default=None, min_length=1)
    extracted_text_locator: str | None = Field(default=None, min_length=1)
    extracted_text_byte_length: int | None = Field(default=None, gt=0)
    extracted_page_count: int | None = Field(default=None, gt=0)
    extracted_pages: list[dict[str, int]] | None = None

    def license(self) -> SourceLicense:
        """Rebuild the license statement this output carries."""

        return SourceLicense(
            identifier=self.license_identifier,
            declared_by=self.license_declared_by,
            statement=self.license_statement,
        )


@final
@dataclass(frozen=True, slots=True)
class FetchedSource:
    """What a fetcher returns to the handler, before anything is stored.

    This value never leaves the handler. It is the only place raw content
    exists outside the snapshot store, and it exists there for exactly as long
    as it takes to write it.
    """

    content: bytes
    media_type: str
    final_uri: str
    license: SourceLicense = field(default_factory=SourceLicense.undeclared)
    """Defaults to *undeclared*, never to a permissive license."""


SourceFetcher = Callable[[SourceFetchInput, ToolCallContext], Awaitable[FetchedSource]]
"""Performs the actual retrieval. Owns transport; owns nothing evidential."""


class HardenedHttpFetcher:
    """SSRF-hardened HTTPS fetcher with host allowlists, DNS IP checks, and bounded redirects."""

    def __init__(
        self,
        *,
        declared_hosts: Sequence[str] = SOURCE_FETCH_OUTBOUND_HOSTS,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        max_bytes: int = DEFAULT_MAX_FETCH_BYTES,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
        dns_resolver: DnsResolver | None = None,
        network_backend: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        if max_redirects < 0:
            raise ValueError("max_redirects must be non-negative")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._declared_hosts = tuple(declared_hosts)
        self._max_redirects = max_redirects
        self._max_bytes = max_bytes
        self._timeout_seconds = timeout_seconds
        self._client = client
        self._dns_resolver = dns_resolver or _default_dns_resolver
        self._network_backend = network_backend

    async def __call__(
        self, request: SourceFetchInput, context: ToolCallContext
    ) -> FetchedSource:
        # Cross-host redirects (e.g. doi.org -> arxiv.org) need the grant's full
        # approved host set, while direct unit callers may supply only allowed_hosts.
        allowed_hosts = context.grant_allowed_hosts or context.allowed_hosts
        if self._client is not None:
            return await self._fetch_with_client(
                self._client, request.source_uri, allowed_hosts=allowed_hosts
            )
        pinned_ips: dict[str, tuple[str, ...]] = {}
        transport = _PinnedIpAsyncTransport(
            network_backend=_PinnedIpNetworkBackend(
                pinned_ips=pinned_ips,
                inner=self._network_backend,
            )
        )
        async with httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(self._timeout_seconds),
        ) as owned_client:
            return await self._fetch_with_client(
                owned_client,
                request.source_uri,
                allowed_hosts=allowed_hosts,
                pinned_ips=pinned_ips,
            )

    async def _fetch_with_client(
        self,
        client: httpx.AsyncClient,
        start_url: str,
        *,
        allowed_hosts: tuple[str, ...],
        pinned_ips: dict[str, tuple[str, ...]] | None = None,
    ) -> FetchedSource:
        current_url = start_url
        hops = 0
        while True:
            parsed, resolved_ips = await validate_fetch_target(
                current_url,
                declared_hosts=self._declared_hosts,
                allowed_hosts=allowed_hosts,
                dns_resolver=self._dns_resolver,
            )
            if pinned_ips is not None:
                pinned_ips[parsed.host.lower()] = resolved_ips

            async with client.stream(
                "GET",
                str(parsed),
                follow_redirects=False,
                extensions={"sni_hostname": parsed.host},
            ) as response:
                if response.status_code in _REDIRECT_STATUS_CODES:
                    location = response.headers.get("location")
                    if not location:
                        raise SSRFBlockedError(
                            "redirect response missing Location header"
                        )
                    if hops >= self._max_redirects:
                        raise SSRFBlockedError(
                            f"exceeded max redirects ({self._max_redirects})"
                        )
                    hops += 1
                    try:
                        current_url = str(parsed.join(location))
                    except httpx.InvalidURL as exc:
                        raise SSRFBlockedError(
                            f"invalid redirect Location {location!r}"
                        ) from exc
                    continue

                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        declared_length = int(content_length.strip())
                    except ValueError:
                        declared_length = None
                    if (
                        declared_length is not None
                        and declared_length > self._max_bytes
                    ):
                        raise ValueError(
                            f"fetched source exceeded {self._max_bytes} byte limit"
                        )

                buf = bytearray()
                async for chunk in response.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > self._max_bytes:
                        raise ValueError(
                            f"fetched source exceeded {self._max_bytes} byte limit"
                        )
                body = bytes(buf)
                if not body:
                    raise ValueError("fetched source returned an empty body")
                raw_content_type = response.headers.get("content-type")
                if raw_content_type is None:
                    media_type = "application/octet-stream"
                else:
                    media_type = (
                        raw_content_type.partition(";")[0].strip()
                        or "application/octet-stream"
                    )
                return FetchedSource(
                    content=body,
                    media_type=media_type,
                    final_uri=str(parsed),
                )


def _target_hosts_resolver(
    declared_hosts: tuple[str, ...],
) -> Callable[[BaseModel], tuple[str, ...]]:
    declared_set = frozenset(h.lower() for h in declared_hosts)

    def resolve_hosts(params: BaseModel) -> tuple[str, ...]:
        assert isinstance(params, SourceFetchInput)
        try:
            parsed = httpx.URL(params.source_uri)
        except httpx.InvalidURL:
            return ()
        host = parsed.host.lower()
        if parsed.scheme == "https" and host in declared_set:
            return (host,)
        return ()

    return resolve_hosts


def snapshot_fetch_tool(
    *,
    store: SnapshotStore,
    fetcher: SourceFetcher,
    timeout_seconds: float,
    name: str = SOURCE_FETCH_TOOL,
    version: str = "1.0.0",
    sensitivity: SensitivityClass = SensitivityClass.READ_ONLY,
    classify_error: Callable[[BaseException], Any] | None = None,
    outbound_hosts: tuple[str, ...] = (),
    resolve_target_hosts: Callable[[BaseModel], tuple[str, ...]] | None = None,
    pdf_extractor: PdfExtractor | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ToolSpec:
    """Build the boundary specification for an acquisition tool."""

    extract_pdf = pdf_extractor or _default_pdf_extractor
    now_fn = clock or (lambda: datetime.now(UTC))

    async def handler(payload: Any, context: ToolCallContext) -> dict[str, Any]:
        request = cast(SourceFetchInput, payload)
        fetched = await fetcher(request, context)
        # Stored before anything is returned: the digest the boundary records
        # is only meaningful if the bytes it names are already durable.
        storage_uri = store.put(fetched.content)
        licence = fetched.license
        result: dict[str, Any] = {
            "content_sha256": snapshot_digest(fetched.content),
            "storage_uri": storage_uri,
            "media_type": fetched.media_type,
            "byte_length": len(fetched.content),
            "final_uri": fetched.final_uri,
            "license_identifier": licence.identifier,
            "license_declared_by": licence.declared_by.value,
            "license_statement": licence.statement,
        }
        if _is_pdf_content(fetched.media_type, fetched.content):
            extracted, _removed = await extract_pdf(fetched.content)
            text_bytes = extracted.text.encode("utf-8")
            text_locator = locate("bytes", 0, len(text_bytes))
            artifact = build_extracted_text_artifact(
                extracted,
                store=store,
                artifact_id=f"extracted-{extracted.text_sha256[:40]}",
                run_id=context.run_id,
                task_id=context.task_id,
                attempt_id=context.attempt_id,
                created_at=now_fn(),
                content_kind="full_text",
                source_uri=fetched.final_uri,
                locator=text_locator,
            )
            result.update(
                {
                    "content_kind": "full_text",
                    "extracted_text_sha256": artifact.content_sha256,
                    "extracted_text_storage_uri": artifact.storage_uri,
                    "extracted_text_locator": text_locator,
                    "extracted_text_byte_length": len(text_bytes),
                    "extracted_page_count": len(extracted.pages),
                    "extracted_pages": [
                        {"page": span.page, "start": span.start, "end": span.end}
                        for span in extracted.pages
                    ],
                }
            )
        return result

    return ToolSpec(
        name=name,
        version=version,
        sensitivity=sensitivity,
        input_model=SourceFetchInput,
        output_model=SourceFetchOutput,
        timeout_seconds=timeout_seconds,
        handler=handler,
        classify_error=classify_error,
        outbound_hosts=outbound_hosts,
        resolve_target_hosts=resolve_target_hosts,
    )


def hardened_source_fetch_tool(
    *,
    store: SnapshotStore,
    timeout_seconds: float,
    fetcher: SourceFetcher | None = None,
    declared_hosts: tuple[str, ...] = SOURCE_FETCH_OUTBOUND_HOSTS,
    name: str = SOURCE_FETCH_TOOL,
    version: str = "1.0.0",
    pdf_extractor: PdfExtractor | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ToolSpec:
    """Build the SSRF-hardened ``source.fetch`` specification with ``EXTERNAL_READ`` policy."""
    active_fetcher = fetcher or HardenedHttpFetcher(
        declared_hosts=declared_hosts,
        timeout_seconds=timeout_seconds,
    )
    return snapshot_fetch_tool(
        store=store,
        fetcher=active_fetcher,
        timeout_seconds=timeout_seconds,
        name=name,
        version=version,
        sensitivity=SensitivityClass.EXTERNAL_READ,
        outbound_hosts=declared_hosts,
        resolve_target_hosts=_target_hosts_resolver(declared_hosts),
        pdf_extractor=pdf_extractor,
        clock=clock,
    )
