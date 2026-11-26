import re

import pytest

from src.core.untrusted_text import (
    SANITIZE_PATTERNS,
    sanitize_model_field,
    strip_invisible,
)

_PATTERN_EXAMPLES = [
    "<b>",
    "&amp;",
    "[t](u)",
    "```",
    "https://x.test",
    "arXiv:2101.00001",
    " //x.test",
    "www.x.test",
    "x.test/a",
]


@pytest.mark.parametrize(
    "text",
    [
        "![x](https://a.example/?d=SECRET)",
        "[click](http://a)",
        "<img src=x>",
        "data:text/html,<script>1</script>",
        "www.a.example/p",
        "a.example/p?q=1",
        "```python\nx\n```",
        "doi:10.1/abc",
        "&lt;script&gt;",
    ],
)
def test_dangerous_markup_becomes_plain_text(text: str) -> None:
    out, removed = sanitize_model_field(f"see {text} end", 500)
    assert removed
    assert not re.search(r"https?:|www\.|\]\(|<|&lt|&amp|```|\.example|data:|doi:", out)


def test_link_text_is_kept() -> None:
    assert (
        sanitize_model_field("a [the paper](http://x.test/p) b", 500)[0]
        == "a the paper b"
    )


def test_ordinary_prose_matches_no_pattern() -> None:
    prose = "as follows: p < 0.05 and t > 2; see <p-value> and e.g. Fig. 3"
    assert not any(re.search(p, prose, re.IGNORECASE) for p in SANITIZE_PATTERNS)
    assert sanitize_model_field(prose, 500) == (prose, False)


@pytest.mark.parametrize(
    ("pattern", "example"), zip(SANITIZE_PATTERNS, _PATTERN_EXAMPLES, strict=True)
)
def test_each_pattern_matches_its_example_once(pattern: str, example: str) -> None:
    assert len(re.findall(pattern, example, re.IGNORECASE)) == 1


def test_invisible_characters_are_stripped() -> None:
    assert strip_invisible("a\u200bb\u202ec\U000e0041d")[0] == "abcd"
    assert sanitize_model_field("a\u200bb", 10) == ("ab", True)


def test_whitespace_collapsed_without_flagging() -> None:
    assert sanitize_model_field("a  \n b\t c", 50) == ("a b c", False)


def test_cap_cuts_at_word_boundary() -> None:
    out, removed = sanitize_model_field("alpha beta gamma delta", 14)
    assert out == "alpha beta" and not removed


def test_cap_without_space_hard_cuts() -> None:
    assert sanitize_model_field("x" * 30, 10)[0] == "x" * 10


def test_empty_after_sanitizing() -> None:
    assert sanitize_model_field("<img src=x>", 50) == ("", True)


@pytest.mark.parametrize(
    "text",
    [
        "<<b>b>img src=x>",
        "<<b>script>x",
        "[a](htt<b>p://x.test)",
        "&amp;lt;b&amp;gt;",
        "ht<b>tp://a.test",
    ],
)
def test_removal_cannot_assemble_new_markup(text: str) -> None:
    out, removed = sanitize_model_field(text, 100)
    assert removed
    assert not any(re.search(p, out, re.IGNORECASE) for p in SANITIZE_PATTERNS)
    assert sanitize_model_field(out, 100) == (out, False)


@pytest.mark.parametrize(
    "text",
    [
        "<img\nsrc=x onerror=alert(1)>",
        '<img src=x alt="' + "a" * 250 + '" onerror=alert(1)>',
        "<details open ontoggle=alert(1)>",
        "<body onload=x>",
        "<svg/onload=x>",
        "<img\nsrc=//1.2.3.4/x?d=SECRET>",
        "<video src=//localhost/x>",
    ],
)
def test_tags_and_hosts_cannot_slip_past_the_allowlist(text: str) -> None:
    out, removed = sanitize_model_field(text, 800)
    assert removed
    assert not re.search(r"<[a-z/!?][^<>]*[\s/=]", out, re.IGNORECASE)
    assert not re.search(r"//[\w.-]+", out)


