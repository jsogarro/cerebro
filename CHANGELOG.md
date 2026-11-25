# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Durable agent runs: runs, tasks, attempts, configuration snapshots, an append-only event log and a transactional outbox, with SSE and WebSocket run streams that resume from a cursor (#97).
- A tool boundary with capability grants, and evidence and claim-support records with provenance (#104).
- Compiled execution plans enforced before dispatch, and one research kernel behind every execution route (#94, #95).
- Execution authority on the convenience endpoints (#96).
- Mediated tool execution from end to end: persisted capability grants, durable tool audit with events and leases, a final-report artifact, claim-support resolution and a model tool-call loop that parses a strict envelope and reports a failure instead of inventing an answer (#120).
- Execution budget ceilings (#227).
- Pinned PDF text extraction that produces an `extracted_text` artifact (#181).
- A deterministic span-alignment primitive for pinned-text checks (#130).
- A minimal author-date citation renderer (#182).
- Detection of citation-shaped prose that the citation renderer did not emit (#133).
- Deterministic citation validation dimensions, with a fixture corpus (#239).
- Academic search results persisted as run-owned evidence, and claim spans bound to citations from sourced metadata (#224, #223).
- A required reason code on abstained evaluation results (#131).

### Changed
- Redesigned the web workbench. The design tokens in `cerebro/web/src/index.css` now give a warm cream and teal light theme with one teal accent, and a warm, non-navy dark theme built on a stepped ladder of surface colors. The Tailwind `.dark` class switches themes. The app loads Fraunces (display), IBM Plex Sans (UI) and IBM Plex Mono (data), and `cerebro/web/tailwind.config.js` points its font families at them.
- Rebuilt the web landing page (`/`). It now has a hero, the six core primitives, a `research-cli agents` command reference, a traceability priorities ledger and an open-source closing section. Removed the mock terminal demo, the invented testimonials and the "beta" pill. Every CLI command shown is real and runs as written.
- Restyled the workflow launch page (`/app/workflows`). Workflow attributes render in hairline grids, maturity is a pill, and provenance and status chips share one pill style. The page has a single primary action ("Start controlled run"), and the per-card configure control is a secondary toggle that reports its state through `aria-pressed`.
- Restyled the run trace detail page (`/app/runs/:id`). Claim-support, evidence-availability, lifecycle and evaluation badges share one `StatusBadge` pill. The claims-to-evidence inspector numbers its citations to match the numbered evidence cards, and the evidence rail scrolls internally under a pinned head so long runs stay navigable. Evidence excerpts, the task and event log, evaluation cards and the operational ledger were restyled; the citation-focus interaction and the provenance and honesty semantics are unchanged.
- Required an authenticated caller on every API route except an explicit public allowlist (#226).
- Probed the database and Redis in `/ready`, which returns 503 when either fails (#198).
- Paged `GET /reports` by offset and reported the real total (#201).
- Failed report generation when a requested export format fails (#192).
- Aligned `research-cli` with the API response contract and stopped leaking the WebSocket token (#196).
- Aligned the web client with the server's authentication and route contract (#230).
- Ran supervisor workers through the bounded verification revision loop, and made the verification gate fail closed (#231, #174).
- Stopped supervisor execute from fabricating completed results and quality scores, and removed randomised values from supervisor API responses (#175, #229).
- Enforced worker `tool_allowlist` on model-initiated tool calls (#189).
- Counted only in-flight executions against the admission cap (#176).
- Reported unenforced cost and token ceilings as declared rather than as limits in execution status (#178).
- Returned null for query estimates the route does not have (#206).
- Derived chain and mixture status from member results (#204).
- Filtered research projects by the statuses the API returns and rejected unknown values with 422 (#188).
- Stopped reporting metadata-only citation sources as verified (#194).
- Fixed the MASR routing circuit breaker: it never opened because each attempt recorded a success first, and it stayed in HALF_OPEN after a successful probe (#195).
- Shared one `JWTService` and Redis client per process instead of per request (#177).
- Bounded routing history and dropped unread execution history (#187).
- Fixed comparative correlation: it now takes numeric series from `comparison_data`, rejects malformed `comparison_data` with an error, and no longer returns stand-in content from a failed analysis (#183).
- Annotated pre-fix fabricated verification rows on the research results read path (#127).
- Made the Kubernetes manifests runnable by mounting JWT keys and dropping the removed worker (#207).
- Copied the alembic and uvicorn console scripts into the production image (#173).
- Supported SQLAlchemy 2.1, after capping it below 2.1 (#138, #221).
- Imported experiment and report models so alembic autogenerate sees every table, and aligned the experiment models with their tables (#191, #209).
- Indexed report project and user filters and `report_formats.report_id`, and batched `BaseRepository` bulk updates and deletes (#205, #124).
- Deleted report files only after the database deletes commit in `cleanup_old_reports` (#122).
- Stopped duplicate WebSocket delivery across instances, and kept broadcasting when a client disconnected mid-broadcast (#242).
- Resolved the tenant for agent metrics, system statistics, active executions and performance comparison from the token without opening a database session (#246).

### Removed
- Six unmounted API routers (#185).
- The unmounted security rate limiter and validators, the unused `CORSSecurityMiddleware` and the unmounted MASR router fork and unused event catalogue (#186, #129, #125).
- Five inert kill-switch settings (#128).
- The never-adopted `agent_tasks` table and `TaskRepository` (#184).
- Four unreachable duplicate authentication tables, through migration `f8c9d0e1a2b3` (#118).
- The worker image build and the dead file-watcher hot-reload (#210, #220, #240).
- The GCR image workflow (`docker-build.yml`) and the unparseable reusable test workflow (`reusable-test.yml`) (#245).

### Fixed
- Cache expiry no longer serves entries older than 24 hours as fresh; it used `timedelta.seconds`, which wraps at 24 hours (#240).
- A rejected request no longer spends an idempotency key (#106); idempotency keys are scoped to the caller and reserved before executing (#190).
- Rejected requests no longer amplify durable audit writes (#225).
- Registration unique-index collisions return 409, and `lower(email)` and `lower(username)` are indexed (#202).

### Security
- Required authenticated WebSocket handshakes and failed closed when token revocation storage is unavailable, persisted durable audit events for request admission and outcome, kept the MCP service on the internal Compose network, ran migrations in the production image entrypoint, and answered CORS preflights at the outer middleware (#118).
- Locked accounts after repeated failed logins, and trusted `X-Forwarded-For` only from configured proxies (#228).
- Keyed unauthenticated rate limiting on the client address rather than `X-API-Key` (#200).
- Restricted GDPR deletion to the account owner or a superuser, and made erasure delete dependent rows and revoke the user's tokens (#134, #244).
- Ended refresh tokens when a session ends, and refused refresh for inactive users (#180).
- Authorized WebSocket subscriptions made by message, and dropped project ids from `/ws/health` (#179).
- Attributed audit rows to the authenticated subject (#203).
- Scoped project, authority and execution reads to the caller's tenant (#98).
- Scoped supervisor progress events, execution resume, agent execution authority and agent monitoring endpoints to the caller's organization (#135, #126, #132, #241).
- Failed closed when a tenant-scoped repository read has no organization (#216).

### Documentation
- Added `docs/reference/` covering every implemented subsystem with Active / Off by default / Not wired status labels; replaced superseded and aspirational documents; added a testing guide.

## [0.2.0] - 2026-06-29

### Added
- RS256 JWT authentication with RSA key files (replaces HS256 secret-based approach)
- Comprehensive smoke test script (`scripts/smoke_test.sh`) with SQLite + ephemeral keypair
- Multi-tenancy support: `ResearchProject.user_id` is now opaque String(255), no FK to users table
- Repository pattern implementation for `UserRepository` and `ResearchRepository`
- Integration test infrastructure with PostgreSQL testcontainers
- WebSocket connection lifecycle improvements (proper disconnect handling)

### Changed
- Research API lifecycle: POST `/api/v1/research/projects` now auto-starts projects (no separate /start endpoint)
- Research endpoints moved to `/api/v1/research/projects/*` (not `/api/v1/projects/*`)
- Password validation: login requires min 8 chars, registration requires min 12 with complexity (upper/lower/digit/special)
- JWT service auto-generates RSA keypair if `JWT_PRIVATE_KEY_PATH` / `JWT_PUBLIC_KEY_PATH` not provided
- CI coverage threshold temporarily relaxed to 25% (was 80%; will re-tighten post-stabilization)
- Bandit security scan runs in advisory mode (`--exit-zero`)
- Hadolint Docker linting failures downgraded to warnings
- Kustomize build step removed from validate-k8s workflow
- E2E tests disabled on PR triggers (manual trigger only)

### Fixed
- WebSocket disconnect spin-loop (async context cleanup)
- JWT token validation with RS256/HS256 mismatch
- Research project cancel endpoint returning 500 (now 204)
- Duplicate `LoginRequest` schema removed from `src/security/validators.py`
- Duplicate `EventPublisher` removed (consolidated to `src/events/publisher.py`)

### Removed
- Legacy research endpoints: `/start`, `/status`, `/report` (replaced by new lifecycle)
- Hardcoded HS256 JWT secret key references

### Security
- JWTService now uses RS256 algorithm with PEM-encoded RSA keys for enhanced security
- Token blacklisting via Redis for immediate revocation
- Device fingerprinting in JWT payload

### Testing
- Current test status (main branch, post-PR#10 merge):
  - Unit: 8 failed, 630 passed, 15 skipped, 18 errors
  - Real coverage: ~27% (pytest --cov)
  - Target coverage: 80% (roadmap item)

### Known Issues
- MFA, OAuth2 providers, password breach checking: documented but not implemented (roadmap items)
- PR #20 (open, not merged): proposes bumping login password min_length to 12 with complexity

---

## [0.1.0] - 2025-09-08

### Added
- Initial release
- FastAPI-based research platform API
- LangGraph orchestration framework
- Multi-agent research system (literature review, comparative analysis, methodology, synthesis, citation)
- PostgreSQL + Redis + SQLAlchemy 2.x data layer
- Alembic database migrations
- React/TypeScript frontend (`cerebro/web`)
- Kubernetes deployment manifests (`k8s/`)
- CLI tool for API interaction
- WebSocket real-time progress updates
- Basic JWT authentication (HS256)
- Docker Compose development environment

[Unreleased]: https://github.com/jsogarro/cerebro/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/jsogarro/cerebro/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/jsogarro/cerebro/releases/tag/v0.1.0
