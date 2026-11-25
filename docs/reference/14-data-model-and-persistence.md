# Data model and persistence

The persistence layer is SQLAlchemy 2.1 over an async engine (`asyncpg` on PostgreSQL, `aiosqlite` on SQLite), with Alembic migrations and a repository layer that applies the tenant filter. Models live in `src/models/db/`, repositories in `src/repositories/`, migrations in `alembic/versions/`.

## Summary

| Feature | Status | Entry point | Source |
|---|---|---|---|
| Async engine and session factory | Active | `init_db()` in the API lifespan | `src/models/db/session.py` (`init_db`, `get_session_factory`) |
| Request-scoped session | Active | `Depends(get_session)` | `src/models/db/session.py` (`get_session`) |
| Transaction helpers and `DatabaseManager` | Not wired | none | `src/models/db/session.py` (`get_transaction`, `execute_in_transaction`, `DatabaseManager`) |
| Soft-delete base model | Active | every `BaseModel` table | `src/models/db/base.py` (`BaseModel`) |
| Append-only guard (ORM and PostgreSQL trigger) | Active | four durable tables | `src/models/db/append_only.py` (`register_append_only`) |
| Tenant filter in repositories | Active | every repository read and write | `src/repositories/base.py` (`BaseRepository.apply_organization_scope`), `src/repositories/tenant_scope.py` (`scope_to_organization`) |
| PostgreSQL row-level security policies | Active (PostgreSQL only) | migrations; context set by `get_tenant_context` | `alembic/versions/`, `src/middleware/tenant_context.py` (`set_postgres_tenant_context`) |
| Alembic migrations at container start | Active | `docker/entrypoint.sh` | `alembic/env.py`, `alembic/versions/` |
| SQLite table creation at startup | Active (SQLite only) | API lifespan | `src/api/main.py` (`lifespan`) |
| Experiment tables | Not wired | none | `src/models/db/experiment.py` |

## Engine and sessions

`src/models/db/session.py` holds a module-level engine and session factory.

- `init_db(database_url=None, **engine_kwargs)` builds the engine from `settings.DATABASE_URL`, creates an `async_sessionmaker` (`expire_on_commit=False`, `autoflush=False`, `autocommit=False`), and runs `SELECT 1` to test the connection. A second call logs `database_already_initialized` and returns. A failed connection test disposes the engine and re-raises. The API lifespan calls `init_db()` with no arguments.
- `create_engine` defaults: `pool_size=20`, `max_overflow=10`, `pool_timeout=30`, `pool_pre_ping=True`, `pool_recycle=3600`, pool class `AsyncAdaptedQueuePool`. When the URL contains `sqlite`, `init_db` sets `use_null_pool=True` and `NullPool` replaces the queue pool, with none of the pool arguments.
- `get_session_factory()` returns the factory and raises `RuntimeError("Database not initialized. Call init_db() first.")` before `init_db()` has run. The durable execution service, the outbox relay, and the run event streams obtain sessions through this factory.
- `get_session()` is the FastAPI dependency. It yields a session, commits when the request handler returns without raising, rolls back and re-raises on an exception, and closes the session.
- `close_db()` disposes the engine and clears both module-level references.

`get_transaction()`, `execute_in_transaction()`, `DatabaseManager`, and `get_db_manager()` are defined in the same module and have no caller elsewhere under `src/`. `DatabaseManager` offers connection checks, pool status, table sizes (`pg_tables`), slow queries (`pg_stat_statements`), index analysis, and `VACUUM ANALYZE`.

## Model base classes

`src/models/db/base.py` defines `Base` (a `DeclarativeBase`), `BaseModel`, and `PortableUUID` (exported as `UUID`). `PortableUUID` stores a native `UUID` on PostgreSQL and `CHAR(36)` elsewhere.

`BaseModel` is abstract and adds these columns to every table that extends it:

| Column | Type | Notes |
|---|---|---|
| `id` | `PortableUUID`, primary key | default `uuid.uuid4` |
| `created_at`, `updated_at` | `DateTime(timezone=True)` | server default `now()`; `updated_at` also has `onupdate` |
| `deleted_at` | `DateTime(timezone=True)`, indexed, nullable | soft delete marker |
| `created_by`, `updated_by` | `String(255)`, nullable | audit fields |