def test_pathological_input_is_bounded() -> None:
    import time

    start = time.perf_counter()
    sanitize_model_field("a-" * 100_000, 800)
    assert time.perf_counter() - start < 1


def test_comparisons_in_prose_keep_their_angle_bracket() -> None:
    assert sanitize_model_field("if x<y then z>w and p<0.05", 100) == (
        "if x<y then z>w and p<0.05",
        False,
    )


@pytest.mark.parametrize(
    "text",
    ["<b" * 2400 + ">" * 2400, "a-" * 4800, "<b>" * 3000, "&amp;" * 1500 + "<" * 3000],
)
def test_hostile_input_stays_under_a_second(text: str) -> None:
    import time

    start = time.perf_counter()
    sanitize_model_field(text, 1200)
    assert time.perf_counter() - start < 1


def test_nested_markup_is_removed_or_the_field_is_dropped() -> None:
    out, removed = sanitize_model_field("<b" * 50 + ">" * 50 + " tail words here", 800)
    assert removed
    assert not re.search(r"<[a-z]", out)


@pytest.mark.parametrize(
    "text",
    [
        "evil.com:8080/steal?d=1",
        "10.0.0.1/admin",
        "10.0.0.1:80/x",
        "https:/evil.com",
        "https:\\\\evil.com",
        "vbscript:msgbox(1)",
        "tel:+1555",
        "ftp:evil.com",
        "a" * 26 + "://evil.com",
        "evil.com?q=1",
    ],
)
def test_more_url_shapes_are_removed(text: str) -> None:
    out, removed = sanitize_model_field(f"see {text} here", 200)
    assert removed
    assert not re.search(r"evil|10\.0|vbscript|tel:|ftp:|1555", out)


@pytest.mark.parametrize(
    "text",
    [
        "<textarea>",
        "<details open>",
        "<video>",
        "</title>",
        "<!-- c -->",
        "<![CDATA[x]]>",
        "< script>",
        "&#60script&#62",
        "<img src=x onerror=alert(1)",
    ],
)
def test_more_markup_shapes_are_neutralised(text: str) -> None:
    out, removed = sanitize_model_field(f"a {text} b", 200)
    assert removed
    assert not re.search(r"<\s*[!/?a-z]|&#", out, re.IGNORECASE)


def test_prose_with_single_letter_comparisons_survives() -> None:
    prose = "if x<y and z>w, or n<30, e.g. 3.5/4 and i.e. ok, 1.2.3.4"
    assert sanitize_model_field(prose, 200) == (prose, False)


def test_cap_does_not_leave_a_tag_opener_at_the_end() -> None:
    out, _ = sanitize_model_field("<imgxyz", 4)
    assert "<" not in out
    assert sanitize_model_field(out, 4) == (out, False)


@pytest.mark.parametrize(
    ("text", "gone", "kept"),
    [
        ("see [c]( evil.com ) now", "evil.com", "c"),
        ('see [c](evil.com "title") now', "evil.com", "c"),
        ('see ![chart]( evil.com/p.png "t" ) now', "evil.com", "chart"),
        ("see [x]: <evil.com> now", "evil.com", "see"),
        ("see [x]: evil.com/a now", "evil.com", "see"),
        ("see sip:alice@evil.com now", "evil.com", "see"),
        ("see magnet:?xt=urn:btih:abc now", "btih", "see"),
        ("see xmpp:bob@evil.com now", "evil.com", "see"),
        ("see zoommtg:join-me now", "join-me", "see"),
    ],
)
def test_more_link_forms_and_schemes_are_removed(
    text: str, gone: str, kept: str
) -> None:
    out, removed = sanitize_model_field(text, 500)
    assert removed and gone not in out and kept in out


@pytest.mark.parametrize(
    "prose",
    [
        "the sample is on attacker.com today",
        "write to a@evil.com for details",
        "ratio 3:1 in both arms",
        "as follows: the groups differ",
        "see [1]: the authors disagree",
        "p < 0.05: significant at the first look",
        "Note: results vary",
    ],
)
def test_bare_domains_emails_and_ordinary_colons_stay_as_plain_text(
    prose: str,
) -> None:
    assert sanitize_model_field(prose, 500) == (prose, False)
