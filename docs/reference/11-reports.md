# Reports

The reports subsystem renders structured research reports into HTML, Markdown, JSON, PDF, LaTeX and DOCX, stores them per tenant and serves them through `/api/v1/reports`. A request creates a database row, queues a background task and returns immediately; the task builds the report and stores its formats.

## Summary

| Feature | Status | Entry point | Source |
|---|---|---|---|
| Report routes (create, list, search, statistics, get, download, integrity, delete) | Active | `/api/v1/reports` | `src/api/routes/reports.py` (`router`) |
| Generation from caller-supplied `workflow_data` | Active | `POST /api/v1/reports/generate` without `project_id` | `src/services/report_generator.py` (`ReportGenerator._extract_input_data`) |
| Generation from a project | Active | `POST /api/v1/reports/generate` with `project_id` | `src/services/report_source_projection.py` (`ReportSourceRepository`) |
| HTML, Markdown and JSON output | Active | `ReportOutputGenerator.generate_formats` | `src/services/report_output_generator.py` (`ReportOutputGenerator`) |
| PDF, LaTeX and DOCX export | Active | `ReportOutputGenerator.generate_formats` | `src/services/exporters/` (`PDFExporter`, `LaTeXExporter`, `DOCXExporter`) |
| Report storage and integrity check | Active | `ReportStorageService` | `src/services/report_storage.py` (`ReportStorageService`) |
| Chart image generation | Active, needs `kaleido` | `ReportOutputGenerator.generate_html` | `src/services/visualization_generator.py` (`VisualizationGenerator`) |
| Completion notification | Not wired | `notify_completion` request field | `src/api/routes/reports.py` (`CreateReportRequest`) |

## Routes

The router is mounted at `/api/v1` with prefix `/reports` in `src/api/main.py`. Every route requires authentication: none is in `PUBLIC_ROUTE_ALLOWLIST`, and each takes `get_tenant_context`, which supplies the caller's user id and organization id. Both ids must parse as UUIDs, otherwise the route answers 403. Every repository query filters on the organization and on the caller's own user id, and ignores soft-deleted rows, so a caller sees only reports they created. See [authentication and tenancy](04-authentication-and-tenancy.md).

| Method and path | Purpose | Success | Errors |
|---|---|---|---|
| `POST /api/v1/reports/generate` | queue a report | 202, a `ReportResponse` with `generation_status="pending"` | 403 for a `user_id` that differs from the token subject; 404 `Project <id> not found` for a project the caller does not own, another tenant's, deleted or missing; 409 `report_source_unavailable` for the caller's own project that is not completed or has no stored results; 500 |
| `GET /api/v1/reports` | list the caller's reports | 200, `ReportListResponse` | 403 for a foreign `user_id` |
| `POST /api/v1/reports/search` | search the caller's completed reports | 200, `ReportListResponse` | 403 for a foreign `user_id` |
| `GET /api/v1/reports/statistics` | counts, averages and storage use | 200, `ReportStatisticsResponse` | 403 for a foreign `user_id` |
| `GET /api/v1/reports/{report_id}` | report details | 200, `ReportResponse` | 404 |
| `GET /api/v1/reports/{report_id}/download/{format_type}` | stored file | 200, streamed bytes | 404 |
| `GET /api/v1/reports/{report_id}/integrity` | verify stored files and hashes | 200, a status dictionary | 500 |
| `DELETE /api/v1/reports/{report_id}` | delete a report | 204 | 404 |

Every handler wraps unexpected exceptions into a 500, so each route can also return 500.

Details:

- **Create.** The route validates the user and project, builds a `ReportConfiguration`, inserts the report row with `generation_status="pending"`, reloads it, queues `_generate_report_task` as a background task and returns the row. The response `id` is the id of that row. Every `ReportResponse` carries `id`, `title`, `query`, `report_type`, `generation_status`, `formats_generated`, `word_count`, `page_count`, `quality_score`, `confidence_score`, `created_at`, `generation_time_seconds` and `download_urls`.
- **List.** Query parameters are `user_id`, `status_filter`, `page` (default 1), `page_size` (default 20, at most 100) and `report_type`. Results are newest first, `total_count` comes from a count query over the same filter, and `has_more` is true when `offset + len(reports) < total_count`. The `report_type` parameter is declared but not applied to the query.
- **Search.** The body takes `search_term`, optional `user_id`, `report_type`, `min_quality_score` (0 to 1), `limit` (default 20, at most 100) and `offset` (default 0). It matches `title`, `query` and `content_preview` case-insensitively, returns only reports with status `completed` and orders newest first.
- **Statistics.** Query parameters are `user_id` and `days` (default 30, 1 to 365). The response holds `total_reports`, `status_counts`, `type_counts`, the average quality score, confidence score, generation time and word count, `total_access_count` and `storage_statistics` with `total_storage_mb` and `total_files`.
- **Download.** The `format_type` path segment is matched against stored formats. The response sets `Content-Disposition: attachment; filename=report_<id>.<extension>` and the stored MIME type, and updates the report's access statistics. Content is read from the database columns `content_text` or `content_binary`; the file on disk is read only when both are empty.
- **Integrity.** For each stored format the check confirms the file exists and, when a hash is stored, that the SHA-256 of the content matches. The result has `status` (`ok`, `degraded` or `failed`), `formats_checked`, `formats_valid`, `formats_invalid`, `missing_files` and `errors`. The existence check runs only when a format has a `file_path`, a missing file skips the hash check, and `status` is `failed` whenever `formats_valid` is 0, including for a report stored with no formats (for example `save_to_storage=false`). An unknown report returns the dictionary `{"status": "error", "message": "Report not found"}` with HTTP 200.
- **Delete.** The query parameter `delete_files` (default true) removes the report's storage directory. The database row is hard-deleted and its `report_formats` rows are removed with it.

