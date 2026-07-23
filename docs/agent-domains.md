# Agent Domains

This document catalogs the current runtime implementation. Agent domains are an
internal execution mechanism; they are not the target product extension
contract.

Cerebro currently organizes workers under four hierarchical supervisors. MASR
classifies a query and routes it to a supervisor, which coordinates its workers
and sends the result through a verification gate.

Gemini is the default provider. The OpenRouter path is active only when
multi-provider routing and the required provider configuration are enabled.

## Current Catalog

| Domain | Supervisor | Worker agents |
| --- | --- | --- |
| Research | `ResearchSupervisor` | `literature_review`, `comparative_analysis`, `methodology`, `synthesis`, `citation` |
| Content | `ContentSupervisor` | `content_planning`, `drafting`, `editing`, `optimization` |
| Analytics | `AnalyticsSupervisor` | `data_analysis`, `statistical_modeling`, `insight_synthesis` |
| Finance | `FinanceSupervisor` | `financial_analysis`, `valuation`, `risk_assessment` |

The verification agent is cross-cutting. The financial calculator is a
deterministic tool wrapper rather than an LLM-reasoning worker.

Only the agent types represented in the public `AgentType` enum are available
through the direct-agent API. Content and Analytics workers are currently
reachable through their supervisors but are not all exposed as direct agent
types.

## Data and Tool Limitations

Content, Analytics, and Finance primarily reason over text, figures, and context
provided in the request. The current Finance domain does not retrieve live
prices, filings, news, or transcripts.

The codebase contains MCP tools and tool registries, but tool availability in a
prompt should not be interpreted as proof that every worker executes a complete
autonomous tool loop. Verify the concrete worker path when documenting or
extending tool behavior.

## Calling the Runtime

### Routed Query

```bash
curl -X POST "http://localhost:8000/api/v1/query/research" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Compare the evidence for two approaches to retrieval evaluation",
    "domains": ["research"]
  }'
```

MASR can use explicit domain hints, but final routing is governed by the current
query analyzer and domain mappings.

### Direct Worker

```bash
curl -X POST "http://localhost:8000/api/v1/agents/synthesis/execute" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Synthesize the supplied findings into a concise research brief",
    "parameters": {}
  }'
```

Current direct agent types:

- `literature-review`
- `citation`
- `methodology`
- `comparative-analysis`
- `synthesis`
- `financial-analysis`
- `valuation`
- `risk-assessment`
- `financial-calculator`
- `verification`

The bypass API also exposes Chain-of-Agents and Mixture-of-Agents composition
for experimentation and testing.

## Finance as an Example Domain

The Finance domain demonstrates how shared agent infrastructure can support
specialized prompts and deterministic calculations:

- `financial_analysis` interprets user-supplied statements and ratios;
- `valuation` applies user-supplied assumptions to basic DCF or comparables
  analysis;
- `risk_assessment` evaluates qualitative thesis or portfolio risks;
- `financial-calculator` provides deterministic arithmetic for supported
  operations.

This is a domain example, not a complete financial research workflow. A credible
financial workflow would additionally require licensed or public data sources,
source snapshots, domain artifacts, finance-specific evaluations, and stronger
calculation models.

## Current Extension Cost

Adding a domain is not yet a single plugin registration. It generally requires:

1. implementing and registering workers in `AgentFactory`;
2. implementing a `BaseSupervisor` subclass;
3. registering that supervisor in the supervisor and direct-execution
   registries;
4. adding `QueryDomain` detection and routing mappings;
5. optionally adding public `AgentType` mappings and API catalog entries;
6. adding routing, execution, fallback, and verification tests.

These integration points are internal implementation details rather than a
standalone extension contract.
