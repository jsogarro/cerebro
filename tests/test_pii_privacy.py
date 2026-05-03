from __future__ import annotations

from src.core.pii_redactor import PIIRedactor, redact_pii


def test_pii_redactor_masks_common_identifiers() -> None:
    raw = (
        "Contact Jane at jane.doe@example.com or (415) 555-2671. "
        "SSN 123-45-6789, card 4111 1111 1111 1111."
    )

    redacted = PIIRedactor.redact(raw)

    assert "jane.doe@example.com" not in redacted
    assert "(415) 555-2671" not in redacted
    assert "123-45-6789" not in redacted
    assert "4111 1111 1111 1111" not in redacted
    assert "[EMAIL]" in redacted
    assert "[PHONE]" in redacted
    assert "[SSN]" in redacted
    assert "[CREDIT_CARD]" in redacted


def test_redact_pii_converts_non_string_values() -> None:
    assert redact_pii({"email": "user@example.com"}) == "{'email': '[EMAIL]'}"
