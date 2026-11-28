# Tool boundary, capabilities and evidence

Every tool call made by an agent goes through one mediator, `ToolBoundary`, which validates the input, authorizes it against capability grants, redacts, applies a deadline and records the call. Around it sit the capability grants issued at run admission, a per-run budget ledger, source snapshots and evidence rows and claim records for the final report.

## Summary

| Feature | Status | Entry point | Source |
|---|---|---|---|
| Tool boundary (validation, authorization, redaction, deadline, circuit breaker, idempotency, audit) | Active | `ToolRegistry.execute` | `src/core/tools/boundary.py` (`ToolBoundary`) |
| Capability grants issued at admission and stored | Active | `DirectExecutionService._admit_run` | `src/core/capabilities/issuer.py` (`PlanCapabilityIssuer`) |
| Self-issued grants for callers without a durable identity | Active | `ToolRegistry.execute`, `MCPIntegration` | `src/agents/tools/mediation.py` (`self_issued_grant`) |
| Capability approvals | Not wired | none | `src/repositories/capability_repository.py` (`create_approval`) |
| Budget ledger | Active | one ledger per execution | `src/core/budget.py` (`BudgetLedger`) |
| Local tool `arithmetic` on `DataAnalysisAgent` | Active | `LLMWorkerAgentBase._execute_model_tool_call` | `src/agents/tools/registry.py` (`ToolRegistry`) |
| Local tools `datetime_info` and `unit_conversion` | Not wired | none | `src/agents/tools/registry.py` (`create_default_registry`) |
| `finance_math` called directly by finance agents | Active | `FinancialAnalysisAgent`, `ValuationAgent`, `FinancialCalculatorAgent` | `src/agents/tools/finance_math.py` |
| Prompt identity verification | Not wired | none | `src/core/tools/prompts.py` (`PromptIdentityVerifier`) |
| Durable tool audit (`agent_tool_invocations`) | Active | `SessionToolAuditStore` | `src/agents/tools/durable_audit.py` |
| MCP tool path for plan workers | Active | `MCP_TOOL_PATH_ENABLED` (default `true`) | `src/ai_brain/integration/masr_supervisor_bridge.py` (`_execute_plan_worker`) |
| Search evidence and source snapshots | Active | `SearchEvidenceRecorder` | `src/core/acquisition/search_evidence.py` |
| Source fetch tool and `acquire` | Not wired | none | `src/core/acquisition/fetch_tool.py`, `service.py` |
| PDF text extraction | Active | document upload ingestion, through the `extract_child.py` subprocess | `src/core/acquisition/pdf_text.py`, `extract_child.py` |
| Final-report artifact and claim records | Active | `_persist_transition` on `succeeded` | `src/core/claims/resolution.py`, `recording.py` |
| Citation validators | Not wired | none | `src/core/claims/citation_binding.py` |

## Tool boundary

`ToolBoundary.invoke` is the only mediated path for a tool call. It performs these steps in this order, and the order is what makes the boundary safe: a denial is decided before the input schema is reported, and persistence precedes publication.

1. Read the clock once (`now`). `invoke` has no `now` parameter, and a naive clock raises `ValueError`.
2. Look up the `ToolSpec` (`ToolNotRegisteredError` for an unknown name).
3. Verify any supplied `RenderedPrompt` (see below).
4. Prepare the input: validate against the tool's input model, serialize, redact and digest it. This step persists, publishes and returns nothing. A schema failure is carried forward as data.
5. Derive the idempotency key, unless the caller supplied one: a digest over tool name and version, run, task, attempt, capability scope, the input digest and, when present, the worker scope (see below).
6. Decide capability (`decide_capability`). A denial is persisted and returned as `denied` before anything else happens, and takes precedence over a schema rejection, so a caller without a grant cannot learn the input schema from the error detail.
7. Look up an existing record under the idempotency key. A terminal record (`succeeded`, `failed`, `cancelled`, `timed_out`) of the same request is replayed; `denied` records are never replayed. A pending record returns `in_progress`. A key that names a different tool, version, scope or input raises `IdempotencyConflictError`.
8. If step 4 rejected the input, record `invalid_input`.
9. Check the circuit breaker; an open breaker records `circuit_open`.
10. Reserve the invocation and persist the `requested` record with its event, then publish. Persistence precedes publication, and a failed write publishes nothing.
11. Run the handler under the tool's deadline, racing it against the caller's `CancellationToken` and, for durable stores, a lease heartbeat.
12. Validate the output against the output model (`invalid_output` on failure), redact it and persist and publish the terminal record.

