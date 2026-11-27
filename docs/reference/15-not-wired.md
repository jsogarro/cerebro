# Code that is not wired

This page lists code and settings present in the repository that no production path calls. Nothing listed here runs in the default configuration or when any setting is changed, unless a row says otherwise. "Production path" means the processes the repository starts: the API (`uvicorn src.api.main:app`) and the `research-cli` script. Other reference pages link here instead of repeating detail.

## Summary

| Feature | Status | Entry point | Source |
|---|---|---|---|
| Multi-tier memory | Not wired | none | `src/ai_brain/memory/` |
| A/B experimentation framework | Not wired | none | `src/ai_brain/experimentation/` (all but the adaptive allocation engine) |
| Learning and self-improvement | Not wired | none | `src/ai_brain/learning/supervision_feedback.py` |
| Model configuration manager | Not wired | none | `src/ai_brain/config/`, `configs/models/` |
| Prompt manager and versioning | Not wired | none | `src/prompts/` |
| Stub packages | Not wired | none | `src/research_platform/` |
| Citation binding | Not wired | none | `src/core/claims/citation_*.py`, `src/core/contracts/citation.py` |
| Orphan modules | Not wired | none | see [Orphan modules](#orphan-modules) |
| Dead execution attributes | Not wired | none | `src/api/services/direct_execution_service.py`, `src/core/config.py` |
| Lease release and typed resume refusal | Not wired | none | `src/repositories/run_lifecycle_repository.py`, `src/api/services/direct_execution_service.py` |
| Settings with no effect | Not wired | none | `src/core/config.py` (`Settings`) |
| Unbacked web pages | Not wired | none | `cerebro/web/src/api/` |
| Row-level security for execution-service sessions | Not wired | none | `src/api/services/direct_execution_service.py` |
| Model-call journal owner | Not wired | none | `src/repositories/model_call_reservation_repository.py`, `src/services/model_invocation.py` |

## Multi-tier memory

> **Not wired.** `MultiTierMemorySystem` composes `WorkingMemoryManager`, `EpisodicMemoryManager`, `SemanticMemoryManager` and `ProceduralMemoryManager`. No production code constructs `MultiTierMemorySystem`, and `app.state.memory_system`, which `get_memory_system` in `src/api/routes/users.py` reads, is never assigned.

Consequences of the missing assignment:

- `MASRouter.episodic_memory`, `MASRouter.procedural_memory` and `LLMWorkerAgentBase.procedural_memory` stay `None`. The memory-informed worker adjustment in `MASRouter` and the procedural-memory prompt context in `LLMWorkerAgentBase` return `None` when their manager is `None`, so `MEMORY_INFORMED_ROUTING_ENABLED=true` changes nothing.
- The memory purge branch of the user-erasure route never runs, because `get_memory_system` always returns `None`.

Modules: `src/ai_brain/memory/multi_tier_memory.py`, `working_memory.py`, `episodic_memory.py`, `semantic_memory.py`, `procedural_memory.py`. The working and episodic managers also use `MemoryEncryption` through its `MEMORY_ENCRYPTION_KEY` fallback; the class itself is live for uploaded documents through `document_cipher()` (see [configuration](02-configuration.md#document-encryption)).

Settings: `WORKING_MEMORY_*`, `EPISODIC_MEMORY_*`, `SEMANTIC_MEMORY_*`, `PROCEDURAL_MEMORY_*`, `MEMORY_ENABLE_CROSS_TIER`, `MEMORY_MAX_RECALL_ITEMS` and `MEMORY_CONSOLIDATION_INTERVAL` are read only by `Settings.get_ai_brain_config`, whose single caller reads only its `providers` entry. `MEMORY_INFORMED_ROUTING_ENABLED` (default `false`), `MEMORY_ROUTING_MAX_WORKER_ADJUST`, `MEMORY_ROUTING_FRESHNESS_DAYS` and `MEMORY_PROMPT_MAX_PROCEDURES` are read, but only the memory paths above use them.

Tests that exercise it: `tests/test_memory_architecture_ttl.py`, `tests/test_context_compaction_telemetry.py`, `tests/test_pii_privacy.py`.

## A/B experimentation framework

> **Not wired.** `statistical/enhanced_statistical_engine.py` (`FrequentistAnalyzer`, `BayesianAnalyzer`, `EnhancedStatisticalEngine`) is imported only by the `eval/` subpackage and tests, apart from `MultiBanditOptimizer` and `BanditAlgorithm`, which `core/adaptive_allocation_engine.py` uses: `MASRouter` builds an `AdaptiveAllocationEngine` only when `ADAPTIVE_ROUTING_ENABLED` is true, and the setting defaults to `false`. See [MASR routing](06-masr-routing.md).

The `eval/` subpackage (`adaptive_routing_eval.py`, `adaptive_routing_promotion.py`) is imported only by `scripts/run_adaptive_routing_eval.py` and `scripts/evaluate_adaptive_routing_promotion.py`.

The five tables `experiments`, `experiment_variants`, `experiment_assignments`, `experiment_results` and `experiment_analyses` are defined in `src/models/db/experiment.py` and created by Alembic migrations. No repository and no route reads or writes them.

Tests that exercise it: `tests/unit/ai_brain/experimentation/test_continuous_beta_update.py`.

## Learning and self-improvement

> **Not wired.** `SupervisionFeedbackLearner` in `src/ai_brain/learning/supervision_feedback.py` records execution feedback and recommends routing strategies. `MASRRoutingService.__init__` assigns one to `self.feedback_learner`, and no code calls a method on it.

`SELF_IMPROVEMENT_REFLECTION_FREQUENCY`, `SELF_IMPROVEMENT_PERFORMANCE_THRESHOLD`, `SELF_IMPROVEMENT_ENABLE_PROMPT_OPTIMIZATION`, `FINE_TUNING_ENABLED`, `FINE_TUNING_MIN_EXAMPLES`, `FINE_TUNING_QUALITY_THRESHOLD` and `FINE_TUNING_AUTO_TRIGGER` are read only by `Settings.get_ai_brain_config`.

## Model configuration manager

> **Not wired.** `ModelConfigManager` in `src/ai_brain/config/model_config_manager.py` loads `configs/models/base.yaml` and an environment file (`development.yaml` or `production.yaml`). It is constructed only by `ConfigurationIntegrationService` (`integration_service.py`) and by the module-level `get_model_config_manager`, and no production code calls either. `MASRouter`, `CostOptimizer` and the providers accept an optional `model_config_manager` argument that production code never passes, so the YAML files are never loaded.

Other modules in the group: `model_schemas.py` is in use: `ModelTier` and `RoutingStrategy` are imported by `MASRRoutingService` and `src/models/masr_api_models.py`. `supervisor_config.py` is imported by `MASRRoutingService`, which stores a `SupervisorConfigurationManager` in `self.config_manager`; nothing reads that attribute.

## Prompt manager and versioning

> **Not wired.** `PromptManager` in `src/prompts/manager.py` loads YAML templates from `src/prompts/templates/` (`agents`, `refinement`, `supervisors`, `workers`). The only production reference is `BaseSupervisor.coordinate_refinement_round`, which calls `get_prompt_manager()` with no configuration. That call raises `ValueError` because nothing initialises the manager, and no production code calls `coordinate_refinement_round`.

`src/prompts/versioning.py` (`PromptVersionManager`) is imported by `UnifiedExperimentManager`, which is itself not wired, and re-exported by `src/prompts/__init__.py`, which loads whenever `src.prompts.manager` is imported. No code constructs or calls it. The prompts the agents send come from Python modules under `src/services/prompts/`, imported by `src/agents/base.py`, `src/agents/literature_review_agent.py`, `src/agents/comparative_analysis_agent.py` and `src/services/gemini_service.py`. Prompt identity pinning is part of the tool boundary; see [tool boundary and evidence](09-tool-boundary-and-evidence.md).

Tests that exercise it: `tests/test_prompt_safety.py`, `tests/test_cache_ttl_expiry.py`.

## Stub packages

> **Not wired.** This package is a script target with no other caller.

| Package | Contents |
|---|---|
| `src/research_platform/` | `main()`, the `research-platform` script target; see [CLI and web clients](13-cli-and-web-clients.md) |

## Citation binding

> **Not wired.** These modules bind claim spans to citations rendered from held metadata and validate citations against a registry corpus. Only their own unit tests import them.

- `src/core/claims/citation_binding.py`, `citation_prose.py`, `citation_render.py`, `citation_validation.py`
- `src/core/contracts/citation.py`

Tests that exercise them live in `tests/unit/core/claims/`. `src/core/acquisition/pdf_text.py` is not in this group: `extract_pdf_text` is live through `extract_child.py` for document uploads; `build_extracted_text_artifact` in the same module has no caller outside tests.

## Evaluation dispatch and results

> **Dark by default; no reader.** With `EVALUATION_DISPATCH_ENABLED` false (the default) no `evaluation` outbox row is written and no handler is registered, so no `agent_evaluation_results` row exists. When it is true, only `ClaimsCoverageEvaluator` is registered. Nothing in production reads the stored rows: `evaluation_states` (`src/core/evaluation/registry.py`) is called only by tests, and `EvaluationResultRepository.list_evaluations_for_run` only by the dispatcher, the handler and tests, and no route or report shows them.

## Orphan modules

> **Not wired.** No production path imports these modules. Tests import most of them.

| Module | What it holds | Imported by |
|---|---|---|
| `src/agents/delegation_contract.py` | `DelegationContract` | `tests/test_fast_path.py` |
| `src/services/evaluation_regression.py` | Golden-dataset regression scoring | `tests/regression/` |
| `src/services/text_alignment/` | Deterministic span alignment over source text | `tests/unit/services/test_text_alignment.py` |

## Dead execution attributes

> **Not wired.** Three names in the execution path and the settings are defined and never read. The live execution path is described in [research execution](05-research-execution.md).

- `DirectExecutionService._fast_path_provider` is initialised to `None`, never assigned a provider and only closed and reset in `close`.
- `Settings.TEMPORAL_HOST` and `Settings.OTEL_EXPORTER_OTLP_ENDPOINT` have no reader under `src/` outside `get_ai_brain_config` (tracked in #297; `config/production.py` and `config/staging.py` read their own copies).
- `Settings.MULTI_DOMAIN_MERGE_STRATEGY` (default `concat`) and `Settings.MULTI_DOMAIN_MERGE_PER_DOMAIN_CHAR_LIMIT` (default 4000) are read by no code under `src/`.

## Lease release and typed resume refusal

> **Not wired.** `RunLifecycleRepository.release_lease` (expires a run's lease at once) has no caller: a process that shuts down leaves its leases to expire, and the expired-run reconciler takes the runs over after at most the lease time. `resume_execution` still answers with a rebuilt status or `None`; it does not return a typed `RESUME_UNSUPPORTED` refusal, because that is a change to the resume route's response contract. The lease time, renewal interval, cancellation poll interval and reconciliation interval are attributes of `DirectExecutionService`, not `Settings`.

## Settings with no effect

95 of the 168 `Settings` fields are declared and read by no code that acts on them, apart from `get_ai_brain_config`. The full list, split from the settings that code does read, is in [configuration](02-configuration.md).

## Unbacked web pages

The web workbench pages for runs, run detail and memory, and the run form on the workflows page, call API routes that are not mounted (`GET /api/v1/workflows` is) and its dashboard, QA, costs and benchmarks pages show fixed values. The page-by-page table is in [CLI and web clients](13-cli-and-web-clients.md).

## Model-call journal owner

> **Not wired.** `ModelCallReservationRepository` has no `JournalOwner` implementation and no production caller, so no `ModelInvocationContext` built in production carries a `journal`, `guard`, `fencing_token` or `trace_parent`. Production research calls are governed by the run's ledger and the plan deadline only (`ExecutionPlanTopologyExecutor` supplies both to every worker), so a crash after dispatch leaves no durable record of the call and `model_call_reservations` stays empty. `DirectExecutionService` does not put a `ModelInvocationContext` in the plan task's context, and it does not reconcile interrupted runs against the journal.

With no `trace_parent` supplied, `invoke_model` opens no provider span even with `LANGFUSE_ENABLED=true`. Contract: [model invocation and journal contract](16-model-invocation-contract.md).

## Source map

- `src/ai_brain/memory/`: the four memory managers and `MultiTierMemorySystem`
- `src/ai_brain/experimentation/`: the adaptive allocation engine `MASRouter` uses, its statistical engine and the offline `eval/` gate
- `src/models/db/experiment.py`: the five experiment tables
- `src/ai_brain/learning/supervision_feedback.py`: `SupervisionFeedbackLearner`
- `src/ai_brain/config/`, `configs/models/`: model configuration manager and YAML files
- `src/prompts/`: `PromptManager`, versioning, templates
- `src/research_platform/`: stub packages
- `src/core/claims/citation_binding.py`, `citation_prose.py`, `citation_render.py`, `citation_validation.py`, `src/core/contracts/citation.py`: citation modules (the rest of `src/core/claims/` is the live claim-support path)
- `tests/architecture/test_module_reachability.py`: import-graph test that lists the modules no entry point reaches
- `src/api/services/direct_execution_service.py`: the dead execution attributes
- `src/repositories/model_call_reservation_repository.py`: the journal with no owner

## Row-level security for execution-service sessions

> **Not wired.** The roles, forced policies and `tenant_session` helper exist (see [authentication and tenancy](04-authentication-and-tenancy.md)), and every other background reader and writer uses them. The sessions that `direct_execution_service.py` opens from the application session factory do not set `app.current_org_id`, and its recovery and unscoped `get_run` reads do not use the maintenance factory.

Consequences:

- Under `cerebro_app` those sessions would read no rows and fail their inserts. The default compose stack therefore keeps `DATABASE_URL` on the `research` superuser, which bypasses row-level security, so for execution-service sessions the repository `organization_id` predicates are the effective boundary.
- A deployment that points `DATABASE_URL` at `cerebro_app` before these sessions adopt `tenant_session` loses run persistence.
