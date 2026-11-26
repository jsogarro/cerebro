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
    "src/models/db/migrations.py",
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


@pytest.mark.parametrize("relpath", NO_EXEC_MODULES)
def test_module_imports_neither_subprocess_nor_pickle(relpath: str) -> None:
    tree = ast.parse((ROOT / relpath).read_text())
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not imported & {"subprocess", "pickle"}
