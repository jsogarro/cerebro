"""Resolving a frozen locator against the bytes it was minted against.

Packet 4A froze the grammar in :mod:`src.core.contracts.locators` and named the
gap this module closes: resolution was *specified* and unimplemented, so
"stable" described the spelling of a locator rather than a demonstrated round
trip. ``resolve`` is that round trip's second half.

**One input, one answer.** Resolution is a pure function of ``(locator,
snapshot_bytes)``. It reads no database, no registry, and no clock. Since the
snapshot is immutable and content-addressed, and the canonical span is
parser-free, the same pair yields the same bytes in any process, at any later
date — which is the property an evidence citation actually rests on.

**Refusal over truncation.** Every out-of-range span raises. This is the one
design decision here worth arguing for, because the alternative is what Python
does by default: ``data[11:99]`` returns whatever is there and reports nothing.
An evidence pointer that silently resolves to *less* than it claims is strictly
worse than one that fails, because a shorter excerpt still looks like a
quotation, and no reader downstream can tell that the source never contained
the rest of it.

**Annotations are read past, deliberately.** ``xpath``, ``css``, ``jsonptr``
and their siblings are non-authoritative by the frozen grammar's own terms —
their resolution depends on a parser version that is not part of the snapshot.
Resolution therefore uses the canonical span and nothing else, so a locator's
meaning cannot change when a parser is upgraded. They are still *validated*,
because a locator carrying a malformed annotation is a malformed locator.

**Line spans are defined on LF alone.** ``bytes.splitlines`` also breaks on
``\\r`` and, for text, on half a dozen Unicode separators — a definition that
would make the same locator mean different things for the same bytes depending
on which of those a source happened to contain. Here a line ends at ``\\n``,
the terminator belongs to the line it ends, and a ``\\r\\n`` source resolves to
spans that still contain their ``\\r``. Nothing is normalized: the excerpt is
what the source said.
"""

from collections.abc import Sequence

from src.core.contracts.locators import (
    LOCATOR_CANONICAL_SCHEMES,
    CanonicalSpan,
    canonical_span,
    parse_locator,
)

__all__ = [
    "UnresolvableLocatorError",
    "locate",
    "resolve",
]


class UnresolvableLocatorError(ValueError):
    """Raised when a well-formed locator does not resolve against these bytes.

    Distinct from
    :class:`~src.core.contracts.locators.InvalidLocatorError`, which means the
    locator is malformed regardless of any snapshot. This one means the locator
    is legal but the snapshot cannot answer it — the wrong snapshot, a
    truncated one, or a span that was never in range. Keeping them apart is
    what lets a caller tell "this citation is corrupt" from "this citation is
    against different content".
    """


def resolve(locator: str, snapshot_bytes: bytes) -> bytes:
    """Return the bytes of ``snapshot_bytes`` that ``locator`` names.

    Args:
        locator: A locator string conforming to the frozen grammar, exactly as
            stored on an ``Evidence`` row.
        snapshot_bytes: The verbatim, unredacted bytes of the snapshot artifact
            the evidence cites. Redaction happens after resolution, never
            before: scrubbing first would shift every offset.

    Returns:
        The named span, as bytes. Bytes rather than text for every scheme, so
        one return type covers a snapshot that is not text at all.

    Raises:
        InvalidLocatorError: ``locator`` does not conform to the grammar.
        UnresolvableLocatorError: The locator is well-formed but its span is
            not present in ``snapshot_bytes``, or a character span was asked of
            a snapshot that is not valid UTF-8.
    """

    span = parse_locator(locator).canonical
    if span.scheme == "bytes":
        return _resolve_bytes(span, snapshot_bytes)
    if span.scheme == "char":
        return _resolve_chars(span, snapshot_bytes)
    return _resolve_lines(span, snapshot_bytes)


def locate(
    scheme: str,
    start: int,
    end: int,
    *,
    annotations: Sequence[str] = (),
) -> str:
    """Build the one legal spelling of a locator for a span.

    Validated by parsing what it just built, so a locator this function returns
    is one ``resolve`` and the ``Evidence`` contract will both accept. Building
    the string by hand elsewhere is what lets an off-by-one spelling — a padded
    offset, a reversed range — reach a row that then cannot be re-resolved.

    Args:
        scheme: One of the canonical schemes, ``bytes``, ``char``, or ``line``.
        start: Half-open range start. 0-based, except ``line``, which is
            1-based.
        end: Half-open range end, strictly greater than ``start``.
        annotations: Non-authoritative ``scheme:argument`` review aids, in
            order, appended after the canonical span.

    Returns:
        The locator string.

    Raises:
        InvalidLocatorError: The resulting locator is not one the frozen
            grammar accepts.
    """

    if scheme not in LOCATOR_CANONICAL_SCHEMES:
        # Reported through the grammar's own error type rather than a local
        # one, so a caller has a single exception to handle for "this is not a
        # locator" however it went wrong.
        parts = [f"{scheme}:{start}-{end}"]
    else:
        parts = [canonical_span(scheme, start, end)]
    parts.extend(annotations)
    value = "|".join(parts)
    parse_locator(value)
    return value


def _refuse_beyond(span: CanonicalSpan, available: int, unit: str) -> None:
    if span.end > available:
        raise UnresolvableLocatorError(
            f"locator {span} runs beyond the snapshot, which holds {available} "
            f"{unit}; refusing rather than resolving to a shorter excerpt than "
            "the locator claims"
        )


def _resolve_bytes(span: CanonicalSpan, snapshot_bytes: bytes) -> bytes:
    _refuse_beyond(span, len(snapshot_bytes), "bytes")
    return snapshot_bytes[span.start : span.end]


def _resolve_chars(span: CanonicalSpan, snapshot_bytes: bytes) -> bytes:
    try:
        text = snapshot_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise UnresolvableLocatorError(
            f"locator {span} asks for a character span of a snapshot that is "
            "not valid UTF-8; only byte spans resolve against binary content"
        ) from error
    _refuse_beyond(span, len(text), "characters")
    # Re-encoding is lossless: the text was decoded strictly from these bytes,
    # so the result is exactly the corresponding byte subrange.
    return text[span.start : span.end].encode("utf-8")


def _resolve_lines(span: CanonicalSpan, snapshot_bytes: bytes) -> bytes:
    starts = _line_starts(snapshot_bytes)
    _refuse_beyond(span, len(starts) + 1, "lines")
    # Line offsets are 1-based, so line N begins at index N-1 of `starts`. The
    # end offset is exclusive and may be one past the last line, which is how a
    # span reaching the end of the snapshot is spelled.
    first = starts[span.start - 1]
    last = starts[span.end - 1] if span.end - 1 < len(starts) else len(snapshot_bytes)
    return snapshot_bytes[first:last]


def _line_starts(snapshot_bytes: bytes) -> list[int]:
    """Return the offset each line begins at, LF being the only delimiter."""

    if not snapshot_bytes:
        # An empty snapshot has zero lines, not one empty one. Without this,
        # `line:1-2` would resolve to `b""` — a citation that looks like a
        # successful quotation of nothing.
        return []
    starts = [0]
    offset = snapshot_bytes.find(b"\n")
    while offset != -1:
        following = offset + 1
        if following < len(snapshot_bytes):
            starts.append(following)
        offset = snapshot_bytes.find(b"\n", following)
    return starts
