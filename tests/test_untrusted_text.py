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


def test_removes_basic_variation_selector_payloads_but_keeps_emoji_presentation() -> (
    None
):
    vs0, vs15, vs16 = chr(0xFE00), chr(0xFE0E), chr(0xFE0F)
    assert strip_invisible(f"a{vs0}b{vs15}c") == ("abc", 2)
    heart = chr(0x2764)
    assert strip_invisible(f"{heart}{vs16}") == (f"{heart}{vs16}", 0)
    assert strip_invisible(f"a{vs16}") == ("a", 1)
    assert strip_invisible(f"{heart}{vs16}{vs16}") == (f"{heart}{vs16}", 1)


def test_removes_control_and_blank_filler_characters_but_keeps_whitespace() -> None:
    assert strip_invisible("a\x1b[2Jb\x07\x7f\x9bc") == ("a[2Jbc", 4)
    assert strip_invisible("a\tb\r\nc") == ("a\tb\r\nc", 0)
    fillers = [0x034F, 0x115F, 0x17B4, 0x180B, 0x2800, 0x3164, 0xFFA0, 0x2028]
    assert strip_invisible("a" + "".join(map(chr, fillers)) + "b") == (
        "ab",
        len(fillers),
    )


def test_removes_private_use_and_unassigned_code_points() -> None:
    assert strip_invisible(f"a{chr(0xE000)}{chr(0xF0000)}{chr(0x0378)}b") == ("ab", 3)


def test_range_edges_are_stripped_and_the_neighbours_are_not() -> None:
    for edge in (0xE007F, 0xE0100, 0xE01EF):
        assert strip_invisible(f"a{chr(edge)}b") == ("ab", 1)
    assert strip_invisible(f"a{chr(0xE01F0)}b")[1] == 1  # unassigned (Cn)
    assert strip_invisible("aéb") == ("aéb", 0)