`ToolOutcome` has one success state and separate failure states, none of which carries a body: `succeeded`, `in_progress`, `denied`, `invalid_input`, `invalid_output`, `timed_out`, `cancelled`, `circuit_open`, `failed`. Only the boundary can construct one. `error_code` values are `capability_denied`, `invalid_input`, `invalid_output`, `timed_out`, `cancelled`, `circuit_open` and `tool_error`. `timed_out` and `circuit_open` are `retriable`; every other failure is `terminal` unless the tool's `classify_error` says otherwise. `ToolOutcome.unwrap()` raises `ToolOutcomeNotSuccessfulError` for a non-success outcome.

**Specification checks.** `ToolSpec` (`spec.py`) requires a name, a version, a positive `timeout_seconds`, a `SensitivityClass` and input and output models. At construction it rejects an input model with `extra="allow"` (an undeclared field would reach the handler without appearing in the record or digest) and any credential-named field (by attribute name or alias) not typed `SecretRef`. Registering a different version under an existing name raises `ToolSpecError`.

**Credentials.** A `SecretRef` field carries a handle, never a value. The handler resolves it with `ToolCallContext.resolve_secret` inside the call, after hashing, persistence and publication. The application's boundaries use `NullSecretProvider`, which holds no values; `MappingSecretProvider` and `validate_secret_provider` exist for a provider that does.

**Circuit breaker.** `CircuitBreaker` (`circuit.py`) is process-local, one per tool in a `BreakerRegistry`. It opens after 5 consecutive failures (`DEFAULT_FAILURE_THRESHOLD`) and stays open for 30 seconds (`DEFAULT_COOLDOWN_SECONDS`), then admits one half-open trial call. A failed trial reopens it; a success closes it; a cancelled trial returns the trial slot without counting as a failure.

**Deadline and cancellation.** The handler runs as a task raced against the deadline and the cancellation token. External cancellation of the calling coroutine abandons the handler task and records `cancelled` in a shielded write before re-raising.

**Prompt identity.** A prompt reaches `invoke` only as a `RenderedPrompt`, which verifies on construction that its digests match its template source and text. `PromptIdentityVerifier` then compares the template digest with the run's pinned digest, or with a `PromptRegistry` entry when no pin exists, and refuses when neither can answer. A boundary with no verifier configured refuses any call that supplies a prompt. The boundaries the application builds (`build_tool_boundary`) configure no verifier, and neither `ToolRegistry.execute` nor `MCPIntegration` supplies a prompt (`SourceAcquisitionService.acquire`, which no code calls, forwards one). The configuration snapshot's `PinnedVersions` carry no component pins, so a prompt pin is never present at run time.

> **Not wired.** `PromptIdentityVerifier`, `PromptRegistry` and `PromptRenderer` have no construction site outside `src/core/tools/`, so no production tool call records a prompt binding.

## Capability grants

A `CapabilityGrant` (`src/core/contracts/capabilities.py`) authorizes one tool for one run and task: `tool_name`, `tool_versions`, `capability_scope`, `sensitivity`, `max_input_trust`, `requires_approval`, `issued_at`, `expires_at`. `decide_capability` returns `allow` only when some grant matches on run, task, scope and tool name and satisfies every constraint; otherwise it returns `deny` with the reason from the grant that matched furthest. The reasons are `no_matching_grant`, `tool_version_not_granted`, `sensitivity_mismatch`, `grant_not_yet_valid`, `grant_expired`, `input_trust_exceeds_grant`, `approval_missing`, `approval_unavailable`, `approval_expired` and `approval_fingerprint_mismatch`. Grants for `external_write` and `exfiltration` sensitivities must set `requires_approval`; the model validator rejects any other combination.

