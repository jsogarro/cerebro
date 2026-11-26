"""Render a stored similarity check as plain text.

Pure: it reads a ``CheckView`` and the document text, and returns a string. The
report is built around its coverage block, so a check with no matches is never
presented as a verdict. It carries no percentage, grade or claim about
uniqueness, and every link is built from a validated id, never taken from data.
"""

import re

from src.repositories.similarity_repository import (
    ATTRIBUTION_RANK,
    CheckView,
    MatchView,
)
from src.services.text_reuse.types import DECLARED_AUTHOR_NOTE, source_link

# Attribution is not shown until its false-accusation gate (09a) has passed; the stored
# class still orders the matches, so unattributed text comes first.
ATTRIBUTION_SHOWN = False
EXCERPT_CHARS = 300
FOOTER = (
    "No match means none was found in the sources searched. It does not show the text "
    "is unique. Similarity evidence is for human review; it is not a finding of "
    "misconduct."
)
NOT_COVERED = (
    "paywalled and subscription full text",
    "books",
    "the general web",
    "student papers from other institutions",
    "documents of other users and organizations",
    "sources not indexed by the providers searched",
    "the body of papers retrieved as abstract only",
)
DETECTION_LIMITS = (
    "verbatim runs shorter than the minimum match length are not reported",
    "blockquotes in a PDF are not recognised as quotation",
    "paraphrase and translation results are advisory",
    "translated sources cannot be retrieved by the open-source queries, which use the "
    "document's own words, so translation flags can only come from your own uploads",
)
_VERBATIM_KINDS = ("verbatim", "normalized")
_WORD = re.compile(r"\S+")
_LICENCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}")


def _one_line(text: str) -> str:
    """Source text may hold line breaks that would forge report lines."""
    shortened = text if len(text) <= EXCERPT_CHARS else text[:EXCERPT_CHARS] + " ..."
    return " ⏎ ".join(shortened.splitlines())


def _counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{reason} {n}" for reason, n in sorted(counts.items())) or "none"


def _covered_tokens(text: str, matches: tuple[MatchView, ...]) -> tuple[int, int]:
    """Whitespace tokens inside the union of verbatim match spans, and in the document."""
    spans = sorted(
        (m.doc_start, m.doc_end) for m in matches if m.kind in _VERBATIM_KINDS
    )
    merged: list[list[int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    covered = sum(len(_WORD.findall(text[a:b])) for a, b in merged)
    return covered, len(_WORD.findall(text))


def _source_count(check: CheckView) -> int:
    sources = check.coverage.get("sources_searched", {})
    # The arXiv row counts works OpenAlex already returned, so it is not added again.
    return sum(
        int(row.get("candidates", 0))
        for origin, row in sources.items()
        if origin != "arxiv"
    )


def _coverage_lines(check: CheckView) -> list[str]:
    cov = check.coverage
    lines = ["Coverage"]
    if reasons := cov.get("partial_reasons"):
        lines.append(f"Partial because: {', '.join(reasons)}")
    lines.append("Sources searched:")
    for origin, row in sorted(cov.get("sources_searched", {}).items()):
        lines.append(
            f"- {origin}: candidates {row['candidates']}, full text {row['full_text']}, "
            f"abstract only {row['abstract_only']}, skipped for the size budget "
            f"{row['skipped_budget']}, queries sent {row['queries_sent']}, "
            f"failures: {_counts(row['failures'])}"
        )
    if cov.get("skipped"):
        lines.append(f"Stopped early by the matcher: {_counts(cov['skipped'])}")
    run = cov.get("comparisons_run", {})
    lines.append(
        "Comparisons run: "
        + "; ".join(f"{name}: {state}" for name, state in run.items())
    )
    lines.append("Excluded regions:")
    regions = cov.get("excluded_regions", [])
    lines.extend(
        f"- reference list from char {r['start']:,} ({r['tokens']:,} tokens not compared)"
        for r in regions
    )
    if not regions and not cov.get("reference_heading_found", False):
        lines.append("- no reference heading found; references were compared")
    lines += ["Not covered:", *(f"- {item}" for item in NOT_COVERED)]
    lines += ["Detection limits:", *(f"- {item}" for item in DETECTION_LIMITS)]
    return lines


def _licence_label(match: MatchView) -> str:
    identifier = match.source_licence.get("identifier")
    if not identifier:
        return '"undeclared"'
    # A licence id comes from a third party and is printed unquoted, so only a plain
    # identifier is shown.
    return (
        str(identifier) if _LICENCE_ID.fullmatch(str(identifier)) else '"unrecognised"'
    )


def _match_lines(number: int, m: MatchView, text: str) -> list[str]:
    lines = [
        "",
        f"{number}. {m.kind}, {m.tokens} tokens",
        f'   Document: "{_one_line(text[m.doc_start : m.doc_end])}"',
    ]
    year = f" ({m.source_year})" if m.source_year else ""
    if m.from_user_upload:
        lines.append(f"   matches your uploaded document {m.source_title}")
    else:
        lines.append(f"   Source: {m.source_title}{year}, {m.source_text_kind}")
        if link := source_link(m.source_origin, m.source_id):
            lines.append(f"   Link: {link}")
    if m.source_excerpt:
        lines.append(f'   Source excerpt: "{_one_line(m.source_excerpt)}"')
    elif m.from_user_upload:
        lines.append(
            "   excerpt withheld: the uploaded document is no longer available"
        )
    else:
        lines.append(
            f"   excerpt withheld: source licence {_licence_label(m)} does not "
            "permit display"
        )
    if m.declared_author_overlap:
        lines.append(f"   {DECLARED_AUTHOR_NOTE}")
    lines.append(
        f"   attribution: {m.attribution.replace('_', ' ')}"
        if ATTRIBUTION_SHOWN
        else "   attribution not determined"
    )
    return lines


def render_report(check: CheckView, document_text: str) -> str:
    sources = _source_count(check)
    lines = [
        "Text similarity report",
        f"Status: {check.status}"
        + (f" ({check.status_reason})" if check.status_reason else ""),
        f"Completed: {check.completed_at.isoformat() if check.completed_at else 'n/a'}",
        "Minimum match length: "
        f"{check.policy_versions.get('MIN_MATCH_TOKENS', 'n/a')} tokens",
        "",
    ]
    if check.matches:
        covered, total = _covered_tokens(document_text, check.matches)
        cut = check.match_total > len(check.matches)
        lines.append(
            f"{covered} of {total} document tokens lie in verbatim matches against "
            "the sources searched" + (", among the listed matches." if cut else ".")
        )
        if cut:
            lines.append(
                f"{len(check.matches)} of {check.match_total} matches are listed."
            )
    else:
        lines.append(f"No matching text was found in the {sources} sources searched.")
    if not sources and check.coverage.get("partial_reasons"):
        lines.append("No source was searched successfully.")
    lines += ["", *_coverage_lines(check)]
    if check.matches:
        lines += ["", "Matches"]
        ordered = sorted(
            check.matches,
            key=lambda m: (ATTRIBUTION_RANK[m.attribution], -m.tokens, m.doc_start),
        )
        for number, m in enumerate(ordered, start=1):
            lines += _match_lines(number, m, document_text)
    lines += ["", FOOTER]
    return "\n".join(lines) + "\n"
