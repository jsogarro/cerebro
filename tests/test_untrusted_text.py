from src.core.untrusted_text import strip_invisible

ZWSP = chr(0x200B)
ZWNJ = chr(0x200C)
ZWJ = chr(0x200D)
LRM = chr(0x200E)
RLO = chr(0x202E)
WORD_JOINER = chr(0x2060)
INVISIBLE_TIMES = chr(0x2062)
ISOLATE = chr(0x2066)
BOM = chr(0xFEFF)
TAG_A = chr(0xE0041)
TAG_BLOCK_START = chr(0xE0000)
SOFT_HYPHEN = chr(0xAD)


def test_removes_and_counts_tag_bidi_and_zero_width_characters() -> None:
    text = f"a{TAG_A}b{RLO}c{ISOLATE}d{ZWSP}e{BOM}f{WORD_JOINER}g"
    assert strip_invisible(text) == ("abcdefg", 6)


def test_removes_the_unassigned_tag_block_start() -> None:
    assert strip_invisible(f"a{TAG_BLOCK_START}b") == ("ab", 1)


def test_keeps_the_soft_hyphen_and_keeps_but_counts_the_joiners() -> None:
    soft = f"co{SOFT_HYPHEN}op"
    joined = f"a{ZWNJ}b{ZWJ}c"
    assert strip_invisible(soft) == (soft, 0)
    assert strip_invisible(joined) == (joined, 2)


def test_removes_other_format_characters() -> None:
    assert strip_invisible(f"a{INVISIBLE_TIMES}b{LRM}c") == ("abc", 2)


def test_plain_text_is_untouched() -> None:
    assert strip_invisible("héllo\nwörld") == ("héllo\nwörld", 0)


def test_removes_variation_selector_supplement_payloads() -> None:
    assert strip_invisible(f"a{chr(0xE0101)}b") == ("ab", 1)