**Plan-issued grants.** `PlanCapabilityIssuer.issue` runs inside the admission transaction (`_admit_run`). For each tool in each admitted worker's `tool_allowlist` it creates a grant with scope `plan-issued:<tool>`, `tool_versions=("1.0.0",)`, `sensitivity=read_only`, `max_input_trust=external_untrusted`, `requires_approval=False` and a lifetime of `max_attempts_per_task * task_timeout_seconds` from the plan budget. A worker that lists tools without a `permission_scopes` entry, or a lifetime that outlasts the plan deadline, raises `ValueError`. Each grant is stored in `agent_capability_grants` and also placed on the execution status, from where `_durable_agent_task_context` copies it into every worker task under `CAPABILITY_GRANTS_CONTEXT_KEY`, together with the run, task, attempt and organization ids.

**Self-issued grants.** `ToolRegistry.execute` and `MCPIntegration` fall back to `self_issued_grant` when the caller supplies no grants and its `ToolCallIdentity` is not durable (`ToolCallIdentity.durable` requires a bound identity with run, task, attempt and organization ids). The grant carries scope `self-issued:<tool>`, lasts 5 minutes (`DEFAULT_GRANT_TTL`) and takes its ceilings from a static `SelfIssuedPolicy`, never from the call: `read_only` sensitivity, `user_supplied` as the maximum input trust for local tools and `external_untrusted` for MCP operations. `self_issued_grant` raises for any sensitivity that requires approval. A self-issued grant records that a check ran; it is not authorization by an authority. For a durable identity with no grants supplied, the registry passes an empty list and the boundary denies the call.

> **Not wired.** `agent_capability_approvals` and `CapabilityRepository.create_approval` have no production caller, so no approval is ever stored, and neither `ToolRegistry.execute` nor `MCPIntegration` passes approvals to `ToolBoundary.invoke`. Boundaries built by `build_tool_boundary` decide through `decide_without_approver`: any approval a caller supplies is ignored, and a grant that requires one is denied as `approval_unavailable` before the handler runs.

## Budget ledger

`BudgetLedger` (`src/core/budget.py`) enforces the plan's `ExecutionBudget` across all tasks and retries of one run. `DirectExecutionService` creates one per execution (`BudgetLedger(execution_plan.routing_decision.budget)`) and passes it to workers under `BUDGET_LEDGER_CONTEXT_KEY`. Three dimensions: tokens, cost and tool invocations.

- `reserve_call(estimated_tokens, estimated_cost)` runs before each model call, inside `invoke_model` (`src/services/model_invocation.py`, see [model invocation contract](16-model-invocation-contract.md)); `settle(reservation, usage)` replaces the reservation with provider-reported usage, once. `release(reservation)` returns a reservation whose call provably never left. Unknown usage settles as the full reservation and fails the ledger closed on cost for every later call.
- `charge_tool_invocation()` is called by `ToolRegistry.execute` and `MCPIntegration` before the boundary is entered.
- A crossed ceiling raises `BudgetExceededError` (`code="BUDGET_EXCEEDED"`), which is terminal. The execution service records it as `run.budget_exceeded` with `usage_payload()`.
- `check_plan_fits` refuses a plan whose token ceiling cannot hold `MIN_WORKER_CALL_TOKENS` (2000) per worker; `ExecutionPlanTopologyExecutor.admit` calls it.

## Local tools

`LLMWorkerAgentBase._get_tool_registry` builds one `ToolRegistry` per worker and calls the worker's `_register_tools`. When the registry is not empty, `execute` appends the tool list and call instructions to the prompt; a model reply that is a tool call goes to `_execute_model_tool_call`, which refuses a tool outside the worker's `tool_allowlist` config, then calls `registry.execute` with the task's identity, grants and budget ledger. One tool call is allowed per task; a second call in the follow-up reply fails with `tool_loop_limit`.

`ToolRegistry.register` derives a `ToolSpec` from an `AgentTool` (`read_only` sensitivity, the tool's `params_model`, a permissive output wrapper and the tool's `timeout_seconds`, default 5 seconds). `execute` returns a `ToolResult` and does not raise for failures.

| Tool | Name | Registered by | Status |
|---|---|---|---|
| `ArithmeticTool` | `arithmetic` | `DataAnalysisAgent._register_tools` | Active |
| `DatetimeTool` | `datetime_info` | `create_default_registry` | Not wired |
| `UnitConversionTool` | `unit_conversion` | `create_default_registry` | Not wired |

