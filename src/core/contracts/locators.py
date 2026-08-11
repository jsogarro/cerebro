"""The frozen grammar for stable evidence locators.

An ``Evidence`` row says "the source states X" by pointing at a span of an
immutable snapshot. That claim survives only if the pointer resolves to the
same bytes every time it is read — after the run terminates, in a new database
session, in a different process, years later.

**The resolution contract.** A locator is resolved against exactly one input:
the bytes of the snapshot artifact named by ``Evidence.snapshot_artifact_id``,
whose digest is ``Evidence.content_sha256``. Resolution is a pure function of
``(locator, snapshot_bytes)``. Because the snapshot is immutable and
content-addressed, that makes resolution stable by construction rather than by
operational discipline.

Three consequences are frozen here:

1. **The first segment is a canonical span** — ``bytes``, ``char``, or
   ``line`` — resolvable with no parser, no DOM, and no library version. This
   is the authority for what the evidence says.
2. **Later segments are annotations** — ``xpath``, ``css``, ``jsonptr``,
   ``page``, ``sheet``, ``heading``. They aid human review and are explicitly
   *not* authoritative, because their resolution depends on a parser whose
   version is not part of the snapshot.
3. **One span has exactly one spelling.** Offsets are canonical decimal, so
   two locators over the same span are byte-identical strings and can be
   compared, deduplicated, and indexed as strings.

Grammar::

    locator     = canonical *( "|" annotation )
    canonical   = canonical-scheme ":" start "-" end
    annotation  = annotation-scheme ":" argument
    start / end = "0" / ( %x31-39 *DIGIT )        ; canonical decimal
    argument    = 1*( %x21-7B / %x7D-7E )         ; printable ASCII, no "|"

``start`` and ``end`` describe a **half-open** range, ``[start, end)``, and
``end`` must be strictly greater than ``start`` — a zero-length span is not
evidence. ``bytes`` and ``char`` offsets are 0-based; ``line`` offsets are
1-based, matching every tool that reports line numbers.
"""

import re
from dataclasses import dataclass
from typing import Final

LOCATOR_CANONICAL_SCHEMES: Final[frozenset[str]] = frozenset({"bytes", "char", "line"})
"""Parser-free span schemes. Exactly one, first, in every locator."""

LOCATOR_ANNOTATION_SCHEMES: Final[frozenset[str]] = frozenset(
    {"xpath", "css", "jsonptr", "page", "sheet", "heading"}
)
"""Non-authoritative review aids. Never the authority for a span."""

_ONE_BASED_SCHEMES: Final[frozenset[str]] = frozenset({"line"})

_CANONICAL_DECIMAL = re.compile(r"^(?:0|[1-9][0-9]*)$")
_SPAN_ARGUMENT = re.compile(r"^([^-]*)-([^-]*)$")
_ANNOTATION_ARGUMENT = re.compile(r"^[\x21-\x7b\x7d-\x7e]+$")
_SEGMENT = re.compile(r"^([A-Za-z0-9_]+):(.+)$")

MAX_LOCATOR_LENGTH: Final[int] = 1024
"""Bounds the column width 4B must reserve and the cost of parsing one."""


class InvalidLocatorError(ValueError):
    """Raised when a locator does not conform to the frozen grammar."""


@dataclass(frozen=True, slots=True)
class LocatorSegment:
    """One ``scheme:argument`` segment of a locator."""

    scheme: str
    argument: str


@dataclass(frozen=True, slots=True)
class CanonicalSpan:
    """The authoritative, parser-free half-open span of a locator."""

    scheme: str
    start: int
    end: int

    def __str__(self) -> str:
        return canonical_span(self.scheme, self.start, self.end)


@dataclass(frozen=True, slots=True)
class Locator:
    """A parsed locator: one canonical span plus non-authoritative annotations."""

    canonical: CanonicalSpan
    annotations: tuple[LocatorSegment, ...]

    def __str__(self) -> str:
        parts = [str(self.canonical)]
        parts.extend(
            f"{segment.scheme}:{segment.argument}" for segment in self.annotations
        )
        return "|".join(parts)


def canonical_span(scheme: str, start: int, end: int) -> str:
    """Render the one legal spelling of a canonical span."""

    return f"{scheme}:{start}-{end}"


