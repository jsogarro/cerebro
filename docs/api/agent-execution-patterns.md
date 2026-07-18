# Agent Execution Patterns (Bypass API)

## Introduction

Cerebro's **bypass agent API** (`/api/v1/agents`) exposes two multi-agent execution
patterns that a caller invokes explicitly: **Chain-of-Agents (CoA)** and
**Mixture-of-Agents (MoA)**. This document describes those two endpoints — their
request fields, execution behavior, and response shapes — as implemented in
`src/api/services/agent_execution_service.py` and modeled in
`src/models/agent_api_models.py`.

> **Scope note — these patterns are bypass-only.** Chain-of-Agents and
> Mixture-of-Agents exist **only** as the bypass endpoints
> `POST /api/v1/agents/chain` and `POST /api/v1/agents/mixture`. The primary,
> MASR-routed query API (`/api/v1/query/research`, `/analyze`, `/synthesize`,
> `/literature`, `/methodology`, `/comparison`) does **not** select or route to
> CoA/MoA — see [How this relates to the primary API](#how-this-relates-to-the-primary-api).

## How this relates to the primary API

The primary query endpoints are thin wrappers over a single handler
(`intelligent_research_query`). A request there flows:

```
Client -> FastAPI -> DirectExecutionService (asyncio background task)
       -> MASRouter -> MASRSupervisorBridge -> domain supervisors -> workers -> verification
```

MASR's routing decision produces a **`CollaborationMode`**, one of:
`FAST_PATH`, `DIRECT`, `PARALLEL`, `HIERARCHICAL`, `DEBATE`, `ENSEMBLE`.
`MASRSupervisorBridge` maps those to supervisor coordination styles
(`masr_supervisor_bridge.py:136-140`): `DIRECT -> sequential`,
`PARALLEL -> parallel`, `HIERARCHICAL -> hybrid`, `DEBATE -> adaptive`,
`ENSEMBLE -> parallel`. `FAST_PATH` is a single LLM call that bypasses
supervisors entirely.

There is **no `CollaborationMode` value for Chain-of-Agents or Mixture-of-Agents**,
and neither `masr.py` nor `query_api.py` references CoA/MoA. To run these patterns
you must call the bypass endpoints directly with an explicit agent list.

> **Confidence and quality values are heuristics, not measurements.** How a worker's
> `confidence` is produced depends on the agent. Agents that use
> `LLMWorkerAgentBase`'s default generation path — the finance agents and
> verification — get a hardcoded value (`0.85` on a non-empty generation, `0.3` on an
> empty/error response, `llm_worker_base.py:252`). The five research agents
> (`literature-review`, `citation`, `methodology`, `comparative-analysis`,
> `synthesis`) override `execute()` and compute their own additive heuristic starting
> from a base of `0.5` (e.g. `literature_review_agent.py:344`,
> `citation_agent.py:153`, `methodology_agent.py:137`, `synthesis_agent.py:138`).
> Either way the value is heuristic, not a measured quality signal. Per-agent
> `quality_score` is set equal to that confidence (`quality_score = agent_result.confidence`,
> `agent_execution_service.py:168`). Every derived score below
> (`overall_confidence`, `chain_quality_score`, `quality_improvement`,
> `consensus_score`, `mixture_quality_score`, `inter_agent_agreement`) is computed
> from those heuristics. They are **not** independent quality signals.

## Bypass agent types

The bypass API accepts only these 10 `AgentType` values
(`src/models/agent_api_models.py:16-28`):

`literature-review`, `citation`, `methodology`, `comparative-analysis`,
`synthesis`, `financial-analysis`, `valuation`, `risk-assessment`,
`financial-calculator`, `verification`.

Content and Analytics domain workers are **not** exposed through the bypass API
and cannot appear in a chain or mixture.

## Chain-of-Agents

**Chain-of-Agents** executes a list of agents **sequentially**, each optionally
receiving the accumulated outputs of the agents before it. It is a **synchronous
request/response** call: the endpoint runs the full chain and returns the
complete result — there is no background task and no per-step streaming.

### Endpoint

```http
POST /api/v1/agents/chain
{
  "query": "Assess the valuation risk of a US equity given its filings",
  "agent_chain": ["financial-analysis", "valuation", "risk-assessment"],
  "pass_intermediate_results": true,
  "early_stopping": false,
  "quality_threshold": 0.85
}
```

### Request fields (`ChainOfAgentsRequest`)

| Field | Type | Default | Meaning |
|---|---|---|---|
| `query` | string (1–2000 chars) | — | Initial query passed to every agent in the chain |
| `agent_chain` | list of `AgentType` (2–5) | — | Ordered list of agents to run |
| `context` | object | `{}` | Initial execution context |
| `pass_intermediate_results` | bool | `true` | Inject each agent's output into the next agent's context (as `previous_<agent>_result`) |
| `early_stopping` | bool | `false` | Stop the chain when an agent's quality falls below `quality_threshold` |
| `quality_threshold` | float 0–1 | `0.85` | Early-stopping threshold (only consulted when `early_stopping` is true) |
| `timeout_per_agent_seconds` | int 30–900 | `180` | Per-agent execution timeout (passed to each agent as `timeout_seconds`) |

### Execution behavior

1. Agents run in order. Each agent receives `query` plus the accumulated context.
2. After each agent, its output, execution time, quality score, and confidence are
   recorded.
3. If `early_stopping` is true and the agent's `quality_score < quality_threshold`,
   the chain stops and records `early_stopped: true` and `stopped_at_agent`.
4. If `pass_intermediate_results` is true, the agent's output and the current step
   number are added to the context before the next agent runs.
5. `final_result` is the last agent's output. `overall_confidence` and
   `chain_quality_score` are the means of the per-agent confidences and quality
   scores. `quality_improvement` is `quality_scores[-1] - quality_scores[0]`
   (only when more than one agent ran) — the difference between the last and first
   agent's heuristic quality values.

### Response (`ChainOfAgentsResponse`)

```json
{
  "execution_id": "…",
  "status": "completed",
  "agent_chain": ["financial-analysis", "valuation", "risk-assessment"],
  "intermediate_results": [ {"…": "…"}, {"…": "…"}, {"…": "…"} ],
  "final_result": {"…": "…"},
  "overall_confidence": 0.85,
  "total_execution_time_seconds": 12.4,
  "agent_execution_times": [4.1, 3.9, 4.4],
  "early_stopped": false,
  "stopped_at_agent": null,
  "chain_quality_score": 0.85,
  "quality_improvement": 0.0,
  "started_at": "…",
  "completed_at": "…",
  "errors": []
}
```

## Mixture-of-Agents

**Mixture-of-Agents** runs a set of agents **in parallel** against the same query,
then aggregates their outputs into a single result. Like the chain endpoint, it is
a **synchronous request/response** call with no streaming.

### Endpoint

```http
POST /api/v1/agents/mixture
{
  "query": "Evaluate this company's investment risk",
  "agent_types": ["financial-analysis", "valuation", "risk-assessment"],
  "aggregation_strategy": "consensus",
  "weight_by_confidence": true,
  "consensus_threshold": 0.8,
  "max_parallel": 3
}
```

### Request fields (`MixtureOfAgentsRequest`)

| Field | Type | Default | Meaning |
|---|---|---|---|
| `query` | string (1–2000 chars) | — | Query sent to every agent |
| `agent_types` | list of `AgentType` (2–5) | — | Agents to run in parallel |
| `context` | object | `{}` | Shared execution context |
| `aggregation_strategy` | string | `"consensus"` | One of `consensus`, `weighted_average`, `best_quality` (any other value falls back to `consensus`) |
| `weight_by_confidence` | bool | `true` | Weight each agent's contribution by its confidence; otherwise weights are uniform |
| `consensus_threshold` | float 0–1 | `0.8` | Minimum consensus score to mark `consensus_achieved` |
| `timeout_seconds` | int 60–1800 | `300` | Total execution timeout; applied per agent as `min(timeout_seconds, 600)` (`agent_execution_service.py:413`) |
| `max_parallel` | int 1–5 | `3` | Intended concurrency limit, but currently **has no runtime effect** — see note below |

> **`max_parallel` is inert.** Every agent coroutine is started eagerly with
> `asyncio.create_task` (`agent_execution_service.py:419`) before the
> `asyncio.Semaphore(max_parallel)` (:426) is ever acquired, and the collecting loop
> only `await`s the already-running tasks one at a time (:437-439). All agents run
> fully in parallel regardless of `max_parallel`; the field has no effect on
> concurrency today.

### Aggregation strategies

- **`consensus`** — combines each agent's output weighted by confidence into a
  single synthesized structure.
- **`weighted_average`** — multiplies each agent's confidence by its weight and sums
  the products into an `overall_confidence`, recording each agent's output, weight,
  and weighted contribution (`_weighted_average_aggregation`,
  `agent_execution_service.py:609-638`). It does not inspect output content, so
  there is no numeric/quantitative bias.
- **`best_quality`** — selects the highest-quality agent output and retains the
  others as alternatives.

`consensus_score` is derived from the spread of agent confidences
(`1 - stdev(confidences)`, or `1.0` when only one agent produced a result), and
`consensus_achieved` is `consensus_score >= consensus_threshold`. Note that failed
agents are **not** excluded from aggregation. `execute_single_agent` catches
`TimeoutError` and every other exception internally and returns a `failed`
`AgentExecutionResponse` with `confidence` `0.0` rather than raising
(`agent_execution_service.py:188-228`), so the mixture loop's `except`/`continue`
(:444-447) almost never fires. A failed agent is therefore included in
`agent_results` and aggregation: its `{"error": …}` output appears in the agent
contributions, it receives roughly zero weight, and its `0.0` confidence widens the
confidence spread — lowering `consensus_score` through the `1 - stdev` formula.

### Response (`MixtureOfAgentsResponse`)

```json
{
  "execution_id": "…",
  "status": "completed",
  "agent_types": ["financial-analysis", "valuation", "risk-assessment"],
  "agent_results": {
    "financial-analysis": {"…": "…"},
    "valuation": {"…": "…"},
    "risk-assessment": {"…": "…"}
  },
  "aggregated_result": {"…": "…"},
  "consensus_score": 0.87,
  "aggregation_strategy": "consensus",
  "agent_weights": {
    "financial-analysis": 0.34,
    "valuation": 0.33,
    "risk-assessment": 0.33
  },
  "consensus_achieved": true,
  "total_execution_time_seconds": 5.2,
  "parallel_efficiency": 1.9,
  "mixture_quality_score": 0.85,
  "inter_agent_agreement": 1.0,
  "started_at": "…",
  "completed_at": "…",
  "errors": []
}
```

`parallel_efficiency` is `sum(per_agent_times) / max(per_agent_time)` — the observed
speedup from running the agents concurrently rather than sequentially.
`inter_agent_agreement` is `1 - stdev(quality_scores)` (or `1.0` for a single
result).

## Choosing between the primary and bypass APIs

**Use the primary query API** (`/api/v1/query/*`) for normal usage. It is
MASR-routed and coordinates domain supervisors and their workers. It does **not**
run Chain-of-Agents or Mixture-of-Agents — those patterns are not part of the
MASR routing path.

**Use the bypass agent API** (`/api/v1/agents/chain` or `/mixture`) when you
specifically want to:

- run an explicit, fixed sequence of agents (chain), or
- fan a single query out across several agents and aggregate (mixture),
- experiment with agent composition, or debug individual agents in a controlled
  order.

The bypass endpoints require you to name the agents yourself; there is no
automatic pattern selection.

## Related workflow endpoints

Two convenience endpoints on the bypass API wrap these patterns with preset agent
lists:

- `POST /api/v1/agents/workflows/literature-analysis` → `ChainOfAgentsResponse`
  (calls the chain handler internally).
- `POST /api/v1/agents/workflows/comprehensive-research` → `MixtureOfAgentsResponse`
  (calls the mixture handler internally).

Both are synchronous and return the same response shapes documented above.
