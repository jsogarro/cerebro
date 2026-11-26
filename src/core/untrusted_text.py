"""Defences for text that came from outside the system."""

import unicodedata

# Format characters whose removal would change Persian, Indic or emoji text.
_JOINERS = frozenset("‌‍")
_KEPT_FORMAT = frozenset("­") | _JOINERS


def strip_invisible(text: str) -> tuple[str, int]:
    """Remove characters a reader cannot see but a model still reads.

    Covers Unicode tags, bidi controls, zero-width characters and every other
    ``Cf`` character except the soft hyphen and ZWNJ/ZWJ. The joiners stay but
    are counted, so the caller can see they were present.

    Returns:
        The cleaned text and the number of characters removed or flagged.
    """
    kept: list[str] = []
    flagged = 0
    for char in text:
        invisible = (
            unicodedata.category(char) == "Cf"
            or "\U000e0000" <= char <= "\U000e007f"
            or "\U000e0100" <= char <= "\U000e01ef"
        )
        if not invisible or char in _KEPT_FORMAT:
            kept.append(char)
            flagged += char in _JOINERS
        else:
            flagged += 1
    return "".join(kept), flagged
