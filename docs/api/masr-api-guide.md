# MASR Dynamic Routing API Guide

> **Canonical MASR API reference.** This document supersedes `masr-api-complete-guide.md`.

## Overview

The MASR (Multi-Agent System Router) Dynamic Routing API exposes Cerebro's routing capabilities through RESTful endpoints. It is informed by the "MasRouter: Learning to Route LLMs" research. The 50-60% cost-reduction and 20-25% quality-improvement figures cited throughout this guide are **research-paper design goals, not Cerebro-measured results** — treat them as target-setting context, not achieved metrics.

All 8 REST endpoints below (`/route`, `/estimate-cost`, `/evaluate-strategies`, `/analyze-complexity`, `/strategies`, `/models`, `/feedback`, `/status`) are live and mounted at the `/api/v1/masr` prefix.

These endpoints use the same FastAPI-lifespan-owned in-process `MASRouter` as
direct execution and active TalkHier sessions. The standalone port-9100 service
is a non-authoritative legacy diagnostics surface available only through the
`legacy-masr-service` Compose profile.

## Key Features

- **Intelligent Routing**: Automatic supervisor and model selection based on query analysis
- **Cost Optimization**: Real-time cost estimation with hierarchical breakdown
- **Strategy Evaluation**: Compare multiple routing strategies for optimal selection
- **Complexity Analysis**: Deep query analysis with feature extraction
- **Feedback Endpoint**: Accepts performance feedback (see the learning-loop caveat under `/feedback` — the closed-loop learning it feeds is a design goal, not yet implemented)
- **Performance Analytics**: Metrics surfaced via the `/status` endpoint (see caveats — several fields are static/illustrative)

## Base URL

```
http://localhost:8000/api/v1/masr
```

> No public `api.cerebro.ai` host exists today. Use the local dev server
> (`uvicorn src.api.main:app --port 8000`, or `./scripts/compose.sh up -d`).
> Any `*.cerebro.ai` URL in this guide is aspirational.

## Authentication

**These endpoints are currently effectively unauthenticated.** The `masr_api` router is mounted with no auth dependency, and the application's `AuthMiddleware` is a no-op that validates nothing. All `/api/v1/masr/*` endpoints accept requests without a token.

Cerebro does implement JWT auth (RS256, per-endpoint `Depends(...)`) for the `auth`, `users`, and parts of the `research`/`reports` routers, but the MASR routes do not currently declare those dependencies. Do not assume a Bearer token is required or enforced here. If/when auth is added to these routes, requests would carry:

```http
Authorization: Bearer YOUR_API_TOKEN
```

## Core Endpoints

### 1. Get Routing Decision

**POST** `/route`

Get an intelligent routing decision for a query with optimal supervisor allocation and model selection.

**Request Body:**
```json
{
  "query": "Analyze the impact of AI on employment in manufacturing",
  "context": {
    "domain": "research",
    "priority": "high"
  },
  "strategy": "balanced",  // Optional: speed_first, cost_efficient, quality_focused, balanced, adaptive
  "max_cost": 0.5,         // Optional: Maximum cost constraint in USD
  "min_quality": 0.85,     // Optional: Minimum quality requirement (0-1)
  "timeout_ms": 30000      // Optional: Timeout in milliseconds
}
```

**Response:**
```json
{
  "routing_id": "550e8400-e29b-41d4-a716-446655440000",
  "domain": "research",
  "complexity": "complex",
  "strategy": "balanced",
  "collaboration_mode": "hierarchical",
  "supervisor_allocations": [
    {
      "supervisor_type": "research",
      "worker_count": 3,
      "refinement_rounds": 2,
      "estimated_latency_ms": 2500
    }
  ],
  "selected_models": [
    {
      "provider": "deepseek",
      "model_id": "deepseek-v3",
      "tier": "premium",
      "cost_per_token": 0.002,
      "quality_score": 0.95
    }
  ],
  "estimated_cost": 0.42,
  "estimated_latency_ms": 2500,
  "confidence_score": 0.91,
  "reasoning": "Complex research query requiring multi-agent coordination...",
  "alternatives": [
    {
      "strategy": "cost_efficient",
      "estimated_cost": 0.18,
      "estimated_latency": 1.8,
      "reason_not_selected": "Quality requirements exceeded cost-efficient capabilities"
    }
  ]
}
```

### 2. Estimate Cost

**POST** `/estimate-cost`

Get detailed cost estimation with breakdown for query execution.

**Request Body:**
```json
{
  "query": "Create a comprehensive market analysis report",
  "strategy": "quality_focused",
  "include_breakdown": true,
  "include_confidence": true
}
```

