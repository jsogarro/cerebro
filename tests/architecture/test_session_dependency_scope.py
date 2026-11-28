"""Routes must take ``SessionDep``, never a bare ``Depends(get_session)``.

The default (request) scope commits in teardown, after the response is sent, and
FastAPI caches per scope, so mixing scopes also opens a second session per request.
"""

from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
SESSION_MODULE = SRC / "models" / "db" / "session.py"


def test_no_dependency_on_get_session_outside_the_session_module() -> None:
    offenders = [
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if path != SESSION_MODULE and "Depends(get_session" in path.read_text()
    ]
    assert offenders == []
