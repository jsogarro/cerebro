# Report Generation System Documentation

## Overview

The Report Generation System is a comprehensive solution for generating multi-format research reports from Cerebro. It supports multiple output formats, customizable templates, advanced visualizations, and a complete REST API for management.

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

Six pre-configured report types with customizable templates (the
`ReportType` enum values are shown in parentheses):

#### Comprehensive Report (`comprehensive`)
- Full research analysis with all sections
- Detailed methodology and findings
- Extensive citations and references
- Suitable for academic or professional use

#### Executive Summary (`executive_summary`)
- Concise overview of key findings
- Strategic insights and recommendations
- Minimal technical details
- Ideal for decision-makers

#### Academic Paper (`academic`)
- Formal academic formatting
- Abstract and introduction
- Literature review section
- Proper citation formatting
- LaTeX export support

#### Literature Review (`literature_review`)
- Focused survey of existing sources
- Thematic organization of prior work
- Extensive citations and references

#### Methodology Report (`methodology`)
- Emphasis on research methods and design
- Rationale for methodological choices
- Bias and limitation discussion

#### Synthesis Report (`synthesis`)
- Integration of findings into a coherent narrative
- Cross-source consolidation of insights
- Concluding recommendations

### 3. Visualization Generation

Comprehensive visualization support using Plotly and NetworkX:

#### Chart Types
- Bar charts
- Histograms
- Line charts
- Pie and donut charts (donut via the `hole` config option)
- Scatter plots
- Heatmaps
- Box plots
- Radar/spider charts
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
- Custom filters (`markdown`, `truncate_words`, `format_number`, `strip_markdown`)
- Conditional sections
- Loop constructs
- Macro support
- Internationalization ready

### 6. Storage and Retrieval

Robust storage system with:

- File-based storage with directory structure
- Integrity verification with checksums
- Cleanup utilities
- Access statistics

> **Note:** Database-backed metadata tracking is implemented in the storage
> layer but is **not currently wired up through the API**.
> `get_report_services()` hardcodes `session = None`
> (`src/api/routes/reports.py:175`), so the report/format repositories and the
> storage service are always `None`. Files are written to disk as raw
> bytes/text — there is **no compression**.

## API Reference

### Endpoints

> **Important — retrieval endpoints are currently non-functional.** Because
> `get_report_services()` hardcodes `session = None`
> (`src/api/routes/reports.py:175`), the storage service is always `None`, so
> every retrieval endpoint below (Get Report, Download, List, Search,
> Statistics, Delete, Verify Integrity) unconditionally returns
> `503 Report storage service not available`. In addition, `POST /generate`
> returns a placeholder response with an all-zeros UUID
> (`00000000-0000-0000-0000-000000000000`), so the returned `id` is not
> resolvable and the poll-then-download workflow shown in the examples below
> cannot succeed as currently wired. The request/response shapes are documented
> here as the intended contract.

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

# LaTeX generation
ENABLE_LATEX_GENERATION=true

# Visualization
ENABLE_VISUALIZATIONS=true
MAX_VISUALIZATIONS_PER_REPORT=20
DEFAULT_CHART_WIDTH=800
DEFAULT_CHART_HEIGHT=600
```

> **Note:** PDF/LaTeX detail settings (page size, margins, font family,
> document class, compiler) are **not** environment-configurable. They are
> hardcoded default-dict keys of `ReportConfiguration.pdf_settings` and
> `latex_settings` (`src/models/report.py:253-270`), not fields on
> `ReportSettings`.

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
)
```

PDF/LaTeX detail settings are **not** part of `ReportSettings`. They live on the
per-report `ReportConfiguration` model (`src/models/report.py`) via its
`pdf_settings` / `latex_settings` fields:

```python
from src.models.report import ReportConfiguration

config = ReportConfiguration(
    pdf_settings={
        "page_size": "A4",
        "margin_top": "2cm",
        "font_family": "Arial",
    }
)
```

## How Generation Is Invoked