`BaseModel` also provides `is_deleted`, `soft_delete()`, `restore()`, and `to_dict()`.

Five tables extend `Base` directly and carry their own columns:

| Table | Primary key | Append-only |
|---|---|---|
| `agent_run_config_snapshots` | `id`, `PortableUUID` | yes |
| `agent_run_events` | `id`, `PortableUUID` | yes |
| `agent_run_event_outbox` | `id`, `BigInteger` autoincrement (`Integer` on SQLite) | no, mutable by design |
| `agent_evidence` | `id`, `PortableUUID` | yes |
| `agent_claim_supports` | `id`, `PortableUUID` | yes |

The other 21 tables extend `BaseModel`, so they have soft deletion. `generated_reports` and `report_formats` redeclare `id` as `postgresql.UUID(as_uuid=True)` with `uuid4` as the default instead of `PortableUUID`.

`register_append_only` (`src/models/db/append_only.py`) is a class decorator that registers `before_update` and `before_delete` listeners raising `AppendOnlyViolationError`. It covers ORM unit-of-work writes only. Migration `c3f5a1d7b209` installs the function `reject_append_only_mutation()` and `BEFORE UPDATE OR DELETE` triggers on `agent_run_events` and `agent_run_config_snapshots`; migration `5d04cec6c232` installs the same trigger on `agent_evidence` and `agent_claim_supports`. The triggers are created on PostgreSQL only.

## Tables

The models declare 26 tables.

| Table | Model class | Module | Tenant column | RLS policy | Append-only | Owner subsystem |
|---|---|---|---|---|---|---|
| `users` | `User` | `user.py` | `organization_id`, nullable | no | no | authentication |
| `oauth_accounts` | `OAuthAccount` | `oauth_account.py` | none | no | no | authentication |
| `audit_logs` | `AuditLog` | `audit_log.py` | none | no | no | security and audit |
| `security_alerts` | `SecurityAlert` | `security_alert.py` | none | no | no | security and audit |
| `research_projects` | `ResearchProject` | `research_project.py` | `organization_id`, nullable | `tenant_isolation_research_projects` | no | research execution |
| `research_results` | `ResearchResult` | `research_result.py` | none | no | no | research execution |
| `workflow_checkpoints` | `WorkflowCheckpoint` | `workflow_checkpoint.py` | none | no | no | research execution |
| `generated_reports` | `GeneratedReport` | `generated_report.py` | `organization_id`, not null | no | no | reports |
| `report_formats` | `ReportFormat` | `generated_report.py` | none | no | no | reports |
| `agent_runs` | `AgentRun` | `run_lifecycle.py` | `organization_id`, nullable | `tenant_isolation_agent_runs` | no | durable runs |
| `agent_run_tasks` | `AgentRunTask` | `run_lifecycle.py` | `organization_id`, nullable | `tenant_isolation_agent_run_tasks` | no | durable runs |
| `agent_task_attempts` | `AgentTaskAttempt` | `run_lifecycle.py` | `organization_id`, nullable | `tenant_isolation_agent_task_attempts` | no | durable runs |
| `agent_run_config_snapshots` | `AgentRunConfigSnapshot` | `run_config_snapshot.py` | `organization_id`, nullable | `tenant_isolation_agent_run_config_snapshots` | yes | durable runs |
| `agent_run_events` | `AgentRunEvent` | `run_event.py` | `organization_id`, nullable | `tenant_isolation_agent_run_events` | yes | durable runs |
| `agent_run_event_outbox` | `AgentRunEventOutbox` | `run_event.py` | `organization_id`, nullable | `tenant_isolation_agent_run_event_outbox` | no | durable runs |
| `agent_capability_grants` | `AgentCapabilityGrant` | `capability.py` | `organization_id`, nullable | `tenant_isolation_agent_capability_grants` | no | tool boundary |
| `agent_capability_approvals` | `AgentCapabilityApproval` | `capability.py` | `organization_id`, nullable | `tenant_isolation_agent_capability_approvals` | no | tool boundary |
| `agent_tool_invocations` | `AgentToolInvocation` | `tool_invocation.py` | `organization_id`, nullable | `tenant_isolation_agent_tool_invocations` | no | tool boundary |
| `agent_artifacts` | `AgentArtifact` | `artifact.py` | `organization_id`, nullable | `tenant_isolation_agent_artifacts` | no | tool boundary and evidence |
| `agent_evidence` | `AgentEvidence` | `evidence.py` | `organization_id`, nullable | `tenant_isolation_agent_evidence` | yes | tool boundary and evidence |
| `agent_claim_supports` | `AgentClaimSupport` | `claim_support.py` | `organization_id`, nullable | `tenant_isolation_agent_claim_supports` | yes | tool boundary and evidence |
| `experiments` | `Experiment` | `experiment.py` | none | no | no | experiments (not wired) |
| `experiment_variants` | `ExperimentVariant` | `experiment.py` | none | no | no | experiments (not wired) |
| `experiment_assignments` | `ExperimentAssignment` | `experiment.py` | none | no | no | experiments (not wired) |
| `experiment_results` | `ExperimentResult` | `experiment.py` | none | no | no | experiments (not wired) |
| `experiment_analyses` | `ExperimentAnalysis` | `experiment.py` | none | no | no | experiments (not wired) |