**Response:**
```json
{
  "estimated_cost": 0.68,
  "breakdown": {
    "model_costs": 0.45,
    "coordination_overhead": 0.18,
    "memory_operations": 0.05,
    "total_cost": 0.68,
    "confidence_interval": [0.54, 0.82]
  },
  "confidence_score": 0.85,
  "cost_factors": {
    "query_complexity": 0.85,
    "model_tier": 4.0,
    "supervisor_count": 1.0,
    "total_workers": 6.0,
    "refinement_rounds": 1.0
  },
  "recommendations": [
    "Consider using balanced strategy for 30% cost reduction",
    "Simple queries can use budget tier models effectively"
  ]
}
```

### 3. Evaluate Strategies

**POST** `/evaluate-strategies`

Compare multiple routing strategies for optimal selection.

**Request Body:**
```json
{
  "query": "Summarize recent AI research papers",
  "strategies": ["cost_efficient", "balanced", "quality_focused"],
  "weights": {
    "cost": 0.3,
    "quality": 0.5,
    "latency": 0.2
  }
}
```

**Response:**
```json
{
  "comparisons": [
    {
      "strategy": "balanced",
      "estimated_cost": 0.35,
      "estimated_quality": 0.85,
      "estimated_latency_ms": 2000,
      "pros": [
        "Good cost-quality trade-off",
        "Versatile for most queries",
        "Adaptive to complexity"
      ],
      "cons": [
        "Not optimal for any single metric",
        "May need tuning for specific use cases"
      ],
      "recommendation_score": 0.82
    }
  ],
  "recommended_strategy": "balanced",
  "reasoning": "For this moderate complexity query, balanced strategy optimizes...",
  "trade_offs": {
    "benefit": "Good all-around performance",
    "trade_off": "Not optimal for specific needs"
  }
}
```

### 4. Analyze Complexity

**POST** `/analyze-complexity`

Analyze query complexity with detailed feature breakdown.

**Request Body:**
```json
{
  "query": "Compare transformer architectures across different NLP tasks",
  "include_features": true,
  "include_recommendations": true
}
```

**Response:**
```json
{
  "complexity": "complex",
  "complexity_score": 0.78,
  "features": {
    "query_length": 9,
    "domain_count": 2,
    "reasoning_depth": 3,
    "data_requirements": [
      "Academic literature access",
      "Comparative data sets"
    ],
    "coordination_needs": "High coordination - multiple agents with refinement",
    "uncertainty_level": 0.3
  },
  "recommended_approach": "Hierarchical supervision with multiple refinement rounds",
  "routing_recommendations": [
    "Consider quality-focused strategy for best results",
    "Multiple refinement rounds recommended",
    "Allocate research supervisor with citation agents"
  ]
}
```

### 5. Submit Feedback

**POST** `/feedback`

Submit performance feedback for a completed routing decision.

> **Learning caveat.** This endpoint accepts feedback but cannot prove that an
> allocation executed or that quality came from an approved evaluator. It
> therefore returns `learning_updated: false`, `recorded: false`, and
> `eligible: false`. The durable adaptive boundary accepts only versioned,
> measured, evaluator-qualified outcomes. `ADAPTIVE_ROUTING_ENABLED` also
> defaults to `false`.

**Request Body:**
```json
{
  "routing_id": "550e8400-e29b-41d4-a716-446655440000",
  "actual_cost": 0.38,
  "actual_latency_ms": 2100,
  "quality_score": 0.92,
  "user_satisfaction": 0.95,
  "error_occurred": false
}
```

**Response:**
```json
{
  "status": "accepted",
  "routing_id": "550e8400-e29b-41d4-a716-446655440000",
  "feedback_processed": true,
  "learning_updated": false,
  "recorded": false,
  "eligible": false,
  "duplicate": false,
  "source": "manual",
  "reason": "manual_feedback_has_no_executed_allocation_or_evaluator_proof"
}
```

### 6. List Available Strategies

**GET** `/strategies`

Get list of available routing strategies with characteristics.

**Response:**
```json
{
  "strategies": [
    {
      "strategy": "cost_efficient",
      "name": "Cost Efficient",
      "description": "Minimizes cost while maintaining acceptable quality",
      "optimization_focus": "cost reduction",
      "use_cases": [
        "High-volume batch processing",
        "Non-critical queries",
        "Budget-constrained operations"
      ],
      "trade_offs": {
        "benefit": "60% cost reduction",
        "trade_off": "15-20% quality reduction"
      }
    }
  ],
  "default_strategy": "balanced",
  "total_count": 5
}
```

