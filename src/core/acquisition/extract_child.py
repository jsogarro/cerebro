"""Child process that turns one untrusted PDF into text.

Run as ``python -I -B -m src.core.acquisition.extract_child <max_pages>
<max_chars>`` with the PDF bytes on stdin and a scrubbed environment; the
reply is one JSON line on stdout. It imports only the standard library, the
extractor and ``untrusted_text`` so a parser bug has no application code,
settings or credentials to reach.

The child bounds hangs, CPU, memory and output and receives no secrets in its
environment. It is non-dumpable and the OOM killer's first choice. It is not
network-isolated: a pypdf code-execution bug could still read any file the app
user can read, including the JWT signing key at ``/secrets/jwt_private.pem``,
and send it to any HTTPS host. That needs a pypdf code-execution bug, which is
rated low likelihood.

Any failure that is not a typed extraction error crashes the child with no
reply, which the parent treats as a malformed PDF.
"""

import json
import sys
from collections.abc import Callable
from typing import TypeVar

from src.core.acquisition import pdf_text
from src.core.untrusted_text import strip_invisible

T = TypeVar("T")

EXIT_MEMORY = 75
_CPU_SOFT_S, _CPU_HARD_S = 45, 50
_ADDRESS_SPACE = 1 << 30
_PR_SET_DUMPABLE = 4

_ERROR_CODES = (
    (pdf_text.TooManyPagesError, "too_many_pages"),
    (pdf_text.TooMuchTextError, "too_much_text"),
    (pdf_text.EncryptedPdfError, "encrypted_pdf"),
    (pdf_text.NoExtractableTextError, "no_text"),
    (pdf_text.NotAPdfError, "unsupported_type"),
)


def make_non_dumpable() -> None:
    """Stop same-uid processes reading this process through ``/proc``."""
    import ctypes

    if ctypes.CDLL(None, use_errno=True).prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        raise OSError("prctl(PR_SET_DUMPABLE) failed")


def _limit_self() -> None:
    """Make this process the OOM killer's first choice and cap it. Linux only."""
    if sys.platform != "linux":
        return
    import resource
    from pathlib import Path

    Path("/proc/self/oom_score_adj").write_text("1000")
    make_non_dumpable()
    for limit, soft, hard in (
        (resource.RLIMIT_AS, _ADDRESS_SPACE, _ADDRESS_SPACE),
        (resource.RLIMIT_CPU, _CPU_SOFT_S, _CPU_HARD_S),
        (resource.RLIMIT_NPROC, 1, 1),
        (resource.RLIMIT_FSIZE, 0, 0),
    ):
        resource.setrlimit(limit, (soft, hard))


def guarded(work: Callable[[], T]) -> T:
    """Run ``work``; running out of memory exits with its own code."""
    try:
        return work()
    except MemoryError:
        sys.exit(EXIT_MEMORY)


def _error_code(error: Exception) -> str:
    for kind, code in _ERROR_CODES:
        if isinstance(error, kind):
            return code
    return "malformed_pdf"


def main(argv: list[str]) -> None:
    _limit_self()
    max_pages, max_chars = int(argv[0]), int(argv[1])
    removed = 0

    def counting_strip(page: str) -> str:
        nonlocal removed
        cleaned, count = strip_invisible(page)
        removed += count
        return cleaned

    try:
        extracted = guarded(
            lambda: pdf_text.extract_pdf_text(
                sys.stdin.buffer.read(),
                max_pages=max_pages,
                max_chars=max_chars,
                clean_page=counting_strip,
            )
        )
    except pdf_text.PdfExtractionError as error:
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