## Request model

`CreateReportRequest` accepts:

| Field | Default | Notes |
|---|---|---|
| `title` | required | 1 to 200 characters |
| `query` | required | 1 to 1000 characters |
| `domains` | `[]` | research domains |
| `project_id` | none | must belong to the caller and organization |
| `user_id` | none | must equal the token subject when given |
| `report_type` | `comprehensive` | `comprehensive`, `executive_summary`, `academic`, `literature_review`, `methodology`, `synthesis` |
| `citation_style` | `APA` | `APA`, `MLA`, `Chicago`, `IEEE`, `Harvard` |
| `formats` | `html`, `markdown` | `markdown`, `html`, `pdf`, `latex`, `docx`, `json` |
| `include_toc`, `include_executive_summary`, `include_visualizations`, `include_citations`, `include_methodology` | true | stored in the report configuration |
| `workflow_data` | `{}` | input data for the report body |
| `save_to_storage` | true | false marks the row completed with no stored formats |
| `notify_completion` | false | accepted and passed on; no code reads it |

The first entry of `formats` becomes the configuration's `format`. `include_methodology` is stored in the configuration; no generation code reads it. `include_toc`, `include_executive_summary`, `include_visualizations` and `include_citations` are read by the HTML templates and the DOCX and LaTeX exporters (`include_visualizations` and `include_executive_summary` also gate steps in the generator).

## Data source

`ReportSourceProjection` (`src/services/report_source_projection.py`) names what a report is built from: a `source_kind` (`project_results` or `user_supplied`) and an `evaluation` availability (`not_attempted` or `unavailable`). Both are copied into the report metadata, so they appear in the JSON, HTML and downloads. No claim-support evaluation pass exists yet, so every report states `not_attempted`.

With a `project_id`, the route checks the project through `ReportSourceRepository.load_project_source`, then the background task opens its own session and loads it again under the authenticated user and organization passed from the route, never from `workflow_data`. The projection is read only for a project that belongs to the caller, is not deleted and has status `completed` with at least one `research_results` row. Result rows map to report input by `result_type`: `finding` (grouped by `content.category`), `source`, `citation`, `methodology`, `comparison`, and `synthesis` or `summary` (insights and recommendations). The title, query and domains come from the project row; the caller's `workflow_data` is ignored when `project_id` is present. The report confidence is the mean of the result confidences, and the quality score is the project's stored `quality_score`. Either is `null` when the project has none.

Without a `project_id`, the route merges the caller's `workflow_data` with `title`, `query` and `domains` from the request, and the report is labelled `user_supplied`. `_validate_workflow_data` requires `title` and `query`. No quality default is added: a report whose input carries no `quality_report.quality_score` has `quality_score` `null`, and the Markdown, HTML, DOCX and LaTeX outputs print `Not evaluated`. Section content comes from the keys of `aggregated_results`: `sources`, `findings`, `insights`, `citations`, `recommendations`, `methodologies`, `comparisons`, `conflict_resolutions`, `limitations`, `metrics` and `confidence_score`.

## Generation pipeline

The background task `_generate_report_task` runs after the response is sent:

1. It sets the row to `generating`.
2. `ReportGenerator._extract_input_data` normalises the input (see above); a project request without its loaded projection fails.
3. `ReportGenerator._build_report_structure` (through `ReportStructureBuilder`) creates a `Report`: sections for the report type, citations, visualization specifications when `include_visualizations` is true, metadata, an executive summary when `include_executive_summary` is true and a quality check. The quality check logs warnings for a word count or source count below the per-type minimum and for missing citations; it never fails a report.
4. `ReportOutputGenerator.generate_formats` renders each requested format. HTML uses `TemplateRenderer`; Markdown and JSON are built directly; PDF renders the HTML through WeasyPrint; LaTeX and DOCX use their exporters. When more than one format is requested and `PARALLEL_REPORT_GENERATION` is true the formats run concurrently.
5. With `save_to_storage`, `ReportStorageService.store_report` writes each format and marks the row `completed`; otherwise the task updates the row's metadata and marks it `completed` with no formats. The task then commits.

A report fails when any requested format fails: the exception rolls the session back, sets `generation_status` to `failed`, appends the message to `generation_errors` and logs the error. A missing WeasyPrint installation, `ENABLE_PDF_GENERATION=false` and `ENABLE_LATEX_GENERATION=false` all make the corresponding format raise.

