"""Defences for text that came from outside the system."""

import unicodedata

# Format characters whose removal would change Persian, Indic or emoji text.
_JOINERS = frozenset("‌‍")
_KEPT_FORMAT = frozenset("­") | _JOINERS
_KEPT_CONTROLS = frozenset("\t\n\r")
# Invisible or blank-looking characters outside the categories stripped below.
_BLANKS = frozenset(
    [
        0x034F,
        0x115F,
        0x1160,
        0x17B4,
        0x17B5,
        0x2800,
        0x3164,
        0xFFA0,
        *range(0x180B, 0x1810),
    ]
)
_ASCII_CONTROLS = {
    c: None for c in [*range(0x20), 0x7F] if chr(c) not in _KEPT_CONTROLS
}
_VS_FIRST, _VS_LAST, _VS_EMOJI = 0xFE00, 0xFE0F, 0xFE0F
_KEYCAP_BASES = frozenset("#*0123456789")


def _is_variation_selector_to_keep(char: str, previous: str) -> bool:
    """Only an emoji presentation selector directly after its base survives."""
    return (
        ord(char) == _VS_EMOJI
        and previous != ""
        and (ord(previous) > 0x7F or previous in _KEYCAP_BASES)
        and not _VS_FIRST <= ord(previous) <= _VS_LAST
    )


def _hidden(char: str, previous: str) -> bool:
    code = ord(char)
    if _VS_FIRST <= code <= _VS_LAST:
        return not _is_variation_selector_to_keep(char, previous)
    if code in _BLANKS or 0xE0000 <= code <= 0xE007F or 0xE0100 <= code <= 0xE01EF:
        return True
    category = unicodedata.category(char)
    if category == "Cc":
        return char not in _KEPT_CONTROLS
    if category == "Cf":
        return char not in _KEPT_FORMAT
    return category in {"Co", "Cn", "Zl", "Zp"}


def strip_invisible(text: str) -> tuple[str, int]:
    """Remove characters a reader cannot see but a model still reads.

    Covers Unicode tags, bidi controls, zero-width characters, every other
    ``Cf`` character except the soft hyphen and ZWNJ/ZWJ, control characters
    other than tab and newlines, variation selectors that do not follow an
    emoji base, blank fillers, line separators and private-use or unassigned
    code points. The joiners stay but are counted, so the caller can see they
    were present.

    Returns:
        The cleaned text and the number of characters removed or flagged.
    """
    if text.isascii():
        cleaned = text.translate(_ASCII_CONTROLS)
        return cleaned, len(text) - len(cleaned)
    kept: list[str] = []
    flagged = 0
    previous = ""
    for char in text:
        if _hidden(char, previous):
            flagged += 1
            continue
        kept.append(char)
        flagged += char in _JOINERS
        previous = char
    return "".join(kept), flagged