### 7. List Available Models

**GET** `/models`

Get available models and their tier classifications.

> **Static/illustrative response.** The model catalog and the `model_availability` block are hardcoded — `_check_model_availability()` (`src/api/services/masr_routing_service.py:1023`) returns `{deepseek: True, llama: True, gemini: True}` regardless of runtime flags. The **default runtime is Gemini-only** (`GEMINI_DEFAULT_MODEL=gemini-pro`); `DEEPSEEK_ENABLED`, `LLAMA_ENABLED`, and `MULTI_PROVIDER_ROUTING_ENABLED` all default `False`. Do not read this endpoint as live provider health. The `deepseek-v3` / `llama-3.3-70b` entries shown here are illustrative and not active by default.

**Response:**
```json
{
  "models": [
    {
      "provider": "deepseek",
      "model_id": "deepseek-v3",
      "tier": "premium",
      "cost_per_token": 0.002,
      "max_tokens": 128000,
      "capabilities": ["reasoning", "code", "analysis"],
      "average_latency_ms": 500,
      "quality_score": 0.95
    }
  ],
  "tiers": {
    "premium": ["deepseek-v3"],
    "standard": ["llama-3.3-70b", "gemini-pro"]
  },
  "total_count": 3,
  "providers": ["deepseek", "llama", "gemini"]
}
```

### 8. Get Router Status

**GET** `/status`

Get MASR router health and performance status.

> **Partially live, partially placeholder.** The `model_availability` map is
> hardcoded (see `/models` note). `learning_metrics` is live adaptive state:
> disabled, fixture-off, cold, degraded, or active status; policy/schema
> versions; revision/store health; outcome counters; per-arm readiness. It does
> not claim learning when evaluator evidence is absent. `total_routes`,
> `average_latency_ms`, `success_rate`, and per-strategy metrics are in-process
> counters that reset on restart. The specific numbers below are illustrative.

**Response:**
```json
{
  "status": "healthy",
  "uptime_seconds": 86400,
  "total_routes": 12543,
  "average_latency_ms": 2134,
  "success_rate": 0.98,
  "active_supervisors": 8,
  "performance_metrics": {
    "cost_efficient": {
      "requests": 3421,
      "success_rate": 0.97,
      "avg_cost": 0.18,
      "avg_latency_ms": 1500,
      "avg_quality": 0.78
    }
  },
  "model_availability": {
    "deepseek": true,
    "llama": true,
    "gemini": true
  },
  "learning_metrics": {
    "status": "disabled",
    "enabled": false,
    "effective": false,
    "policy_version": "masr-adaptive-v1",
    "schema_version": "1",
    "state_revision": 0,
    "store_healthy": true
  }
}
```

Fixture-mode execution forces adaptive routing and memory influence off before
selection and does not access Redis. Promotion is an offline operator workflow,
not an API endpoint. Use the versioned promotion-gate CLI documented in the
configuration reference; its report never enables adaptive routing.

## WebSocket Events

**Not yet implemented.** There is no MASR WebSocket endpoint. The route is commented out in the source (`src/api/routes/masr_api.py:340-341`, marked "future enhancement"). Real-time routing events over WebSocket are a planned feature, not a live capability — use the REST endpoints above. (WebSocket support does exist elsewhere in Cerebro, e.g. under `/ws` and the supervisor/talkhier routers, but not for MASR routing.)

## Error Handling

All endpoints return structured error responses:

```json
{
  "error": {
    "code": "INVALID_REQUEST",
    "message": "Invalid routing strategy",
    "details": {
      "provided_strategy": "ultra_fast",
      "valid_strategies": ["speed_first", "cost_efficient", "quality_focused", "balanced", "adaptive"]
    }
  }
}
```

The application-wide error adapter intentionally exposes the canonical nested
`error` envelope. Legacy route-local fields such as `suggestions` are not
included in the mounted HTTP response.

## Rate Limiting

A single **global** limiter applies across the application: **100 requests per minute** (`MAX_REQUESTS_PER_MINUTE=100`, `ENABLE_RATE_LIMITING=True`). There are no tiers, no burst allowance, and no per-endpoint configuration.

Rate limit information is returned in response headers:
```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1694184000
```

## Best Practices

### 1. Strategy Selection

- Use `cost_efficient` for high-volume, non-critical queries
- Use `quality_focused` for research and critical analysis
- Use `balanced` as default for most production workloads
- Use `speed_first` for real-time user interactions

### 2. Cost Optimization

