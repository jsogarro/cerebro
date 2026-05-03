"""
Template rendering service for report generation.

This service handles Jinja2 template rendering with custom filters and functions,
following functional programming principles.
"""

import logging
import os
import re
from typing import Any, cast

import jinja2
from jinja2 import Environment, FileSystemLoader, select_autoescape

try:
    import markdown as markdown_module  # type: ignore[import-untyped]

    MARKDOWN_AVAILABLE = True
except ImportError:
    markdown_module = None
    MARKDOWN_AVAILABLE = False

from src.models.report import Report, ReportType
from src.services.report_config import ReportSettings, ReportTemplateConfig

logger = logging.getLogger(__name__)

ALLOWED_REPORT_HTML_TAGS = {
    "a",
    "blockquote",
    "br",
    "code",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "ol",
    "p",
    "pre",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}


class TemplateRenderingError(Exception):
    """Exception raised during template rendering."""
    pass


class TemplateRenderer:
    """Service for rendering reports using Jinja2 templates."""
    
    def __init__(
        self,
        settings: ReportSettings | None = None,
        template_config: ReportTemplateConfig | None = None,
    ):
        """Initialize the template renderer."""
        self.settings = settings or ReportSettings()
        self.template_config = template_config or ReportTemplateConfig(self.settings)
        
        # Initialize Jinja2 environment
        self.env = self._create_jinja_environment()
        
        # Template cache
        self._template_cache: dict[str, jinja2.Template] = {}
    
    def _create_jinja_environment(self) -> Environment:
        """Create and configure Jinja2 environment with custom filters."""
        # Ensure template directory exists
        template_dir = self.settings.template_path
        if not os.path.exists(template_dir):
            os.makedirs(template_dir, exist_ok=True)
            logger.warning(f"Created template directory: {template_dir}")
        
        # Create environment
        env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(['html', 'xml']),
            trim_blocks=True,
            lstrip_blocks=True,
            enable_async=False,  # For simplicity, using sync templates
        )
        
        # Add custom filters
        env.filters.update(self._get_custom_filters())
        
        # Add custom functions
        env.globals.update(self._get_custom_functions())
        
        return env
    
    def _get_custom_filters(self) -> dict[str, Any]:
        """Get custom Jinja2 filters for report rendering."""
        return {
            'markdown': self._markdown_filter,
            'truncate_words': self._truncate_words_filter,
            'format_number': self._format_number_filter,
            'format_percentage': self._format_percentage_filter,
            'strip_markdown': self._strip_markdown_filter,
            'format_citation_count': self._format_citation_count_filter,
            'capitalize_words': self._capitalize_words_filter,
            'extract_first_sentence': self._extract_first_sentence_filter,
            'format_duration': self._format_duration_filter,
        }
    
    def _get_custom_functions(self) -> dict[str, Any]:
        """Get custom Jinja2 global functions."""
        return {
            'get_section_number': self._get_section_number,
            'generate_toc_id': self._generate_toc_id,
            'count_words': self._count_words,
            'get_confidence_level': self._get_confidence_level,
            'format_date': self._format_date,
        }
    
    def render_report(
        self,
        report: Report,
        template_name: str | None = None
    ) -> str:
        """
        Render a report using the appropriate template.
        
        Args:
            report: Report object to render
            template_name: Optional override for template name
            
        Returns:
            Rendered HTML content
        """
        try:
            # Determine template to use
            if template_name:
                template_file = template_name
            else:
                template_file = self._get_template_for_report_type(report.configuration.type)
            
            # Get or load template
            template = self._get_template(template_file)
            
            # Prepare template context
            context = self._build_template_context(report)
            
            # Render template
            rendered = template.render(**context)
            
            logger.info(f"Successfully rendered report using template: {template_file}")
            return str(rendered)
            
        except Exception as e:
            logger.error(f"Template rendering failed: {e}")
            raise TemplateRenderingError(f"Failed to render template: {e}") from e
    
    def _get_template_for_report_type(self, report_type: ReportType) -> str:
        """Get the appropriate template file for a report type."""
        template_mapping = {
            ReportType.COMPREHENSIVE: "comprehensive_report.html.j2",
            ReportType.EXECUTIVE_SUMMARY: "executive_summary.html.j2",
            ReportType.ACADEMIC_PAPER: "academic_paper.html.j2",
            ReportType.LITERATURE_REVIEW: "literature_review.html.j2",
            ReportType.METHODOLOGY_REPORT: "methodology_report.html.j2",
            ReportType.SYNTHESIS_REPORT: "synthesis_report.html.j2",
        }
        
        template_file = template_mapping.get(report_type, "comprehensive_report.html.j2")
        
        # Check if template exists, fall back to base template
        template_path = os.path.join(self.settings.template_path, template_file)
        if not os.path.exists(template_path):
            logger.warning(f"Template {template_file} not found, using base.html.j2")
            return "base.html.j2"
        
        return template_file
    
    def _get_template(self, template_name: str) -> jinja2.Template:
        """Get template from cache or load it."""
        if self.settings.enable_template_cache and template_name in self._template_cache:
            return self._template_cache[template_name]
        
        try:
            template = self.env.get_template(template_name)
            
            if self.settings.enable_template_cache:
                self._template_cache[template_name] = template
            
            return template
            
        except jinja2.TemplateNotFound:
            logger.error(f"Template not found: {template_name}")
            # Try to fall back to base template
            try:
                template = self.env.get_template("base.html.j2")
                logger.info("Using base template as fallback")
                return template
            except jinja2.TemplateNotFound:
                raise TemplateRenderingError("No templates found (including base.html.j2)") from None
    
    def _build_template_context(self, report: Report) -> dict[str, Any]:
        """Build the context dictionary for template rendering."""
        return {
            'report': report,
            'config': report.configuration,
            'metadata': report.metadata,
            'settings': self.settings,
            # Utility functions available in templates
            'enumerate': enumerate,
            'len': len,
            'sum': sum,
            'max': max,
            'min': min,
            'sorted': sorted,
            'zip': zip,
        }
    
    # Custom Jinja2 filters
    def _markdown_filter(self, text: str) -> str:
        """Convert markdown text to HTML."""
        if not text:
            return ""
        
        try:
            if not MARKDOWN_AVAILABLE:
                return self._basic_markdown_filter(text)

            # Configure markdown extensions
            markdown_api = cast(Any, markdown_module)
            md = markdown_api.Markdown(
                extensions=[
                    'markdown.extensions.tables',
                    'markdown.extensions.fenced_code',
                    'markdown.extensions.codehilite',
                    'markdown.extensions.toc',
                ],
                extension_configs={
                    'codehilite': {
                        'css_class': 'highlight',
                        'use_pygments': False,
                    }
                }
            )
            result = md.convert(text)
            return self._sanitize_rendered_html(str(result))
            
        except Exception as e:
            logger.warning(f"Markdown conversion failed: {e}")
            # Return plain text with line breaks converted
            return self._sanitize_rendered_html(text.replace('\n', '<br>'))

    def _basic_markdown_filter(self, text: str) -> str:
        """Render a small markdown subset when the optional package is unavailable."""
        rendered = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        rendered = re.sub(r"\*(.+?)\*", r"<em>\1</em>", rendered)
        return self._sanitize_rendered_html(rendered.replace("\n", "<br>"))

    def _sanitize_rendered_html(self, html: str) -> str:
        """Remove unsafe HTML before templates mark rendered markdown as safe."""

        sanitized = re.sub(
            r"<(script|style)[^>]*>.*?</\1>",
            "",
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )

        def replace_tag(match: re.Match[str]) -> str:
            closing_slash = match.group(1)
            tag_name = match.group(2).lower()
            trailing_slash = match.group(3)
            if tag_name not in ALLOWED_REPORT_HTML_TAGS:
                return ""
            if closing_slash:
                return f"</{tag_name}>"
            return f"<{tag_name}{trailing_slash}>"

        return re.sub(
            r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9]*)\b[^>]*(/?)\s*>",
            replace_tag,
            sanitized,
        )
    
    def _truncate_words_filter(self, text: str, length: int = 50, suffix: str = "...") -> str:
        """Truncate text to specified number of words."""
        if not text:
            return ""
        
        words = text.split()
        if len(words) <= length:
            return text
        
        return ' '.join(words[:length]) + suffix
    
    def _format_number_filter(self, value: Any) -> str:
        """Format number with thousands separators."""
        try:
            if isinstance(value, (int, float)):
                return f"{value:,}"
            return str(value)
        except (ValueError, TypeError):
            return str(value)
    
    def _format_percentage_filter(self, value: Any, decimals: int = 1) -> str:
        """Format value as percentage."""
        try:
            if isinstance(value, (int, float)):
                return f"{value * 100:.{decimals}f}%"
            return str(value)
        except (ValueError, TypeError):
            return str(value)
    
    def _strip_markdown_filter(self, text: str) -> str:
        """Remove markdown formatting from text."""
        if not text:
            return ""
        
        # Remove markdown headers
        text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
        
        # Remove markdown emphasis
        text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)  # Bold
        text = re.sub(r'\*(.*?)\*', r'\1', text)      # Italic
        text = re.sub(r'`(.*?)`', r'\1', text)        # Code
        
        # Remove markdown links
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
        
        # Remove markdown lists
        text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
        
        return text.strip()
    
    def _format_citation_count_filter(self, count: int) -> str:
        """Format citation count with appropriate pluralization."""
        if count == 0:
            return "no citations"
        elif count == 1:
            return "1 citation"
        else:
            return f"{count:,} citations"
    
    def _capitalize_words_filter(self, text: str) -> str:
        """Capitalize each word in text."""
        if not text:
            return ""
        return text.title()
    
    def _extract_first_sentence_filter(self, text: str) -> str:
        """Extract the first sentence from text."""
        if not text:
            return ""
        
        # Find first sentence ending
        match = re.search(r'^[^.!?]*[.!?]', text.strip())
        if match:
            return match.group(0).strip()
        
        # If no sentence ending found, return first 100 characters
        return text[:100] + "..." if len(text) > 100 else text
    
    def _format_duration_filter(self, seconds: float) -> str:
        """Format duration in seconds to human-readable format."""
        if seconds < 60:
            return f"{seconds:.1f} seconds"
        elif seconds < 3600:
            minutes = seconds / 60
            return f"{minutes:.1f} minutes"
        else:
            hours = seconds / 3600
            return f"{hours:.1f} hours"
    
    # Custom Jinja2 global functions
    def _get_section_number(self, sections: list[Any], current_index: int) -> str:
        """Get section number for table of contents."""
        return str(current_index + 1)
    
    def _generate_toc_id(self, title: str) -> str:
        """Generate a clean ID for table of contents linking."""
        # Convert to lowercase and replace spaces/special chars with hyphens
        clean_id = re.sub(r'[^\w\s-]', '', title.lower())
        clean_id = re.sub(r'[-\s]+', '-', clean_id)
        return clean_id.strip('-')
    
    def _count_words(self, text: str) -> int:
        """Count words in text."""
        if not text:
            return 0
        # Remove markdown and count words
        clean_text = self._strip_markdown_filter(text)
        return len(clean_text.split())
    
    def _get_confidence_level(self, score: float) -> str:
        """Get confidence level description from score."""
        if score >= 0.9:
            return "Very High"
        elif score >= 0.8:
            return "High"
        elif score >= 0.7:
            return "Good"
        elif score >= 0.6:
            return "Moderate"
        elif score >= 0.5:
            return "Fair"
        else:
            return "Low"
    
    def _format_date(self, date: Any, format_string: str = "%B %d, %Y") -> str:
        """Format datetime object."""
        try:
            result = date.strftime(format_string)
            return str(result)
        except (AttributeError, ValueError):
            return str(date)
    
    def clear_cache(self) -> None:
        """Clear the template cache."""
        self._template_cache.clear()
        logger.info("Template cache cleared")
    
    def validate_template(self, template_name: str) -> bool:
        """Validate that a template exists and can be loaded."""
        try:
            self.env.get_template(template_name)
            return True
        except jinja2.TemplateNotFound:
            return False
        except Exception as e:
            logger.error(f"Template validation error: {e}")
            return False
    
    def list_available_templates(self) -> list[str]:
        """List all available template files."""
        template_dir = self.settings.template_path
        if not os.path.exists(template_dir):
            return []
        
        templates = []
        for file in os.listdir(template_dir):
            if file.endswith('.j2') or file.endswith('.html'):
                templates.append(file)
        
        return sorted(templates)


def create_template_renderer(
    settings: ReportSettings | None = None,
    template_config: ReportTemplateConfig | None = None,
) -> TemplateRenderer:
    """Factory function to create a template renderer."""
    return TemplateRenderer(settings, template_config)


__all__ = [
    "TemplateRenderer",
    "TemplateRenderingError",
    "create_template_renderer",
]