Authentication tables are described in [authentication and tenancy](04-authentication-and-tenancy.md), audit and alert tables in [security, audit and observability](12-security-audit-observability.md), research and checkpoint tables in [research execution](05-research-execution.md), report tables in [reports](11-reports.md), run and event tables in [durable runs and events](08-durable-runs-and-events.md), and capability, invocation, artifact, evidence, and claim tables in [tool boundary and evidence](09-tool-boundary-and-evidence.md).

Tables dropped by migrations and absent from the models: `api_keys`, `password_history`, `user_sessions`, and `mfa_settings` (migration `f8c9d0e1a2b3`), and `agent_tasks` (migration `d2e6f0a4b8c1`).

### `users`

Columns: `email` (`String(255)`), `username` (`String(100)`), `hashed_password`, `full_name`, `organization` (free text), `organization_id` (UUID, nullable, indexed), `role`, `is_active`, `is_superuser`, `is_verified`, `last_login`, `login_count`, `max_projects`, `api_rate_limit`. Both `email` and `username` carry a unique index. Migration `b5d7e9f1a3c2` adds the functional unique indexes `uq_users_email_lower` and `uq_users_username_lower` on `lower(email)` and `lower(username)`; they cover soft-deleted rows, so a deleted account's email or username is not reusable. Composite indexes: `idx_user_org_active` (`organization_id`, `is_active`), `idx_user_active`, `idx_user_login`. Relationships: `audit_logs`, `oauth_accounts`, and `security_alerts` (the last two with `cascade="all, delete-orphan"`).

### `research_projects`

Columns: `title`, `query`, `domains` (JSON list), `status` (`ProjectStatus`: `draft`, `in_progress`, `completed`, `failed`, `cancelled`), `quality_score`, `user_id` (UUID, not null, indexed, no foreign key to `users`), `organization_id` (UUID, nullable, indexed), `workflow_id`, and `metadata` (JSON, mapped to the attribute `project_metadata`). Relationships `results` and `checkpoints` use `lazy="selectin"` and `cascade="all, delete-orphan"`. Composite indexes: `idx_project_org_user_status`, `idx_project_user_status`, `idx_project_org_status`, `idx_project_workflow`, `idx_project_quality`.

### `research_results`

Columns: `project_id` (foreign key to `research_projects.id`), `result_type`, `content` (JSON), `confidence_score`, `agent_type`, `metadata` (JSON, attribute `result_metadata`), and `source_id` (`String(500)`, indexed; an external identifier such as a DOI or URL). Composite indexes: `idx_result_project_type`, `idx_result_agent`, `idx_result_confidence`, `idx_result_source`.

### `workflow_checkpoints`

Columns: `workflow_id`, `project_id` (foreign key to `research_projects.id`), `run_id` (nullable foreign key to `agent_runs.run_id`, added by migration `c3f5a1d7b209`), `checkpoint_data` (JSON), `phase`, `checkpoint_type` (default `automatic`), `checkpoint_version`, `is_recoverable` (default true), `recovery_metadata`, `execution_metrics`. `CheckpointRepository.create_checkpoint` has no `run_id` parameter, so rows created through the repository leave `run_id` null.

