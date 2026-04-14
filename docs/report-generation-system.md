# Report Generation System Documentation

## Overview

The Report Generation System is a comprehensive, production-ready solution for generating multi-format research reports from the Multi-Agent Research Platform. It supports multiple output formats, customizable templates, advanced visualizations, and a complete REST API for management.

## Architecture

### Core Components

```
src/
├── models/
│   └── report.py                    # Core data models
├── services/
│   ├── report_config.py            # Configuration management
│   ├── report_generator.py         # Main generation service
│   ├── template_renderer.py        # Jinja2 rendering
│   ├── visualization_generator.py  # Chart/graph generation
│   ├── report_storage.py          # Storage service
│   └── exporters/
│       ├── pdf_exporter.py        # PDF generation
│       ├── latex_exporter.py      # LaTeX generation
│       └── docx_exporter.py       # DOCX generation
├── templates/reports/
│   ├── base.html.j2               # Base template
│   ├── comprehensive_report.html.j2
│   ├── executive_summary.html.j2
│   └── academic_paper.html.j2
├── models/db/
│   └── generated_report.py        # Database models
├── repositories/
│   └── report_repository.py       # Data access layer
└── api/routes/
    └── reports.py                 # REST API endpoints
```

## Features

### 1. Multi-Format Report Generation

The system supports generating reports in multiple formats:

- **HTML**: Interactive web-based reports with styling
- **PDF**: Professional documents via WeasyPrint
- **LaTeX**: Academic papers with BibTeX support
- **DOCX**: Microsoft Word documents
- **Markdown**: Plain text with formatting
- **JSON**: Structured data for programmatic access

### 2. Report Types

Four pre-configured report types with customizable templates:

#### Comprehensive Report
- Full research analysis with all sections
- Detailed methodology and findings
- Extensive citations and references
- Suitable for academic or professional use

#### Executive Summary
- Concise overview of key findings
- Strategic insights and recommendations
- Minimal technical details
- Ideal for decision-makers

#### Academic Paper
- Formal academic formatting
- Abstract and introduction
- Literature review section
- Proper citation formatting
- LaTeX export support

#### Technical Analysis
- Detailed technical findings
- Code examples and algorithms
- Performance metrics
- Implementation recommendations

### 3. Visualization Generation

Comprehensive visualization support using Plotly and NetworkX:

#### Chart Types
- Bar charts and histograms
- Line and area charts
- Pie and donut charts
- Scatter plots and bubble charts
- Heatmaps and contour plots
- Box plots and violin plots
- Radar/spider charts
- Sankey diagrams
- Network graphs
- Word clouds

#### Features
- Interactive HTML visualizations
- Static image export (PNG, SVG)
- Customizable color schemes
- Responsive design
- Data-driven configurations

### 4. Citation Management

Professional citation formatting with multiple styles:

- **APA** (American Psychological Association)
- **MLA** (Modern Language Association)
- **Chicago** (Chicago Manual of Style)
- **IEEE** (Institute of Electrical and Electronics Engineers)
- **Harvard** referencing

Features:
- Automatic formatting from structured data
- In-text citations
- Bibliography generation
- DOI and URL support
- Multiple author handling

### 5. Template System

Jinja2-based template system with:

- Template inheritance
- Custom filters (markdown, truncate, format_number)
- Conditional sections
- Loop constructs
- Macro support
- Internationalization ready

### 6. Storage and Retrieval

Robust storage system with:

- File-based storage with directory structure
- Database tracking for metadata
- Integrity verification with checksums
- Compression support
- Cleanup utilities
- Access statistics

## API Reference

### Endpoints

#### Generate Report
```http
POST /api/v1/reports/generate
```

Request body:
```json
{
  "title": "Research Report Title",
  "query": "Research question",
  "domains": ["AI", "Education"],
  "report_type": "comprehensive",
  "citation_style": "APA",
  "formats": ["html", "pdf", "markdown"],
  "include_toc": true,
  "include_visualizations": true,
  "workflow_data": {
    "aggregated_results": {...}
  }
}
```

Response:
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "title": "Research Report Title",
  "generation_status": "generating",
  "formats_generated": [],
  "created_at": "2024-01-01T00:00:00Z"
}
```

#### Get Report
```http
GET /api/v1/reports/{report_id}
```

Response:
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "title": "Research Report Title",
  "generation_status": "completed",
  "formats_generated": ["html", "pdf", "markdown"],
  "word_count": 5000,
  "page_count": 15,
  "quality_score": 0.85,
  "download_urls": {
    "html": "/reports/{id}/download/html",
    "pdf": "/reports/{id}/download/pdf",
    "markdown": "/reports/{id}/download/markdown"
  }
}
```