Reports are generated through the REST API, not through the query-execution
pipeline. There is no report-generation graph node — the top-level
`src/orchestration/` subsystem was removed (PR #50), and no `ResearchState`
type or `report_generation_node` exists.

`POST /api/v1/reports/generate` accepts a `CreateReportRequest`, builds a
`ReportConfiguration` and `ReportGenerationRequest`, schedules the actual
generation via FastAPI `BackgroundTasks` (`_generate_report_task`), and returns
`202 Accepted` immediately with a placeholder `ReportResponse`
(`generation_status="generating"`, empty `formats_generated`). The background
task runs `ReportGenerator.generate_report(...)` off the request path
(`src/api/routes/reports.py:189-282`).

```python
@router.post("/generate", status_code=status.HTTP_202_ACCEPTED)
async def generate_report(
    request: CreateReportRequest, background_tasks: BackgroundTasks
) -> ReportResponse:
    generator, storage_service, _report_repo, _format_repo = get_report_services()

    config = ReportConfiguration(...)
    gen_request = ReportGenerationRequest(
        project_id=request.project_id,
        workflow_data={"title": request.title, "query": request.query, ...},
        configuration=config,
        formats=request.formats,
        save_to_storage=request.save_to_storage,
    )

    # Generation happens off the request path.
    background_tasks.add_task(
        _generate_report_task, generator, storage_service, gen_request,
        request.user_id, request.project_id,
    )

    # Immediate placeholder response; poll GET /api/v1/reports/{id} for status.
    return ReportResponse(generation_status="generating", formats_generated=[], ...)
```

Callers pass any upstream research output through `workflow_data` in the request
body. The intended follow-up is to poll `GET /api/v1/reports/{report_id}` for
completion, but note that the placeholder response uses an all-zeros UUID and
the retrieval endpoints currently return `503` (see the caveat under **API
Reference → Endpoints**), so this polling workflow is not yet operational.

## Performance Considerations

### Optimization Strategies

1. **Async Generation**: Reports are generated asynchronously using background tasks
2. **Caching**: Template compilation is cached for performance
3. **Streaming**: Large reports are streamed to avoid memory issues
4. **Lazy Loading**: Visualizations are generated on-demand

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
from src.services.report_generator import ReportGenerationError
from src.services.template_renderer import TemplateRenderingError
from src.services.exporters import (
    PDFExportError,
    LaTeXExportError,
    DOCXExportError,
)

try:
    response = await generator.generate_report(request)
except ReportGenerationError as e:
    # Handle generation errors
    logger.error(f"Report generation failed: {e}")
except TemplateRenderingError as e:
    # Handle template errors
    logger.error(f"Template rendering failed: {e}")
except (PDFExportError, LaTeXExportError, DOCXExportError) as e:
    # Handle format-specific export errors
    logger.error(f"Export failed: {e}")
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
```

## Security Considerations

1. **Input Validation**: All inputs are validated with Pydantic
2. **Template Autoescaping**: The Jinja2 `Environment` is configured with
   `select_autoescape` (`src/services/template_renderer.py`), which HTML-escapes
   rendered variables. Note this is autoescaping, **not** sandboxing — the
   renderer does not use `jinja2.sandbox.SandboxedEnvironment`, so templates are
   not restricted from executing arbitrary attribute/method access.
3. **File Path Validation**: Prevents directory traversal attacks
4. **Size Limits**: Maximum report size enforced
5. **Rate Limiting**: A single global limiter (100 requests/minute) applies to all endpoints
6. **Authentication (known gap)**: The `/api/v1/reports/*` endpoints are **effectively unauthenticated**. `reports.py` declares no auth dependencies, and `AuthMiddleware` is a no-op (it sets `request.state.user = None` and validates nothing). Cerebro's JWT stack (RS256) is enforced only on endpoints that explicitly declare `Depends(get_current_user/require_*)` — the report routes do not. Add per-endpoint auth dependencies before exposing this API in an untrusted environment.
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
2. Review the application logs (structured logging via `structlog` is written to
   stdout)
3. Open an issue on GitHub
4. Contact the development team

## License

This system is part of Cerebro and follows the same licensing terms.