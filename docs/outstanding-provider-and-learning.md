# Outstanding Work: Multi-Provider Enablement & Adaptive Learning

This document records the current state of **multi-provider routing** and **adaptive learning** capabilities in the codebase. As of PR #56, multi-provider routing is **built and available behind a feature flag**. Adaptive learning remains experiment-scoped.

## 1. Multi-provider model routing (NOW BUILT — behind flag)

### Current state (Updated: PR #56 merged)

- **Multi-provider routing is BUILT and AVAILABLE** behind `MULTI_PROVIDER_ROUTING_ENABLED` flag (default `false`)
- New `OpenRouterProvider` in `src/ai_brain/providers/openrouter_provider.py` provides unified access to Claude, Llama, DeepSeek, Gemini, and other models through OpenRouter's OpenAI-compatible API
- `ModelRouter` wired into `src/agents/llm_worker_base.py` via `_generate_with_routing()` with graceful fallback to `GeminiService`
- **Default behavior (flag OFF)**: All requests use `GeminiService` — byte-for-byte prior behavior preserved
- **Enabled behavior (flag ON + API key set)**: Routes through `ModelRouter` → `OpenRouterProvider` with tier-based model selection; falls back to `GeminiService` if OpenRouter unavailable

### Configuration

Set these environment variables to enable:
```bash
OPENROUTER_API_KEY=your-key-here
MULTI_PROVIDER_ROUTING_ENABLED=true
```

**Tier mapping** (configurable via `OPENROUTER_TIER_MAPPING`):
- `simple`: `deepseek/deepseek-chat` (cost-minimized)
- `balanced`: `anthropic/claude-sonnet-4.6` (mid-tier quality)
- `complex`: `anthropic/claude-sonnet-4.6` (quality-focused)

### How to go live

1. Obtain an OpenRouter API key (provides access to Claude, DeepSeek, Llama, etc. via single key)
2. Set `OPENROUTER_API_KEY` in environment
3. Set `MULTI_PROVIDER_ROUTING_ENABLED=true`
4. Restart service
5. Monitor fallback behavior in logs (degrades gracefully to Gemini on OpenRouter failure)

**No code changes required** — the feature is production-ready behind the flag.

### Recommendation

Enable when there is a concrete need for models beyond Gemini (cost optimization, capability requirements, or availability). OpenRouter provides single-key access to multiple providers without managing separate API integrations.

## 2. Memory-informed routing & adaptive learning (PARTIALLY BUILT — behind flag)

### Current state (Updated: PR #55 merged)

**Memory-informed routing** is **BUILT and AVAILABLE** behind `MEMORY_INFORMED_ROUTING_ENABLED` flag (default `false`):
- Episodic memory integration in `src/ai_brain/router/masr.py` nudges worker allocation based on past similar queries
- Procedural memory integration in `src/agents/llm_worker_base.py` injects successful past approaches into worker prompts
- Bounded adaptation: worker count adjustment capped at `±MEMORY_ROUTING_MAX_WORKER_ADJUST` (default `±2`)
- Freshness decay: older routing history contributes less weight (exponential decay over `MEMORY_ROUTING_FRESHNESS_DAYS`, default 30 days)

**Broader adaptive learning** (outcome → feedback → allocation) remains experiment-scoped:
- `src/ai_brain/experimentation/core/adaptive_allocation_engine.py` and feedback loop optimizer are wired **only** into experiment APIs (`/api/routes/experiment_agent_api.py`)
- **NOT wired** into the main `/query` → MASR → supervisor → worker path
- Memory subsystem (procedural, episodic, semantic, multi-tier) is partially wired but the closed feedback loop does not run on live traffic

### Configuration (memory-informed routing only)

Set these environment variables to enable memory-informed routing:
```bash
MEMORY_INFORMED_ROUTING_ENABLED=true
MEMORY_ROUTING_MAX_WORKER_ADJUST=2     # Max ±N from analytic baseline
MEMORY_ROUTING_FRESHNESS_DAYS=30       # Decay weight for older history
MEMORY_PROMPT_MAX_PROCEDURES=3         # Max procedural context items
```

**Behavior when enabled**:
1. MASR queries episodic memory for similar past queries → suggests worker count adjustment (bounded)
2. Worker agents query procedural memory for successful past approaches → injects into prompt as context
3. All adjustments are bounded and gracefully degrade if memory is unavailable

**Behavior when disabled** (default): Zero memory influence; routing uses purely analytic complexity scoring; worker prompts contain no procedural context.

### What full adaptive learning requires

1. **A product decision** on whether request-path behavior should adapt based on past outcomes at all — this changes reproducibility and makes responses history-dependent (evaluation and support implications)
2. **Supporting data and evaluation** — offline/experimental demonstration that the full adaptive loop measurably improves outcome quality before promotion to main request path
3. **Promotion + guardrails** — wiring the validated loop into the main path with clear on/off control, bounded adaptation, and monitoring

### Recommendation

**Memory-informed routing** (PR #55) can be enabled when there is a desire for adaptive behavior based on past successful patterns. It is production-ready behind the flag with graceful degradation.

**Full adaptive learning** should remain in the experimentation harness until an experiment demonstrates measurable quality improvement, then promote behind an explicit toggle.

## Summary

| Capability | Code status | Gated on | Next step |
|---|---|---|---|
| Multi-provider routing (PR #56) | **BUILT — behind flag** | `OPENROUTER_API_KEY` + `MULTI_PROVIDER_ROUTING_ENABLED=true` | Enable when there is a concrete need for models beyond Gemini |
| Memory-informed routing (PR #55) | **BUILT — behind flag** | `MEMORY_INFORMED_ROUTING_ENABLED=true` | Enable when adaptive behavior based on past patterns is desired |
| Full adaptive learning loop | Built, experiment-scoped only | Product decision + evaluation | Prove value in experimentation harness, then promote behind a toggle |

**Status update**: Multi-provider routing and memory-informed routing are now production-ready behind feature flags. Full adaptive learning remains experiment-scoped pending evaluation.
