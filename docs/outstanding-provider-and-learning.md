# Outstanding Work: Multi-Provider Enablement & Adaptive Learning

This document records two capabilities that are **implemented in the codebase but
not active in the production request path**. Unlike the other agent-framework work
completed recently, neither can simply be "turned on" — each is gated on an
external dependency (credentials) or a product decision plus supporting evaluation.
This is the honest status so the next contributor does not mistake "code exists"
for "capability is live."

## 1. Multi-provider model routing (credential-gated)

### Current state

- `src/ai_brain/providers/` contains a complete provider abstraction:
  `BaseProvider`, `ModelRouter`, `ProviderRegistry`, plus `GeminiProvider`,
  `DeepSeekProvider`, and `LlamaProvider`. It supports health checks, fallback
  chains, capability-based selection, and cost accounting.
- **The active execution path does not use it.** Agents and API services call
  `GeminiService` directly (~20 call sites across `src/agents/` and
  `src/api/services/`). `ModelRouter` is instantiated only by
  `src/ai_brain/config/integration_service.py`, which itself has **no inbound
  usage from the API or agent layers**. In effect, the multi-provider layer is
  built but dormant; every live request goes to Gemini.

### What enabling it requires

1. **External credentials / endpoints** — the non-Gemini providers are inert
   without them:
   - OpenAI / Anthropic (Claude): API keys, if those providers are added.
   - DeepSeek: hosted API key **or** a local inference endpoint.
   - Llama: a running local inference server (e.g. vLLM / llama.cpp) and its URL.
   These belong in environment configuration, never in source (see the repo's
   API-key sourcing conventions).
2. **A wiring change** — route the active path through `ModelRouter` instead of
   the direct `GeminiService` calls, so MASR routing decisions actually select a
   provider/model. This is a deliberate refactor of the agent execution layer,
   not a config flag, and should land with its own tests and a fallback-to-Gemini
   default so a missing credential degrades gracefully.

### Recommendation

Keep Gemini as the sole live provider until there is a concrete need for a second
model (cost, capability, or availability). When that need arrives, enable one
additional provider end-to-end (credential → `ModelRouter` wiring → fallback test)
rather than activating the whole registry at once.

## 2. Adaptive / feedback-driven learning (decision-gated)

### Current state

- `src/ai_brain/experimentation/core/adaptive_allocation_engine.py` and
  `src/ai_brain/experimentation/optimization/feedback_loop_optimizer.py` implement
  adaptive allocation and a feedback-optimization loop.
- These are wired **only into experiment-scoped surfaces** —
  `src/api/routes/experiment_agent_api.py` and the
  `src/ai_brain/experimentation/integration/*` modules — **not** the main
  `/query` → MASR → supervisor → worker path.
- The memory subsystem (`procedural_memory`, `episodic_memory`,
  `multi_tier_memory`) is partially wired (referenced by `core/config.py`, the
  memory package, and the `users` route), but the closed learning loop
  (outcome → feedback → allocation adaptation) does not run on live traffic.

### What enabling it requires

1. **A product decision** on whether request-path behavior should adapt based on
   past outcomes at all — this changes reproducibility and makes responses
   history-dependent, which has evaluation and support implications.
2. **Supporting data and evaluation** — an offline/experimental demonstration that
   the adaptive loop measurably improves outcome quality before it is promoted
   from the experimentation harness into the main request path. The
   experimentation subsystem already exists to run exactly this kind of study.
3. **Promotion + guardrails** — wiring the validated loop into the main path with
   clear on/off control, bounded adaptation, and monitoring.

### Recommendation

Treat this as a research task run inside the existing experimentation subsystem
first. Do not wire adaptive allocation into the live request path until an
experiment shows it helps; then promote it behind an explicit, monitored toggle.

## Summary

| Capability | Code status | Gated on | Next step |
|---|---|---|---|
| Multi-provider routing | Built, dormant | External credentials + active-path wiring | Enable one provider end-to-end when a second model is actually needed |
| Adaptive learning loop | Built, experiment-scoped only | Product decision + evaluation | Prove value in the experimentation harness, then promote behind a toggle |

Both items are intentionally left inactive. They are documented here so their
status is explicit and the activation prerequisites are known.
