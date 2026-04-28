"""
Regression tests for the dependency-drift cleanup (group-002).

These tests pin two invariants:

1. The platform imports cleanly without the unlisted ``temporalio`` package.
   Specifically: ``tests/integration/conftest.py`` must collect, and no module
   under ``src/`` may unconditionally import ``temporalio`` or the phantom
   ``src.temporal`` namespace.

2. ``pyproject.toml`` declares a ``[project.optional-dependencies] vector``
   extra so users can opt into the vector-storage backend.

Each test was written FIRST, demonstrably failing on the pre-fix tree, and
asserts the post-fix invariant.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"


def _module_imports(path: Path) -> set[str]:
    """Return the set of fully-qualified module names imported at module top."""
    tree = ast.parse(path.read_text())
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


class TestNoOrphanTemporalImports:
    """No source module may unconditionally import temporalio or src.temporal."""

    def test_temporal_bridge_is_removed(self) -> None:
        """``temporal_bridge.py`` was orphaned by the Temporal extraction."""
        assert not (SRC_ROOT / "orchestration" / "temporal_bridge.py").exists(), (
            "src/orchestration/temporal_bridge.py imports temporalio and the "
            "phantom src.temporal package. It has zero inbound imports and "
            "must be deleted as part of the dependency-drift cleanup."
        )

    def test_workflow_service_is_removed(self) -> None:
        """``workflow_service.py`` referenced the phantom ``src.temporal.client``."""
        assert not (SRC_ROOT / "api" / "services" / "workflow_service.py").exists(), (
            "src/api/services/workflow_service.py imports the non-existent "
            "src.temporal.client module and has zero inbound imports. "
            "It must be deleted as part of the dependency-drift cleanup."
        )

    def test_no_src_module_imports_temporalio_unconditionally(self) -> None:
        """No module under src/ may have ``import temporalio`` at module top."""
        offenders: list[str] = []
        for py_file in SRC_ROOT.rglob("*.py"):
            try:
                imports = _module_imports(py_file)
            except SyntaxError:  # pragma: no cover - syntactically invalid file
                continue
            for imp in imports:
                if imp == "temporalio" or imp.startswith("temporalio."):
                    offenders.append(f"{py_file.relative_to(REPO_ROOT)} → {imp}")
        assert not offenders, (
            "These src/ modules import temporalio at module top, but "
            "temporalio is not in pyproject.toml dependencies:\n  "
            + "\n  ".join(offenders)
        )

    def test_no_src_module_imports_phantom_src_temporal(self) -> None:
        """No module under src/ may import the phantom ``src.temporal`` namespace.

        Lazy imports inside function bodies are excluded — ``ast.ImportFrom``
        appearing inside a ``FunctionDef`` is not flagged. This permits
        ``src/reliability/connection_pools.py``'s lazy import inside
        ``TemporalPoolManager.initialize`` (deferred to a follow-up plan).
        """
        offenders: list[str] = []
        for py_file in SRC_ROOT.rglob("*.py"):
            tree = ast.parse(py_file.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                module = node.module or ""
                if not (module == "src.temporal" or module.startswith("src.temporal.")):
                    continue
                # Skip lazy imports nested inside function bodies.
                # Walk the parent chain by re-walking and matching.
                # A simpler heuristic: any ImportFrom at column 0 is module-level.
                if node.col_offset == 0:
                    offenders.append(f"{py_file.relative_to(REPO_ROOT)}:{node.lineno} → {module}")
        assert not offenders, (
            "These src/ modules import the phantom src.temporal namespace at "
            "module top:\n  " + "\n  ".join(offenders)
        )


class TestIntegrationConftestCollects:
    """``tests/integration/conftest.py`` must collect without temporalio."""

    def test_integration_conftest_does_not_eagerly_import_temporalio(self) -> None:
        path = REPO_ROOT / "tests" / "integration" / "conftest.py"
        if not path.exists():
            pytest.skip("integration conftest absent")
        imports = _module_imports(path)
        offenders = [imp for imp in imports if imp == "temporalio" or imp.startswith("temporalio.")]
        assert not offenders, (
            "tests/integration/conftest.py imports temporalio at module top, "
            "which crashes test collection on a fresh install where "
            "temporalio is not pinned. Offenders: " + ", ".join(offenders)
        )


class TestVectorExtraDeclared:
    """``pyproject.toml`` must declare a ``[vector]`` optional-dependencies extra."""

    def test_pyproject_declares_vector_extra(self) -> None:
        try:
            import tomllib  # py311+
        except ImportError:  # pragma: no cover
            import tomli as tomllib  # type: ignore[no-redef]
        data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
        extras = data.get("project", {}).get("optional-dependencies", {})
        assert "vector" in extras, (
            "pyproject.toml must declare a [project.optional-dependencies] "
            "'vector' extra so qdrant-client and sentence-transformers are "
            "installable via `pip install .[vector]`."
        )
        vector_deps = " ".join(extras["vector"]).lower()
        assert "qdrant-client" in vector_deps, (
            "[vector] extra must include qdrant-client (used by "
            "src/ai_brain/memory/semantic_memory.py)."
        )


class TestStaleCiWorkflowRemoved:
    """The temporal-test CI workflow referenced non-existent paths."""

    def test_temporal_test_workflow_is_removed(self) -> None:
        path = REPO_ROOT / ".github" / "workflows" / "temporal-test.yml"
        assert not path.exists(), (
            ".github/workflows/temporal-test.yml references non-existent "
            "paths (src/temporal/**, tests/test_temporal_workflows.py) and "
            "must be deleted."
        )


class TestSemanticMemoryStillImports:
    """Sanity: removing temporal code must not break semantic_memory's import."""

    def test_semantic_memory_module_imports(self) -> None:
        """Import semantic_memory.py directly via importlib.

        Avoids the heavy `src.ai_brain` package __init__ chain and proves the
        target module's own imports are fine without qdrant-client installed.
        """
        path = SRC_ROOT / "ai_brain" / "memory" / "semantic_memory.py"
        spec = importlib.util.spec_from_file_location("_sm_under_test", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["_sm_under_test"] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop("_sm_under_test", None)
        assert hasattr(module, "SemanticMemoryManager")
