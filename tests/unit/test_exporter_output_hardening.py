"""Model output and citation metadata must not reach TeX or WeasyPrint as markup."""

import re
import socket
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.models.report import (
    Citation,
    Report,
    ReportConfiguration,
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

ROOT = Path(__file__).resolve().parents[2]
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
def test_non_http_url_renders_as_escaped_text_not_a_link(url: str) -> None:
    out = LaTeXExporter()._format_citation_latex(_citation(url=url))
    assert "\\url" not in out
    assert LaTeXExporter()._escape_latex(url) in out


def test_unlinkable_doi_renders_as_escaped_text_and_url_still_wins() -> None:
    exporter = LaTeXExporter()
    bad = exporter._format_citation_latex(_citation(doi="not a doi {"))
    assert "\\url" not in bad
    assert exporter._escape_latex("not a doi {") in bad
    fallback = exporter._format_citation_latex(
        _citation(doi="not a doi", url="https://example.org/p")
    )
    assert "\\url{https://example.org/p}" in fallback


def test_valid_doi_and_url_still_render() -> None:
    exporter = LaTeXExporter()
    doi = exporter._format_citation_latex(_citation(doi="10.1000/abc.123"))
    assert "\\url{https://doi.org/10.1000/abc.123}" in doi
    url = exporter._format_citation_latex(_citation(url="https://example.org/p?q=1"))
    assert "\\url{https://example.org/p?q=1}" in url


@pytest.mark.parametrize(
    "stored",
    [
        "doi:10.1000/x",
        "DOI: 10.1000/x",
        "https://doi.org/10.1000/x",
        "http://dx.doi.org/10.1000/x",
    ],
)
def test_prefixed_doi_is_normalised_to_the_bare_doi(stored: str) -> None:
    out = LaTeXExporter()._format_citation_latex(_citation(doi=stored))
    assert "\\url{https://doi.org/10.1000/x}" in out


def test_percent_hash_and_tilde_are_kept_and_escaped_inside_url() -> None:
    out = LaTeXExporter()._format_citation_latex(
        _citation(url="https://example.org/~a/b%20c#sec")
    )
    assert "\\url{https://example.org/~a/b\\%20c\\#sec}" in out


UNSAFE_LINK_FRAGMENTS = ["}", "{", "\\", "^", "\n", " ", "\\input{/etc/passwd}"]


@pytest.mark.parametrize("fragment", UNSAFE_LINK_FRAGMENTS)
@pytest.mark.parametrize("kind", ["url", "doi"])
def test_unsafe_link_characters_render_as_escaped_text(
    kind: str, fragment: str
) -> None:
    exporter = LaTeXExporter()
    value = (
        f"https://example.org/p{fragment}q"
        if kind == "url"
        else f"10.1000/ab{fragment}cd"
    )
    out = exporter._format_citation_latex(_citation(**{kind: value}))
    assert "\\url" not in out
    assert exporter._escape_latex(value) in out
    assert _unescaped(out, "{") == _unescaped(out, "}")


def test_dollar_sign_is_legal_inside_a_url_link() -> None:
    out = LaTeXExporter()._format_citation_latex(_citation(url="https://a.org/$x"))
    assert "\\url{https://a.org/$x}" in out


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


def test_report_configuration_has_no_custom_css_injection_point() -> None:
    assert "custom_css" not in ReportConfiguration.model_fields
    template = Path(ROOT / "src/templates/reports/base.html.j2").read_text()
    assert "custom_css" not in template