### `generated_reports` and `report_formats`

`generated_reports` stores report metadata: `project_id` (nullable foreign key to `research_projects.id`), `workflow_id`, `user_id` (nullable foreign key to `users.id`), `organization_id` (UUID, **not null**, indexed), `title`, `report_type`, `query`, `domains`, `configuration`, `formats_generated`, quality and count metrics, `generation_status` (default `pending`), generation timestamps, `generation_errors`, `agents_used`, `storage_path`, `file_sizes`, `executive_summary`, `content_preview`, `key_findings`, `is_public`, `access_count`, `last_accessed_at`. Composite indexes: `idx_generated_reports_org_project_created` and `idx_generated_reports_org_user_created`. `report_formats` holds one row per rendered format: `report_id` (foreign key with `ondelete="CASCADE"`), `format_type`, `mime_type`, `file_extension`, `encoding`, `file_path`, `file_size`, `file_hash`, `content_text`, `content_binary`, generation timing, `generator_version`. The `formats` relationship on `GeneratedReport` cascades `all, delete-orphan`. Neither table has a row-level security policy.

### Run, tool, evidence, claim, and capability tables

- `agent_runs`, `agent_run_tasks`, `agent_task_attempts`: the run, task, and attempt lifecycle; see [durable runs and events](08-durable-runs-and-events.md).
- `agent_run_config_snapshots`: the frozen configuration a run was admitted with, one row per run.
- `agent_run_events`, `agent_run_event_outbox`: the ordered event log and its delivery outbox.
- `agent_capability_grants`, `agent_capability_approvals`: capability grants issued at admission and approvals bound to a request fingerprint; see [tool boundary and evidence](09-tool-boundary-and-evidence.md).
- `agent_tool_invocations`: one row per mediated tool call, with idempotency key, trust labels, capability decision fields, and lease columns.
- `agent_artifacts`: content-addressed outputs, including the final report of a run and source snapshots.
- `agent_evidence`: citations of snapshot spans, each referencing an artifact and optionally the invocation that produced it.
- `agent_claim_supports`: one verdict row per material claim of an artifact.

## Tenancy in the data layer

Fifteen tables carry an `organization_id` column: `users`, `research_projects`, `generated_reports`, and the twelve `agent_*` run, capability, invocation, artifact, evidence, and claim tables. `generated_reports.organization_id` is not null; every other tenant column is nullable.

**Row-level security.** Migrations `b7a9c2d4e8f1`, `c3f5a1d7b209`, and `5d04cec6c232` run `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` and create a policy `tenant_isolation_<table>` on thirteen tables (the table column above lists each). Every policy has the same predicate: `organization_id = current_setting('app.current_org_id', true)::uuid`. The migrations create them only when the connection is PostgreSQL. No migration issues `FORCE ROW LEVEL SECURITY`. `set_postgres_tenant_context` (`src/middleware/tenant_context.py`) sets the value with `SELECT set_config('app.current_org_id', :organization_id, true)` for the current transaction, and does nothing on a non-PostgreSQL dialect. Only `get_tenant_context`, a request dependency that shares the request's session, calls it. Sessions opened from `get_session_factory()` by the durable execution service, the outbox relay, and the run event streams do not set the value. The enforcement those code paths rely on is the repository filter described next.

**Repository filter.**

- `BaseRepository.apply_organization_scope(query, organization_id)` adds `organization_id = :id` to a select or update. It fails closed in both directions: a model with an `organization_id` column accessed with `organization_id=None` raises `ValueError` unless the repository sets `allows_unscoped_access = True` (only `UserRepository` does); a model without the column accessed with an organization raises `ValueError`.
- `scope_to_organization(query, column, organization_id)` (`src/repositories/tenant_scope.py`) is the read filter for repositories that do not extend `BaseRepository`. A `None` organization returns the query unchanged, which lets the startup recovery scan read every tenant.
- `normalize_organization_id(value)` returns a `UUID` and raises `MissingOrganizationContextError` for `None`. Every write path calls it.
- `enforce_tenant_identity(tenant_id=..., organization_id=...)` raises `TenantMismatchError` when a contract's `tenant_id` is not a UUID or differs from the organization context.
- `get_run_organization_id(session, run_id)` returns the organization that owns a run, or `None`. Child-row repositories (events, evidence, claim supports, artifacts, tool invocations) compare it with the caller's organization before writing.

