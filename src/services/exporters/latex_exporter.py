"""
LaTeX export functionality for academic papers.

This module provides LaTeX generation from report data,
following functional programming principles with pure transformation functions.
"""

import re
from typing import Any

from structlog import get_logger

from src.models.report import (
    Citation,
    Report,
    ReportFormat,
    ReportOutput,
    ReportSection,
    source_label,
)
from src.services.report_config import ReportSettings

logger = get_logger()

# One pass, so no replacement is itself re-escaped.
_LATEX_ESCAPES = {
    "&": "\\&",
    "%": "\\%",
    "$": "\\$",
    "#": "\\#",
    "^": "\\textasciicircum{}",
    "_": "\\_",
    "{": "\\{",
    "}": "\\}",
    "~": "\\textasciitilde{}",
    "\\": "\\textbackslash{}",
}
_LATEX_SPECIAL = re.compile("[" + re.escape("".join(_LATEX_ESCAPES)) + "]")
# fullmatch, not match-with-$: "$" accepts a trailing newline.
_SAFE_DOI = re.compile(r"10\.\d{4,9}/[^\s{}\\^]+")
_SAFE_URL = re.compile(r"https?://[^\s{}\\^]+")
_DOI_PREFIX = re.compile(r"\s*(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)


class LaTeXExportError(Exception):
    """Exception raised during LaTeX export."""

    pass


class LaTeXExporter:
    """Service for exporting reports to LaTeX format."""

    def __init__(self, settings: ReportSettings | None = None):
        """Initialize LaTeX exporter."""
        self.settings = settings or ReportSettings()
        self.latex_settings: dict[str, Any] = {}

        # LaTeX document configuration
        self.document_class = self.latex_settings.get("document_class", "article")
        self.font_size = self.latex_settings.get("font_size", "11pt")
        self.paper_size = self.latex_settings.get("paper_size", "a4paper")
        self.packages = self.latex_settings.get(
            "packages",
            [
                "geometry",
                "graphicx",
                "hyperref",
                "cite",
                "amsmath",
                "amsfonts",
                "xcolor",
                "authblk",
            ],
        )

    def export_to_latex(
        self,
        report: Report,
        include_bibliography: bool = True,
    ) -> ReportOutput:
        """
        Export report to LaTeX format.

        Args:
            report: Report object to export
            include_bibliography: Whether to include bibliography

        Returns:
            ReportOutput with LaTeX content
        """
        try:
            logger.info(f"Starting LaTeX export for report: {report.id}")

            # Generate LaTeX content
            latex_content = self._generate_latex_document(report, include_bibliography)

            return ReportOutput(
                format=ReportFormat.LATEX,
                content=latex_content,
                file_path=None,
                file_size=len(latex_content.encode("utf-8")),
                mime_type="application/x-latex",
                encoding="utf-8",
            )

        except Exception as e:
            logger.error(f"LaTeX export failed: {e}")
            raise LaTeXExportError(f"Failed to generate LaTeX: {e}") from e

    def _generate_latex_document(
        self, report: Report, include_bibliography: bool = True
    ) -> str:
        """Generate complete LaTeX document from report."""
        sections = []

        # Document class and preamble
        sections.append(self._generate_preamble(report))

        # Begin document
        sections.append("\\begin{document}")

        # Title page
        sections.append(self._generate_title_page(report))

        # Abstract (if available)
        if report.abstract or report.executive_summary:
            sections.append(self._generate_abstract(report))

        # Table of contents
        if report.configuration.include_toc:
            sections.append("\\tableofcontents")
            sections.append("\\newpage")

        for section in report.sections:
            sections.append(self._generate_section(section))

        # Bibliography
        if include_bibliography and report.citations:
            sections.append(self._generate_bibliography(report.citations))

        # End document
        sections.append("\\end{document}")

        return "\n\n".join(sections)

    def _generate_preamble(self, report: Report) -> str:
        """Generate LaTeX document preamble."""
        preamble_parts = []

        # Document class
        preamble_parts.append(
            f"\\documentclass[{self.font_size},{self.paper_size}]{{{self.document_class}}}"
        )

        # Packages
        for package in self.packages:
            if package == "geometry":
                preamble_parts.append(
                    "\\usepackage[margin=2.5cm,top=3cm,bottom=3cm]{geometry}"
                )
            elif package == "hyperref":
                preamble_parts.append(
                    "\\usepackage[colorlinks=true,linkcolor=blue,citecolor=blue,urlcolor=blue]{hyperref}"
                )
            else:
                preamble_parts.append(f"\\usepackage{{{package}}}")

        # Title and author information
        preamble_parts.append(f"\\title{{{self._escape_latex(report.title)}}}")

        # authblk's default \\author prints a stray "immediate" on the title page.
        preamble_parts.append(
            f"\\author{{{self._escape_latex(report.configuration.author_name or '')}}}"
        )

        if report.configuration.institution:
            preamble_parts.append(
                f"\\affil{{{self._escape_latex(report.configuration.institution)}}}"
            )

        preamble_parts.append(
            f"\\date{{{report.metadata.generated_at.strftime('%B %d, %Y')}}}"
        )

        # Custom commands
        preamble_parts.extend(
            [
                "% Custom commands",
                "\\newcommand{\\researchquery}[1]{\\textit{#1}}",
                "\\newcommand{\\finding}[1]{\\textbf{#1}}",
                "\\newcommand{\\confidence}[1]{\\textcolor{blue}{(Confidence: #1)}}",
            ]
        )

        return "\n".join(preamble_parts)

    def _generate_title_page(self, report: Report) -> str:
        """Generate LaTeX title page."""
        title_parts = []

        title_parts.append("\\maketitle")

        if label := source_label(report.metadata.source_kind):
            title_parts.append(
                f"\\begin{{center}}\\textit{{{self._escape_latex(label)}}}\\end{{center}}"
            )

        # Research question box
        title_parts.extend(
            [
                "\\begin{center}",
                "\\fbox{\\parbox{0.8\\textwidth}{",
                "\\textbf{Research Question:} \\\\",
                f"\\researchquery{{{self._escape_latex(report.query)}}}",
                "}}",
                "\\end{center}",
            ]
        )

        # Metadata table
        if report.domains or report.metadata.total_sources > 0:
            title_parts.extend(
                [
                    "\\vspace{1cm}",
                    "\\begin{center}",
                    "\\begin{tabular}{|l|l|}",
                    "\\hline",
                    "\\textbf{Research Metadata} & \\textbf{Value} \\\\",
                    "\\hline",
                ]
            )

            if report.domains:
                domains_str = ", ".join(report.domains)
                title_parts.append(
                    f"Research Domains & {self._escape_latex(domains_str)} \\\\"
                )
                title_parts.append("\\hline")

            if report.metadata.total_sources > 0:
                title_parts.append(
                    f"Sources Analyzed & {report.metadata.total_sources} \\\\"
                )
                title_parts.append("\\hline")

            quality = report.metadata.quality_score
            quality_text = (
                "Not evaluated" if quality is None else f"{quality * 100:.1f}\\%"
            )
            title_parts.append(f"Quality Score & {quality_text} \\\\")
            title_parts.append("\\hline")

            title_parts.extend(
                [
                    "\\end{tabular}",
                    "\\end{center}",
                ]
            )

        title_parts.append("\\newpage")

        return "\n".join(title_parts)

    def _generate_abstract(self, report: Report) -> str:
        """Generate LaTeX abstract section."""
        abstract_parts = []

        abstract_parts.append("\\begin{abstract}")

        if report.abstract:
            abstract_content = self._convert_markdown_to_latex(report.abstract)
        else:
            # Generate abstract from executive summary
            abstract_content = self._extract_abstract_from_summary(
                report.executive_summary or ""
            )

        abstract_parts.append(abstract_content)

        # Keywords
        if report.domains:
            keywords = ", ".join(report.domains)
            abstract_parts.extend(
                [
                    "\\vspace{0.5cm}",
                    f"\\textbf{{Keywords:}} {self._escape_latex(keywords)}",
                ]
            )

        abstract_parts.append("\\end{abstract}")
        abstract_parts.append("\\newpage")

        return "\n".join(abstract_parts)

    def _generate_section(self, section: ReportSection) -> str:
        """Generate LaTeX for a report section."""
        section_parts = []

        # Section header
        if section.level == 1:
            section_parts.append(f"\\section{{{self._escape_latex(section.title)}}}")
        elif section.level == 2:
            section_parts.append(f"\\subsection{{{self._escape_latex(section.title)}}}")
        elif section.level == 3:
            section_parts.append(
                f"\\subsubsection{{{self._escape_latex(section.title)}}}"
            )
        else:
            section_parts.append(f"\\paragraph{{{self._escape_latex(section.title)}}}")

        # Section content
        latex_content = self._convert_markdown_to_latex(section.content)
        section_parts.append(latex_content)

        # Subsections
        for subsection in section.subsections:
            subsection_latex = self._generate_subsection(subsection, section.level + 1)
            section_parts.append(subsection_latex)

        return "\n\n".join(section_parts)

    def _generate_subsection(self, section: ReportSection, level: int) -> str:
        """Generate LaTeX for a subsection."""
        subsection_parts = []

        # Subsection header
        if level == 2:
            subsection_parts.append(
                f"\\subsection{{{self._escape_latex(section.title)}}}"
            )
        elif level == 3:
            subsection_parts.append(
                f"\\subsubsection{{{self._escape_latex(section.title)}}}"
            )
        else:
            subsection_parts.append(
                f"\\paragraph{{{self._escape_latex(section.title)}}}"
            )

        # Subsection content
        latex_content = self._convert_markdown_to_latex(section.content)
        subsection_parts.append(latex_content)

        return "\n\n".join(subsection_parts)

    def _generate_bibliography(self, citations: list[Citation]) -> str:
        """Generate LaTeX bibliography."""
        bib_parts = []

        bib_parts.append("\\begin{thebibliography}{99}")

        for i, citation in enumerate(citations, 1):
            # Generate bibitem
            cite_key = f"ref{i}"
            bib_parts.append(f"\\bibitem{{{cite_key}}}")

            # Format citation
            latex_citation = self._format_citation_latex(citation)
            bib_parts.append(latex_citation)

        bib_parts.append("\\end{thebibliography}")

        return "\n".join(bib_parts)

    def _format_citation_latex(self, citation: Citation) -> str:
        """Format a single citation in LaTeX."""
        parts = []

        # Authors
        if citation.authors:
            authors_str = ", ".join(citation.authors)
            parts.append(self._escape_latex(authors_str))

        # Year
        if citation.year:
            parts.append(f"({citation.year})")

        # Title
        if citation.title:
            parts.append(f"\\textit{{{self._escape_latex(citation.title)}}}")

        # Journal/Publisher info
        if citation.journal:
            journal_part = self._escape_latex(citation.journal)
            if citation.volume:
                journal_part += f", \\textbf{{{self._escape_latex(citation.volume)}}}"
                if citation.issue:
                    journal_part += f"({self._escape_latex(citation.issue)})"
            if citation.pages:
                journal_part += f", {self._escape_latex(citation.pages)}"
            parts.append(journal_part)
        elif citation.publisher:
            parts.append(self._escape_latex(citation.publisher))

        link = self._format_link_latex(citation)
        if link:
            parts.append(link)

        return ". ".join(parts) + "."

    def _format_link_latex(self, citation: Citation) -> str:
        """A DOI or URL as a link when it is a plain one, else as escaped text."""
        doi = _DOI_PREFIX.sub("", citation.doi or "", count=1).strip()
        if _SAFE_DOI.fullmatch(doi):
            return f"DOI: \\url{{https://doi.org/{self._url_argument(doi)}}}"
        if citation.url and _SAFE_URL.fullmatch(citation.url):
            return f"\\url{{{self._url_argument(citation.url)}}}"
        text = citation.doi or citation.url
        if not text:
            return ""
        prefix = "DOI: " if citation.doi else ""
        return prefix + self._escape_latex(text)

    @staticmethod
    def _url_argument(url: str) -> str:
        """hyperref's \\url reads \\% and \\# as literal % and #."""
        return url.replace("%", "\\%").replace("#", "\\#")

    def _convert_markdown_to_latex(self, markdown_text: str) -> str:
        """Convert markdown text to LaTeX."""
        if not markdown_text:
            return ""

        latex_text = re.sub(
            r"^#{1,6}\s+(.+)$", r"\1", markdown_text, flags=re.MULTILINE
        )
        return self._convert_lists_to_latex(latex_text)

    _INLINE_RE = re.compile(
        r"\*\*(?P<b1>.+?)\*\*|__(?P<b2>.+?)__|\*(?P<i1>.+?)\*|(?<!\w)_(?P<i2>.+?)_(?!\w)"
        r"|`(?P<code>.+?)`|\[(?P<text>[^\]]+)\]\((?P<url>[^)]+)\)"
    )

    def _inline_to_latex(self, text: str) -> str:
        """Convert inline markdown, escaping literal text so markup is not escaped."""
        out: list[str] = []
        pos = 0
        for m in self._INLINE_RE.finditer(text):
            out.append(self._escape_latex(text[pos : m.start()]))
            pos = m.end()
            if m.group("b1") or m.group("b2"):
                out.append(
                    f"\\textbf{{{self._inline_to_latex(m.group('b1') or m.group('b2'))}}}"
                )
            elif m.group("i1") or m.group("i2"):
                out.append(
                    f"\\textit{{{self._inline_to_latex(m.group('i1') or m.group('i2'))}}}"
                )
            elif m.group("code"):
                out.append(f"\\texttt{{{self._escape_latex(m.group('code'))}}}")
            else:
                url = re.sub(r"[\\{}]", "", m.group("url"))
                url = re.sub(r"([%#&])", r"\\\1", url)
                out.append(
                    f"\\href{{{url}}}{{{self._inline_to_latex(m.group('text'))}}}"
                )
        out.append(self._escape_latex(text[pos:]))
        return "".join(out)

    def _convert_lists_to_latex(self, text: str) -> str:
        """Convert markdown lists to LaTeX."""
        lines = text.split("\n")
        result_lines = []
        in_list = False
        list_type = None

        for line in lines:
            stripped = line.strip()

            if re.match(r"^[-*+]\s+", stripped):  # Unordered list
                if not in_list or list_type != "itemize":
                    if in_list:
                        result_lines.append(f"\\end{{{list_type}}}")
                    result_lines.append("\\begin{itemize}")
                    in_list = True
                    list_type = "itemize"

                item_text = re.sub(r"^[-*+]\s+", "", stripped)
                result_lines.append(f"\\item {self._inline_to_latex(item_text)}")

            elif re.match(r"^\d+\.\s+", stripped):  # Ordered list
                if not in_list or list_type != "enumerate":
                    if in_list:
                        result_lines.append(f"\\end{{{list_type}}}")
                    result_lines.append("\\begin{enumerate}")
                    in_list = True
                    list_type = "enumerate"

                item_text = re.sub(r"^\d+\.\s+", "", stripped)
                result_lines.append(f"\\item {self._inline_to_latex(item_text)}")

            else:
                if in_list and stripped == "":
                    continue  # Skip empty lines in lists
                elif in_list:
                    result_lines.append(f"\\end{{{list_type}}}")
                    in_list = False
                    list_type = None

                result_lines.append(self._inline_to_latex(line))

        # Close any open list
        if in_list:
            result_lines.append(f"\\end{{{list_type}}}")

        return "\n".join(result_lines)

    def _escape_latex(self, text: str) -> str:
        """Escape LaTeX special characters."""
        if not text:
            return ""

        return _LATEX_SPECIAL.sub(lambda m: _LATEX_ESCAPES[m.group()], text)

    def _extract_abstract_from_summary(self, executive_summary: str) -> str:
        """Extract abstract content from executive summary."""
        if not executive_summary:
            return "Abstract not available."

        # Remove markdown headers and extract first few sentences
        lines = executive_summary.split("\n")
        content_lines = []

        for line in lines:
            clean_line = line.strip()
            if (
                clean_line
                and not clean_line.startswith("#")
                and not clean_line.startswith("**")
            ):
                content_lines.append(clean_line)

        # Take first few sentences (up to ~200 words)
        abstract_text = " ".join(content_lines)
        words = abstract_text.split()

        if len(words) > 200:
            abstract_text = " ".join(words[:200]) + "..."

        return self._convert_markdown_to_latex(abstract_text)

    def validate_latex_content(self, latex_content: str) -> list[str]:
        """Validate LaTeX content and return warnings."""
        warnings = []

        open_braces = latex_content.count("{")
        close_braces = latex_content.count("}")
        if open_braces != close_braces:
            warnings.append(
                f"Unmatched braces: {open_braces} open, {close_braces} close"
            )

        error_patterns = [
            (
                r"\\begin\{(\w+)\}(?!.*\\end\{\1\})",
                "Unmatched \\begin{} without \\end{}",
            ),
            (r"(?<!\\)&(?![&\s])", "Unescaped & character"),
            (r"(?<!\\)%(?![%\s])", "Unescaped % character"),
            (r"(?<!\\)\$(?![_\s])", "Unescaped $ character"),
        ]

        for pattern, message in error_patterns:
            if re.search(pattern, latex_content):
                warnings.append(message)

        return warnings


def create_latex_exporter(settings: ReportSettings | None = None) -> LaTeXExporter:
    """Factory function to create a LaTeX exporter."""
    return LaTeXExporter(settings)


__all__ = [
    "LaTeXExportError",
    "LaTeXExporter",
    "create_latex_exporter",
]
