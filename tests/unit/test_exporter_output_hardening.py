"""Model output and citation metadata must not reach TeX or WeasyPrint as markup."""

import os
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


def _stub_weasyprint(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    html_cls, css_cls = MagicMock(), MagicMock()
    monkeypatch.setattr(pdf_exporter, "HTML", html_cls)
    monkeypatch.setattr(pdf_exporter, "CSS", css_cls)
    monkeypatch.setattr(pdf_exporter, "WEASYPRINT_AVAILABLE", True)
    monkeypatch.setattr(pdf_exporter, "FontConfiguration", MagicMock())
    return html_cls, css_cls


def _all_constructions(mock: MagicMock) -> list[object]:
    return [call.kwargs.get("url_fetcher") for call in mock.call_args_list]


@pytest.mark.parametrize("stream", [False, True])
def test_every_weasyprint_construction_gets_the_deny_all_fetcher(
    monkeypatch: pytest.MonkeyPatch, stream: bool
) -> None:
    html_cls, css_cls = _stub_weasyprint(monkeypatch)
    exporter = pdf_exporter.PDFExporter(ReportSettings())  # constructs HTML() too
    monkeypatch.setattr(exporter, "_generate_pdf_bytes", lambda *_: b"%PDF")
    export = exporter.export_to_pdf_stream if stream else exporter.export_to_pdf
    export("<p>x</p>", Report(id="r", title="t", query="q"), custom_css="p{}")
    fetchers = _all_constructions(html_cls) + _all_constructions(css_cls)
    assert len(fetchers) >= 4  # dependency probe, document, base CSS, custom CSS
    assert all(f is pdf_exporter.DENY_ALL_FETCHER for f in fetchers)


needs_weasyprint = pytest.mark.skipif(
    not pdf_exporter.WEASYPRINT_AVAILABLE
    and not os.environ.get("CEREBRO_REQUIRE_WEASYPRINT"),
    reason="WeasyPrint not importable",
)


@pytest.mark.skipif(
    not os.environ.get("CEREBRO_REQUIRE_WEASYPRINT"),
    reason="only the Linux renderer job sets CEREBRO_REQUIRE_WEASYPRINT",
)
def test_renderer_job_fails_when_weasyprint_is_missing() -> None:
    assert pdf_exporter.WEASYPRINT_AVAILABLE, "renderer job needs WeasyPrint"


@needs_weasyprint
@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "http://127.0.0.1:1/", "data:text/plain,a", "ftp://example.com/x"]
)
def test_deny_all_fetcher_refuses_every_scheme(url: str) -> None:
    with pytest.raises(ValueError, match="disallowed"):
        pdf_exporter.DENY_ALL_FETCHER(url)


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


def _hostile_html(sentinel: Path, port: int) -> str:
    # Each reference is a different WeasyPrint fetch path: attachment, image,
    # stylesheet import and a CSS background.
    return (
        "<html><head>"
        f'<link rel="attachment" href="{sentinel.as_uri()}">'
        f'<link rel="stylesheet" href="http://127.0.0.1:{port}/a.css">'
        f'<style>@import url("{sentinel.as_uri()}");'
        f'p{{background:url("http://127.0.0.1:{port}/b.png")}}</style></head>'
        f'<body><p>x</p><img src="http://127.0.0.1:{port}/c.png"></body></html>'
    )


@needs_weasyprint
@pytest.mark.parametrize(("hardened", "leaks"), [(True, False), (False, True)])
def test_hostile_html_reaches_neither_file_nor_socket(
    tmp_path: Path, hardened: bool, leaks: bool
) -> None:
    # hardened=False is the control: it proves the sentinel and the listener
    # would see a fetch, so the hardened run cannot pass because WeasyPrint is
    # inert or the detector is blind.
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("SENTINEL-MARKER")
    server, hits = _loopback_listener()
    html = _hostile_html(sentinel, server.getsockname()[1])
    if hardened:
        exporter = pdf_exporter.PDFExporter(ReportSettings())
        pdf = exporter.export_to_pdf(html, Report(id="r", title="t", query="q"))
        pdf_bytes = pdf.content
    else:
        pdf_bytes = pdf_exporter.HTML(string=html).write_pdf()
    server.close()
    assert isinstance(pdf_bytes, bytes)
    assert (b"EmbeddedFile" in pdf_bytes) is leaks
    assert bool(hits) is leaks


@needs_weasyprint
@pytest.mark.parametrize("template_fails", [False, True])
async def test_hostile_report_pdf_embeds_nothing_and_connects_nowhere(
    tmp_path: Path, template_fails: bool
) -> None:
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("SENTINEL-MARKER")
    server, hits = _loopback_listener()
    port = server.getsockname()[1]
    payload = (
        f'<link rel="attachment" href="{sentinel.as_uri()}">'
        f'<img src="http://127.0.0.1:{port}/c.png">'
    )
    report = Report(
        id="r",
        title=payload,
        query="q",
        executive_summary=payload,
        sections=[ReportSection(title=payload, content=payload)],
    )
    generator = _generator()
    if template_fails:
        generator.template_renderer = MagicMock()
        generator.template_renderer.render_report.side_effect = TemplateRenderingError(
            "x"
        )
    pdf = (await generator.generate_pdf(report)).content
    server.close()
    assert isinstance(pdf, bytes) and pdf.startswith(b"%PDF")
    assert b"EmbeddedFile" not in pdf
    assert not hits


def test_report_configuration_has_no_custom_css_injection_point() -> None:
    assert "custom_css" not in ReportConfiguration.model_fields
    template = Path(ROOT / "src/templates/reports/base.html.j2").read_text()
    assert "custom_css" not in template
