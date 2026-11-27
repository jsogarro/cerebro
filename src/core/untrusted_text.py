"""Handling for text that originates outside the system: uploads and model output."""

import re
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


# Ordinary prose ("as follows:", "p < 0.05 and t > 2", "<p-value>") matches none of these.
SANITIZE_PATTERNS = (
    r"</?(?:a|b|i|u|em|strong|span|div|p|br|hr|img|script|style|iframe|object|embed|svg|math|sup|sub|code|pre|table|tr|td|th|ul|ol|li|h[1-6]|link|meta|form|input|button)(?=[\s/>])[^<>\n]{0,200}>",
    r"&(?:[a-z]{2,10}|#\d{1,7}|#x[0-9a-f]{1,6});",
    r"!?\[[^\]\n]{0,200}\]\([^)\s]{1,500}\)",
    r"`{3}",
    r"\b[a-z][a-z0-9+.-]{1,20}://\S+",
    r"\b(?:doi|arxiv|mailto|javascript|data|file):\S+",
    r"(?<![\w:])//[a-z0-9.-]+\.[a-z]{2,}\S*",
    r"\bwww\.\S+",
    r"\b[\w-]+\.[a-z]{2,}/\S*",
)
# Beyond the pinned patterns: any leftover tag-shaped opener (attributes follow) loses its
# "<", and "//host" is a URL whatever the host looks like (IPs, "localhost").
_STRAY_TAG_OPEN = re.compile(r"<(?=[a-z][\w-]*[\s/][^<>]*=[^<>]*>)", re.IGNORECASE)
_ANY_HOST = re.compile(r"(?<![\w:])//[\w.-]+\S*")
# Tag names beyond the pinned list; single letters are left to the pinned full-tag pattern
# because they are common in comparisons ("a<b").
_MORE_TAGS = (
    "textarea|details|summary|video|audio|source|track|title|body|html|head|base|center|font|"
    "label|select|option|canvas|dialog|frame|frameset|noscript|template|marquee|xmp|plaintext|"
    "applet|bgsound|image|picture|map|area|nav|section|article|header|footer|aside|main|"
    "figure|figcaption|fieldset|legend|caption|colgroup|thead|tbody|tfoot|kbd|samp|big|"
    "small|strike|del|ins|mark|cite|dfn|abbr|time|output|progress|meter|slot|"
    "img|script|style|iframe|object|embed|svg|math|link|meta|form|input|button"
)
_TAG_OPENER = re.compile(
    rf"<\s*/?\s*(?=(?:{_MORE_TAGS})(?:[\s/>]|$))|<[!?]|&#x?[0-9a-f]+;?",
    re.IGNORECASE,
)
# URL shapes the pinned patterns miss: ports, IP hosts, one-slash and backslash schemes,
# long scheme names, and a bare domain followed by a query.
_MORE_URLS = (
    re.compile(r"\b[a-z][a-z0-9+.-]*:[/\\]+\S*", re.IGNORECASE),
    re.compile(r"\b(?:vbscript|tel|ftp|sms|callto|ws|wss):\S+", re.IGNORECASE),
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?:[:/?]\S*)"),
    re.compile(r"\b[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}(?::\d+)?[/?]\S*", re.IGNORECASE),
)
# Link forms the pinned patterns miss: a destination with spaces or a title, link
# reference definitions, and any other "scheme:" (sip:, magnet:, xmpp:). A bare domain or
# an email is left alone: the report is plain text and stripping them would damage prose.
_SPACED_LINK = re.compile(r"!?\[([^\]\n]{0,200})\]\(\s*[^)\n]{1,500}\)")
_LINK_DEFINITION = re.compile(r"\[[^\]\n]{1,200}\]:\s*<?[^\s>]*[./:][^\s>]*>?")
_ANY_SCHEME = re.compile(r"\b[a-z][a-z0-9+.-]{1,20}:(?=[^\s:])\S+", re.IGNORECASE)
_PRE_CAP_FACTOR = 2
_MAX_PASSES = 4
# The pinned link pattern with its text captured, so the text survives the removal.
_LINK_KEEPING_TEXT = SANITIZE_PATTERNS[2].replace(
    r"\[[^\]\n]{0,200}\]", r"\[([^\]\n]{0,200})\]"
)
_RULES = (
    *(
        (
            re.compile(pattern, re.IGNORECASE),
            r"\1" if pattern is _LINK_KEEPING_TEXT else "",
        )
        for pattern in (
            *SANITIZE_PATTERNS[:2],
            _LINK_KEEPING_TEXT,
            *SANITIZE_PATTERNS[3:],
        )
    ),
    (_SPACED_LINK, r"\1"),
    (_LINK_DEFINITION, ""),
    (_ANY_SCHEME, ""),
    (_STRAY_TAG_OPEN, ""),
    (_ANY_HOST, ""),
    (_TAG_OPENER, ""),
    *((pattern, "") for pattern in _MORE_URLS),
)


def sanitize_model_field(text: str, max_len: int) -> tuple[str, bool]:
    """Reduce model-written text to plain prose; also report whether anything was removed."""
    # Bounds regex work on hostile input; the cap below would discard most of it anyway.
    window = text[: max_len * _PRE_CAP_FACTOR]
    visible = strip_invisible(window)[0]
    removed = len(window) < len(text) or visible != window
    cleaned = " ".join(visible.split())
    # Removing one construct can splice its neighbours into a new one ("<<b>b>"), so repeat.
    # Input that keeps changing past the pass budget is hostile nesting: drop the field.
    for _ in range(_MAX_PASSES):
        before = cleaned
        for pattern, replacement in _RULES:
            cleaned = pattern.sub(replacement, cleaned)
        if cleaned == before:
            break
        removed = True
    else:
        return "", True
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > max_len:
        cut = cleaned[:max_len]
        if cleaned[max_len] != " ":
            cut = cut.rpartition(" ")[0] or cut
        # A cut can leave an unclosed tag opener at the end, which no pass saw whole.
        cleaned = re.sub(r"<[^>]*$", "", cut).rstrip()
    return cleaned, removed
