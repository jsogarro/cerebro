# Uploaded documents

A caller can upload a manuscript, list and read its metadata, and delete it. The feature is off by default. No route returns document text: the text is stored encrypted and read back only by server-side jobs such as [critique](#critique).

## Routes

Mounted at `/api/v1/documents` in `src/api/main.py` (`src/api/routes/documents.py`). Every route requires authentication. Every query filters on the caller's organization and user id, so another caller's document is a 404, never a 403.

| Method and path | Purpose | Success | Errors |
|---|---|---|---|
| `POST /api/v1/documents` | upload one file (`multipart/form-data`, field `file`, optional `declared_authors` as a JSON list of at most 20 names of at most 100 characters) | 201, `DocumentOut` | 404 when uploads are off; 411 without `Content-Length`; 413 over the size limit; 415 for an unsupported or mismatched type; 400 `invalid_multipart` (or the multipart parser's own status) for a body that is not valid multipart; 422 for a missing file, bad authors, or an extraction refusal (`too_many_pages`, `too_much_text`, `encrypted_pdf`, `no_text`, `malformed_pdf`, `extraction_timeout`, `output_too_large`, `extraction_resource_limit`, `extraction_cpu_limit`); 429 `upload_quota_exceeded`; 503 `retention_unavailable`, `document_storage_unavailable` or `extraction_busy` |
| `GET /api/v1/documents` | the caller's newest documents (`limit` 1 to 50) | 200, a list of `DocumentOut` | 404 when uploads are off; 503 `document_storage_unavailable` |
| `GET /api/v1/documents/{document_id}` | one document's metadata | 200, `DocumentOut` | 404; 503 `document_storage_unavailable` |
| `DELETE /api/v1/documents/{document_id}` | delete the document | 204 | 404 |

`DocumentOut` carries `id`, `filename`, `media_type`, `char_count`, `page_count`, `text_sha256` (the SHA-256 of the extracted text as UTF-8), `invisible_chars_removed` and `expires_at`. `DELETE` works whether or not uploads are enabled, so data stored earlier can always be removed.

Accepted types are `application/pdf`, `text/markdown` and `text/plain`. The declared type must agree with the content: a PDF declared as text, or text declared as PDF, is a 415. The request body is cut off at `DOCUMENT_MAX_BYTES` plus 64 KiB whatever the headers claim. A PDF is extracted in a child process with its own deadline (`DOCUMENT_EXTRACT_TIMEOUT_S`), which also covers the wait for an extraction slot; plain text and Markdown are decoded in a thread, not a child process.

## Settings

| Setting | Default | Effect |
|---|---|---|
| `DOCUMENT_UPLOADS_ENABLED` | `false` | Serves `POST` and the two `GET` routes; off, they answer 404 |
| `DOCUMENT_ENCRYPTION_KEY` | empty | Fernet key (a comma-separated list rotates: the first encrypts, all decrypt). Startup fails when a document feature is on and the key is blank or invalid |
| `DOCUMENT_MAX_BYTES` | 20 MiB | Largest accepted upload |
| `DOCUMENT_MAX_PAGES` | 300 | Largest PDF page count |
| `DOCUMENT_MAX_CHARS` | 2,000,000 | Largest extracted text |
| `DOCUMENT_EXTRACT_TIMEOUT_S` | 60 | Deadline for one extraction |
| `DOCUMENT_RETENTION_DAYS` | 30 | Days until `expires_at` |
| `DOCUMENT_UPLOADS_PER_DAY` | 20 | Uploads per user per day; a failed extraction still counts |
| `DOCUMENT_PURGE_INTERVAL_S` | 3600 | Seconds between sweeps that delete expired documents |
| `DOCUMENT_CRITIQUE_ENABLED` | `false` | Serves the critique routes (see below) |
| `PROVIDER_TERMS_VERIFIED_ON` | empty | ISO date the model provider's terms were checked; critique starts only when it is not in the future |

## Storage and retention

The extracted text, the filename and the declared authors are stored encrypted; the original bytes are never stored. Expired documents are deleted by a sweep that runs in every worker at start and then every `DOCUMENT_PURGE_INTERVAL_S`. An upload is refused with 503 `retention_unavailable` while that sweep is not running (no database, or the loop has stopped), so nothing is stored that nothing will expire. Erasing a user (`DELETE /api/v1/users/{user_id}/gdpr`) deletes their documents.

## Critique

A critique sends the document text to a third-party model, so it also needs `PROVIDER_TERMS_VERIFIED_ON`, `GEMINI_API_KEY`, and a request that acknowledges the consent text for the configured model and host. Each retained finding quotes a located span of the document. Routes in `src/api/routes/critiques.py`, all under authentication and owner scope; all but the first answer 404 while `DOCUMENT_CRITIQUE_ENABLED` is false:

| Route | Purpose |
|---|---|
| `GET /api/v1/critique-config` | enablement, model, hosts, limits and consent texts |
| `POST /api/v1/documents/{document_id}/critiques` | start a critique (202) |
| `GET /api/v1/documents/{document_id}/critiques` | list a document's critiques |
| `GET /api/v1/critiques/{critique_id}` | status and findings |
| `GET /api/v1/critiques/{critique_id}/report` | plain-text report |
| `DELETE /api/v1/critiques/{critique_id}` | delete and stop the run |

## End-to-end stack

`docker-compose.e2e.yml` extends `docker-compose.yml` for the journey suite only: it turns uploads and critique on with a throwaway key and serves `tests/e2e/stack_app.py`, which replaces the review model with a fixture, so no journey calls a provider. The base compose file and production keep every document feature off.

## Source map

- `src/api/routes/documents.py`: the routes
- `src/services/document_ingestion.py`: type sniffing, extraction, `require_document_key`, `run_purge_loop`
- `src/repositories/uploaded_document_repository.py`: owner-scoped storage, quota ledger, purge
- `src/models/db/uploaded_document.py`: `UploadedDocument`, `UsageLedger`
- `src/core/acquisition/extract_child.py`: the extraction child process
- `src/core/memory_encryption.py`: `document_cipher`