Authentication, token claims, and the HTTP dependencies that supply the organization are covered in [authentication and tenancy](04-authentication-and-tenancy.md).

## Repositories

`BaseRepository[ModelType]` (`src/repositories/base.py`) is generic CRUD over a `BaseModel` subclass. It is constructed with `BaseRepository(model, session)`, and its methods flush but never commit.

| Method | Key parameters |
|---|---|
| `create(**kwargs)` | flushes and refreshes the new entity |
| `get(id, include_deleted=False, load_relationships=None, organization_id=None)` | live rows only unless `include_deleted` |
| `get_many(filters=None, limit=None, offset=None, order_by=None, order_desc=False, include_deleted=False, load_relationships=None, organization_id=None)` | list values become `IN`, `None` becomes `IS NULL`; default order is `created_at` descending |
| `update(id, data, updated_by=None, organization_id=None)` | ignores `id` and `created_at` keys |
| `delete(id, soft=True, deleted_by=None, organization_id=None)` | soft delete sets `deleted_at`; `soft=False` removes the row |
| `hard_delete(id)` | calls `delete(id, soft=False)` with no organization |
| `restore(id, restored_by=None)` | clears `deleted_at`; takes no organization |
| `exists(id=None, filters=None, include_deleted=False, organization_id=None)` | |
| `count(filters=None, include_deleted=False, organization_id=None)` | |
| `bulk_create(entities)` | one flush for the batch |
| `bulk_update(updates, updated_by=None, organization_id=None)` | one `SELECT` for the live rows in scope, then attribute updates in one flush |
| `bulk_delete(ids, soft=True, deleted_by=None, organization_id=None)` | soft delete is one `UPDATE`; hard delete goes through the ORM so cascades run |
| `search(search_term, search_fields, filters=None, limit=None, offset=None, organization_id=None)` | case-insensitive `contains` across the fields |
| `build_query()` | `select(model)` excluding soft-deleted rows |
| `apply_organization_scope(query, organization_id)` | described above |

`hard_delete` and `restore` pass no organization, so on a repository whose model has `organization_id` and which does not set `allows_unscoped_access`, they raise the `ValueError` from `apply_organization_scope`.

Repository modules:

| Class | Model | Base | Notable methods |
|---|---|---|---|
| `UserRepository` | `User` | `BaseRepository`, `allows_unscoped_access = True` | `get_by_email`, `get_by_username`, `create_with_password`, `verify_credentials`, `update_password`, `update_last_login`, `toggle_active`, `verify_email`, `search_users`, `get_user_statistics` |
| `ResearchRepository` | `ResearchProject` | `BaseRepository` | `get_by_user`, `get_for_user`, `get_in_progress`, `update_status`, `update_quality_score`, `get_with_results`, `search_projects`, `get_statistics` |
| `ResultRepository` | `ResearchResult` | `BaseRepository` | `get_by_project`, `get_by_agent`, `bulk_create`, `get_high_confidence`, `aggregate_by_type`, `search_content`, `get_citations`, `get_sources`, `merge_duplicates`, `get_statistics`, `update_confidence_scores` |
| `CheckpointRepository` | `WorkflowCheckpoint` | `BaseRepository` | `create_checkpoint`, `get_latest`, `get_by_phase`, `get_recovery_point`, `list_checkpoints`, `restore_from_checkpoint`, `mark_error_checkpoint`, `get_checkpoint_statistics`, `cleanup_old`, `cleanup_expired` |
| `ReportRepository` | `GeneratedReport` | `BaseRepository` | `create_report`, `get_by_workflow_id`, `get_by_project_id`, `get_by_user_id`, `search_reports`, `mark_report_completed`, `update_report_status`, `increment_access_count`, `cleanup_old_reports`, `get_report_with_formats`, `delete_report` |
| `ReportFormatRepository` | `ReportFormat` | `BaseRepository` | `create_format`, `get_by_report_and_format`, `get_formats_for_report`, `verify_format_integrity`, `cleanup_orphaned_formats` |
| `RunLifecycleRepository` | `AgentRun`, `AgentRunTask`, `AgentTaskAttempt` | session only | `create_run`, `record_run_transition`, `get_run`, `list_non_terminal_runs`, `create_task`, `record_task_transition`, `get_tasks_for_run`, `create_attempt`, `record_attempt_transition`, `write_journaled_result`, `get_attempts_for_task` |
| `RunConfigSnapshotRepository` | `AgentRunConfigSnapshot` | session only | `create_snapshot`, `get_by_run`, `find_by_authority`, `list_all`; module function `hash_configuration` |
| `RunEventRepository` | `AgentRunEvent`, `AgentRunEventOutbox` | session only | `append_event`, `read_events_after`, `read_from_cursor` |
| `CapabilityRepository` | `AgentCapabilityGrant`, `AgentCapabilityApproval` | session only | `create_grant`, `get_grant`, `list_grants_for_task`, `create_approval`, `list_approvals_for_grant` |
| `ToolInvocationRepository` | `AgentToolInvocation` | session only | `create_tool_invocation`, `record_transition`, `get_tool_invocation`, `list_tool_invocations_for_attempt` |
| `ArtifactRepository` | `AgentArtifact` | session only | `create_artifact`, `get_artifact`, `list_artifacts_for_run`, `record_status_transition` |
| `EvidenceRepository` | `AgentEvidence` | session only | `record_evidence`, `get_evidence`, `list_evidence_for_run`; raises `EvidenceDigestMismatchError` |
| `ClaimSupportRepository` | `AgentClaimSupport` | session only | `record_claim_support`, `get_claim_support`, `list_claim_supports_for_claim` |

