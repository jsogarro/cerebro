"""Model output and citation metadata must not reach TeX or WeasyPrint as markup."""

import re
import socket
import threading
from unittest.mock import MagicMock

import pytest

from src.models.report import (
    Citation,
    Report,
    ReportSection,
    ReportType,
)
from src.services.exporters import pdf_exporter
from src.services.exporters.latex_exporter import LaTeXExporter
from src.services.report_config import (
    ReportFormatConfig,
    ReportSettings,
    ReportTemplateConfig,
)
from src.services.report_output_generator import ReportOutputGenerator
from src.services.template_renderer import TemplateRenderingError

INJECTION = r"x}\input{/etc/passwd}%"
FORBIDDEN_TEX = ("\\input", "\\openin", "\\write", "\\catcode", "file:")


def _unescaped(text: str, char: str) -> int:
    return len(re.findall(rf"(?<!\\){re.escape(char)}", text))


@pytest.mark.parametrize(
    ("char", "expected"),
    [
        ("\\", "\\textbackslash{}"),
        ("{", "\\{"),
        ("}", "\\}"),
        ("%", "\\%"),
        ("$", "\\$"),
        ("&", "\\&"),
        ("#", "\\#"),
        ("_", "\\_"),
        ("^", "\\textasciicircum{}"),
        ("~", "\\textasciitilde{}"),
    ],
)
def test_escape_latex_maps_each_special_character_once(
    char: str, expected: str
) -> None:
    assert LaTeXExporter()._escape_latex(char) == expected


def _citation(**fields: str) -> Citation:
    return Citation(id="c", authors=["A"], title="T", journal="J", **fields)


@pytest.mark.parametrize("field", ["volume", "issue", "pages", "doi", "url"])
def test_citation_fields_cannot_inject_tex(field: str) -> None:
    fields = {"volume": "1", field: INJECTION}
    out = LaTeXExporter()._format_citation_latex(_citation(**fields))
    assert not any(bad in out for bad in FORBIDDEN_TEX), out
    assert _unescaped(out, "{") == _unescaped(out, "}")


@pytest.mark.parametrize("url", ["file:///etc/passwd", "javascript:x", INJECTION])
def test_url_must_be_plain_http(url: str) -> None:
    out = LaTeXExporter()._format_citation_latex(_citation(url=url))
    assert "\\url" not in out and "file:" not in out and "javascript" not in out


def test_valid_doi_and_url_still_render() -> None:
    exporter = LaTeXExporter()
    doi = exporter._format_citation_latex(_citation(doi="10.1000/abc.123"))
    assert "\\url{https://doi.org/10.1000/abc.123}" in doi
    url = exporter._format_citation_latex(_citation(url="https://example.org/p?q=1"))
    assert "\\url{https://example.org/p?q=1}" in url


def test_trailing_newline_does_not_pass_the_url_check() -> None:
    out = LaTeXExporter()._format_citation_latex(_citation(url="https://a.org/x\n"))
    assert "\\url" not in out


def _hostile_report(report_type: ReportType) -> Report:
    report = Report(
        id="r",
        title="<img src=x>",
        query="q",
        sections=[ReportSection(title="<img src=x>", content="body")],
    )
    report.configuration.type = report_type
    return report


def _generator() -> ReportOutputGenerator:
    settings = ReportSettings()
    return ReportOutputGenerator(
        settings, ReportFormatConfig(settings), ReportTemplateConfig(settings)
    )


@pytest.mark.parametrize("report_type", list(ReportType))
async def test_html_output_escapes_titles_for_every_report_type(
    report_type: ReportType,
) -> None:
    # Goes through the generator so a template that fails to render is covered
    # by the escaped fallback rather than hidden by an exception.
    html = (await _generator().generate_html(_hostile_report(report_type))).content
    assert isinstance(html, str)
    assert "&lt;img" in html
    assert "<img src=x>" not in html


async def test_basic_html_fallback_escapes_everything() -> None:
    generator = _generator()
    generator.template_renderer = MagicMock()
    generator.template_renderer.render_report.side_effect = TemplateRenderingError("x")
    report = Report(
        id="r",
        title="<img src=http://127.0.0.1:1/x>",
        query="q",
        executive_summary="<script>s</script>",
        sections=[
            ReportSection(
                title="<b>t</b>",
                content="<link rel=attachment href=file:///etc/passwd>",
                subsections=[ReportSection(title="<i>u</i>", content="<u>v</u>")],
            )
        ],
        citations=[Citation(id="c", authors=["<a>"], title="<em>T</em>")],
    )
    html = (await generator.generate_html(report)).content
    assert isinstance(html, str)
    body = html.split("<body>")[1]
    for tag in ("<img", "<link", "<script", "<b>", "<i>", "<u>", "<a>", "<em>"):
        assert tag not in body, tag
    assert "&lt;img" in html and "&lt;link" in html


def test_pdf_export_passes_the_data_only_fetcher_to_html_and_css(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html_cls, css_cls = MagicMock(), MagicMock()
    monkeypatch.setattr(pdf_exporter, "HTML", html_cls)
    monkeypatch.setattr(pdf_exporter, "CSS", css_cls)
    monkeypatch.setattr(pdf_exporter, "WEASYPRINT_AVAILABLE", True)
    monkeypatch.setattr(pdf_exporter, "FontConfiguration", MagicMock())
    exporter = pdf_exporter.PDFExporter(ReportSettings())
    monkeypatch.setattr(exporter, "_generate_pdf_bytes", lambda *_: b"%PDF")
    exporter.export_to_pdf("<p>x</p>", Report(id="r", title="t", query="q"))
    assert html_cls.call_args.kwargs["url_fetcher"] is pdf_exporter.DATA_ONLY_FETCHER
    assert css_cls.call_args.kwargs["url_fetcher"] is pdf_exporter.DATA_ONLY_FETCHER


needs_weasyprint = pytest.mark.skipif(
    not pdf_exporter.WEASYPRINT_AVAILABLE, reason="WeasyPrint not importable"
)


@needs_weasyprint
def test_data_only_fetcher_refuses_files_and_network() -> None:
    fetcher = pdf_exporter.DATA_ONLY_FETCHER
    with pytest.raises(ValueError, match="disallowed"):
        fetcher("file:///etc/passwd")
    with pytest.raises(ValueError, match="disallowed"):
        fetcher("http://127.0.0.1:1/")
    assert fetcher("data:text/plain,a") is not None


def _loopback_listener() -> tuple[socket.socket, list[int]]:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    server.settimeout(5)
    hits: list[int] = []

    def accept() -> None:
        try:
            while True:
                conn, _ = server.accept()
                hits.append(1)
                conn.close()
        except OSError:
            return

    threading.Thread(target=accept, daemon=True).start()
    return server, hits


@needs_weasyprint
@pytest.mark.parametrize(("hardened", "expected_hits"), [(True, 0), (False, 1)])
def test_render_never_connects_to_remote_hosts(
    hardened: bool, expected_hits: int
) -> None:
    # The control run (hardened=False) proves the listener would see a fetch.
    server, hits = _loopback_listener()
    port = server.getsockname()[1]
    html = f'<html><body><img src="http://127.0.0.1:{port}/x.png"></body></html>'
    kwargs = {"url_fetcher": pdf_exporter.DATA_ONLY_FETCHER} if hardened else {}
    pdf_exporter.HTML(string=html, **kwargs).write_pdf()
    server.close()
    assert len(hits) == expected_hits
