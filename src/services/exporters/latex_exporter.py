"""
LaTeX export functionality for academic papers.

This module provides LaTeX generation from report data,
following functional programming principles with pure transformation functions.
"""

import logging
import os
import re
import subprocess
import tempfile
from typing import Any

from src.models.report import (
    Citation,
    Report,
    ReportFormat,
    ReportOutput,
    ReportSection,
)
from src.services.report_config import ReportSettings

logger = logging.getLogger(__name__)


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
        self.document_class = self.latex_settings.get('document_class', 'article')
        self.font_size = self.latex_settings.get('font_size', '11pt')
        self.paper_size = self.latex_settings.get('paper_size', 'a4paper')
        self.packages = self.latex_settings.get('packages', [
            'geometry', 'graphicx', 'hyperref', 'cite', 'amsmath', 'amsfonts'
        ])
        
        # Check if pdflatex is available for compilation
        self.pdflatex_available = self._check_pdflatex_availability()
    
    def _check_pdflatex_availability(self) -> bool:
        """Check if pdflatex is available for PDF compilation."""
        try:
            result = subprocess.run(
                ['pdflatex', '--version'],
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            logger.warning("pdflatex not available - LaTeX compilation to PDF disabled")
            return False
    
    def export_to_latex(
        self,
        report: Report,
        include_bibliography: bool = True,
        compile_to_pdf: bool = False
    ) -> ReportOutput:
        """
        Export report to LaTeX format.
        
        Args:
            report: Report object to export
            include_bibliography: Whether to include bibliography
            compile_to_pdf: Whether to compile LaTeX to PDF
            
        Returns:
            ReportOutput with LaTeX content
        """
        try:
            logger.info(f"Starting LaTeX export for report: {report.id}")
            
            # Generate LaTeX content
            latex_content = self._generate_latex_document(report, include_bibliography)
            
            # Compile to PDF if requested and available
            if compile_to_pdf and self.pdflatex_available:
                pdf_bytes = self._compile_latex_to_pdf(latex_content)
                return ReportOutput(
                    format=ReportFormat.PDF,
                    content=pdf_bytes,
                    file_path=None,
                    file_size=len(pdf_bytes),
                    mime_type="application/pdf",
                    encoding="binary"
                )
            else:
                return ReportOutput(
                    format=ReportFormat.LATEX,
                    content=latex_content,
                    file_path=None,
                    file_size=len(latex_content.encode("utf-8")),
                    mime_type="application/x-latex",
                    encoding="utf-8"
                )
                
        except Exception as e:
            logger.error(f"LaTeX export failed: {e}")
            raise LaTeXExportError(f"Failed to generate LaTeX: {e}") from e
    
    def _generate_latex_document(
        self,
        report: Report,
        include_bibliography: bool = True
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
        
        # Main content sections
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
            if package == 'geometry':
                preamble_parts.append(
                    "\\usepackage[margin=2.5cm,top=3cm,bottom=3cm]{geometry}"
                )
            elif package == 'hyperref':
                preamble_parts.append(
                    "\\usepackage[colorlinks=true,linkcolor=blue,citecolor=blue,urlcolor=blue]{hyperref}"
                )
            else:
                preamble_parts.append(f"\\usepackage{{{package}}}")
        
        # Title and author information
        preamble_parts.append(f"\\title{{{self._escape_latex(report.title)}}}")
        
        if report.configuration.author_name:
            preamble_parts.append(
                f"\\author{{{self._escape_latex(report.configuration.author_name)}}}"
            )
            
        if report.configuration.institution:
            preamble_parts.append(
                f"\\affil{{{self._escape_latex(report.configuration.institution)}}}"
            )
        
        preamble_parts.append(f"\\date{{{report.metadata.generated_at.strftime('%B %d, %Y')}}}")
        
        # Custom commands
        preamble_parts.extend([
            "% Custom commands",
            "\\newcommand{\\researchquery}[1]{\\textit{#1}}",
            "\\newcommand{\\finding}[1]{\\textbf{#1}}",
            "\\newcommand{\\confidence}[1]{\\textcolor{blue}{(Confidence: #1)}",
        ])
        
        return "\n".join(preamble_parts)
    
    def _generate_title_page(self, report: Report) -> str:
        """Generate LaTeX title page."""
        title_parts = []
        
        title_parts.append("\\maketitle")
        
        # Research question box
        title_parts.extend([
            "\\begin{center}",
            "\\fbox{\\parbox{0.8\\textwidth}{",
            "\\textbf{Research Question:} \\\\",
            f"\\researchquery{{{self._escape_latex(report.query)}}}",
            "}}",
            "\\end{center}",
        ])
        
        # Metadata table
        if report.domains or report.metadata.total_sources > 0:
            title_parts.extend([
                "\\vspace{1cm}",
                "\\begin{center}",
                "\\begin{tabular}{|l|l|}",
                "\\hline",
                "\\textbf{Research Metadata} & \\textbf{Value} \\\\",
                "\\hline",
            ])
            
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
            
            if report.metadata.quality_score > 0:
                quality_pct = report.metadata.quality_score * 100
                title_parts.append(
                    f"Quality Score & {quality_pct:.1f}\\% \\\\"
                )
                title_parts.append("\\hline")
            
            title_parts.extend([
                "\\end{tabular}",
                "\\end{center}",
            ])
        
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
            abstract_content = self._extract_abstract_from_summary(report.executive_summary or "")
        
        abstract_parts.append(abstract_content)
        
        # Keywords
        if report.domains:
            keywords = ", ".join(report.domains)
            abstract_parts.extend([
                "\\vspace{0.5cm}",
                f"\\textbf{{Keywords:}} {self._escape_latex(keywords)}"
            ])
        
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
            section_parts.append(f"\\subsubsection{{{self._escape_latex(section.title)}}}")
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
            subsection_parts.append(f"\\subsection{{{self._escape_latex(section.title)}}}")
        elif level == 3:
            subsection_parts.append(f"\\subsubsection{{{self._escape_latex(section.title)}}}")
        else:
            subsection_parts.append(f"\\paragraph{{{self._escape_latex(section.title)}}}")
        
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
                journal_part += f", \\textbf{{{citation.volume}}}"
                if citation.issue:
                    journal_part += f"({citation.issue})"
            if citation.pages:
                journal_part += f", {citation.pages}"
            parts.append(journal_part)
        elif citation.publisher:
            parts.append(self._escape_latex(citation.publisher))
        
        # DOI or URL
        if citation.doi:
            parts.append(f"DOI: \\url{{https://doi.org/{citation.doi}}}")
        elif citation.url:
            parts.append(f"\\url{{{citation.url}}}")
        
        return ". ".join(parts) + "."
    
    def _convert_markdown_to_latex(self, markdown_text: str) -> str:
        """Convert markdown text to LaTeX."""
        if not markdown_text:
            return ""
        
        latex_text = markdown_text
        
        # Convert headers (already handled at section level)
        latex_text = re.sub(r'^#{1,6}\s+(.+)$', r'\1', latex_text, flags=re.MULTILINE)
        
        # Convert bold text
        latex_text = re.sub(r'\*\*(.*?)\*\*', r'\\textbf{\1}', latex_text)
        latex_text = re.sub(r'__(.*?)__', r'\\textbf{\1}', latex_text)
        
        # Convert italic text
        latex_text = re.sub(r'\*(.*?)\*', r'\\textit{\1}', latex_text)
        latex_text = re.sub(r'_(.*?)_', r'\\textit{\1}', latex_text)
        
        # Convert code spans
        latex_text = re.sub(r'`(.*?)`', r'\\texttt{\1}', latex_text)
        
        # Convert unordered lists
        latex_text = self._convert_lists_to_latex(latex_text)
        
        # Convert links
        latex_text = re.sub(
            r'\[([^\]]+)\]\(([^\)]+)\)',
            r'\\href{\2}{\1}',
            latex_text
        )
        
        # Convert line breaks
        latex_text = latex_text.replace('\n\n', '\n\n\\par\n')
        
        # Escape remaining LaTeX special characters
        latex_text = self._escape_latex(latex_text)
        
        return latex_text
    
    def _convert_lists_to_latex(self, text: str) -> str:
        """Convert markdown lists to LaTeX."""
        lines = text.split('\n')
        result_lines = []
        in_list = False
        list_type = None
        
        for line in lines:
            stripped = line.strip()
            
            # Check for list items
            if re.match(r'^[-*+]\s+', stripped):  # Unordered list
                if not in_list or list_type != 'itemize':
                    if in_list:
                        result_lines.append(f'\\end{{{list_type}}}')
                    result_lines.append('\\begin{itemize}')
                    in_list = True
                    list_type = 'itemize'
                
                item_text = re.sub(r'^[-*+]\s+', '', stripped)
                result_lines.append(f'\\item {item_text}')
                
            elif re.match(r'^\d+\.\s+', stripped):  # Ordered list
                if not in_list or list_type != 'enumerate':
                    if in_list:
                        result_lines.append(f'\\end{{{list_type}}}')
                    result_lines.append('\\begin{enumerate}')
                    in_list = True
                    list_type = 'enumerate'
                
                item_text = re.sub(r'^\d+\.\s+', '', stripped)
                result_lines.append(f'\\item {item_text}')
                
            else:
                if in_list and stripped == '':
                    continue  # Skip empty lines in lists
                elif in_list:
                    result_lines.append(f'\\end{{{list_type}}}')
                    in_list = False
                    list_type = None
                
                result_lines.append(line)
        
        # Close any open list
        if in_list:
            result_lines.append(f'\\end{{{list_type}}}')
        
        return '\n'.join(result_lines)
    
    def _escape_latex(self, text: str) -> str:
        """Escape LaTeX special characters."""
        if not text:
            return ""
        
        # LaTeX special characters that need escaping
        escape_chars = {
            '&': '\\&',
            '%': '\\%',
            '$': '\\$',
            '#': '\\#',
            '^': '\\textasciicircum{}',
            '_': '\\_',
            '{': '\\{',
            '}': '\\}',
            '~': '\\textasciitilde{}',
            '\\': '\\textbackslash{}',
        }
        
        escaped_text = text
        for char, escape in escape_chars.items():
            escaped_text = escaped_text.replace(char, escape)
        
        return escaped_text
    
    def _extract_abstract_from_summary(self, executive_summary: str) -> str:
        """Extract abstract content from executive summary."""
        if not executive_summary:
            return "Abstract not available."
        
        # Remove markdown headers and extract first few sentences
        lines = executive_summary.split('\n')
        content_lines = []
        
        for line in lines:
            clean_line = line.strip()
            if clean_line and not clean_line.startswith('#') and not clean_line.startswith('**'):
                content_lines.append(clean_line)
        
        # Take first few sentences (up to ~200 words)
        abstract_text = ' '.join(content_lines)
        words = abstract_text.split()
        
        if len(words) > 200:
            abstract_text = ' '.join(words[:200]) + "..."
        
        return self._convert_markdown_to_latex(abstract_text)
    
    def _compile_latex_to_pdf(self, latex_content: str) -> bytes:
        """Compile LaTeX content to PDF using pdflatex."""
        if not self.pdflatex_available:
            raise LaTeXExportError("pdflatex not available for compilation")
        
        with tempfile.TemporaryDirectory() as temp_dir:
            # Write LaTeX file
            tex_file = os.path.join(temp_dir, "document.tex")
            with open(tex_file, 'w', encoding='utf-8') as f:
                f.write(latex_content)
            
            # Compile with pdflatex (run twice for cross-references)
            for run in range(2):
                try:
                    result = subprocess.run(
                        ['pdflatex', '-interaction=nonstopmode', '-output-directory', temp_dir, tex_file],
                        capture_output=True,
                        text=True,
                        timeout=60
                    )
                    
                    if result.returncode != 0:
                        logger.error(f"pdflatex compilation failed:\n{result.stdout}\n{result.stderr}")
                        if run == 0:  # Try once more
                            continue
                        raise LaTeXExportError(f"pdflatex failed: {result.stderr}")
                        
                except subprocess.TimeoutExpired:
                    raise LaTeXExportError("pdflatex compilation timed out") from None
            
            # Read generated PDF
            pdf_file = os.path.join(temp_dir, "document.pdf")
            if not os.path.exists(pdf_file):
                raise LaTeXExportError("PDF file was not generated")
            
            with open(pdf_file, 'rb') as f:
                return f.read()
    
    def validate_latex_content(self, latex_content: str) -> list[str]:
        """Validate LaTeX content and return warnings."""
        warnings = []
        
        # Check for unmatched braces
        open_braces = latex_content.count('{')
        close_braces = latex_content.count('}')
        if open_braces != close_braces:
            warnings.append(f"Unmatched braces: {open_braces} open, {close_braces} close")
        
        # Check for common LaTeX errors
        error_patterns = [
            (r'\\begin\{(\w+)\}(?!.*\\end\{\1\})', "Unmatched \\begin{} without \\end{}"),
            (r'(?<!\\)&(?![&\s])', "Unescaped & character"),
            (r'(?<!\\)%(?![%\s])', "Unescaped % character"),
            (r'(?<!\\)\$(?![_\s])', "Unescaped $ character"),
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