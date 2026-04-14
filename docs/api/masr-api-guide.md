# MASR Dynamic Routing API Guide

## Overview

The MASR (Multi-Agent System Router) Dynamic Routing API exposes Cerebro's intelligent routing capabilities through RESTful endpoints and WebSocket connections. Based on the "MasRouter: Learning to Route LLMs" research, this API enables cost-optimized, quality-aware routing decisions that can reduce costs by 50-60% while maintaining high output quality.

## Key Features

- **Intelligent Routing**: Automatic supervisor and model selection based on query analysis
- **Cost Optimization**: Real-time cost estimation with hierarchical breakdown
- **Strategy Evaluation**: Compare multiple routing strategies for optimal selection
- **Complexity Analysis**: Deep query analysis with feature extraction
- **Learning Integration**: Continuous improvement through feedback loops
- **Real-time Updates**: WebSocket support for live routing events
- **Performance Analytics**: Comprehensive metrics and trend analysis

## Base URL

```
https://api.cerebro.ai/api/v1/masr
```

## Authentication

All endpoints require authentication via Bearer token:

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
  "strategy": "balanced",  // Optional: cost_efficient, quality_focused, balanced, speed_optimized
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
    "query_complexity": "complex",
    "model_tier": "premium",
    "supervisor_count": 2,
    "total_workers": 6,
    "refinement_rounds": 3
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

Submit performance feedback for continuous learning and optimization.

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
  "learning_updated": true
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
  "total_count": 4
}
```

### 7. List Available Models

**GET** `/models`

Get available models and their tier classifications.

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
    "standard": ["llama-3.3-70b", "gemini-pro"],
    "budget": ["llama-3.1-8b"]
  },
  "total_count": 5,
  "providers": ["deepseek", "llama", "gemini"]
}
```

### 8. Get Router Status

**GET** `/status`

Get MASR router health and performance status.

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
    "total_feedback": 8934,
    "cost_prediction_accuracy": 0.89,
    "quality_prediction_accuracy": 0.91,
    "last_model_update": "2025-09-08T12:00:00Z"
  }
}
```

## WebSocket Events

Connect to real-time routing events via WebSocket:

```javascript
const ws = new WebSocket('wss://api.cerebro.ai/api/v1/masr/ws');

ws.on('message', (event) => {
  const data = JSON.parse(event);
  
  switch(data.event_type) {
    case 'routing_started':
      console.log('Routing initiated:', data.routing_id);
      break;
      
    case 'cost_update':
      console.log('Cost optimized:', data.cost_reduction_percent);
      break;
      
    case 'strategy_change':
      console.log('Strategy updated:', data.current_best);
      break;
      
    case 'routing_complete':
      console.log('Routing complete:', data.final_decision);
      break;
  }
});
```

## Error Handling

All endpoints return structured error responses:

```json
{
  "error": "Invalid routing strategy",
  "error_code": "INVALID_REQUEST",
  "details": {
    "provided_strategy": "ultra_fast",
    "valid_strategies": ["cost_efficient", "balanced", "quality_focused", "speed_optimized"]
  },
  "suggestions": [
    "Check query format",
    "Verify strategy is valid"
  ]
}
```

## Rate Limiting

- **Standard tier**: 100 requests per minute
- **Premium tier**: 1000 requests per minute
- **Enterprise tier**: Unlimited

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
- Use `speed_optimized` for real-time user interactions

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
            base_url="https://api.cerebro.ai/api/v1/masr",
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
      baseURL: 'https://api.cerebro.ai/api/v1/masr',
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
response = await api.post("/agents/research/execute", {
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
# 50-60% cost reduction with maintained quality
```

## Support

For questions and support:
- Documentation: https://docs.cerebro.ai/masr
- API Status: https://status.cerebro.ai
- Support: support@cerebro.ai

## Changelog

### v2.0.0 (September 2025)
- Initial release of MASR Dynamic Routing API
- 8 production endpoints with full routing intelligence
- WebSocket support for real-time events
- Comprehensive analytics and learning integration
- Research-validated implementation achieving 50-60% cost reduction