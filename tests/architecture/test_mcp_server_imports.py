"""Architecture guard: src/mcp/server.py must stay a thin adapter over services.

The MCP server may never read repositories, database models, or internal tool
implementations directly; it delegates to the same application services the
REST routes call.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = REPO_ROOT / "src" / "mcp" / "server.py"

ALLOWED_SRC_PREFIXES: tuple[str, ...] = (
    "src.api.services.research_kernel",
    "src.api.services.evidence_service",
    "src.middleware.tenant_context",
    "src.auth.models",
    "src.security.audit_logger",
)
ALLOWED_THIRD_PARTY: frozenset[str] = frozenset(
    {"fastapi", "fastmcp", "pydantic", "structlog"}
)
FORBIDDEN_SRC_PREFIXES: tuple[str, ...] = (
    "src.repositories",
    "src.models.db",
    "src.core.tools",
    "src.mcp.tools",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_mcp_server_imports_only_allowed_service_and_auth_modules() -> None:
    assert SERVER_PATH.is_file(), f"missing {SERVER_PATH}"
    disallowed: list[str] = []
    for mod in sorted(_imports(SERVER_PATH)):
        root = mod.split(".")[0]
        if root in sys.stdlib_module_names or root in ALLOWED_THIRD_PARTY:
            continue
        if mod.startswith(FORBIDDEN_SRC_PREFIXES):
            disallowed.append(mod)
            continue
        if root == "src" and any(
            mod == prefix or mod.startswith(f"{prefix}.")
            for prefix in ALLOWED_SRC_PREFIXES
        ):
            continue
        disallowed.append(mod)

    assert not disallowed, (
        f"src/mcp/server.py imports disallowed module(s): {disallowed}. "
        f"Allowed src modules: {ALLOWED_SRC_PREFIXES}"
    )