The "session only" repositories take an `AsyncSession` in the constructor and `organization_id` on every method. They write through the Pydantic contracts in `src/core/contracts/` (`Run`, `Task`, `Attempt`, `ToolInvocation`, `Artifact`, `Evidence`, `ClaimSupport`) and never re-implement the state machines. No module under `src/repositories/` exists for `audit_logs`, `oauth_accounts`, `security_alerts`, or the experiment tables. `src/repositories/__init__.py` re-exports `BaseRepository`, `CheckpointRepository`, `ResearchRepository`, `ResultRepository`, `RunConfigSnapshotRepository`, `RunEventRepository`, `RunLifecycleRepository`, `UserRepository`, `hash_configuration`, and the tenant helpers; the remaining repositories are imported from their own modules.

## Migrations

Alembic scripts are in `alembic/versions/`; `alembic/env.py` reads `DATABASE_URL` (rewriting `postgres://` and `postgresql://` to `postgresql+asyncpg://`), then the `sqlalchemy.url` option of `alembic.ini`, then a localhost development URL. The revision chain, oldest first, has a single head, `d9a3c5e7b1f4`:

| Revision | Purpose |
|---|---|
| `4141aabbf97d` | initial schema: `users`, `api_keys`, `research_projects`, `agent_tasks`, `research_results`, `workflow_checkpoints` |
| `5bf6a31d1957` | `password_history`, `user_sessions`, `audit_logs`, `oauth_accounts`, `mfa_settings`, `security_alerts` |
| `e1c02ed69b45` | the five experiment tables |
| `9d8c7b6a5e4f` | nullable `organization_id` on `users`, `research_projects`, `agent_tasks`, with composite indexes |
| `b7a9c2d4e8f1` | row-level security policies on `research_projects` and `agent_tasks` |
| `c3f5a1d7b209` | `agent_runs`, `agent_run_tasks`, `agent_task_attempts`, `agent_run_config_snapshots`, `agent_run_events`, `agent_run_event_outbox`; `workflow_checkpoints.run_id`; append-only triggers and row-level security |
| `5d04cec6c232` | `agent_capability_grants`, `agent_capability_approvals`, `agent_artifacts`, `agent_tool_invocations`, `agent_evidence`, `agent_claim_supports`; append-only triggers and row-level security |
| `7b1e4c9d2a08` | replaces the unconditional unique constraint on `agent_tool_invocations (attempt_id, idempotency_key)` with a partial unique index that excludes `denied` rows, plus a plain lookup index |
| `c4a8d1e2f307` | `generated_reports` and `report_formats` |
| `f8c9d0e1a2b3` | drops `mfa_settings`, `user_sessions`, `password_history`, `api_keys` |
| `a1b2c3d4e5f6` | `lease_owner_id` and `lease_expires_at` on `agent_tool_invocations`, and a partial index on pending statuses |
| `d2e6f0a4b8c1` | drops `agent_tasks` and its policy |
| `b5d7e9f1a3c2` | case-insensitive unique indexes on `users` |
| `f3b8a6d2c4e1` | aligns the experiment tables with their models (NOT NULL columns, timezone-aware timestamps, `deleted_at` indexes) |
| `d9a3c5e7b1f4` | report filter indexes |

