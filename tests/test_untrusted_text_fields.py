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
    assert strip_invisible("a\u200bb\u202ec\U000e0041d") == "abcd"
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
    ["<<b>b>img src=x>", "<<b>script>x", "[a](htt<b>p://x.test)", "&amp;lt;b&amp;gt;", "ht<b>tp://a.test"],
)
def test_removal_cannot_assemble_new_markup(text: str) -> None:
    out, removed = sanitize_model_field(text, 100)
    assert removed
    assert not any(re.search(p, out, re.IGNORECASE) for p in SANITIZE_PATTERNS)
    assert sanitize_model_field(out, 100) == (out, False)
