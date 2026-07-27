# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Web workbench redesign (Direction A): migrated the frontend design-token system in `web/src/index.css` to a warm cream/teal light palette with a single teal signal accent and a warm (non-navy) dark mode with a stepped surface ladder. Dark mode is carried by the Tailwind `.dark` class. Loaded Fraunces (display), IBM Plex Sans (UI), and IBM Plex Mono (data) type families and pointed `tailwind.config.js` font families at them.
- Rebuilt the web landing page (`/`) around the project's real positioning: an editorial hero, the six core primitives, a `research-cli agents` command reference, a traceability priorities ledger, and an open-source closing. Removed the previous fabricated content (mock terminal demo, invented testimonials, "beta" pill). All CLI commands shown are real and copy-paste runnable.
- Restyled the workflow launch surface (`/app/workflows`): workflow attributes now render in hairline grids, maturity is a pill, and provenance/status chips share a consistent pill treatment. Fixed the call-to-action hierarchy so the page has a single primary action ("Start controlled run"); the per-card configure control is now a secondary toggle that reports its state via `aria-pressed`.
- Restyled the run trace detail surface (`/app/runs/:id`): consolidated claim-support, evidence-availability, lifecycle, and evaluation badges into one semantic pill (`StatusBadge`); gave the claims↔evidence inspector numbered citations that match numbered evidence cards; and gave the evidence rail an internal scroll region with a pinned head so long runs stay navigable. Evidence excerpts, task/event log, evaluation cards, and the operational ledger were restyled while preserving the existing citation-focus interaction and provenance/honesty semantics.

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