`create_default_registry` has no caller under `src/`. `src/agents/tools/finance_math.py` holds pure functions (`dcf_gordon_growth`, `npv`, `financial_ratios`, `compound_interest`, `loan_amortization`, `descriptive_stats`, `calculate`). `FinancialAnalysisAgent` and `ValuationAgent` call them in `_precompute`, and `FinancialCalculatorAgent` (agent type `financial_calculator`) dispatches on `parameters.operation`; none of these calls goes through the boundary.

## Durable tool audit

> **On by default.** Tool invocations are written to `agent_tool_invocations` and `agent_run_events` on the MCP tool path (`MCP_TOOL_PATH_ENABLED`, default `true`), through `SessionToolAuditStore`. Registries and integrations built without an explicit store keep records in `InMemoryToolAuditStore`, which lives for the process and is not durable.

`agent_tool_invocations` columns of note:

- identity: `tool_invocation_id` (unique), `run_id`, `task_id`, `attempt_id` (each a foreign key), `organization_id`;
- request: `tool_name`, `tool_version`, `capability_scope`, `idempotency_key`, redacted `input` with `input_sha256` and `input_trust`;
- result: `status` (`requested`, `running`, `succeeded`, `failed`, `cancelled`, `timed_out`, `denied`), redacted `output` with `output_sha256` and `output_trust`, `error_code`, `status_reason`, `requested_at`, `completed_at`;
- decision: `capability_decision_effect`, `capability_grant_id`, `capability_approval_id`, `capability_denial_reason`, `request_fingerprint`;
- lease: `lease_owner_id`, `lease_expires_at`;
- prompt binding: `producer_kind` (no default) and the four prompt digest columns, all set together or all null.

**Worker scope.** Every worker of an execution plan runs under the run's one durable root task and attempt, so run, task and attempt alone cannot tell two workers' identical calls apart. `ToolBoundary.invoke` therefore takes an optional `worker_scope`, and `_derive_idempotency_key` digests a JSON object that gains the key `"worker_scope"` only when it is set (keys derived without a scope are unchanged). The scope is the string `<execution_plan_id>:<worker_id>`. `ExecutionPlanTopologyExecutor` writes it into each worker task's context under `WORKER_SCOPE_CONTEXT_KEY` (`"worker_scope"`, `src/agents/tools/mediation.py`), overwriting any value inherited from the parent context; `ToolCallIdentity.from_agent_task` reads it into `ToolCallIdentity.worker_scope`, and `ToolRegistry.execute` and `MCPIntegration` pass it to the boundary. It never comes from tool arguments or request input. Two workers calling one tool with identical arguments get independent invocations; a repeat by the same worker under the same plan replays its recorded result; a call under a different plan id is a different invocation. The scope is persisted only through the idempotency key digest, which is how a consumer that needs a per-worker call identity (a model-call journal, for instance) should derive its own key: reuse `ToolCallIdentity.worker_scope` as the extra component beside run, task and attempt. A caller-supplied `idempotency_key` bypasses derivation and carries no scope.

Idempotency is enforced by the partial unique index `uq_agent_tool_invocation_idempotency` on `(attempt_id, idempotency_key)` where `status <> 'denied'`, so refusals do not occupy a key. CHECK constraints tie the decision fields together: a success requires an `allow` decision, an `allow` names its grant, a `deny` carries a reason and a row with no decision must have `error_code = 'invalid_input'`.

`SessionToolAuditStore.persist` writes one invocation transition and its events in one transaction, requires the run, task, attempt and organization to exist as a consistent parent chain, and rolls back and re-raises on any failure, with no in-memory fallback. A lost insert race on the idempotency index raises `ToolInvocationConflictError` carrying the winning row. A `requested` row holds a lease; the boundary renews it with a heartbeat while the handler runs. A pending row whose lease expired is terminalized as `failed` with `error_code` `unknown_after_lease_expired` and is never re-executed. Tool events (`tool.invocation.requested`, `tool.invocation.completed`) go to the run event log with no outbox destinations; see [durable runs and events](08-durable-runs-and-events.md).

## MCP tool path

> **On by default.** With `MCP_TOOL_PATH_ENABLED` at its default of `true`, plan workers receive an `MCPIntegration` whose four tools run through the boundary with durable audit. Set `MCP_TOOL_PATH_ENABLED=false` to disable it. The bridge needs an explicit audit store or a session factory, and `_execute_plan_worker` raises `RuntimeError` when the path is enabled without either; `DirectExecutionService` passes its `session_factory`, which the API lifespan sets only when the database initialized.

