"""PII redaction helpers for log-safe text."""

from __future__ import annotations

import re


class PIIRedactor:
    """Redact common PII patterns before values are written to logs."""

    PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
        (
            re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
            "[EMAIL]",
        ),
        (
            re.compile(
                r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(\d{3}\)|\d{3})[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)"
            ),
            "[PHONE]",
        ),
        (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
        (
            re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
            "[CREDIT_CARD]",
        ),
    )

    @classmethod
    def redact(cls, value: object) -> str:
        """Return a string with common PII values replaced by placeholders."""

        redacted = str(value)
        for pattern, replacement in cls.PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        return redacted


def redact_pii(value: object) -> str:
    """Convenience wrapper for log-call sites."""

    return PIIRedactor.redact(value)


__all__ = ["PIIRedactor", "redact_pii"]
