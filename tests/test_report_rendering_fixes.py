"""Regression tests for LaTeX export, report templates and the HTML sanitizer."""

import shutil
import subprocess
from pathlib import Path

import pytest

from src.models.report import (
    Report,
    ReportConfiguration,
    ReportSection,
    ReportType,
)
from src.services.exporters.latex_exporter import LaTeXExporter
from src.services.template_renderer import TemplateRenderer


def _report(report_type: ReportType = ReportType.ACADEMIC_PAPER) -> Report:
    report = Report(
        id="r1",
        title="Rendering",
        query="What is 100% of a_b?",
        executive_summary="Summary line one\nSummary line two",
        configuration=ReportConfiguration(
            type=report_type, author_name="Ada", institution="Lab"
        ),
    )
    report.add_section(
        "Results",
        "This is **bold** and *slanted* and `code_x` with 50% & [a link](https://e.com/a_b?x=1#f).\n\n"
        "- first_item\n- second\n\n1. one\n2. two",
        level=1,
    )
    report.sections[0].subsections.append(
        ReportSection(title="Sub", content="sub **text**", level=2)
    )
    return report


@pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex not installed")
def test_latex_export_compiles_and_bold_is_bold(tmp_path: Path) -> None:
    tex = LaTeXExporter()._generate_latex_document(_report())
    (tmp_path / "doc.tex").write_text(tex, encoding="utf-8")

    result = subprocess.run(
        ["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "doc.tex"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout[-2000:]
    assert (tmp_path / "doc.pdf").exists()
    assert "\\textbf{bold}" in tex
    assert "textbackslash{}textbf" not in tex


def test_latex_markdown_inline_and_special_chars() -> None:
    out = LaTeXExporter()._convert_markdown_to_latex("**b** 50% a_b {x}")
    assert out == "\\textbf{b} 50\\% a\\_b \\{x\\}"


def test_latex_lists_are_real_environments() -> None:
    out = LaTeXExporter()._convert_markdown_to_latex("- a\n- b")
    assert out == "\\begin{itemize}\n\\item a\n\\item b\n\\end{itemize}"


@pytest.mark.parametrize(
    "template",
    ["executive_summary.html.j2", "academic_paper.html.j2"],
)
def test_report_templates_render(template: str) -> None:
    html = TemplateRenderer().render_report(_report(), template)
    assert "Rendering" in html
    assert "Results" in html


def test_sanitizer_nested_img_is_inert() -> None:
    out = TemplateRenderer()._sanitize_rendered_html("<<b>img src=x onerror=alert(1)>")
    assert "<img" not in out
    assert "onerror" not in out or "<" not in out


def test_basic_markdown_nested_script_is_inert() -> None:
    out = TemplateRenderer()._basic_markdown_filter("<<b>script>alert(1)<<b>/script>")
    assert "<script" not in out.lower()