#### Download Report
```http
GET /api/v1/reports/{report_id}/download/{format}
```

Returns the report file in the specified format.

#### List Reports
```http
GET /api/v1/reports?user_id={user_id}&page=1&page_size=20
```

#### Search Reports
```http
POST /api/v1/reports/search
```

Request body:
```json
{
  "search_term": "artificial intelligence",
  "user_id": "user-uuid",
  "report_type": "comprehensive",
  "min_quality_score": 0.7,
  "limit": 20,
  "offset": 0
}
```

#### Get Statistics
```http
GET /api/v1/reports/statistics?days=30
```

#### Delete Report
```http
DELETE /api/v1/reports/{report_id}?delete_files=true
```

#### Verify Integrity
```http
GET /api/v1/reports/{report_id}/integrity
```

## Usage Examples

### Python Client Example

```python
import httpx
import asyncio

async def generate_report():
    async with httpx.AsyncClient() as client:
        # Generate report
        response = await client.post(
            "http://localhost:8000/api/v1/reports/generate",
            json={
                "title": "AI Impact on Education",
                "query": "How does AI affect modern education?",
                "domains": ["AI", "Education"],
                "report_type": "comprehensive",
                "formats": ["html", "pdf"],
                "workflow_data": {
                    "aggregated_results": {
                        "sources": [...],
                        "findings": {...},
                        "citations": [...]
                    }
                }
            }
        )
        report = response.json()
        report_id = report["id"]
        
        # Poll for completion
        while True:
            response = await client.get(
                f"http://localhost:8000/api/v1/reports/{report_id}"
            )
            status = response.json()
            if status["generation_status"] == "completed":
                break
            await asyncio.sleep(5)
        
        # Download PDF
        response = await client.get(
            f"http://localhost:8000/api/v1/reports/{report_id}/download/pdf"
        )
        with open("report.pdf", "wb") as f:
            f.write(response.content)

asyncio.run(generate_report())
```

### CLI Usage

```bash
# Generate a report
curl -X POST http://localhost:8000/api/v1/reports/generate \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Research Report",
    "query": "Climate change impacts",
    "formats": ["html", "pdf"]
  }'

# Check status
curl http://localhost:8000/api/v1/reports/{report_id}

# Download report
curl -o report.pdf \
  http://localhost:8000/api/v1/reports/{report_id}/download/pdf
```

## Configuration

### Environment Variables

```bash
# Report generation settings
REPORT_STORAGE_PATH=/var/reports
REPORT_TEMPLATE_PATH=/app/templates/reports
MAX_REPORT_SIZE_MB=50
DEFAULT_REPORT_FORMAT=html
DEFAULT_CITATION_STYLE=APA

# PDF generation
ENABLE_PDF_GENERATION=true
PDF_PAGE_SIZE=A4
PDF_MARGIN_TOP=2cm
PDF_MARGIN_BOTTOM=2cm
PDF_FONT_FAMILY=Arial

# LaTeX generation
ENABLE_LATEX_GENERATION=true
LATEX_COMPILER=pdflatex
LATEX_DOCUMENT_CLASS=article

# Visualization
ENABLE_VISUALIZATIONS=true
MAX_VISUALIZATIONS_PER_REPORT=20
DEFAULT_CHART_WIDTH=800
DEFAULT_CHART_HEIGHT=600
```

### Python Configuration

```python
from src.services.report_config import ReportSettings

settings = ReportSettings(
    report_storage_path="/var/reports",
    enable_pdf_generation=True,
    enable_latex_generation=True,
    enable_visualizations=True,
    max_report_size_mb=50,
    default_format=ReportFormat.HTML,
    default_citation_style=CitationStyle.APA,
    pdf_settings={
        "page_size": "A4",
        "margin_top": "2cm",
        "font_family": "Arial"
    }
)
```

## Integration with LangGraph

The Report Generation System is fully integrated with the LangGraph orchestration workflow:

