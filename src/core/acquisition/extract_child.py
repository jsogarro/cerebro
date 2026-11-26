"""Child process that turns one untrusted PDF into text.

Run as ``python -I -m src.core.acquisition.extract_child <max_pages>
<max_chars>`` with the PDF bytes on stdin and a scrubbed environment; the
reply is one JSON line on stdout. It imports only the standard library, the
extractor and ``untrusted_text`` so a parser bug has no application code,
settings or credentials to reach.
Any other failure crashes the child with no reply, which the parent treats
as a malformed PDF.
"""

import json
import os
import sys
import time

from src.core.acquisition import pdf_text
from src.core.untrusted_text import strip_invisible

# Honoured only because the parent passes them, and only from test code.
_HANG = "CEREBRO_EXTRACT_TEST_HANG"
_ECHO = "CEREBRO_EXTRACT_TEST_ECHO_ENV"

_ERROR_CODES = (
    (pdf_text.TooManyPagesError, "too_many_pages"),
    (pdf_text.TooMuchTextError, "too_much_text"),
    (pdf_text.EncryptedPdfError, "encrypted_pdf"),
    (pdf_text.NoExtractableTextError, "no_text"),
    (pdf_text.NotAPdfError, "unsupported_type"),
)


def _limit_self() -> None:
    """Cap memory, CPU, forks and file writes. A no-op off Linux."""
    if sys.platform != "linux":
        return
    import resource

    for limit, value in (
        (resource.RLIMIT_AS, 1 << 30),
        (resource.RLIMIT_CPU, 60),
        (resource.RLIMIT_NPROC, 1),
        (resource.RLIMIT_FSIZE, 0),
    ):
        resource.setrlimit(limit, (value, value))


def _error_code(error: Exception) -> str:
    for kind, code in _ERROR_CODES:
        if isinstance(error, kind):
            return code
    return "malformed_pdf"


def main(argv: list[str]) -> None:
    _limit_self()
    if os.environ.get(_HANG):
        time.sleep(10**6)
    if os.environ.get(_ECHO):
        prefixes = ("langchain", "src.services", "src.core.config")
        modules = sorted(m for m in sys.modules if m.startswith(prefixes))
        sys.stdout.write(
            json.dumps({"env": dict(os.environ), "modules": modules}) + "\n"
        )
        return

    max_pages, max_chars = int(argv[0]), int(argv[1])
    removed = 0

    def counting_strip(page: str) -> str:
        nonlocal removed
        cleaned, count = strip_invisible(page)
        removed += count
        return cleaned

    try:
        extracted = pdf_text.extract_pdf_text(
            sys.stdin.buffer.read(),
            max_pages=max_pages,
            max_chars=max_chars,
            clean_page=counting_strip,
        )
    except (pdf_text.PdfExtractionError, MemoryError) as error:
        reply: dict[str, object] = {"ok": False, "error": _error_code(error)}
    else:
        reply = {
            "ok": True,
            "text": extracted.text,
            "pages": [[s.page, s.start, s.end] for s in extracted.pages],
            "source_sha256": extracted.source_sha256,
            "text_sha256": extracted.text_sha256,
            "invisible_chars_removed": removed,
        }
    sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
