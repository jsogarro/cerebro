"""The uploaded-documents reference page matches the routes and settings in code."""

import re
from pathlib import Path

from src.api.routes.critiques import router as critiques_router
from src.api.routes.documents import router as documents_router
from src.core.config import Settings

PAGE = (
    Path(__file__).parent.parent / "docs" / "reference" / "11a-documents.md"
).read_text()


def _section(title: str) -> str:
    match = re.search(rf"^## {title}\n(.*?)(?=^## |\Z)", PAGE, re.M | re.S)
    assert match, title
    return match.group(1)


def _documented(value: str) -> object:
    value = value.strip().strip("`")
    if value == "empty":
        return ""
    if value in ("true", "false"):
        return value == "true"
    if value.endswith(" MiB"):
        return int(value.removesuffix(" MiB")) * 1024 * 1024
    return int(value.replace(",", ""))


def test_every_setting_row_carries_the_real_default() -> None:
    rows = dict(re.findall(r"^\| `(\w+)` \| ([^|]+) \|", _section("Settings"), re.M))
    expected = {
        name
        for name in Settings.model_fields
        if name.startswith("DOCUMENT_") or name == "PROVIDER_TERMS_VERIFIED_ON"
    }
    assert set(rows) == expected
    for name, value in rows.items():
        assert _documented(value) == Settings.model_fields[name].default, name


def _routes(router, section: str) -> None:  # type: ignore[no-untyped-def]
    body = _section(section)
    for route in router.routes:
        for method in route.methods:
            assert f"`{method} /api/v1{route.path}`" in body, (method, route.path)


def test_every_document_route_is_in_the_routes_table() -> None:
    _routes(documents_router, "Routes")


def test_every_critique_route_is_in_the_critique_table() -> None:
    _routes(critiques_router, "Critique")
