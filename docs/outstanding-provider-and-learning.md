# Outstanding Work: Multi-Provider Enablement & Adaptive Learning

This document records the current state of **multi-provider routing** and
**adaptive learning** capabilities in the codebase. Multi-provider routing is
built behind a feature flag. Adaptive decision/state primitives are integrated
as a dark capability, but product outcome capture and promotion evidence remain
incomplete.

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

**Model slug validation** (enabled by default via `OPENROUTER_VALIDATE_SLUGS_ON_STARTUP=true`):
- At provider initialization, fetches the live OpenRouter model catalog and validates that every slug in `OPENROUTER_TIER_MAPPING` exists
- Stale slugs (models no longer available) trigger **ERROR-level** logs naming each invalid tier→slug pair and mark the provider's health status as `degraded` for monitoring visibility
- Prevents silent fallback failures when model slugs change (real incident: `anthropic/claude-3.5-sonnet` no longer exists → 404 → silent Gemini fallback while all mocked tests stayed green)
- Validation is non-blocking: network failures skip with a warning, stale slugs do not crash the provider
- Set `OPENROUTER_VALIDATE_SLUGS_ON_STARTUP=false` to skip validation (e.g., air-gapped dev environments)

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
- Freshness decay: older routing history contributes less weight (linear decay over `MEMORY_ROUTING_FRESHNESS_DAYS`, default 30 days — weight ramps linearly to zero at the horizon)

**Broader adaptive learning** (outcome → feedback → allocation):
- FastAPI owns one settings-backed `MASRouter` shared by direct execution, the mounted MASR routes, and active TalkHier sessions.
- The Thompson allocator remains behind `ADAPTIVE_ROUTING_ENABLED=false`.
  `MASR_ENABLE_ADAPTIVE` is a deprecated compatibility alias for the older
  history-based strategy heuristic and cannot enable Thompson sampling.
- Decisions retain literal proposed/applied arms and clamps. Arm `0` is control
  for cold, unsafe, incompatible, or degraded paths.
- Enabling the flag against an empty state namespace is intentionally
  **effectively inactive**: readiness remains `cold`, only arm `0` executes,
  and ordinary traffic cannot promote the allocator. Live activation requires
  a separately validated seed snapshot containing evaluator-qualified coverage
  for every configured arm; Cerebro does not perform unsafe warm-up exploration.
- Versioned non-PII state can be stored atomically in Redis with opaque outcome
  idempotency. Only measured, successful outcomes from an allow-listed
  evaluator/version and matching policy/schema are eligible.
- Product execution now records allocation-correlated operational outcomes with
  retry-stable opaque IDs, but no neutral product evaluator currently supplies
  eligible quality outcomes. Measured latency is retained; cost and quality are
  explicitly unavailable unless measured. The mounted manual `/feedback`
  endpoint remains ineligible and does not train the Thompson allocator.
- Outcome delivery has only bounded same-process retry. There is no durable
  transactional outbox yet, so a process failure can still lose an outcome.
- The standalone MASR service is legacy-only and forces Thompson off.

### Configuration (memory-informed routing only)

Set these environment variables to enable memory-informed routing:
```bash
MEMORY_INFORMED_ROUTING_ENABLED=true
MEMORY_ROUTING_MAX_WORKER_ADJUST=2     # Max ±N from analytic baseline
MEMORY_ROUTING_FRESHNESS_DAYS=30       # Decay weight for older history
MEMORY_PROMPT_MAX_PROCEDURES=3         # Max procedural context items
ADAPTIVE_ROUTING_ENABLED=false         # Gate for the Thompson-sampling bandit loop (config.py:236, default False)
MASR_ENABLE_ADAPTIVE=true              # Older heuristic only; not Thompson
ADAPTIVE_ROUTING_SCHEMA_VERSION=1
ADAPTIVE_ROUTING_POLICY_VERSION=masr-adaptive-v1
ADAPTIVE_ROUTING_ALLOWED_EVALUATORS={} # Empty allow-list: no eligible learning
```

**Behavior when enabled**:
1. MASR queries episodic memory for similar past queries → suggests worker count adjustment (bounded)
2. Worker agents query procedural memory for successful past approaches → injects into prompt as context
3. All adjustments are bounded and gracefully degrade if memory is unavailable

**Behavior when disabled** (default): Zero memory influence; routing uses purely analytic complexity scoring; worker prompts contain no procedural context.

### What full adaptive learning requires

1. **Correlated outcome capture** — record only allocations that actually
   execute; retain multi-domain sub-decision attribution and retry-stable opaque
   IDs.
2. **Truthful observability and fixture isolation** — measured/unavailable
   cost and quality, no placeholder learning claims, deterministic fixture runs,
   and zero adaptive-store access in fixture mode.
3. **Supporting data and evaluation** — the non-activating promotion gate is
   implemented, but still needs a real neutral evaluator and representative,
   versioned evaluator-qualified corpus. It rejects unapproved criteria,
   incompatible versions, insufficient held-out samples, and synthetic
   evidence as non-promotional.
4. **Explicit promotion** — an operator decision after a versioned `pass`;
   synthetic success and `insufficient_evidence` cannot enable the flag.

Run the evidence gate with an explicit output path outside the repository:

```bash
python scripts/evaluate_adaptive_routing_promotion.py \
  --criteria config/adaptive_routing_promotion_criteria.example.json \
  --corpus /path/to/versioned-evaluator-corpus.json \
  --output /absolute/private/path/adaptive-routing-promotion.json
```

The example criteria are intentionally unapproved. Criteria bind the currently
serialized diagnostic guardrails and require evidence for every in-scope
collaboration mode and arm. Exact deployed-policy replay remains explicitly
unsupported, so the gate cannot yet return a promotional `pass`. The command
never mutates runtime configuration, refuses to overwrite an existing output,
and emits sanitized `insufficient_evidence` for malformed evidence. Promotion
reports remain private artifacts and should not be committed.

### Recommendation

**Memory-informed routing** (PR #55) can be enabled when there is a desire for adaptive behavior based on past successful patterns. It is production-ready behind the flag with graceful degradation.

**Full adaptive learning** should remain disabled until a neutral evaluator,
durable delivery/outbox boundary, representative corpus, approved criteria,
and held-out report all pass. Fixture/restart/replica paths validate mechanics
only. No runtime or evaluation path flips the flag automatically.

## Summary

| Capability | Code status | Gated on | Next step |
|---|---|---|---|
| Multi-provider routing (PR #56) | **BUILT — behind flag** | `OPENROUTER_API_KEY` + `MULTI_PROVIDER_ROUTING_ENABLED=true` | Enable when there is a concrete need for models beyond Gemini |
| Memory-informed routing (PR #55) | **BUILT — behind flag** | `MEMORY_INFORMED_ROUTING_ENABLED=true` | Enable when adaptive behavior based on past patterns is desired |
| Adaptive allocation/state core | **Integrated dark; no live evaluator outcomes** | `ADAPTIVE_ROUTING_ENABLED=false` | Add neutral evaluator and durable outcome outbox |
| Promotion gate | **Built; evidence insufficient** | Versioned representative corpus + approved criteria | Generate a held-out report after evaluator/outbox work |
| Live adaptive promotion | **Not approved** | Held-out evaluator-qualified `pass` + operator approval | Keep disabled until evidence exists |

**Status update**: Multi-provider routing and memory-informed routing remain
flag-gated. Adaptive runtime/state integration and its promotion gate are dark
and non-promotional; full learning remains incomplete pending a product
evaluator, durable delivery, representative evidence, and operator approval.