When enabled (the default), `MASRSupervisorBridge._execute_plan_worker` builds, per plan worker, an `MCPIntegration` with `enable_fallback=False`, the worker's grants filtered to its `tool_allowlist`, the task's identity and the run's budget ledger. The audit store is the configured one or `SessionToolAuditStore(session_factory)`.

`MCPIntegration` registers four tools, each version `1.0.0`, `read_only`, with a 45-second default deadline (`tool_timeout_seconds` in its config):

| Boundary tool | Method | In-process MCP tool |
|---|---|---|
| `mcp.academic_search` | `search_academic_sources` | `search_academic` |
| `mcp.format_citations` | `format_citations` | `citation_formatter` |
| `mcp.analyze_statistics` | `analyze_statistics` | `statistics_analyzer` |
| `mcp.build_knowledge_graph` | `build_knowledge_graph` | `knowledge_graph` |

Output trust is declared `external_untrusted`. A failed call returns `success=false`, `degraded=true` and the outcome's `error_code` and `retry`; with `enable_fallback=False` the stand-in payload is empty rather than invented. The default input trust is `user_supplied`.

`MCPIntegration` dispatches directly to `AcademicSearchTool`, `CitationTool`, `StatisticsTool` and `KnowledgeGraphTool` through `ResearchToolSet` (constructed lazily inside the boundary handlers). No standalone MCP server process or compose service exists.

Bridge construction: `DirectExecutionService` creates its bridge with its `session_factory`, so the path has a durable store there. `TalkHierSessionService`, `MASRRoutingService` and `SupervisorCoordinationService` construct `MASRSupervisorBridge(component_registry=...)` with neither a session factory nor an audit store; they never call `execute_execution_plan`, the method that reaches `_execute_plan_worker`. `AgentFactory.resolve_agent` also builds an `MCPIntegration` when an agent config contains `mcp.enabled`, using an in-memory store; no code under `src/` sets that key.

## Source acquisition and snapshots

> **On by default.** With the MCP tool path enabled (the default) and a durable audit store, every successful `mcp.academic_search` call records each result that has an abstract and a valid source URL or DOI as evidence. Snapshots are written under `SOURCE_SNAPSHOT_DIR` (default `data/source_snapshots`, resolved to an absolute path at startup; the `api` compose service mounts the `source_snapshots` volume at `/var/lib/cerebro/source_snapshots`).

Flow for one search result (`SearchEvidenceRecorder` to `SourceAcquisitionService.record_retrieved_source`):

1. The record's text is its title and abstract; a record with no abstract, or with neither a valid `http` or `https` URL nor a DOI (recorded as `https://doi.org/<doi>`), is skipped and counted. URLs with userinfo or over 2048 characters are refused.
2. `FilesystemSnapshotStore.put` stores the UTF-8 bytes at `<root>/<first two hex digits>/<sha256>` with mode `0444`, content-addressed and write-once. `get` refuses a `storage_uri` outside the root and re-checks the digest.
3. An `agent_artifacts` row of kind `source_snapshot` records `storage_uri`, `content_sha256`, `source_type` and the producing tool invocation.
4. An `agent_evidence` row cites the whole snapshot with locator `bytes:0-<length>`, `producer_kind=system` and `producer_tool_invocation_id`. Evidence and artifact ids derive from the invocation, the source URI and the content digest, so replaying an invocation records nothing twice.
5. The trust label comes from `trust_for_source(SourceType.SEARCH_RESULT)`, which is `external_untrusted`. No acquisition parameter lets a caller set it; the table covers `web_page`, `academic_paper`, `search_result` and `api_response` (`external_untrusted`) and `uploaded_document` (`user_supplied`), and import fails if any source type is unmapped or maps into the trusted tier.

Each source commits in its own session; a failed write is reported against its URI in the returned summary (`recorded`, `already_recorded`, `skipped`, `failed`) and does not stop the others. The recorder is not given a `SecretProvider`, so it uses `NullSecretProvider`.

