# Agent Domains

Cerebro's agent framework is organized into **domains**, each a hierarchical
supervisor coordinating a team of specialized worker agents. MASR routes a query
to the appropriate domain supervisor. A subset of workers is also callable
directly via the Bypass API — only the 10 `AgentType` enum values (the Research
and Finance workers plus `verification` and `financial-calculator`). The 4
Content workers (`content_planning`, `drafting`, `editing`, `optimization`) and
the 3 Analytics workers (`data_analysis`, `statistical_modeling`,
`insight_synthesis`) are **not** exposed on `/api/v1/agents`.

All worker agents are LLM-reasoning agents. Gemini is the default provider; a
flag-gated OpenRouter multi-provider path also exists (active only when both
`MULTI_PROVIDER_ROUTING_ENABLED` and `OPENROUTER_API_KEY` are set — both default
off).
They reason over the query text (and any prior-stage context passed by their
supervisor) — the Content, Analytics, and Finance domains require **no external
data feeds, API keys, or datasets**; supply figures/assumptions/text in the query.

## Domains and workers

| Domain | Supervisor | Worker agents (factory keys) |
|--------|------------|------------------------------|
| Research | `ResearchSupervisor` | `literature_review`, `comparative_analysis`, `methodology`, `synthesis`, `citation` |
| Content | `ContentSupervisor` | `content_planning`, `drafting`, `editing`, `optimization` |
| Analytics | `AnalyticsSupervisor` | `data_analysis`, `statistical_modeling`, `insight_synthesis` |
| Finance | `FinanceSupervisor` | `financial_analysis`, `valuation`, `risk_assessment` |

Each supervisor is a `BaseSupervisor` that runs its workers through a LangGraph
workflow with TalkHier worker messaging. Supervisors are registered in
`SupervisorFactory` and the direct-execution registry; workers are registered in
`AgentFactory`.

### Finance domain

LLM-reasoning only — operates on the figures/assumptions/theses in the query:

- **financial_analysis** — statement/ratio analysis and health assessment.
- **valuation** — DCF / comparables valuation from provided assumptions.
- **risk_assessment** — qualitative risk analysis of a thesis or portfolio.

## How to reach the agents

### Primary API (MASR-routed)

MASR detects the query domain and routes to the matching supervisor:

```bash
# Routes to the finance supervisor
curl -X POST "http://localhost:8000/api/v1/query/research" \
  -H "Content-Type: application/json" \
  -d '{"query": "Value a company with a DCF: FCF $100M growing 5%, discount rate 10%", "domains": ["finance"]}'
```

### Bypass API (direct single agent)

Call a specific worker agent directly (hyphenated `AgentType` values):

```bash
curl -X POST "http://localhost:8000/api/v1/agents/valuation/execute" \
  -H "Content-Type: application/json" \
  -d '{"query": "DCF: year-1 FCF $50M, growing 4% forever, discount rate 9%. Compute the value."}'
```

Available Bypass agent types: `literature-review`, `citation`, `methodology`,
`comparative-analysis`, `synthesis`, `financial-analysis`, `valuation`,
`risk-assessment`, `financial-calculator`, `verification`. Chain-of-Agents
(`/agents/chain`) and Mixture-of-Agents (`/agents/mixture`) compose multiple
agents.

## Adding a new domain

1. Add worker agents (subclass `LLMWorkerAgentBase` for prompt-driven LLM
   workers) and register them in `AgentFactory._agent_registry`.
2. Add a `BaseSupervisor` subclass that registers the workers as its
   `worker_definitions` (using the real agent classes) and defines a LangGraph
   workflow; register it in `SupervisorFactory` and the direct-execution
   registry.
3. For MASR auto-routing, add a `QueryDomain` value + detection patterns in
   `query_analyzer.py` and the domain→supervisor mapping in `masr.py`.
4. To expose workers on the Bypass API, add `AgentType` enum entries + the
   factory-key mapping in `agent_execution_service.py`, and list them in
   `get_agent_list()`.
