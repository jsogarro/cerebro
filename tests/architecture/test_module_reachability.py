"""Every module under ``src/`` must be reachable from a real entry point.

Tests here are written against constructors, so a module with a green test file
and no caller is indistinguishable, in review and in CI, from one that runs.
That is how the security audit trail, the claim-support recorder, and a forked
MASR router all shipped "covered" while nothing executed them.

This is a static import graph (``ast``, including function-local imports) from
the processes the repository actually starts: the API (``uvicorn
src.api.main:app``), the ``research-cli`` script, the MCP server container, and
the live agent cost pilot.

It is a **ratchet**. ``KNOWN_UNREACHABLE`` lists today's unreachable modules and
may only shrink: a new unreachable module fails, and so does a baseline entry
that has since become reachable or been deleted, so the list cannot rot into a
permission slip. Wire a module or delete it, then delete its line here.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"

# The last entry is the agent cost pilot, run by evals/live/test_cost_pilot.py.
ENTRY_POINTS = (
    "src.api.main",
    "src.cli.main",
    "src.mcp.server",
    "src.evals.pilot_runner",
)

KNOWN_UNREACHABLE: frozenset[str] = frozenset(
    {
        "src.ai_brain.config.integration_service",
        "src.ai_brain.experimentation.eval",
        "src.ai_brain.experimentation.eval.adaptive_routing_eval",
        "src.ai_brain.experimentation.eval.adaptive_routing_promotion",
        "src.core.claims.citation_binding",
        "src.core.claims.citation_render",
        "src.core.claims.citation_validation",
        "src.core.contracts.citation",
        # Storage, registry and dispatcher land before their only caller: the
        # outbox handler registered in src/api/main.py (P10-A2). Remove these
        # when that wiring merges.
        "src.core.evaluation",
        "src.core.evaluation.coverage",
        "src.core.evaluation.dispatcher",
        "src.core.evaluation.events",
        "src.core.evaluation.registry",
        "src.repositories.evaluation_result_repository",
        "src.services.evaluation_regression",
        "src.services.visualization_generator",
        # Launch contract frozen ahead of its callers; durable launch wires it.
        "src.api.services.workflow_launch_service",
    }
)


def _module_index() -> dict[str, Path]:
    index = {}
    for path in SRC.rglob("*.py"):
        parts = list(path.relative_to(REPO_ROOT).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        index[".".join(parts)] = path
    return index


def _imported_modules(name: str, path: Path, index: dict[str, Path]) -> set[str]:
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    candidates: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            candidates.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join([*anchor, *([node.module] if node.module else [])])
            candidates.add(base)
            candidates.update(f"{base}.{alias.name}" for alias in node.names)
    found = set()
    for candidate in candidates:
        # Importing ``a.b.c`` executes ``a`` and ``a.b`` too.
        while candidate:
            if candidate in index:
                found.add(candidate)
            candidate = candidate.rpartition(".")[0]
    return found


def unreachable_modules() -> set[str]:
    index = _module_index()
    seen: set[str] = set()
    stack = list(ENTRY_POINTS)
    while stack:
        name = stack.pop()
        if name not in seen:
            seen.add(name)
            stack.extend(_imported_modules(name, index[name], index) - seen)
    return set(index) - seen


def test_no_new_unreachable_modules() -> None:
    new = sorted(unreachable_modules() - KNOWN_UNREACHABLE)
    assert not new, (
        f"{len(new)} module(s) under src/ are not imported, directly or "
        f"transitively, by any entry point {ENTRY_POINTS}: {new}. Wire them to a "
        "real caller or delete them. (Baseline: "
        f"{len(KNOWN_UNREACHABLE)} known unreachable modules.)"
    )


def test_unreachable_baseline_only_shrinks() -> None:
    stale = sorted(KNOWN_UNREACHABLE - unreachable_modules())
    assert not stale, (
        f"{len(stale)} baseline entr(ies) are now reachable or deleted: {stale}. "
        "Remove them from KNOWN_UNREACHABLE so the ratchet keeps its ground."
    )


def test_entry_points_exist() -> None:
    index = _module_index()
    assert all(entry in index for entry in ENTRY_POINTS)
