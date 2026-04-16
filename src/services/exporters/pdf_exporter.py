"""
PDF export functionality using WeasyPrint.

This module provides PDF generation from HTML reports using WeasyPrint,
following functional programming principles with pure transformation functions.
"""

import io
import logging
import os
import tempfile
from typing import Any

try:
    import weasyprint
    from weasyprint import CSS, HTML
    from weasyprint.text.fonts import FontConfiguration
    WEASYPRINT_AVAILABLE = True
except ImportError:
    WEASYPRINT_AVAILABLE = False
    weasyprint = None
    HTML = None
    CSS = None
    FontConfiguration = None

from src.models.report import Report, ReportFormat, ReportOutput
from src.services.report_config import ReportSettings

logger = logging.getLogger(__name__)


class PDFExportError(Exception):
    """Exception raised during PDF export."""
    pass


class PDFExporter:
    """Service for exporting reports to PDF format using WeasyPrint."""
    
    def __init__(self, settings: ReportSettings | None = None):
        """Initialize PDF exporter."""
        if not WEASYPRINT_AVAILABLE:
            raise PDFExportError(
                "WeasyPrint is not available. Install with: pip install weasyprint"
            )
        
        self.settings = settings or ReportSettings()
        self.font_config = FontConfiguration()

        # PDF-specific settings
        self.pdf_settings: dict[str, Any] = {}
        
        # Ensure required dependencies are available
        self._validate_dependencies()
    
    def _validate_dependencies(self) -> None:
        """Validate that all required dependencies are available."""
        try:
            # Test WeasyPrint installation
            HTML(string="<html><body>Test</body></html>").write_pdf()
        except Exception as e:
            logger.warning(f"WeasyPrint validation warning: {e}")
            # Don't fail on warnings, but log them
    
    def export_to_pdf(
        self,
        html_content: str,
        report: Report,
        custom_css: str | None = None
    ) -> ReportOutput:
        """
        Export HTML content to PDF.
        
        This is a pure function that transforms HTML content into PDF bytes.
        
        Args:
            html_content: HTML content to convert
            report: Report metadata for configuration
            custom_css: Optional custom CSS styles
            
        Returns:
            ReportOutput with PDF content
        """
        try:
            logger.info(f"Starting PDF export for report: {report.id}")
            
            # Prepare CSS styles
            css_styles = self._build_css_styles(report, custom_css)
            
            # Create WeasyPrint HTML document
            html_doc = HTML(string=html_content, base_url=".")
            
            # Create CSS stylesheets
            css_objects = []
            for css_content in css_styles:
                css_objects.append(CSS(string=css_content, font_config=self.font_config))
            
            # Generate PDF
            pdf_bytes = self._generate_pdf_bytes(html_doc, css_objects, report)
            
            # Create report output
            output = ReportOutput(
                format=ReportFormat.PDF,
                content=pdf_bytes,
                file_path=None,
                file_size=len(pdf_bytes),
                mime_type="application/pdf",
                encoding="binary"
            )
            
            logger.info(f"PDF export completed. Size: {len(pdf_bytes)} bytes")
            return output
            
        except Exception as e:
            logger.error(f"PDF export failed: {e}")
            raise PDFExportError(f"Failed to generate PDF: {e}")
    
    def _build_css_styles(
        self,
        report: Report,
        custom_css: str | None = None
    ) -> list[str]:
        """Build CSS styles for PDF generation."""
        css_styles = []
        
        # Base PDF styles
        base_css = self._get_base_pdf_css(report)
        css_styles.append(base_css)
        
        # Print-specific styles
        print_css = self._get_print_css(report)
        css_styles.append(print_css)
        
        # Custom CSS from configuration
        if report.configuration.custom_css:
            css_styles.append(report.configuration.custom_css)
        
        # Override custom CSS
        if custom_css:
            css_styles.append(custom_css)
        
        return css_styles
    
    def _get_base_pdf_css(self, report: Report) -> str:
        """Get base CSS styles optimized for PDF generation."""
        pdf_config = self.pdf_settings
        
        return f"""
        /* Base PDF styles */
        @page {{
            size: {pdf_config.get('page_size', 'A4')};
            margin-top: {pdf_config.get('margin_top', '2cm')};
            margin-bottom: {pdf_config.get('margin_bottom', '2cm')};
            margin-left: {pdf_config.get('margin_left', '2cm')};
            margin-right: {pdf_config.get('margin_right', '2cm')};
            
            @top-center {{
                content: "{pdf_config.get('header_text', '')}";
                font-size: 10pt;
                color: #666;
            }}
            
            @bottom-center {{
                content: "{pdf_config.get('footer_text', 'Page ') or 'Page '}" counter(page);
                font-size: 10pt;
                color: #666;
            }}
        }}
        
        /* Typography for PDF */
        body {{
            font-family: {pdf_config.get('font_family', 'Arial, sans-serif')};
            font-size: {pdf_config.get('font_size', '11pt')};
            line-height: 1.6;
            color: #333;
            background: white;
        }}
        
        /* Headings */
        h1, h2, h3, h4, h5, h6 {{
            page-break-after: avoid;
            font-weight: bold;
            margin-top: 1.5em;
            margin-bottom: 0.5em;
        }}
        
        h1 {{
            font-size: 18pt;
            page-break-before: always;
            text-align: center;
            border-bottom: 2px solid #333;
            padding-bottom: 0.5em;
        }}
        
        h1:first-of-type {{
            page-break-before: auto;
        }}
        
        h2 {{
            font-size: 14pt;
            border-bottom: 1px solid #666;
            padding-bottom: 0.25em;
        }}
        
        h3 {{
            font-size: 12pt;
        }}
        
        /* Paragraphs and text */
        p {{
            margin: 0.5em 0;
            text-align: justify;
            orphans: 2;
            widows: 2;
        }}
        
        /* Lists */
        ul, ol {{
            margin: 0.5em 0;
            padding-left: 1.5em;
        }}
        
        li {{
            margin: 0.25em 0;
            page-break-inside: avoid;
        }}
        
        /* Tables */
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 1em 0;
            page-break-inside: avoid;
            font-size: 10pt;
        }}
        
        th, td {{
            border: 1px solid #ddd;
            padding: 8px;
            text-align: left;
        }}
        
        th {{
            background-color: #f5f5f5;
            font-weight: bold;
        }}
        
        /* Metadata box */
        .metadata {{
            background: #f8f9fa;
            border: 1px solid #dee2e6;
            padding: 1em;
            margin: 1em 0;
            page-break-inside: avoid;
            border-radius: 0;
        }}
        
        /* Executive summary */
        .executive-summary {{
            background: #f8f9fa;
            border-left: 4px solid #007bff;
            padding: 1em;
            margin: 1em 0;
            page-break-inside: avoid;
        }}
        
        /* Sections */
        .section {{
            margin: 1em 0;
            page-break-inside: avoid;
        }}
        
        .subsection {{
            margin: 0.5em 0 0.5em 1em;
            border-left: 2px solid #e9ecef;
            padding-left: 1em;
        }}
        
        /* Citations */
        .citations {{
            background: #fdfdfd;
            border: 1px solid #e9ecef;
            padding: 1em;
            margin: 1em 0;
            page-break-inside: avoid;
        }}
        
        .citation {{
            margin: 0.5em 0;
            font-size: 10pt;
            line-height: 1.4;
        }}
        
        /* Images and figures */
        img {{
            max-width: 100%;
            height: auto;
            page-break-inside: avoid;
        }}
        
        .visualization {{
            margin: 1em 0;
            text-align: center;
            page-break-inside: avoid;
        }}
        
        .figure-caption {{
            font-size: 10pt;
            font-style: italic;
            text-align: center;
            margin-top: 0.5em;
        }}
        
        /* Table of contents */
        .toc {{
            background: #f8f9fa;
            border: 1px solid #dee2e6;
            padding: 1em;
            margin: 1em 0;
            page-break-inside: avoid;
        }}
        
        .toc ul {{
            list-style: none;
            padding: 0;
        }}
        
        .toc a {{
            text-decoration: none;
            color: #007bff;
        }}
        
        /* Footer */
        .report-footer {{
            margin-top: 2em;
            padding-top: 1em;
            border-top: 1px solid #e9ecef;
            font-size: 10pt;
            color: #666;
            text-align: center;
        }}
        
        /* Code blocks */
        pre, code {{
            font-family: 'Courier New', monospace;
            font-size: 9pt;
            background: #f8f9fa;
            padding: 0.25em;
            border-radius: 0;
        }}
        
        pre {{
            padding: 1em;
            border: 1px solid #e9ecef;
            page-break-inside: avoid;
        }}
        """
    
    def _get_print_css(self, report: Report) -> str:
        """Get print-specific CSS styles."""
        return """
        /* Print-specific styles */
        * {
            -webkit-print-color-adjust: exact !important;
            color-adjust: exact !important;
        }
        
        /* Page breaks */
        .page-break {
            page-break-before: always;
        }
        
        .page-break-avoid {
            page-break-inside: avoid;
        }
        
        .page-break-after {
            page-break-after: always;
        }
        
        /* Hide elements not needed in print */
        .no-print {
            display: none !important;
        }
        
        /* Ensure backgrounds print */
        .metadata,
        .executive-summary,
        .citations,
        .toc {
            -webkit-print-color-adjust: exact;
            color-adjust: exact;
        }
        
        /* Optimize spacing for print */
        body {
            margin: 0;
            padding: 0;
        }
        
        /* Ensure proper font rendering */
        * {
            text-rendering: optimizeLegibility;
        }
        """
    
    def _generate_pdf_bytes(
        self,
        html_doc: Any,
        css_objects: list[Any],
        report: Report
    ) -> bytes:
        """Generate PDF bytes from HTML document and CSS."""
        try:
            # Create a temporary file for PDF generation
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as temp_file:
                temp_path = temp_file.name
            
            try:
                # Generate PDF to temporary file
                html_doc.write_pdf(
                    temp_path,
                    stylesheets=css_objects,
                    font_config=self.font_config,
                    presentational_hints=True,
                    optimize_images=True,
                )
                
                # Read PDF bytes
                with open(temp_path, 'rb') as pdf_file:
                    pdf_bytes = pdf_file.read()
                
                return pdf_bytes
                
            finally:
                # Clean up temporary file
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                    
        except Exception as e:
            logger.error(f"PDF generation failed: {e}")
            raise PDFExportError(f"WeasyPrint failed: {e}")
    
    def export_to_pdf_stream(
        self,
        html_content: str,
        report: Report,
        custom_css: str | None = None
    ) -> io.BytesIO:
        """
        Export HTML content to PDF as a BytesIO stream.
        
        Args:
            html_content: HTML content to convert
            report: Report metadata for configuration
            custom_css: Optional custom CSS styles
            
        Returns:
            BytesIO stream containing PDF data
        """
        pdf_output = self.export_to_pdf(html_content, report, custom_css)
        
        if isinstance(pdf_output.content, bytes):
            return io.BytesIO(pdf_output.content)
        else:
            raise PDFExportError("PDF export did not return bytes")
    
    def validate_html_for_pdf(self, html_content: str) -> list[str]:
        """
        Validate HTML content for PDF generation and return warnings.
        
        Args:
            html_content: HTML content to validate
            
        Returns:
            List of validation warnings
        """
        warnings = []
        
        # Check for unsupported CSS features
        unsupported_features = [
            'position: fixed',
            'position: sticky',
            'transform:',
            'animation:',
            '@keyframes',
            'flex-wrap',
        ]
        
        for feature in unsupported_features:
            if feature in html_content:
                warnings.append(f"Unsupported CSS feature detected: {feature}")
        
        # Check for large images that might cause issues
        if 'data:image' in html_content:
            warnings.append("Base64 encoded images detected - may increase PDF size")
        
        # Check for JavaScript
        if '<script' in html_content:
            warnings.append("JavaScript detected - will be ignored in PDF")
        
        # Check for form elements
        form_elements = ['<input', '<textarea', '<select', '<button']
        for element in form_elements:
            if element in html_content:
                warnings.append(f"Form element detected: {element} - may not render properly")
        
        return warnings
    
    def get_pdf_info(self, pdf_bytes: bytes) -> dict[str, Any]:
        """
        Extract information from generated PDF.
        
        Args:
            pdf_bytes: PDF content as bytes
            
        Returns:
            Dictionary with PDF information
        """
        try:
            info = {
                'size_bytes': len(pdf_bytes),
                'size_kb': round(len(pdf_bytes) / 1024, 2),
                'size_mb': round(len(pdf_bytes) / (1024 * 1024), 2),
                'format': 'PDF',
                'version': 'PDF-1.4+',  # WeasyPrint typically generates PDF 1.4+
            }
            
            # Try to extract more detailed information if possible
            # This would require additional PDF libraries like PyPDF2
            
            return info
            
        except Exception as e:
            logger.warning(f"Could not extract PDF info: {e}")
            return {
                'size_bytes': len(pdf_bytes),
                'format': 'PDF',
                'error': str(e)
            }


