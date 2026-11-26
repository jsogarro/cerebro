"""The five fixed reviewer roles. There is no way to choose or add roles per request."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Role:
    name: str
    focus: str
    categories: tuple[str, ...]


ROLES = (
    Role(
        "methods",
        "Review the study design: sampling, confounds, leakage between training and "
        "evaluation data, controls, and whether another group could reproduce the work.",
        ("design", "sampling", "confound", "leakage", "reproducibility", "other"),
    ),
    Role(
        "statistics",
        "Review the statistics: choice of tests and their assumptions, multiple "
        "comparisons, effect sizes, how intervals are used, and numbers in the text "
        "that disagree with the paper's own tables.",
        (
            "test_choice",
            "assumptions",
            "multiplicity",
            "effect_size",
            "intervals",
            "numeric_inconsistency",
            "other",
        ),
    ),
    Role(
        "claims_evidence",
        "Review claims against evidence: statements stronger than the evidence shown "
        "or cited, and contradictions between sections. For a contradiction, give the "
        "first passage as `quote` and the other as `second_quote`.",
        ("overclaim", "unsupported", "contradiction", "scope", "other"),
    ),
    Role(
        "citations",
        "Review citation bookkeeping only: in-text citations that are missing from the "
        "reference list, and reference entries that are never cited in the text. You "
        "have no access to the cited works, so do not judge whether a reference exists, "
        "is accurate, or supports the claim it is attached to.",
        ("missing_from_list", "never_cited", "inconsistent_format", "other"),
    ),
    Role(
        "red_team",
        "You are the last reviewer. Other reviewers' findings are shown after the "
        "document. Say what a hostile referee would add: weaknesses they missed, "
        "or consequences of their findings taken together.",
        ("missed_weakness", "compound_risk", "alternative_explanation", "other"),
    ),
)


def role_named(name: str) -> Role:
    return next(role for role in ROLES if role.name == name)