```python
# In report_generation_node.py
async def report_generation_node(state: ResearchState) -> ResearchState:
    """Generate the final research report."""
    
    # Build configuration from state
    config = _build_report_configuration(state)
    
    # Prepare workflow data
    workflow_data = _prepare_workflow_data(state)
    
    # Create generation request
    request = ReportGenerationRequest(
        workflow_data=workflow_data,
        configuration=config,
        formats=[ReportFormat.HTML, ReportFormat.PDF],
        save_to_storage=True
    )
    
    # Generate report
    generator = ReportGenerator(settings)
    response = await generator.generate_report(request)
    
    # Store results in state
    state.context["final_report_response"] = {
        "report_id": response.report_id,
        "status": response.status,
        "formats_generated": response.formats_generated,
        "download_urls": response.download_urls
    }
    
    return state
```

## Performance Considerations

### Optimization Strategies

1. **Async Generation**: Reports are generated asynchronously using background tasks
2. **Caching**: Template compilation is cached for performance
3. **Streaming**: Large reports are streamed to avoid memory issues
4. **Compression**: Files are compressed for storage efficiency
5. **Lazy Loading**: Visualizations are generated on-demand

### Benchmarks

- HTML generation: ~100ms for 10-page report
- PDF generation: ~2-3 seconds for 10-page report
- LaTeX compilation: ~5-10 seconds depending on complexity
- Visualization generation: ~200ms per chart
- Storage write: ~50ms
- Database tracking: ~10ms

## Error Handling

The system implements comprehensive error handling:

```python
try:
    response = await generator.generate_report(request)
except ReportGenerationError as e:
    # Handle generation errors
    logger.error(f"Report generation failed: {e}")
except TemplateError as e:
    # Handle template errors
    logger.error(f"Template rendering failed: {e}")
except ExportError as e:
    # Handle export errors
    logger.error(f"Export failed: {e}")
except StorageError as e:
    # Handle storage errors
    logger.error(f"Storage failed: {e}")
```

### Fallback Mechanisms

- If PDF generation fails, HTML is still generated
- If visualization fails, report continues without charts
- If LaTeX compilation fails, raw .tex file is provided
- If storage fails, report is returned in-memory

## Testing

Comprehensive test coverage includes:

```bash
# Run all report tests
pytest tests/test_report_generation.py -v
pytest tests/test_visualization.py -v
pytest tests/test_api_reports.py -v

# Run with coverage
pytest --cov=src.services.report_generator \
       --cov=src.services.visualization_generator \
       --cov=src.api.routes.reports

# Integration tests
pytest tests/test_report_integration.py -v
```

## Security Considerations

1. **Input Validation**: All inputs are validated with Pydantic
2. **Template Sandboxing**: Jinja2 templates are sandboxed
3. **File Path Validation**: Prevents directory traversal attacks
4. **Size Limits**: Maximum report size enforced
5. **Rate Limiting**: API endpoints are rate-limited
6. **Authentication**: JWT authentication for API access
7. **Sanitization**: HTML content is sanitized

## Monitoring

Key metrics to monitor:

- Report generation time
- Format conversion success rate
- Storage usage
- API response times
- Error rates by type
- Visualization generation performance

Example Prometheus metrics:
```python
report_generation_duration = Histogram(
    'report_generation_duration_seconds',
    'Time to generate report',
    ['report_type', 'format']
)

report_generation_errors = Counter(
    'report_generation_errors_total',
    'Total report generation errors',
    ['error_type']
)
```

## Troubleshooting

### Common Issues

1. **PDF Generation Fails**
   - Check WeasyPrint installation
   - Verify system fonts are available
   - Check CSS compatibility

2. **LaTeX Compilation Errors**
   - Ensure pdflatex is installed
   - Check for missing LaTeX packages
   - Verify BibTeX file format

3. **Visualization Not Rendering**
   - Verify Plotly/NetworkX installation
   - Check data format compatibility
   - Ensure sufficient memory

4. **Storage Issues**
   - Check disk space
   - Verify write permissions
   - Check storage path configuration

5. **Template Errors**
   - Validate template syntax
   - Check variable availability
   - Review filter usage

## Future Enhancements

Planned improvements:

1. **Real-time Collaboration**: Multiple users editing reports
2. **Version Control**: Track report revisions
3. **Custom Templates**: User-uploadable templates
4. **Advanced Analytics**: Report usage analytics
5. **Export Plugins**: Extensible export format system
6. **Internationalization**: Multi-language support
7. **Accessibility**: WCAG compliance for HTML reports
8. **Performance**: CDN integration for assets

## Support

For issues or questions:

1. Check the [API Documentation](http://localhost:8000/docs)
2. Review error logs in `/var/log/reports/`
3. Open an issue on GitHub
4. Contact the development team

## License

This system is part of the Multi-Agent Research Platform and follows the same licensing terms.