def _parse_offset(raw: str, *, field: str) -> int:
    if raw.startswith("-"):
        raise InvalidLocatorError(f"locator {field} must be non-negative: {raw!r}")
    if not _CANONICAL_DECIMAL.fullmatch(raw):
        raise InvalidLocatorError(
            f"locator {field} must be canonical decimal with no padding or sign: "
            f"{raw!r}"
        )
    return int(raw)


def _parse_canonical(segment: LocatorSegment) -> CanonicalSpan:
    if segment.scheme not in LOCATOR_CANONICAL_SCHEMES:
        if segment.scheme in LOCATOR_ANNOTATION_SCHEMES:
            raise InvalidLocatorError(
                f"a locator must begin with a canonical span scheme "
                f"{sorted(LOCATOR_CANONICAL_SCHEMES)}, not {segment.scheme!r}"
            )
        raise InvalidLocatorError(f"unknown locator scheme {segment.scheme!r}")

    match = _SPAN_ARGUMENT.fullmatch(segment.argument)
    if match is None:
        # A leading "-" lands here because it produces three dash-delimited
        # parts; report it as the negative offset it is.
        if segment.argument.startswith("-"):
            raise InvalidLocatorError(
                f"locator start must be non-negative: {segment.argument!r}"
            )
        raise InvalidLocatorError(
            f"canonical span must be 'start-end': {segment.argument!r}"
        )

    start = _parse_offset(match.group(1), field="start")
    end = _parse_offset(match.group(2), field="end")

    if segment.scheme in _ONE_BASED_SCHEMES and start < 1:
        raise InvalidLocatorError(f"{segment.scheme} spans are 1-based; got {start}")
    if end <= start:
        raise InvalidLocatorError(
            f"canonical span must be half-open and strictly increasing: "
            f"{segment.argument!r}"
        )
    return CanonicalSpan(scheme=segment.scheme, start=start, end=end)


def _split_segment(raw: str) -> LocatorSegment:
    if raw != raw.strip():
        raise InvalidLocatorError(
            f"locator segment has surrounding whitespace: {raw!r}"
        )
    match = _SEGMENT.fullmatch(raw)
    if match is None:
        raise InvalidLocatorError(f"locator segment must be 'scheme:argument': {raw!r}")
    return LocatorSegment(scheme=match.group(1), argument=match.group(2))


def parse_locator(value: str) -> Locator:
    """Parse and validate ``value`` against the frozen locator grammar.

    Args:
        value: The locator string as it is stored on an ``Evidence`` row.

    Returns:
        The parsed locator.

    Raises:
        InvalidLocatorError: ``value`` does not conform to the grammar. Every
            rejection is a hard error; nothing is coerced, defaulted, or
            silently dropped, because a locator that "mostly parses" points at
            the wrong span rather than at none.
    """

    if not value:
        raise InvalidLocatorError("locator must not be empty")
    if len(value) > MAX_LOCATOR_LENGTH:
        raise InvalidLocatorError(
            f"locator exceeds {MAX_LOCATOR_LENGTH} characters: {len(value)}"
        )

    raw_segments = value.split("|")
    if any(not segment for segment in raw_segments):
        raise InvalidLocatorError(f"locator has an empty segment: {value!r}")

    segments = [_split_segment(raw) for raw in raw_segments]
    canonical = _parse_canonical(segments[0])

    annotations: list[LocatorSegment] = []
    for segment in segments[1:]:
        if segment.scheme in LOCATOR_CANONICAL_SCHEMES:
            raise InvalidLocatorError(
                "a locator carries exactly one canonical span; "
                f"{segment.scheme!r} is a second one"
            )
        if segment.scheme not in LOCATOR_ANNOTATION_SCHEMES:
            raise InvalidLocatorError(f"unknown locator scheme {segment.scheme!r}")
        if not _ANNOTATION_ARGUMENT.fullmatch(segment.argument):
            raise InvalidLocatorError(
                f"annotation argument must be printable ASCII without '|': "
                f"{segment.argument!r}"
            )
        annotations.append(segment)

    return Locator(canonical=canonical, annotations=tuple(annotations))


__all__ = [
    "LOCATOR_ANNOTATION_SCHEMES",
    "LOCATOR_CANONICAL_SCHEMES",
    "MAX_LOCATOR_LENGTH",
    "CanonicalSpan",
    "InvalidLocatorError",
    "Locator",
    "LocatorSegment",
    "canonical_span",
    "parse_locator",
]
