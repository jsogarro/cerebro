"""Execution primitives that no request reaches stay deleted."""

import ast
import inspect
import subprocess
from pathlib import Path

import pytest

from src.models.report import Report, ReportFormat
from src.services.exporters.latex_exporter import LaTeXExporter

ROOT = Path(__file__).resolve().parents[2]
NO_EXEC_MODULES = [
    "src/services/exporters/latex_exporter.py",
    "src/ai_brain/learning/supervision_feedback.py",
]


def _forbid_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("subprocess used")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)


def test_latex_export_spawns_no_process(monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_subprocess(monkeypatch)
    report = Report(id="r1", title="T", query="q")
    output = LaTeXExporter().export_to_latex(report)
    assert output.format == ReportFormat.LATEX


def test_latex_compile_path_is_gone() -> None:
    assert (
        "compile_to_pdf"
        not in inspect.signature(LaTeXExporter.export_to_latex).parameters
    )
    assert not hasattr(LaTeXExporter, "_compile_latex_to_pdf")


BANNED = {"subprocess", "pickle"}


def banned_references(source: str) -> set[str]:
    """Imports of, or string names for, the banned modules (covers import_module)."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.add(node.value.split(".")[0])
    return found & BANNED


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess",
        "import pickle as p",
        "from subprocess import run",
        "importlib.import_module('subprocess')",
        "__import__('pickle')",
        "pkgutil.resolve_name('subprocess.run')",
    ],
)
def test_scanner_flags_each_way_to_reach_the_banned_modules(source: str) -> None:
    assert banned_references(source)


@pytest.mark.parametrize("relpath", NO_EXEC_MODULES)
def test_module_references_neither_subprocess_nor_pickle(relpath: str) -> None:
    assert not banned_references((ROOT / relpath).read_text())
