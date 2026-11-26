"""Only scholarly_retrieval.py may import an HTTP client or name the scholarly hosts."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
OWNER = SRC / "services" / "scholarly_retrieval.py"
FEATURE_FILES = [
    *sorted((SRC / "services" / "text_reuse").glob("*.py")),
    *sorted((SRC / "services").glob("document_*.py")),
    *sorted((SRC / "services").glob("critique*.py")),
    *sorted((SRC / "services").glob("similarity*.py")),
    OWNER,
]
NETWORK_ROOTS = {"httpx", "requests", "aiohttp", "socket"}
HOSTS = {"api.openalex.org", "arxiv.org"}


def _imports(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{a.name}" for a in node.names)
    return found


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text())


def test_the_feature_file_set_is_not_empty():
    assert OWNER.exists() and len(FEATURE_FILES) > 4


def _is_network(module: str) -> bool:
    return module.partition(".")[0] in NETWORK_ROOTS or module.startswith(
        "urllib.request"
    )


def test_only_the_retrieval_module_imports_a_network_client():
    offenders = {
        f.name: sorted(m for m in _imports(_tree(f)) if _is_network(m))
        for f in FEATURE_FILES
        if f != OWNER
    }
    assert {k: v for k, v in offenders.items() if v} == {}


def test_the_retrieval_module_does_import_httpx():
    assert "httpx" in _imports(_tree(OWNER))


def test_no_host_literal_outside_the_retrieval_module():
    hits = [
        (f.name, node.value)
        for f in FEATURE_FILES
        if f != OWNER
        for node in ast.walk(_tree(f))
        if isinstance(node, ast.Constant) and node.value in HOSTS
    ]
    assert hits == []


def test_no_httpx_instrumentation_in_src():
    assert not any(
        "HTTPXClientInstrumentor" in p.read_text() for p in SRC.rglob("*.py")
    )
