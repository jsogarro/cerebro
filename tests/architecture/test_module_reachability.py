"""Every module under ``src/`` must be reachable from a real entry point.

Tests here are written against constructors, so a module with a green test file
and no caller is indistinguishable, in review and in CI, from one that runs.
That is how the security audit trail, the claim-support recorder, and a forked
MASR router all shipped "covered" while nothing executed them.

This is a static import graph (``ast``, including function-local imports) from
the processes the repository actually starts: the API (``uvicorn
src.api.main:app``), the ``research-cli`` script, and the MCP server container.

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

ENTRY_POINTS = ("src.api.main", "src.cli.main", "src.mcp.server")

KNOWN_UNREACHABLE: frozenset[str] = frozenset(
    {
        "src.agents.delegation_contract",
        "src.ai_brain.config.integration_service",
        "src.ai_brain.config.legacy_adapter",
        "src.ai_brain.experimentation.core.system_experiment_registry",
        "src.ai_brain.experimentation.core.unified_experiment_manager",
        "src.ai_brain.experimentation.eval",
        "src.ai_brain.experimentation.eval.adaptive_routing_eval",
        "src.ai_brain.experimentation.eval.adaptive_routing_promotion",
        "src.ai_brain.experimentation.integration.agent_framework_integration",
        "src.ai_brain.experimentation.integration.api_pattern_experiments",
        "src.ai_brain.experimentation.integration.masr_experiment_integration",
        "src.ai_brain.experimentation.monitoring.real_time_dashboard",
        "src.ai_brain.experimentation.optimization.feedback_loop_optimizer",
        "src.ai_brain.experimentation.statistical.bayesian_experiment_design",
        "src.ai_brain.experimentation.statistical.multi_variate_analysis",
        "src.ai_brain.router.masr_service",
        "src.api.services.masr_analytics",
        "src.benchmarks",
        "src.core.acquisition",
        "src.core.acquisition.fetch_tool",
        "src.core.acquisition.pdf_text",
        "src.core.acquisition.resolution",
        "src.core.acquisition.service",
        "src.core.acquisition.snapshots",
        "src.core.acquisition.sources",
        "src.core.production_config",
        "src.costs",
        "src.improvement",
        "src.memory",
        "src.models.base",
        "src.models.db.experiment",
        "src.models.db.migrations",
        "src.repositories.evidence_repository",
        "src.research_platform",
        "src.services.evaluation_regression",
        "src.services.gemini_metrics",
        "src.services.hallucination_detector",
        "src.services.parsers.base_parser",
        "src.services.text_alignment",
        "src.services.text_alignment.alignment",
        "src.services.visualization_generator",
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
