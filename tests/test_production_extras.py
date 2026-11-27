"""Every module reachable from ``src.api.main`` must import only production packages.

Static check backed by the reachability walker in
``tests/architecture/test_module_reachability.py`` and the locked dependency
graph exported by ``uv export`` for the extras installed by the production
``builder`` stage in ``Dockerfile``.
"""

from __future__ import annotations

import ast
import importlib.metadata
import re
import subprocess
import sys
from pathlib import Path

from tests.architecture.test_module_reachability import _imported_modules, _module_index

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "Dockerfile"
ENTRY_POINT = "src.api.main"


def _builder_extras() -> list[str]:
    content = DOCKERFILE.read_text(encoding="utf-8")
    stages = re.split(r"^FROM\s+.*?\s+as\s+(\w+)\s*$", content, flags=re.M | re.I)
    builder_body = ""
    for i in range(1, len(stages) - 1, 2):
        if stages[i].lower() == "builder":
            builder_body = stages[i + 1]
            break
    assert builder_body, "Dockerfile has no 'builder' stage"

    match = re.search(
        r"^RUN\s+scripts/install-locked\.sh([^\n]*)$", builder_body, flags=re.M
    )
    assert match, "builder stage does not run scripts/install-locked.sh"
    return re.findall(r"--extra\s+\S+", match.group(1))


def _production_distributions() -> set[str]:
    extra_args: list[str] = []
    for flag in _builder_extras():
        extra_args.extend(flag.split())

    output = subprocess.check_output(
        [
            "uv",
            "export",
            "--locked",
            "--no-emit-project",
            "--no-hashes",
            "--format",
            "requirements-txt",
            *extra_args,
        ],
        cwd=REPO_ROOT,
        text=True,
    )
    distributions: set[str] = set()
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        name = re.split(r"[=<>!;\[~\s]", line, maxsplit=1)[0]
        if name:
            distributions.add(name.lower().replace("_", "-"))
    return distributions


def _handles_import_error(handlers: list[ast.ExceptHandler]) -> bool:
    for handler in handlers:
        if handler.type is None:
            return True
        if isinstance(handler.type, ast.Name) and handler.type.id in {
            "ImportError",
            "ModuleNotFoundError",
            "Exception",
        }:
            return True
        if isinstance(handler.type, ast.Tuple):
            for elt in handler.type.elts:
                if isinstance(elt, ast.Name) and elt.id in {
                    "ImportError",
                    "ModuleNotFoundError",
                    "Exception",
                }:
                    return True
    return False


def _is_type_checking_guard(test: ast.expr) -> bool:
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _unconditional_third_party_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()

    def _visit(node: ast.AST) -> None:
        if isinstance(node, ast.If) and _is_type_checking_guard(node.test):
            for child in node.orelse:
                _visit(child)
            return
        if isinstance(node, ast.Try) and _handles_import_error(node.handlers):
            for child in [*node.handlers, *node.orelse, *node.finalbody]:
                _visit(child)
            return
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root != "src" and root not in sys.stdlib_module_names:
                    roots.add(root)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            root = node.module.split(".")[0]
            if root != "src" and root not in sys.stdlib_module_names:
                roots.add(root)
        for child in ast.iter_child_nodes(node):
            _visit(child)

    _visit(tree)
    return roots


def _reachable_from(entry_point: str) -> dict[str, Path]:
    index = _module_index()
    seen: set[str] = set()
    stack = [entry_point]
    while stack:
        name = stack.pop()
        if name not in seen:
            seen.add(name)
            stack.extend(_imported_modules(name, index[name], index) - seen)
    return {name: index[name] for name in seen}


def test_production_image_covers_every_import_reachable_from_api() -> None:
    prod_dists = _production_distributions()
    pkg_to_dists = importlib.metadata.packages_distributions()

    missing: dict[str, list[str]] = {}
    for mod_name, path in sorted(_reachable_from(ENTRY_POINT).items()):
        for pkg in sorted(_unconditional_third_party_imports(path)):
            candidate_dists = {
                dist.lower().replace("_", "-") for dist in pkg_to_dists.get(pkg, [])
            }
            if not candidate_dists:
                candidate_dists = {pkg.lower().replace("_", "-")}
            if not (candidate_dists & prod_dists):
                missing.setdefault(pkg, []).append(mod_name)

    assert not missing, (
        "Modules reachable from src.api.main unconditionally import packages "
        f"not installed by the production builder stage: {missing}"
    )
