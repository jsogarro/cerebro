# LLM providers

Language-model calls go through two layers. `GeminiService` is the default client that workers, supervisors and the TalkHier services (the supervisors' multi-round messaging) call directly. The provider package (`BaseProvider` and a `ModelRouter`) serves execution-plan routes and the optional multi-provider route.

## Summary

| Feature | Status | Entry point | Source |
|---|---|---|---|
| `GeminiService` text and structured generation | Active | `LLMWorkerAgentBase._generate_with_routing`, supervisors, TalkHier services | `src/services/gemini_service.py` (`GeminiService`) |
| `GeminiService` rate limiting and circuit breaker | Active | every `GeminiService` call | `src/services/gemini_limiter.py` (`RateLimiter`), `src/services/gemini_service.py` (`generate_content_result`) |
| `GeminiService` response cache | Not wired | `GeminiService(cache_client=...)` | `src/services/gemini_service.py` (`_get_cached_response`) |
| Plan-backed provider route | Active | tasks that carry `provider_model_policy` | `src/agents/llm_worker_base.py` (`_build_plan_route`) |
| Multi-provider OpenRouter route | Off by default | `MULTI_PROVIDER_ROUTING_ENABLED=true` with `OPENROUTER_API_KEY` | `src/agents/llm_worker_base.py` (`_build_legacy_openrouter_route`) |
| `ModelRouter` provider registry | Active | plan-backed and OpenRouter routes | `src/ai_brain/providers/model_router.py` (`ModelRouter`) |
| DeepSeek, Llama and OpenRouter providers | Active | `ModelRouter` on plan-backed routes | `src/ai_brain/providers/` (`DeepSeekProvider`, `LlamaProvider`, `OpenRouterProvider`) |
| Gemini provider | Not wired | `ModelRouter` (plan-backed route naming `gemini`) | `src/ai_brain/providers/gemini_provider.py` (`GeminiProvider`) |
| OpenRouter slug validation | Active | `OpenRouterProvider.load_configuration` | `src/ai_brain/providers/openrouter_provider.py` (`validate_model_slugs`) |
| LLM call metrics | Active | `BaseProvider._postprocess_response` | `src/core/observability.py` (`record_llm_call`) |
| Provider-call Langfuse spans | Not wired | `OpenRouterProvider.generate` | `src/core/tracing.py` (`trace_provider_call`) |

## Gemini service

`GeminiService` wraps `ChatGoogleGenerativeAI` from LangChain. Production code builds it in five places: the application lifespan (`src/api/main.py`), `LLMWorkerAgentBase._ensure_gemini_service`, `src/api/services/talkhier_round_executor.py`, `src/api/services/supervisor_coordination_service.py` and `GeminiProvider.__init__` (positional `GeminiServiceClass(self.api_key)`). The first four call `GeminiService(api_key=settings.GEMINI_API_KEY)`. Passing `api_key` builds `GeminiConfig` from its dataclass defaults, so the effective configuration is:

| Setting | Value |
|---|---|
| model | `gemini-2.5-flash` |
| temperature, top-p, top-k | 0.7, 0.9, 40 |
| `max_output_tokens` | 32768 |
| rate limit | 10 requests per 60 seconds |
| concurrent requests | 2 |
| attempts per call | 1; a retry is the caller's decision |
| SDK retries | 1 (`max_retries=1`; the SDK default of 6 would bill each resend under one reservation) |
| circuit breaker | opens after 5 consecutive provider-side failures (timeouts, connection errors, HTTP 5xx, 408, 429), 30 second cooldown, one trial call; request errors such as an oversized prompt do not count |

The `GEMINI_*` environment variables are read by `GeminiConfig.from_env`, which `GeminiService` uses unless a complete `config` is given; an `api_key` argument replaces only `GEMINI_API_KEY`. See [configuration](02-configuration.md).

- **Generation.** `generate_content_result(prompt)` returns a `GeminiResult` with the text, finish reason and the token counts the provider reported (`None` when it reported none), and records one `LLMCallMetrics` entry; `generate_content(prompt)` returns its text. A call while the breaker is open raises `GeminiCircuitOpenError`. `generate_structured_content(prompt, schema, max_retries=0)` appends Pydantic format instructions and parses the reply with `PydanticOutputParser`; each re-ask allowed by `max_retries` is a new billed call.
- **Research helpers.** `generate_research_plan`, `analyze_literature`, `synthesize_findings`, `generate_citations` and `validate_hypothesis` build a prompt from `src/services/prompts/`, call the model and parse JSON with `parse_json_response`.
- **Rate limiting.** `RateLimiter` is a token bucket plus a semaphore. Each call waits for a token and a free concurrency slot before the request.
- **Parsers and prompts.** `src/services/parsers/json_parser.py` (`parse_json_response`) is the only parser function imported outside the package; it tolerates Markdown fences around JSON. The prompt builders in `src/services/prompts/` (`research_prompts`, `agent_prompts`, `validation_prompts`) are used by `GeminiService` and by the literature and comparative agents. The other parser modules (`citation_parser`, `text_parser`, `response_sanitizer`) are imported only by the package `__init__`.

> **Not wired.** `GeminiService` can cache the research-helper results in Redis under keys `gemini:<prefix>:<sha256>` with a TTL from `GeminiConfig.cache_ttl`. It caches only when a `cache_client` is passed, and no production construction passes one, so every call reaches the model. `generate_content` and `generate_structured_content` never use the cache.

### Without `GEMINI_API_KEY`

The lifespan in `src/api/main.py` skips building the service and logs a warning that `GEMINI_API_KEY` is not set and that agents use fallback responses. Workers built on `LLMWorkerAgentBase` then behave as follows:

- `_ensure_gemini_service` tries `GeminiService(api_key=None)`, which falls through to `GeminiConfig.from_env` and raises `ValueError`; the worker logs `gemini_service_unavailable` and keeps `gemini_service` as `None`.
- Text generation (`_generate_with_gemini`) returns the string `[<agent_type>] No language model configured; unable to produce a full <agent type with spaces>.` with confidence 0.3.
- Structured generation raises `ValueError("No language model configured")`.

## Provider abstraction

`src/ai_brain/providers/` holds one interface and four providers.

- **`BaseProvider`** (`base_provider.py`) defines `generate`, `stream`, `health_check`, request validation, per-model cost lookup (`estimate_cost`) and `_postprocess_response`, which updates success rate and latency and records the call. Request and response types are `ModelRequest` and `ModelResponse`; health is `ProviderHealthStatus`.
- **`ModelRouter`** (`model_router.py`) holds a `ProviderRegistry`. Its configuration is a dictionary with a `providers` mapping plus `health_check_interval` (default 300 seconds), `max_retries` and `enable_fallback`. For each provider with `enabled` not false it looks up the class through `PROVIDER_KEYS` (`deepseek`, `gemini`, `llama`, `openrouter`) in the component registry of `src/api/services/component_catalog.py` and instantiates it. An unknown name, or a provider whose constructor raises, is logged and skipped.
- **Health.** The first request to a provider runs its `health_check`, which sends a small real generation request; the result is cached for `health_check_interval`. An unhealthy provider makes `_try_provider` raise, and `route_and_generate` returns a failed `ModelResponse` with `provider="router"` and `error_type="routing_failure"`.
- **Fallback.** `route_and_generate` tries the primary route, then each entry of `fallback_models` when `enable_fallback` is true and the primary attempt returned a failed response. An unknown or unhealthy primary provider makes `_try_provider` raise, which returns the routing-failure response without trying any fallback. Worker routes always pass an empty `fallback_models`.

| Provider | Configuration keys | Models | Request limits |
|---|---|---|---|
| `DeepSeekProvider` | `endpoint` (default `https://api.deepseek.com/v1`), `api_key`, `default_model` (`deepseek-v3`) | `deepseek-v3`, `deepseek-coder`, `deepseek-math` | `max_tokens` up to 8000 |
| `LlamaProvider` (Ollama) | `endpoint` (default `http://localhost:11434`), `default_model` (`llama3.3:70b`), `keep_alive` (`5m`) | `llama3.3:70b`, `llama3.3:8b`, `codellama:34b` | `max_tokens` up to 4000 |
| `OpenRouterProvider` | `endpoint`, `api_key`, `tier_mapping` | the slugs in `tier_mapping`; cost table for three slugs | `max_tokens` up to 8000 |
| `GeminiProvider` | `api_key` (required; the constructor raises `ValueError` without it), `default_model` (`gemini-pro`) | `gemini-pro`, `gemini-pro-vision`, `gemini-ultra` | `max_tokens` up to 8000 |

Behaviour specific to each provider:

- `LlamaProvider` lists the models Ollama has (`/api/tags`) before a request and pulls a missing model through `/api/pull`.
- `OpenRouterProvider` picks the model from `request.metadata["tier"]` through `tier_mapping` when no model name is given, and falls back to the `balanced` tier. Plan-backed calls always name a model.
- `DeepSeekProvider` and `LlamaProvider` return `Unsupported model: ...` for any model name outside their table; only `OpenRouterProvider` accepts the slugs in its tier mapping. A plan-backed route that names a model must name one the provider lists.
- `GeminiProvider` declares `model_specs`, but `supported_models` is never populated: its `_get_supported_models` has no caller and the provider never loads configuration. `supports_model` therefore returns false for every name, and `generate` returns an error response (`error_type="model_error"`, message `Unsupported model: ...`) for every request.
- `DeepSeekProvider`, `LlamaProvider` and `OpenRouterProvider` load their model tables on the first request. Without a configuration manager they use the legacy tables in each class, which hold the context windows and per-1k-token costs used for cost estimates.

## How a worker picks a provider

`LLMWorkerAgentBase._select_generation_route` decides per call:

1. **Plan-backed task.** When `task.input_data["provider_model_policy"]` is present (attached by `ExecutionPlanTopologyExecutor` for a compiled execution plan), `_build_plan_route` builds a `ModelRouter` containing only the plan's primary provider. Its configuration is `settings.get_ai_brain_config()["providers"][<provider>]` with `enabled` forced to true, with `enable_fallback=False`. The routing decision names the primary provider and model and lists no fallbacks. A failed response or an exception raises `PlanProviderUnavailableError`; the worker never substitutes another provider.
2. **Multi-provider route.** Otherwise `_build_legacy_openrouter_route` applies.

   > **Off by default.** With `MULTI_PROVIDER_ROUTING_ENABLED=true`, non-plan worker calls go to a `ModelRouter` that holds an OpenRouter provider, with models picked by tier. It also needs `OPENROUTER_API_KEY`; `OPENROUTER_ENDPOINT` and `OPENROUTER_TIER_MAPPING` set the URL and the tier map.

   When the route returns a failed response or raises, the worker logs a warning and falls back to `GeminiService`.
3. **Default.** With no policy and the flag off, the worker calls `GeminiService` directly (`_generate_with_gemini`, or `generate_structured_content` for structured output).

Request defaults on the router routes are `max_tokens=2000` and `temperature=0.7` for text, and `max_tokens=4000` for structured output with `response_format` set to a JSON object in the request metadata. The request metadata carries the tier from `_determine_tier`. When the task context carries a budget ledger, each routed call reserves its estimated tokens and cost before the provider is contacted and settles afterwards; a call the budget cannot hold raises `BudgetExceededError` first.

### Slug validation

`OpenRouterProvider.load_configuration`, which runs on the first request or health probe, calls `validate_model_slugs` when an API key and HTTP client exist and the provider's `validate_slugs_on_startup` key is true. That key defaults to true and no caller supplies it, so validation always runs; the `OPENROUTER_VALIDATE_SLUGS_ON_STARTUP` setting is not read. Validation fetches the model catalog from the models endpoint derived from `endpoint` and compares each `tier_mapping` slug. A network error is logged and does not stop the provider. Stale slugs are logged at error level and reported in the health status metadata, which degrades `api_status`.

## Redaction and cost signals

- **Redaction.** `redact_pii` (`src/core/pii_redactor.py`) replaces e-mail addresses, phone numbers, US social security numbers and card-like digit runs with placeholders. Among providers, only `OpenRouterProvider` uses it, on the error text sent to a Langfuse span. Prompts and responses are not redacted on any provider call.
- **Metrics.** Every provider's `_postprocess_response` calls `record_llm_call`, which updates the Prometheus metrics `llm_call_duration_seconds`, `llm_tokens_total` and `llm_cost_usd_total` and logs `llm_call_completed`. Calls made directly through `GeminiService` are not recorded by it. Per-request cost drift (`llm_request_cost_drift_ratio`, `llm_cost_drift_events_total`) is computed from the same call records.
- **Langfuse.** `invoke_model` opens one `model_call` generation per real attempt when its context carries a `trace_parent` (metadata and usage only; see [model invocation contract](16-model-invocation-contract.md)); no production owner sets one yet. `OpenRouterProvider.generate` opens a generation span only when `request.metadata["_langfuse_trace"]` holds a parent observation. No code in `src` sets that key.

> **Not wired.** Provider-call Langfuse spans exist in `OpenRouterProvider.generate` through `trace_provider_call`. Nothing populates the `_langfuse_trace` metadata key they require, so no provider-call spans are produced even with `LANGFUSE_ENABLED=true`.

See [security, audit and observability](12-security-audit-observability.md) for the metrics endpoint and the middleware that records cost drift.

## Source map

- `src/services/gemini_service.py`: `GeminiService` generation, structured output, research helpers, cache hooks
- `src/services/gemini_config.py`: `GeminiConfig` defaults and `from_env`
- `src/services/gemini_limiter.py`: `RateLimiter` token bucket
- `src/services/parsers/`: response parsers; `json_parser.py` is the one used in production
- `src/services/prompts/`: prompt builders used by `GeminiService` and agents
- `src/ai_brain/providers/base_provider.py`: `BaseProvider`, request, response and health types
- `src/ai_brain/providers/model_router.py`: `ModelRouter`, provider registry, health caching
- `src/ai_brain/providers/gemini_provider.py`, `deepseek_provider.py`, `llama_provider.py`, `openrouter_provider.py`: provider implementations
- `src/api/services/component_catalog.py`: provider class registration
- `src/core/kernel/component_keys.py`: `PROVIDER_KEYS`
- `src/agents/llm_worker_base.py`: route selection and budget-metered generation
- `src/core/pii_redactor.py`: `redact_pii`
- `src/core/observability.py`: `record_llm_call` and LLM metrics
- `src/core/tracing.py`: Langfuse client and provider-call spans