Locators are `bytes`, `char` or `line` spans, optionally followed by annotation segments (`xpath`, `css`, `jsonptr`, `page`, `sheet`, `heading`); only the span is authoritative. `resolve(locator, snapshot_bytes)` returns the cited bytes and raises `UnresolvableLocatorError` for an out-of-range span rather than truncating.

> **Not wired.** The `source.fetch` tool (`snapshot_fetch_tool`), `SourceAcquisitionService.acquire` and `read_excerpt`, and `build_extracted_text_artifact` have no caller outside their own modules. `extract_pdf_text` is live: `src/core/acquisition/extract_child.py` calls it in the sandboxed subprocess that `src/services/document_ingestion.py` launches for uploaded PDFs. Only `record_retrieved_source` and `locate` are reached, through `SearchEvidenceRecorder`.

## Claims and provenance

The final-report path is active on the default configuration. When a run's terminal transition is `succeeded`, `_persist_transition` builds an `Artifact` of kind `research_report` from the run's `final_output`: canonical JSON, `media_type` `application/json`, `status=final`, `trust=derived_untrusted`, `producer_kind=system`, `storage_uri` `memory://artifacts/<artifact_id>`, with the full text in `metadata.content`. The artifact id is a SHA-256 over tenant, run, task, attempt and kind. In the same transaction as the status change:

1. `ArtifactRepository.create_artifact` stores it in `agent_artifacts`.
2. `build_inventory` segments the text and classifies each segment as a material claim or an excluded segment (`no_alphabetic_content`, `interrogative`, `below_minimum_assertion_length` for fewer than `MINIMUM_ASSERTION_WORDS` = 4 words). An artifact kind in neither `DELIVERABLE_ARTIFACT_KINDS` nor `NON_DELIVERABLE_ARTIFACT_KINDS` raises `UnclassifiedArtifactKindError`.
3. `ClaimSupportResolver.resolve` is called with no verdicts, so every material claim is recorded as `unsupported` with `absent_evidence_reason=not_attempted`, `producer_kind=system`, evaluator `claim-support-resolver` and no evidence ids.
4. `ClaimSupportRecorder.record` writes one `agent_claim_supports` row per claim.

No evaluator produces verdicts in the application, so no claim in a completed run's report is recorded with another status. `ClaimSupportResolution` validates on construction that it holds exactly one verdict per inventory claim, and `unsupported_ratio` is `None` rather than `0.0` when the inventory is empty. `_validate_persisted_final_report` re-checks the stored row against the report built for the transition, and `_terminal_transition_already_committed` uses the same check to recognize an earlier commit.

`agent_claim_supports` statuses are `supported`, `partially_supported`, `disputed` and `unsupported`; `absent_evidence_reason` values are `not_attempted`, `no_source_found`, `retrieval_failed`, `source_inaccessible` and `capability_denied`. `agent_claim_supports` and `agent_evidence` are append-only. `agent_artifacts` and `agent_tool_invocations` share `OptionalPromptBindingMixin`, and `agent_evidence` and `agent_claim_supports` declare the same nullable prompt columns directly; on all four tables CHECK constraints require the four prompt digest columns together and tie `producer_kind` (`model_turn` or `system`) to their presence.

Trust labels (`TrustClassification`) are ordered `trusted_control`, `application`, `user_supplied`, `external_untrusted`, `derived_untrusted`. `propagate_trust` returns the least-trusted input's label when that label is `trusted_control` or `application`, and `derived_untrusted` otherwise. `redact` (`src/core/contracts/redaction.py`) applies three layers to boundary payloads: `SecretRef` indirection, exact held values (at least 8 characters, `MIN_REDACTABLE_SECRET_LENGTH`) and credential-shaped key names and value patterns, replacing matches with `[REDACTED]`. `boundary_digest` hashes the redacted canonical JSON; `snapshot_digest` hashes verbatim snapshot bytes.

## Citation validators

> **Not wired.** `src/core/claims/citation_binding.py`, `citation_prose.py`, `citation_render.py`, `citation_validation.py`, and `src/core/contracts/citation.py` have no importer under `src/` outside their own group; only tests exercise them. (`src/core/acquisition/pdf_text.py` is imported by `extract_child.py`, the document ingestion service, the document repository and the critique panel.) See [not wired](15-not-wired.md).

## Research tools

