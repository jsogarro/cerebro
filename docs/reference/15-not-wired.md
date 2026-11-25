# Code that is not wired

This page lists code and settings present in the repository that no production path calls. Nothing listed here runs in the default configuration or when any setting is changed, unless a row says otherwise. "Production path" means the processes the repository starts: the API (`uvicorn src.api.main:app`), the `research-cli` script, and the MCP server container. Other reference pages link here instead of repeating detail.

## Summary

| Feature | Status | Entry point | Source |
|---|---|---|---|
| Multi-tier memory | Not wired | none | `src/ai_brain/memory/`, `src/core/memory_encryption.py` |
| A/B experimentation framework | Not wired | none | `src/ai_brain/experimentation/` (all but the adaptive allocation engine) |
| Learning and self-improvement | Not wired | none | `src/ai_brain/learning/supervision_feedback.py` |
| Model configuration manager | Not wired | none | `src/ai_brain/config/`, `configs/models/` |
| Prompt manager and versioning | Not wired | none | `src/prompts/` |
| Reliability service registry | Not wired | none | `src/reliability/service_registry.py` |
| Stub packages | Not wired | none | `src/benchmarks/`, `src/costs/`, `src/improvement/`, `src/memory/`, `src/research_platform/` |
| Citation binding and PDF text extraction | Not wired | none | `src/core/claims/citation_*.py`, `src/core/contracts/citation.py`, `src/core/acquisition/pdf_text.py` |
| Orphan modules | Not wired | none | see [Orphan modules](#orphan-modules) |
| Dead execution attributes | Not wired | none | `src/api/services/direct_execution_service.py`, `src/core/config.py` |
| Settings with no effect | Not wired | none | `src/core/config.py` (`Settings`) |
| Unbacked web pages | Not wired | none | `cerebro/web/src/api/` |

## Multi-tier memory

> **Not wired.** `MultiTierMemorySystem` composes `WorkingMemoryManager`, `EpisodicMemoryManager`, `SemanticMemoryManager`, and `ProceduralMemoryManager`. No production code constructs `MultiTierMemorySystem`, and `app.state.memory_system`, which `get_memory_system` in `src/api/routes/users.py` reads, is never assigned.

Consequences of the missing assignment:

- `MASRouter.episodic_memory`, `MASRouter.procedural_memory`, and `LLMWorkerAgentBase.procedural_memory` stay `None`. The memory-informed worker adjustment in `MASRouter` and the procedural-memory prompt context in `LLMWorkerAgentBase` return `None` when their manager is `None`, so `MEMORY_INFORMED_ROUTING_ENABLED=true` changes nothing.
- The memory purge branch of the user-erasure route never runs, because `get_memory_system` always returns `None`.

Modules: `src/ai_brain/memory/multi_tier_memory.py`, `working_memory.py`, `episodic_memory.py`, `semantic_memory.py`, `procedural_memory.py`, and `src/core/memory_encryption.py` (`MemoryEncryption`, keyed by `MEMORY_ENCRYPTION_KEY`, used only by the working and episodic managers).

Settings: `WORKING_MEMORY_*`, `EPISODIC_MEMORY_*`, `SEMANTIC_MEMORY_*`, `PROCEDURAL_MEMORY_*`, `MEMORY_ENABLE_CROSS_TIER`, `MEMORY_MAX_RECALL_ITEMS`, and `MEMORY_CONSOLIDATION_INTERVAL` are read only by `Settings.get_ai_brain_config`, whose single caller reads only its `providers` entry. `MEMORY_INFORMED_ROUTING_ENABLED` (default `false`), `MEMORY_ROUTING_MAX_WORKER_ADJUST`, `MEMORY_ROUTING_FRESHNESS_DAYS`, and `MEMORY_PROMPT_MAX_PROCEDURES` are read, but only the memory paths above use them.

Tests that exercise it: `tests/test_memory_architecture_ttl.py`, `tests/test_context_compaction_telemetry.py`, `tests/test_pii_privacy.py`.

## A/B experimentation framework

> **Not wired.** `src/ai_brain/experimentation/` holds an experiment manager, a registry, statistical analyzers, a dashboard, an optimizer, and integrations for MASR, the API, and the agent framework. Nothing on a production path imports them. The exceptions are `core/adaptive_allocation_engine.py` and, through it, `MultiBanditOptimizer` and `BanditAlgorithm` in `statistical/enhanced_statistical_engine.py`: `MASRouter` builds an `AdaptiveAllocationEngine` only when `ADAPTIVE_ROUTING_ENABLED` is true, and the setting defaults to `false`. See [MASR routing](06-masr-routing.md).

Not wired:

| Module | Classes and functions |
|---|---|
| `core/unified_experiment_manager.py` | `UnifiedExperimentManager` |
| `core/system_experiment_registry.py` | `SystemExperimentRegistry` |
| `integration/masr_experiment_integration.py` | `MASRExperimentalRouter` (a `MASRouter` subclass) |
| `integration/api_pattern_experiments.py` | `APIPatternExperimentor` |
| `integration/agent_framework_integration.py` | `AgentFrameworkExperimentor` |
| `statistical/bayesian_experiment_design.py` | `BayesianExperimentDesigner` |
| `statistical/multi_variate_analysis.py` | `MultiVariateAnalyzer` |
| `statistical/enhanced_statistical_engine.py` | `FrequentistAnalyzer` (`power_analysis`), `BayesianAnalyzer`, `EnhancedStatisticalEngine` (`early_stopping_analysis`, `_bonferroni_correction`, `_fdr_correction`) |
| `monitoring/real_time_dashboard.py` | `RealTimeDashboard`, `get_dashboard` |
| `optimization/feedback_loop_optimizer.py` | `FeedbackLoopOptimizer`, `get_feedback_optimizer` |

The `eval/` subpackage (`adaptive_routing_eval.py`, `adaptive_routing_promotion.py`) is imported only by `scripts/run_adaptive_routing_eval.py` and `scripts/evaluate_adaptive_routing_promotion.py`.

The five tables `experiments`, `experiment_variants`, `experiment_assignments`, `experiment_results`, and `experiment_analyses` are defined in `src/models/db/experiment.py` and created by Alembic migrations. No repository and no route reads or writes them.

Tests that exercise it: `tests/test_ab_testing_integration.py`, `tests/test_ab_testing_math.py`, `tests/test_ab_testing_optimizer_integration.py`, `tests/test_ab_testing_dashboard.py`, `tests/unit/ai_brain/experimentation/test_continuous_beta_update.py`.

## Learning and self-improvement

> **Not wired.** `SupervisionFeedbackLearner` in `src/ai_brain/learning/supervision_feedback.py` records execution feedback and recommends routing strategies. `MASRRoutingService.__init__` assigns one to `self.feedback_learner`, and no code calls a method on it.

The `src/improvement/` package is listed under [Stub packages](#stub-packages). `SELF_IMPROVEMENT_REFLECTION_FREQUENCY`, `SELF_IMPROVEMENT_PERFORMANCE_THRESHOLD`, `SELF_IMPROVEMENT_ENABLE_PROMPT_OPTIMIZATION`, `FINE_TUNING_ENABLED`, `FINE_TUNING_MIN_EXAMPLES`, `FINE_TUNING_QUALITY_THRESHOLD`, and `FINE_TUNING_AUTO_TRIGGER` are read only by `Settings.get_ai_brain_config`.

## Model configuration manager

> **Not wired.** `ModelConfigManager` in `src/ai_brain/config/model_config_manager.py` loads `configs/models/base.yaml` and an environment file (`development.yaml` or `production.yaml`). It is constructed only by `ConfigurationIntegrationService` (`integration_service.py`) and by the module-level `get_model_config_manager`, and no production code calls either. `MASRouter`, `CostOptimizer`, and the providers accept an optional `model_config_manager` argument that production code never passes, so the YAML files are never loaded.

Other modules in the group: `legacy_adapter.py` (`LegacyConfigurationAdapter`, `LegacyProviderWrapper`), which nothing imports. `model_schemas.py` is in use: `ModelTier` and `RoutingStrategy` are imported by `MASRRoutingService` and `src/models/masr_api_models.py`. `supervisor_config.py` is imported by `MASRRoutingService`, which stores a `SupervisorConfigurationManager` in `self.config_manager`; nothing reads that attribute.

## Prompt manager and versioning

> **Not wired.** `PromptManager` in `src/prompts/manager.py` loads YAML templates from `src/prompts/templates/` (`agents`, `refinement`, `supervisors`, `workers`). The only production reference is `BaseSupervisor.coordinate_refinement_round`, which calls `get_prompt_manager()` with no configuration. That call raises `ValueError` because nothing initialises the manager, and no production code calls `coordinate_refinement_round`.

`src/prompts/versioning.py` (`PromptVersionManager`) is imported only by `UnifiedExperimentManager`, which is itself not wired. The prompts the agents send come from Python modules under `src/services/prompts/`, imported by `src/agents/base.py`, `src/agents/literature_review_agent.py`, `src/agents/comparative_analysis_agent.py`, and `src/services/gemini_service.py`. Prompt identity pinning is part of the tool boundary; see [tool boundary and evidence](09-tool-boundary-and-evidence.md).

Tests that exercise it: `tests/test_prompt_safety.py`, `tests/test_cache_ttl_expiry.py`.

## Reliability service registry

> **Not wired.** `src/reliability/service_registry.py` defines `ServiceRegistry`, `ServiceDiscovery`, `LoadBalancer`, and the helpers `initialize_service_registry`, `get_service_registry`, and `get_service_discovery`. `src/reliability/__init__.py` re-exports them, and no code constructs or calls any of them.

The one part of `src/reliability/` in production use is `retry_strategies.CircuitBreaker`, which `MASRouter` uses as its routing circuit breaker.

## Stub packages

> **Not wired.** These packages hold small in-memory dataclass modules. Nothing under `src/`, `tests/`, `evals/`, or `scripts/` imports them.

| Package | Contents |
|---|---|
| `src/benchmarks/` | `BenchmarkDataset`, `BenchmarkLibrary`, `ReplicationPackage`, `ResearchReplicationToolkit`, `BenchmarkEvaluator`, `PeerReviewSystem` |
| `src/costs/` | `CostTransaction`, `CostTracker` (with a fixed per-model price table), `Budget`, `BudgetManager`, `CostForecaster`, `CostOptimizer` |
| `src/improvement/` | `FeedbackCollectionService`, `RewardModel`, `RLTrainer`, `PromptOptimizer`, `AgentReflection`, `MetaLearner`, `ContinuousImprovementPipeline` |
| `src/memory/` | `WorkingMemoryManager`, `EpisodicMemoryService`, `EntityExtractionService`, `ProceduralMemoryService`, `QuerySuggestionService` |
| `src/research_platform/` | `main()`, the `research-platform` script target; see [CLI and web clients](13-cli-and-web-clients.md) |

## Citation binding and PDF text extraction

> **Not wired.** These modules bind claim spans to citations rendered from held metadata, validate citations against a registry corpus, and extract text from PDF snapshots. Only their own unit tests import them.

- `src/core/claims/citation_binding.py`, `citation_prose.py`, `citation_render.py`, `citation_validation.py`
- `src/core/contracts/citation.py`
- `src/core/acquisition/pdf_text.py`

Tests that exercise them live in `tests/unit/core/claims/` and `tests/unit/core/acquisition/test_pdf_text.py`.

## Orphan modules

> **Not wired.** No production path imports these modules. Tests import most of them.

| Module | What it holds | Imported by |
|---|---|---|
| `src/agents/delegation_contract.py` | `DelegationContract` | `tests/test_fast_path.py` |
| `src/api/services/masr_analytics.py` | `MASRAnalyticsService` | nothing |
| `src/core/production_config.py` and the top-level `config/` package | `ProductionSettings` and per-environment settings classes | `config/` is imported by `production_config.py` and tests; `production_config.py` by `tests/docker/test_production_jwt_contract.py` |
| `src/services/gemini_metrics.py` | Prometheus series and `GeminiMetrics` for the Gemini service | nothing |
| `src/services/hallucination_detector.py`, `src/services/evaluation_regression.py` | Offline claim and citation checks, and golden-dataset regression scoring | `tests/regression/` |
| `src/services/visualization_generator.py` | `VisualizationGenerator` (Plotly and NetworkX) | `tests/test_visualization*.py` |
| `src/services/text_alignment/` | Deterministic span alignment over source text | `tests/unit/services/test_text_alignment.py` |
| `src/services/parsers/base_parser.py` | Base parser utilities | `tests/test_gemini_parsers.py` |
| `src/models/base.py` | A SQLAlchemy `declarative_base()` | `tests/utils/db_utils.py` |
| `src/models/db/migrations.py` | `MigrationManager` | nothing |

## Dead execution attributes

> **Not wired.** Four names in the execution path are defined and never read. The live execution path is described in [research execution](05-research-execution.md).

- `DirectExecutionService.max_domain_parallelism` is assigned `4` in `__init__` and read nowhere.
- `DirectExecutionService._fast_path_passes_quality` is a method that nothing calls.
- `DirectExecutionService._fast_path_provider` is initialised to `None`, never assigned a provider, and only closed and reset in `close`.
- `Settings.MULTI_DOMAIN_MERGE_STRATEGY` (default `concat`) is read by no code under `src/`.

## Settings with no effect

Many `Settings` fields are declared and read by no code that acts on them, apart from `get_ai_brain_config`. The full list, split from the settings that code does read, is in [configuration](02-configuration.md).

## Unbacked web pages

The web workbench pages for workflows, runs, run detail, and memory call API routes that are not mounted, and its dashboard, QA, costs, and benchmarks pages show fixed values. The page-by-page table is in [CLI and web clients](13-cli-and-web-clients.md).

## Source map

- `src/ai_brain/memory/` — the four memory managers and `MultiTierMemorySystem`
- `src/core/memory_encryption.py` — `MemoryEncryption`
- `src/ai_brain/experimentation/` — experiment framework; `core/adaptive_allocation_engine.py` is the part `MASRouter` uses
- `src/models/db/experiment.py` — the five experiment tables
- `src/ai_brain/learning/supervision_feedback.py` — `SupervisionFeedbackLearner`
- `src/ai_brain/config/`, `configs/models/` — model configuration manager and YAML files
- `src/prompts/` — `PromptManager`, versioning, templates
- `src/reliability/` — `service_registry.py` (not wired) and `retry_strategies.py` (used)
- `src/benchmarks/`, `src/costs/`, `src/improvement/`, `src/memory/`, `src/research_platform/` — stub packages
- `src/core/claims/`, `src/core/contracts/citation.py`, `src/core/acquisition/pdf_text.py` — citation and PDF text modules
- `tests/architecture/test_module_reachability.py` — import-graph test that lists the modules no entry point reaches
- `src/api/services/direct_execution_service.py` — the dead execution attributes