def create_pdf_exporter(settings: ReportSettings | None = None) -> PDFExporter:
    """Factory function to create a PDF exporter."""
    return PDFExporter(settings)


# Utility functions for PDF generation
def optimize_html_for_pdf(html_content: str) -> str:
    """
    Optimize HTML content for better PDF generation.
    
    This function applies transformations to make HTML more PDF-friendly.
    """
    # Remove or replace problematic CSS
    optimizations = {
        'position: fixed': 'position: static',
        'position: sticky': 'position: static',
        'overflow: hidden': 'overflow: visible',
        'overflow-x: auto': 'overflow: visible',
        'overflow-y: auto': 'overflow: visible',
    }
    
    optimized_html = html_content
    for old, new in optimizations.items():
        optimized_html = optimized_html.replace(old, new)
    
    return optimized_html


def add_pdf_page_breaks(html_content: str, break_elements: list[str] | None = None) -> str:
    """
    Add strategic page breaks to HTML content for better PDF layout.
    
    Args:
        html_content: Original HTML content
        break_elements: List of CSS selectors where page breaks should be added
        
    Returns:
        HTML with page break classes added
    """
    if break_elements is None:
        break_elements = ['h1', 'h2.major-section', '.new-page']
    
    # This is a simplified implementation
    # In practice, you might use BeautifulSoup for more sophisticated HTML manipulation
    
    modified_html = html_content
    
    # Add page break before major headings
    modified_html = modified_html.replace('<h1>', '<h1 class="page-break">')
    
    return modified_html


__all__ = [
    "PDFExportError",
    "PDFExporter",
    "add_pdf_page_breaks",
    "create_pdf_exporter",
    "optimize_html_for_pdf",
]