The four tool classes in `src/mcp/tools/` are `AcademicSearchTool`, `CitationTool`, `StatisticsTool` and `KnowledgeGraphTool`. `src/agents/integrations/mcp_tool_specs.py` binds each one to its boundary `ToolSpec` (`mcp.academic_search`, `mcp.format_citations`, `mcp.analyze_statistics`, `mcp.build_knowledge_graph`), enforcing closed input schemas, capability checks, deadlines, redaction, and audit logging.

## MCP server

When `MCP_SERVER_ENABLED=true` (default `false`), `src/api/main.py` mounts a stateless Streamable HTTP FastMCP server (`src/mcp/server.py::build_mcp_server`) at `/mcp`. `McpOriginMiddleware` rejects requests carrying a foreign `Origin` header with `403` (allowing absent `Origin`, loopback origins, and configured `CORS_ORIGINS`), and requests inherit `AuthMiddleware`, `RateLimitMiddleware`, `AuditTrailMiddleware`, CORS, and security headers. Every tool call resolves caller identity from `request.state.token_payload` (failing closed outside HTTP, when `organization_id` is absent, or when a PAT lacks the tool's required scope) and writes an audit event with action `mcp.tool:<name>`.

Read-only tools (PAT scope `mcp:read`):

- `get_research_status(execution_id)` — returns the same status payload as `GET /api/v1/query/execution/{execution_id}/status` (including `run_id`).
- `get_research_results(execution_id)` — returns `run_id`, results with externally sourced items labelled `external_untrusted`, and per-claim verdict summaries from `agent_claim_supports` (or an explicit `unverified` marker when none exist).
- `get_research_evidence(run_id, cursor, limit)` — calls the same `list_run_evidence` service as `GET /api/v1/runs/{run_id}/evidence` (metadata only: source URI, locator, SHA-256, trust label; keyset pagination on `(acquired_at, id)`, default 100, cap 500; foreign or unknown run returns not found).

Execution tool (PAT scope `mcp:start`):

- `start_research(template_authority_id, template_authority_version, request_id, query, domains)` — validates a closed input schema with no `context` field, deterministically mints a single-run execution authority from an operator-registered org template via `PersistedExecutionAuthorityResolver.mint_authority_from_template`, and starts or replays the run through the shared `start_intelligent_research` path. Returns `{execution_id, run_id, status, replayed}`, enforces replay parameter conflict detection (`RUN_PARAMS_CONFLICT`), writes a `DATA_MODIFIED` audit event with PAT identity and `query_sha256` (never raw query text), and declares annotations `readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True`.

## Source map

- `src/core/tools/boundary.py`: `ToolBoundary`, `CancellationToken`
- `src/core/tools/spec.py`, `outcome.py`, `errors.py`: specifications, outcomes, boundary exceptions
- `src/core/tools/circuit.py`: `CircuitBreaker`, `BreakerRegistry`
- `src/core/tools/secrets.py`: secret providers
- `src/core/tools/prompts.py`, `src/core/contracts/pinning.py`: prompt identity and version pins
- `src/core/tools/audit.py`: audit store protocols and `ToolAuditEvent`
- `src/core/contracts/capabilities.py`: grants, requests, decisions
- `src/core/contracts/trust.py`, `redaction.py`, `provenance.py`: trust labels, redaction, provenance records
- `src/core/capabilities/issuer.py`: `PlanCapabilityIssuer`
- `src/core/budget.py`: `BudgetLedger`
- `src/agents/tools/registry.py`, `mediation.py`, `durable_audit.py`, `base_tool.py`: local tool registry, self-issued grants, durable store
- `src/agents/tools/arithmetic_tool.py`, `datetime_tool.py`, `unit_conversion_tool.py`, `finance_math.py`: local tools
- `src/agents/integrations/mcp_integration.py`, `mcp_tool_specs.py`: mediated MCP operations
- `src/ai_brain/integration/masr_supervisor_bridge.py`: MCP path wiring
- `src/core/acquisition/`: snapshots, sources, search evidence, resolution, unused fetch and PDF modules
- `src/core/claims/`: materiality, resolution, recording, unused citation validators
- `src/mcp/`: research tools and academic search filters
- `src/repositories/capability_repository.py`, `tool_invocation_repository.py`, `artifact_repository.py`, `evidence_repository.py`, `claim_support_repository.py`: persistence
