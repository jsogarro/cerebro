"""Report output generation service.

This module owns conversion of a structured report into concrete output formats.
"""

import asyncio
import json
import logging

from src.models.report import Report, ReportFormat, ReportOutput
from src.services.exporters import (
    DOCXExporter,
    DOCXExportError,
    LaTeXExporter,
    LaTeXExportError,
    PDFExporter,
    PDFExportError,
)
from src.services.report_config import (
    ReportFormatConfig,
    ReportSettings,
    ReportTemplateConfig,
)
from src.services.template_renderer import TemplateRenderer, TemplateRenderingError

logger = logging.getLogger(__name__)


class ReportOutputGenerator:
    """Generate report outputs in the requested formats."""

    def __init__(
        self,
        settings: ReportSettings,
        format_config: ReportFormatConfig,
        template_config: ReportTemplateConfig,
    ) -> None:
        self.settings = settings
        self.format_config = format_config
        self.template_renderer = TemplateRenderer(settings, template_config)
        self._pdf_exporter: PDFExporter | None = None
        self._latex_exporter: LaTeXExporter | None = None
        self._docx_exporter: DOCXExporter | None = None

    async def generate_formats(
        self, report: Report, formats: list[ReportFormat]
    ) -> dict[ReportFormat, ReportOutput]:
        """Generate report outputs in the requested formats."""
        outputs = {}

        if self.settings.parallel_generation and len(formats) > 1:
            tasks = [self.generate_single_format(report, fmt) for fmt in formats]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for fmt, result in zip(formats, results, strict=True):
                if isinstance(result, Exception):
                    logger.error(f"Failed to generate {fmt}: {result}")
                elif isinstance(result, ReportOutput):
                    outputs[fmt] = result
        else:
            for fmt in formats:
                try:
                    output = await self.generate_single_format(report, fmt)
                    outputs[fmt] = output
                except Exception as e:
                    logger.error(f"Failed to generate {fmt}: {e}")

        return outputs

    async def generate_single_format(
        self, report: Report, format: ReportFormat
    ) -> ReportOutput:
        """Generate report output in a single format."""
        if format == ReportFormat.HTML:
            return await self.generate_html(report)
        if format == ReportFormat.MARKDOWN:
            return await self.generate_markdown(report)
        if format == ReportFormat.JSON:
            return await self.generate_json(report)
        if format == ReportFormat.PDF:
            return await self.generate_pdf(report)
        if format == ReportFormat.LATEX:
            return await self.generate_latex(report)
        if format == ReportFormat.DOCX:
            return await self.generate_docx(report)
        raise ValueError(f"Unsupported format: {format}")

    async def generate_html(self, report: Report) -> ReportOutput:
        """Generate HTML report output."""
        try:
            html_content = self.template_renderer.render_report(report)
        except TemplateRenderingError as e:
            logger.warning(f"Template rendering unavailable, using basic HTML: {e}")
            html_content = self.generate_basic_html(report)

        return ReportOutput(
            format=ReportFormat.HTML,
            content=html_content,
            file_path=None,
            file_size=len(html_content.encode("utf-8")),
            mime_type=self.format_config.get_mime_type(ReportFormat.HTML),
            encoding="utf-8",
        )

    async def generate_markdown(self, report: Report) -> ReportOutput:
        """Generate Markdown report output."""
        markdown = f"# {report.title}\n\n"
        markdown += f"**Generated:** {report.metadata.generated_at}\n"
        markdown += f"**Quality Score:** {report.metadata.quality_score:.2%}\n\n"

        if report.executive_summary:
            markdown += report.executive_summary + "\n\n"

        for section in report.sections:
            markdown += f"{'#' * section.level} {section.title}\n\n"
            markdown += section.content + "\n\n"

            for subsection in section.subsections:
                markdown += f"{'#' * (section.level + 1)} {subsection.title}\n\n"
                markdown += subsection.content + "\n\n"

        if report.citations:
            markdown += "## References\n\n"
            for citation in report.citations:
                formatted = citation.format_citation(report.configuration.citation_style)
                markdown += f"- {formatted}\n"

        return ReportOutput(
            format=ReportFormat.MARKDOWN,
            content=markdown,
            file_path=None,
            file_size=len(markdown.encode("utf-8")),
            mime_type=self.format_config.get_mime_type(ReportFormat.MARKDOWN),
            encoding="utf-8",
        )

    async def generate_json(self, report: Report) -> ReportOutput:
        """Generate JSON report output."""
        json_content = json.dumps(report.model_dump(), indent=2, default=str)

        return ReportOutput(
            format=ReportFormat.JSON,
            content=json_content,
            file_path=None,
            file_size=len(json_content.encode("utf-8")),
            mime_type=self.format_config.get_mime_type(ReportFormat.JSON),
            encoding="utf-8",
        )

    async def generate_pdf(self, report: Report) -> ReportOutput:
        """Generate PDF report output."""
        if self.settings.enable_pdf_generation:
            try:
                html_output = await self.generate_html(report)
                if isinstance(html_output.content, str):
                    return self._get_pdf_exporter().export_to_pdf(html_output.content, report)
            except PDFExportError as e:
                logger.warning(f"PDF export unavailable, using legacy placeholder: {e}")

        pdf_content = f"PDF version of: {report.title}".encode()
        return ReportOutput(
            format=ReportFormat.PDF,
            content=pdf_content,
            file_path=None,
            file_size=len(pdf_content),
            mime_type=self.format_config.get_mime_type(ReportFormat.PDF),
            encoding="binary",
        )

    async def generate_latex(self, report: Report) -> ReportOutput:
        """Generate LaTeX report output."""
        if self.settings.enable_latex_generation:
            try:
                return self._get_latex_exporter().export_to_latex(report)
            except LaTeXExportError as e:
                logger.warning(f"LaTeX export unavailable, using legacy placeholder: {e}")

        latex_content = f"""\\documentclass{{article}}
\\title{{{report.title}}}
\\author{{Research Platform}}
\\date{{\\today}}

\\begin{{document}}
\\maketitle

\\section{{Introduction}}
{report.query}

\\end{{document}}
"""

        return ReportOutput(
            format=ReportFormat.LATEX,
            content=latex_content,
            file_path=None,
            file_size=len(latex_content.encode("utf-8")),
            mime_type=self.format_config.get_mime_type(ReportFormat.LATEX),
            encoding="utf-8",
        )

    async def generate_docx(self, report: Report) -> ReportOutput:
        """Generate DOCX report output."""
        try:
            return self._get_docx_exporter().export_to_docx(report)
        except DOCXExportError as e:
            logger.warning(f"DOCX export unavailable, using legacy placeholder: {e}")

        docx_content = b"DOCX placeholder content"
        return ReportOutput(
            format=ReportFormat.DOCX,
            content=docx_content,
            file_path=None,
            file_size=len(docx_content),
            mime_type=self.format_config.get_mime_type(ReportFormat.DOCX),
            encoding="binary",
        )

    def generate_basic_html(self, report: Report) -> str:
        """Generate basic HTML without templates."""
        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>{report.title}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; }}
        h1 {{ color: #333; }}
        h2 {{ color: #666; }}
        .metadata {{ background: #f0f0f0; padding: 10px; margin: 20px 0; }}
        .section {{ margin: 20px 0; }}
    </style>
</head>
<body>
    <h1>{report.title}</h1>

    <div class="metadata">
        <strong>Generated:</strong> {report.metadata.generated_at}<br>
        <strong>Quality Score:</strong> {report.metadata.quality_score:.2%}
    </div>
"""

        if report.executive_summary:
            html += f"<div class='section'>{report.executive_summary}</div>"

        for section in report.sections:
            html += (
                f"<div class='section'><h{section.level + 1}>{section.title}"
                f"</h{section.level + 1}>"
            )
            html += f"<p>{section.content}</p>"

            for subsection in section.subsections:
                html += f"<h{section.level + 2}>{subsection.title}</h{section.level + 2}>"
                html += f"<p>{subsection.content}</p>"

            html += "</div>"

        if report.citations:
            html += "<div class='section'><h2>References</h2><ul>"
            for citation in report.citations:
                formatted = citation.format_citation(report.configuration.citation_style)
                html += f"<li>{formatted}</li>"
            html += "</ul></div>"

        html += "</body></html>"
        return html

    def _get_pdf_exporter(self) -> PDFExporter:
        """Lazily initialize the PDF exporter."""
        if self._pdf_exporter is None:
            self._pdf_exporter = PDFExporter(self.settings)
        return self._pdf_exporter

    def _get_latex_exporter(self) -> LaTeXExporter:
        """Lazily initialize the LaTeX exporter."""
        if self._latex_exporter is None:
            self._latex_exporter = LaTeXExporter(self.settings)
        return self._latex_exporter

    def _get_docx_exporter(self) -> DOCXExporter:
        """Lazily initialize the DOCX exporter."""
        if self._docx_exporter is None:
            self._docx_exporter = DOCXExporter(self.settings)
        return self._docx_exporter


__all__ = ["ReportOutputGenerator"]
