"""The uploaded-documents reference page names every route and setting the code has."""

import re
from pathlib import Path

from src.api.routes.documents import router
from src.core.config import Settings

PAGE = (
    Path(__file__).parent.parent / "docs" / "reference" / "11a-documents.md"
).read_text()


def test_every_document_setting_is_documented_with_its_default() -> None:
    for name in Settings.model_fields:
        if name.startswith("DOCUMENT_"):
            assert f"`{name}`" in PAGE, name
    rows = dict(re.findall(r"^\| `(DOCUMENT_\w+)` \| ([^|]+) \|", PAGE, re.M))
    assert rows["DOCUMENT_UPLOADS_ENABLED"].strip("` ") == "false"
    assert rows["DOCUMENT_RETENTION_DAYS"].strip() == str(
        Settings.model_fields["DOCUMENT_RETENTION_DAYS"].default
    )
    assert rows["DOCUMENT_UPLOADS_PER_DAY"].strip() == str(
        Settings.model_fields["DOCUMENT_UPLOADS_PER_DAY"].default
    )


def test_every_document_route_is_documented() -> None:
    for route in router.routes:
        method = sorted(route.methods)[0]  # type: ignore[attr-defined]
        path = "/api/v1/documents" + route.path.removeprefix("/documents")  # type: ignore[attr-defined]
        assert f"`{method} {path}`" in PAGE, (method, path)