- Set `max_cost` constraints to prevent runaway expenses
- Submit feedback to improve cost predictions
- Monitor the cost breakdown to identify optimization opportunities
- Use strategy evaluation endpoint to find optimal configurations

### 3. Performance Monitoring

- Track routing IDs for end-to-end tracing
- Submit feedback for all completed routes
- Monitor the status endpoint for system health
- Use WebSocket events for real-time monitoring

### 4. Error Recovery

- Implement exponential backoff for rate limit errors
- Use alternative strategies when primary fails
- Monitor error rates via the status endpoint
- Enable fallback mechanisms for high-uncertainty queries

## Integration Examples

### Python Example

```python
import httpx
import asyncio

class MASRClient:
    def __init__(self, api_key: str):
        self.client = httpx.AsyncClient(
            base_url="http://localhost:8000/api/v1/masr",
            # Bearer header is optional today — MASR routes are unauthenticated
            headers={"Authorization": f"Bearer {api_key}"}
        )
    
    async def route_query(self, query: str, strategy: str = "balanced"):
        response = await self.client.post("/route", json={
            "query": query,
            "strategy": strategy
        })
        return response.json()
    
    async def analyze_and_route(self, query: str):
        # First analyze complexity
        complexity = await self.client.post("/analyze-complexity", json={
            "query": query
        })
        
        # Then evaluate strategies
        strategies = await self.client.post("/evaluate-strategies", json={
            "query": query
        })
        
        # Finally route with best strategy
        best_strategy = strategies.json()["recommended_strategy"]
        routing = await self.route_query(query, best_strategy)
        
        return routing

# Usage
async def main():
    client = MASRClient("your-api-key")
    result = await client.analyze_and_route(
        "Analyze the impact of climate change on global agriculture"
    )
    print(f"Routing ID: {result['routing_id']}")
    print(f"Estimated cost: ${result['estimated_cost']:.2f}")
    print(f"Strategy: {result['strategy']}")

asyncio.run(main())
```

### Node.js Example

```javascript
const axios = require('axios');

class MASRClient {
  constructor(apiKey) {
    this.client = axios.create({
      baseURL: 'http://localhost:8000/api/v1/masr',
      // Bearer header is optional today — MASR routes are unauthenticated
      headers: { 'Authorization': `Bearer ${apiKey}` }
    });
  }
  
  async routeQuery(query, options = {}) {
    const response = await this.client.post('/route', {
      query,
      ...options
    });
    return response.data;
  }
  
  async optimizeForCost(query) {
    // Evaluate strategies first
    const evaluation = await this.client.post('/evaluate-strategies', {
      query,
      weights: { cost: 0.7, quality: 0.2, latency: 0.1 }
    });
    
    // Route with cost-optimized strategy
    return this.routeQuery(query, {
      strategy: evaluation.data.recommended_strategy,
      max_cost: 0.3
    });
  }
}

// Usage
const client = new MASRClient('your-api-key');

client.optimizeForCost('Summarize this document')
  .then(result => {
    console.log(`Cost optimized routing: $${result.estimated_cost}`);
    console.log(`Savings: ${(1 - result.estimated_cost / 0.5) * 100}%`);
  });
```

## Migration Guide

For users migrating from direct agent APIs:

### Before (Direct Agent Access)
```python
# Direct agent execution - no optimization
response = await api.post("/agents/literature-review/execute", {
    "query": query,
    "agent_config": {...}
})
```

### After (MASR Routing)
```python
# Intelligent routing with optimization
response = await api.post("/masr/route", {
    "query": query,
    "strategy": "balanced"
})
# Design goal: 50-60% cost reduction with maintained quality (research-paper
# target, not a Cerebro-measured result)
```

## Support

The `docs.cerebro.ai`, `status.cerebro.ai`, and `support@cerebro.ai` endpoints below are **aspirational and do not exist today**. For now, refer to the in-repo docs under `docs/` and the OpenAPI schema at `http://localhost:8000/docs` (served only when `DEBUG=True`).

- Documentation (aspirational): https://docs.cerebro.ai/masr
- API Status (aspirational): https://status.cerebro.ai
- Support (aspirational): support@cerebro.ai

## Changelog

> The dates and version tag below are historical and may be stale relative to the current code.

### v2.0.0 (September 2025)
- Initial release of MASR Dynamic Routing API
- 8 REST endpoints
- Analytics and feedback endpoints (closed-loop learning is a design goal, not yet implemented)
- Research-informed implementation; the 50-60% cost-reduction figure is a research-paper design goal, not a measured result

> Note: a MASR WebSocket endpoint was planned for this release but is not implemented (commented out in source).
