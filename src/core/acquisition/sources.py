"""Where an ingestion trust label comes from, and how a license is recorded.

**Packet 4A's non-guarantee 3, and what closing it can and cannot mean.** The
label on acquired content used to be whatever a caller declared. Propagation
was already a total, order-independent, monotone function — but a function is
only as good as the value it starts from, and nothing derived that value. 4A's
fix was to assign it at one acquisition seam, keyed by source type, never from
a caller-supplied argument. This module is that assignment.

Two structural properties do the work, and both are checked at import rather
than at call time:

1. :data:`_TRUST_BY_SOURCE_TYPE` is **total** over :class:`SourceType`. Adding
   a source type without deciding its label breaks the import, rather than
   surfacing much later as a ``KeyError`` inside an acquisition — or, worse, as
   a default that nobody chose and that looks deliberate in a review.
2. Its **codomain excludes the trusted tier**. No source type maps to a label
   that :func:`~src.core.contracts.trust.is_trusted_tier` accepts, so acquired
   content can never be read as control however the caller describes it.

**The residual, stated rather than papered over.** A caller still names the
source type, so it can lie about it — calling a fetched page an uploaded
document yields ``user_supplied`` instead of ``external_untrusted``. Both are
untrusted-tier, so the security-relevant boundary holds under any such lie and
only the finer rank is falsifiable. Deriving the rank without trusting *some*
declaration of where the bytes came from would require the acquisition seam to
own transport, which is a different packet's boundary; this is recorded as a
known limit rather than hidden behind the word "derived".
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Self, final

from pydantic import JsonValue

from src.core.contracts.trust import TrustClassification, is_trusted_tier

__all__ = [
    "LicenseDeclaration",
    "SourceLicense",
    "SourceType",
    "trust_for_source",
]


class SourceType(StrEnum):
    """Where acquired content came from.

    A closed enum rather than a free string, because it is the sole input to
    the trust assignment. A string would make the mapping partial and the
    default silent.
    """

    WEB_PAGE = "web_page"
    """A page fetched over the network."""

    ACADEMIC_PAPER = "academic_paper"
    """A paper or preprint retrieved from a scholarly source."""

    SEARCH_RESULT = "search_result"
    """A result body returned by a search provider."""

    API_RESPONSE = "api_response"
    """A structured response from a third-party API."""

    UPLOADED_DOCUMENT = "uploaded_document"
    """A document supplied by the authenticated user of this run."""


_TRUST_BY_SOURCE_TYPE: Final[dict[SourceType, TrustClassification]] = {
    SourceType.WEB_PAGE: TrustClassification.EXTERNAL_UNTRUSTED,
    SourceType.ACADEMIC_PAPER: TrustClassification.EXTERNAL_UNTRUSTED,
    SourceType.SEARCH_RESULT: TrustClassification.EXTERNAL_UNTRUSTED,
    SourceType.API_RESPONSE: TrustClassification.EXTERNAL_UNTRUSTED,
    # A user's own upload is not external content, but it is still data: a
    # document a user pastes in can carry an injected instruction just as a
    # fetched page can. `user_supplied` records the provenance difference
    # without moving it into the tier that may be read as control.
    SourceType.UPLOADED_DOCUMENT: TrustClassification.USER_SUPPLIED,
}


def _validate_trust_table() -> None:
    """Check totality and codomain once, at import.

    Both failures are configuration errors that are invisible at the call site:
    a missing entry raises far from the omission, and a trusted-tier entry
    would look like an ordinary line of a mapping while silently making
    acquired content executable as control.
    """

    missing = sorted(
        source_type.value
        for source_type in SourceType
        if source_type not in _TRUST_BY_SOURCE_TYPE
    )
    if missing:
        raise RuntimeError(
            f"source types {missing} have no ingestion trust label; every "
            "source type decides one, because a default here is a trust "
            "assignment nobody made"
        )
    elevated = sorted(
        source_type.value
        for source_type, label in _TRUST_BY_SOURCE_TYPE.items()
        if is_trusted_tier(label)
    )
    if elevated:
        raise RuntimeError(
            f"source types {elevated} are labelled into the trusted tier; "
            "acquired content is data and may never be read as control"
        )


_validate_trust_table()


def trust_for_source(source_type: SourceType) -> TrustClassification:
    """Return the ingestion trust label for content from ``source_type``.

    This is the only place an acquired artifact's label is decided. There is no
    parameter anywhere in the acquisition path through which a caller can
    supply one instead.

    Args:
        source_type: The kind of source the content came from.

    Returns:
        The label, always outside the trusted tier.

    Raises:
        TypeError: ``source_type`` is not a :class:`SourceType`. The check is
            not ceremony: ``SourceType`` is a ``StrEnum``, so a bare
            ``"web_page"`` compares equal to a member and would sail through a
            mapping lookup — making the closed enum decorative exactly where it
            is load-bearing.
    """

    if not isinstance(source_type, SourceType):
        raise TypeError(
            f"ingestion trust is keyed by SourceType, not "
            f"{type(source_type).__name__}; a string would reopen the "
            "caller-declared label this seam exists to close"
        )
    return _TRUST_BY_SOURCE_TYPE[source_type]


class LicenseDeclaration(StrEnum):
    """Where a license statement was found, including "nowhere"."""

    HTTP_HEADER = "http_header"
    META_TAG = "meta_tag"
    API_FIELD = "api_field"
    MANIFEST = "manifest"
    UNDECLARED = "undeclared"
    """The source was examined and stated no license."""


@final
@dataclass(frozen=True, slots=True)
class SourceLicense:
    """What a source said about reuse, and where it said it.

    ``UNDECLARED`` is a value, not an absence. A missing key in the artifact
    metadata reads as "nobody looked"; an explicit ``undeclared`` reads as "we
    looked and the source stated nothing", and those call for different
    decisions downstream. Nothing here infers a permissive default from
    silence — an unstated license is the most restrictive answer available, not
    the most convenient one.
    """

    identifier: str | None
    declared_by: LicenseDeclaration
    statement: str | None = None

    def __post_init__(self) -> None:
        if self.declared_by is LicenseDeclaration.UNDECLARED:
            if self.identifier is not None:
                raise ValueError(
                    "an undeclared license cannot name one; if the identifier "
                    "came from somewhere, say where it came from"
                )
        elif self.identifier is None:
            raise ValueError(
                f"a license declared by {self.declared_by.value} names one; "
                "otherwise 'the source stated something' and 'the source "
                "stated nothing' become indistinguishable once stored"
            )

    @classmethod
    def undeclared(cls) -> Self:
        """Return the license of a source that declared none."""

        return cls(identifier=None, declared_by=LicenseDeclaration.UNDECLARED)

    @property
    def permits_redistribution_by_default(self) -> bool:
        """Always ``False``. Present so the assumption is written down.

        Silence is not permission. This property exists so a caller reaching
        for "can we republish this excerpt" gets an answer from the record
        rather than from whichever default a reader assumed.
        """

        return False

    def as_metadata(self) -> dict[str, JsonValue]:
        """Return the JSON form stored under an artifact's ``license`` key."""

        return {
            "identifier": self.identifier,
            "declared_by": self.declared_by.value,
            "statement": self.statement,
        }