Report types map to section lists in `ReportTemplateConfig`; only sections with a builder are produced (`introduction`, `methodology`, `literature_review`, `findings`, `analysis`, `discussion`, `conclusions`, `recommendations`, `limitations`, `abstract`, `results`, `key_findings`, `strategic_insights`). Section names without a builder (for example `executive_summary`, `references`, `search_methodology` and `research_design`) produce no section; the executive summary comes from `generate_executive_summary` and citations are rendered by the output step.

Formats:

| Format | Producer | Notes |
|---|---|---|
| `html` | `TemplateRenderer.render_report` | falls back to a built-in basic HTML page when templating fails |
| `markdown` | `ReportOutputGenerator.generate_markdown` | title, summary, sections, references |
| `json` | `ReportOutputGenerator.generate_json` | the serialised `Report` |
| `pdf` | `PDFExporter` (WeasyPrint) | needs `weasyprint`; built from the HTML output |
| `latex` | `LaTeXExporter` | returns `.tex` source; no PDF compilation is requested, and the exporters read neither `pdf_settings` nor `latex_settings` from the configuration. The HTML template `base.html.j2` does read `config.pdf_settings` (font, size, margins) and the PDF is built from that HTML, so the defaults of `pdf_settings` affect PDF output; the request model cannot set them |
| `docx` | `DOCXExporter` (`python-docx`) | |

### Templates

`TemplateRenderer` builds a Jinja environment over `REPORT_TEMPLATE_PATH` with `FileSystemLoader`, HTML and XML autoescaping, `trim_blocks` and `lstrip_blocks` and registers filters (`markdown`, `truncate_words`, `format_number`, `format_percentage`, `format_score`, `strip_markdown`, `format_citation_count`, `capitalize_words`, `extract_first_sentence`, `format_duration`) and helper functions. The directory `src/templates/reports/` holds four templates: `base.html.j2`, `comprehensive_report.html.j2`, `executive_summary.html.j2` and `academic_paper.html.j2`, plus the partial `_visualization.html.j2` that the base and academic templates import. The report types `literature_review`, `methodology` and `synthesis` name templates that do not exist; the renderer then uses `base.html.j2`. When even the base template is missing, `generate_html` logs a warning and returns the basic HTML page. With `ENABLE_TEMPLATE_CACHE` true, loaded templates are cached per renderer instance with no expiry.

`ReportOutputGenerator.generate_html` renders each visualization specification through `VisualizationGenerator` to a PNG and embeds it as a `data:image/png;base64,...` image; only strictly base64 payloads are embedded. PNG export needs the `kaleido` package, which is not a declared dependency; without it no image is produced and the templates print the plotted values as an escaped table. The builder records a source-by-year bar chart when sources span more than one year, and a pie of sources per domain when sources carry at least two distinct `domain` values.

## Configuration

The `ReportSettings` variables (for example `REPORT_STORAGE_PATH`, default `./reports`) are listed in [configuration](02-configuration.md). Each request constructs a `ReportGenerator`, which creates the storage directory if it is missing.

## Persistence

Reports live in two tables; see [data model and persistence](14-data-model-and-persistence.md).

| Table | Key columns |
|---|---|
| `generated_reports` | `id`, `organization_id`, `user_id`, `project_id`, `title`, `report_type`, `query`, `domains`, `configuration`, `generation_status` (`pending`, `generating`, `completed`, `failed`), `quality_score`, `confidence_score`, `word_count`, `page_count`, `generation_time_seconds`, `generation_errors`, `storage_path`, `content_preview`, `access_count`, `last_accessed_at` |
| `report_formats` | `id`, `report_id` (foreign key, cascade delete), `format_type`, `mime_type`, `file_extension`, `encoding`, `file_path`, `file_size`, `file_hash` (SHA-256), `content_text`, `content_binary` |

Stored files are written to `<REPORT_STORAGE_PATH>/<report_id>/report.<extension>`. The same bytes are also stored in the format row, so a download does not depend on the file system.

## Source map

- `src/api/routes/reports.py`: routes, request and response models, `_generate_report_task`
- `src/services/report_generator.py`: `ReportGenerator`, input normalisation
- `src/services/report_source_projection.py`: `ReportSourceProjection` and the owner-scoped `ReportSourceRepository`
- `src/services/report_structure_builder.py`: sections, citations, visualization specifications, quality check
- `src/services/report_output_generator.py`: per-format rendering and the basic HTML fallback
- `src/services/template_renderer.py`: Jinja environment, filters and template selection
- `src/services/exporters/`: PDF, LaTeX and DOCX exporters
- `src/services/report_storage.py`: file and database storage, search, statistics, integrity, deletion
- `src/services/report_config.py`: `ReportSettings` and the type, format and quality tables
- `src/services/visualization_generator.py`: chart generator with no production importer
- `src/templates/reports/`: Jinja templates
- `src/models/report.py`: `Report`, `ReportConfiguration`, request models and enums
- `src/models/db/generated_report.py`: `GeneratedReport` and `ReportFormat` tables
- `src/repositories/report_repository.py`: tenant-scoped report and format queries