Migrations that create row-level security or triggers do so inside an `_is_postgresql()` guard; on another dialect they create tables and indexes only.

`docker/entrypoint.sh` runs `alembic upgrade head` before starting the application. If the upgrade fails it prints the exit status and exits with it, so the container does not start; with no application command it exits with status 64. The `api` service in `docker-compose.yml` uses this entrypoint. The `mcp-server` service sets `entrypoint: []` and runs no migration.

Commands (the `Makefile` targets run them inside the `api` container):

```bash
make db-init                                  # alembic upgrade head
make db-migrate                               # prompts for a message, then alembic revision --autogenerate
alembic upgrade head                          # apply every pending revision
alembic revision --autogenerate -m "message"  # create a revision from model changes
alembic heads                                 # list head revisions; one is expected
```

## SQLite and PostgreSQL differences

When `settings.DATABASE_URL` contains `sqlite`, the API lifespan calls `Base.metadata.create_all` for nine tables: `research_projects`, `research_results`, `workflow_checkpoints`, `agent_runs`, `agent_run_tasks`, `agent_task_attempts`, `agent_run_config_snapshots`, `agent_run_events`, and `agent_run_event_outbox`. No other table is created at startup, and migrations are not run by this path. On SQLite:

- there are no row-level security policies and no append-only triggers; the ORM guard in `append_only.py` still applies;
- the engine uses `NullPool`;
- `PortableUUID` columns are `CHAR(36)` and the outbox primary key is `Integer`;
- `set_postgres_tenant_context` is skipped, and the repository filter is the only tenant enforcement;
- the partial unique index on `agent_tool_invocations` declares `sqlite_where` as well as `postgresql_where`.

## Experiment tables

> **Not wired.** The tables `experiments`, `experiment_variants`, `experiment_assignments`, `experiment_results`, and `experiment_analyses` and their models exist, and migrations create them. No module under `src/` other than the `src/models/db/__init__.py` re-export imports `src/models/db/experiment.py`, so no repository and no route reads or writes them. See [not wired](15-not-wired.md).

## Source map

- `src/models/db/base.py` — `Base`, `BaseModel`, `PortableUUID`
- `src/models/db/session.py` — engine, session factory, `get_session`, unused helpers
- `src/models/db/append_only.py` — `register_append_only`, `AppendOnlyViolationError`
- `src/models/db/provenance_columns.py` — shared prompt-binding columns and CHECK builders
- `src/models/db/user.py`, `oauth_account.py`, `audit_log.py`, `security_alert.py` — account and audit tables
- `src/models/db/research_project.py`, `research_result.py`, `workflow_checkpoint.py` — research tables
- `src/models/db/generated_report.py` — report tables
- `src/models/db/run_lifecycle.py`, `run_config_snapshot.py`, `run_event.py` — durable run tables
- `src/models/db/capability.py`, `tool_invocation.py`, `artifact.py`, `evidence.py`, `claim_support.py` — tool, evidence, and claim tables
- `src/models/db/experiment.py` — experiment tables
- `src/repositories/base.py` — `BaseRepository`
- `src/repositories/tenant_scope.py` — tenant filter and identity helpers
- `src/repositories/*_repository.py` — one module per repository class
- `src/middleware/tenant_context.py` — `TenantContext`, `set_postgres_tenant_context`
- `alembic/env.py`, `alembic/versions/` — migration environment and revisions
- `docker/entrypoint.sh` — migration-then-start entrypoint
- `src/api/main.py` — `lifespan` (`init_db`, SQLite table creation)
