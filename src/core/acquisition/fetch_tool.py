"""The acquisition tool, and why raw source bytes never cross the boundary.

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

This also keeps the whole call under the boundary's guarantees — capability
authorization, deadline, cancellation, circuit breaking, idempotency, and a
durable audit record written before anything is published — which is the
requirement that all acquisition go through one mediated path.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final, cast, final

from pydantic import BaseModel, Field

from src.core.contracts.capabilities import SensitivityClass
from src.core.contracts.redaction import SecretRef, snapshot_digest
from src.core.tools.spec import ToolCallContext, ToolSpec

from .snapshots import SnapshotStore
from .sources import LicenseDeclaration, SourceLicense, SourceType

__all__ = [
    "SOURCE_FETCH_TOOL",
    "FetchedSource",
    "SourceFetchInput",
    "SourceFetchOutput",
    "SourceFetcher",
    "snapshot_fetch_tool",
]

SOURCE_FETCH_TOOL: Final[str] = "source.fetch"
"""The registered name of the acquisition tool."""


class SourceFetchInput(BaseModel):
    """What the acquisition tool is asked for.

    ``credential`` is ``SecretRef``-typed rather than a string, so a literal
    cannot type-check its way into a payload that is hashed, persisted, and
    published. The boundary's registration-time audit enforces the same rule on
    any tool declaring a credential-named field, and this is that rule's own
    tool obeying it.
    """

    source_uri: str = Field(min_length=1)
    source_type: SourceType
    credential: SecretRef | None = None


class SourceFetchOutput(BaseModel):
    """The handle a fetch returns. Deliberately carries no content.

    Everything here survives redaction intact and identifies the snapshot
    without reproducing it. ``content_sha256`` is the claim the acquisition
    service checks the store against; it is not taken on trust.

    **``storage_uri`` must be credential-free — scheme and path, never a
    presigned URL or a token-bearing query string.** This handle *does* cross
    the boundary, so it is redacted like every other output, and redaction
    mangles exactly the two shapes a credentialed URI takes:

    ``https://KEYID:SECRET@bucket/obj`` becomes ``[REDACTED]bucket/obj`` (the
    userinfo pattern), and ``https://store/obj?token=ghp_...`` becomes
    ``...?token=[REDACTED]``. Both come back **unusable, silently**, because a
    redacted string is still a string and nothing downstream can tell it was
    altered — the snapshot would simply not be found. A store needing
    credentials resolves them at read time through the secret provider.

    ``test_the_acquisition_handle_survives_redaction_unchanged`` pins this as a
    property of the seam, so swapping in a presigned store fails a test rather
    than being rediscovered in production.
    """

    content_sha256: str = Field(min_length=64, max_length=64)
    storage_uri: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    byte_length: int = Field(gt=0)
    final_uri: str = Field(min_length=1)
    license_identifier: str | None = None
    license_declared_by: LicenseDeclaration
    license_statement: str | None = None

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
    """Defaults to *undeclared*, never to a permissive license.

    A fetcher that does not look for a license statement records that the
    source declared none, which is the restrictive reading. Defaulting the
    other way would let an omission in a fetcher read, downstream, as
    permission the source never gave.
    """


SourceFetcher = Callable[[SourceFetchInput, ToolCallContext], Awaitable[FetchedSource]]
"""Performs the actual retrieval. Owns transport; owns nothing evidential."""


def snapshot_fetch_tool(
    *,
    store: SnapshotStore,
    fetcher: SourceFetcher,
    timeout_seconds: float,
    name: str = SOURCE_FETCH_TOOL,
    version: str = "1.0.0",
    sensitivity: SensitivityClass = SensitivityClass.READ_ONLY,
    classify_error: Callable[[BaseException], Any] | None = None,
) -> ToolSpec:
    """Build the boundary specification for an acquisition tool.

    Args:
        store: The quarantined snapshot store the handler writes into.
        fetcher: Performs the retrieval. Everything evidential — hashing,
            storing, and the handle that comes back — happens around it rather
            than inside it, so a new transport cannot accidentally change what
            a snapshot means.
        timeout_seconds: The deadline. No default, per the boundary's rule that
            every tool has a deadline somebody chose.
        name: The registered tool name.
        version: The tool version a capability grant authorizes.
        sensitivity: What the call can do outside the run. Reading a public
            source is ``read_only``; a fetcher that can reach a tenant's own
            systems is not, and says so here.
        classify_error: Optional retry classification, defaulting to terminal.

    Returns:
        The specification to register with the boundary.
    """

    async def handler(
        payload: Any, context: ToolCallContext
    ) -> dict[str, str | int | None]:
        request = cast(SourceFetchInput, payload)
        fetched = await fetcher(request, context)
        # Stored before anything is returned: the digest the boundary records
        # is only meaningful if the bytes it names are already durable.
        storage_uri = store.put(fetched.content)
        licence = fetched.license
        return {
            "content_sha256": snapshot_digest(fetched.content),
            "storage_uri": storage_uri,
            "media_type": fetched.media_type,
            "byte_length": len(fetched.content),
            "final_uri": fetched.final_uri,
            "license_identifier": licence.identifier,
            "license_declared_by": licence.declared_by.value,
            "license_statement": licence.statement,
        }

    return ToolSpec(
        name=name,
        version=version,
        sensitivity=sensitivity,
        input_model=SourceFetchInput,
        output_model=SourceFetchOutput,
        timeout_seconds=timeout_seconds,
        handler=handler,
        classify_error=classify_error,
    )
