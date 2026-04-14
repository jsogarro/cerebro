# Agent Framework API Reference

## Overview

Complete API reference for Cerebro's Agent Framework APIs, including both the Primary API (MASR-routed) and Bypass API (direct access) with comprehensive examples, request/response schemas, and usage patterns.

## API Base URL

- **Development**: `http://localhost:8000`
- **Production**: `https://api.cerebro.ai`

## Authentication

**Current**: Basic validation only (full authentication in Task #18)
**Future**: JWT tokens, API keys, role-based access control

---

## Primary API Endpoints (Recommended - 90% usage)

### Intelligent Query API (`/api/v1/query/*`)

The Primary API routes all requests through MASR intelligence for optimal agent selection, cost optimization, and quality assurance.

#### Research Query Endpoint

```http
POST /api/v1/query/research
```

**Purpose**: General research queries with intelligent MASR routing  
**Research Basis**: "MasRouter: Learning to Route LLMs" cost optimization patterns

**Request Schema**:
```json
{
  "query": "What are the ethical implications of AI in healthcare?",
  "domains": ["ai", "healthcare", "ethics"],
  "context": {
    "user_preference": "comprehensive",
    "time_sensitivity": "normal"
  },
  "routing_strategy": "quality_focused",
  "quality_preference": 0.9,
  "cost_preference": 0.3,
  "enable_real_time_updates": true,
  "timeout_seconds": 300,
  "user_id": "researcher-123",
  "session_id": "session-456"
}
```

**Response Schema**:
```json
{
  "execution_id": "exec-789",
  "query_id": "query-101112", 
  "status": "completed",
  "routing_decision": {
    "collaboration_mode": "hierarchical",
    "supervisor_type": "research",
    "estimated_cost": 0.025,
    "estimated_quality": 0.91,
    "confidence_score": 0.87
  },
  "results": {
    "literature_findings": [...],
    "ethical_analysis": [...],
    "recommendations": [...]
  },
  "quality_scores": {
    "overall": 0.89,
    "consensus": 0.92
  },
  "confidence": 0.88,
  "routing_time_ms": 45.2,
  "execution_time_seconds": 267.8,
  "started_at": "2025-09-08T10:30:00Z",
  "completed_at": "2025-09-08T10:34:27Z"
}
```

**Example Usage**:
```bash
curl -X POST "http://localhost:8000/api/v1/query/research" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Impact of AI on employment in healthcare sector",
    "domains": ["ai", "healthcare", "employment"],
    "routing_strategy": "balanced"
  }'
```

#### Analysis Query Endpoint

```http
POST /api/v1/query/analyze
```

**Purpose**: Analysis-focused queries with methodological emphasis  
**Research Basis**: Domain-specific optimization for analytical tasks

**Request Schema**:
```json
{
  "query": "Analyze the effectiveness of remote learning technologies",
  "analysis_type": "comparative",
  "domains": ["education", "technology"],
  "depth": "comprehensive",
  "include_methodology": true,
  "include_citations": true,
  "enable_comparison": true,
  "context": {},
  "user_id": "analyst-456"
}
```

**Example Usage**:
```bash
curl -X POST "http://localhost:8000/api/v1/query/analyze" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Effectiveness of renewable energy policies",
    "analysis_type": "comprehensive",
    "include_methodology": true
  }'
```

#### Synthesis Query Endpoint

```http
POST /api/v1/query/synthesize
```

**Purpose**: Synthesis-focused queries for integration and narrative building

**Request Schema**:
```json
{
  "query": "Synthesize findings on AI impact across multiple sectors",
  "synthesis_focus": "comprehensive",
  "source_materials": [
    {"source": "healthcare_analysis", "content": "..."},
    {"source": "education_study", "content": "..."}
  ],
  "narrative_style": "academic",
  "include_visualizations": true,
  "citation_style": "APA",
  "context": {},
  "user_id": "researcher-789"
}
```

### Execution Status and Results

#### Get Execution Status

```http
GET /api/v1/query/execution/{execution_id}/status
```

**Response**:
```json
{
  "execution_id": "exec-789",
  "status": "running",
  "progress_percentage": 65.0,
  "current_phase": "synthesis",
  "supervisor_type": "research",
  "workers_used": 4,
  "execution_time_seconds": 142.3,
  "errors": []
}
```

#### Get Execution Results

```http
GET /api/v1/query/execution/{execution_id}/results
```

**Response**: Complete execution results with all agent outputs, quality scores, and metadata.

### Routing Intelligence Endpoints

#### Get Routing Recommendation

```http
GET /api/v1/query/routing/recommend?query=Complex+analysis+query
```

**Response**:
```json
{
  "query_analysis": {
    "complexity": "moderate",
    "estimated_domains": ["research"],
    "confidence": 0.85
  },
  "routing_recommendation": {
    "suggested_strategy": "balanced",
    "expected_agents": ["literature-review", "methodology", "synthesis"],
    "estimated_cost": 0.015,
    "estimated_time_seconds": 180,
    "estimated_quality": 0.87
  },
  "explanation": "Query classified as moderate complexity - routing through balanced strategy"
}
```

#### Get Available Routing Strategies

```http
GET /api/v1/query/routing/strategies
```

**Response**: Complete list of routing strategies with characteristics and use cases.

---

## Bypass API Endpoints (Specialized - 10% usage)

### Direct Agent API (`/api/v1/agents/*`)

The Bypass API provides direct access to individual agents and manual execution pattern control.

#### List Available Agents

```http
GET /api/v1/agents
```

**Response**:
```json
{
  "agents": [
    {
      "agent_type": "literature-review",
      "name": "Literature Review Agent",
      "description": "Searches and analyzes academic literature from multiple databases",
      "capabilities": ["database_search", "source_evaluation"],
      "average_execution_time_ms": 45000,
      "reliability_score": 0.95,
      "quality_score": 0.90,
      "endpoints": [
        "/api/v1/agents/literature-review/execute",
        "/api/v1/agents/literature-review/metrics"
      ]
    }
  ],
  "total_agents": 5,
  "system_health": "healthy"
}
```

#### Get Agent Information

```http
GET /api/v1/agents/{agent_type}
```

**Agent Types**: `literature-review`, `citation`, `methodology`, `comparative-analysis`, `synthesis`

**Example**:
```bash
curl "http://localhost:8000/api/v1/agents/literature-review"
```

#### Execute Single Agent

```http
POST /api/v1/agents/{agent_type}/execute
```

**Request Schema**:
```json
{
  "query": "Find recent papers on AI ethics in healthcare",
  "context": {"domain": "healthcare"},
  "parameters": {
    "max_sources": 50,
    "date_range": "2020-2025"
  },
  "timeout_seconds": 300,
  "quality_threshold": 0.8,
  "enable_refinement": true,
  "max_refinement_rounds": 3,
  "user_id": "researcher-123"
}
```

**Response Schema**:
```json
{
  "execution_id": "agent-exec-123",
  "agent_type": "literature-review", 
  "status": "completed",
  "output": {
    "sources": [...],
    "analysis": {...},
    "quality_metrics": {...}
  },
  "confidence": 0.87,
  "quality_score": 0.89,
  "execution_time_seconds": 42.3,
  "refinement_rounds": 2,
  "consensus_achieved": true,
  "started_at": "2025-09-08T10:30:00Z",
  "completed_at": "2025-09-08T10:30:42Z",
  "errors": [],
  "warnings": []
}
```

**Example Usage**:
```bash
curl -X POST "http://localhost:8000/api/v1/agents/literature-review/execute" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "AI ethics in healthcare applications",
    "parameters": {"max_sources": 25}
  }'
```

#### Execute Chain-of-Agents

```http
POST /api/v1/agents/chain
```

**Request Schema**:
```json
{
  "query": "Comprehensive AI ethics analysis",
  "agent_chain": ["literature-review", "methodology", "comparative-analysis", "synthesis"],
  "context": {"analysis_depth": "comprehensive"},
  "pass_intermediate_results": true,
  "early_stopping": false,
  "quality_threshold": 0.85,
  "timeout_per_agent_seconds": 180,
  "enable_validation": true
}
```

**Response Schema**:
```json
{
  "execution_id": "chain-exec-456",
  "status": "completed",
  "agent_chain": ["literature-review", "methodology", "comparative-analysis", "synthesis"],
  "intermediate_results": [
    {"agent": "literature-review", "output": {...}},
    {"agent": "methodology", "output": {...}},
    {"agent": "comparative-analysis", "output": {...}},
    {"agent": "synthesis", "output": {...}}
  ],
  "final_result": {"comprehensive_analysis": "..."},
  "overall_confidence": 0.88,
  "chain_quality_score": 0.91,
  "quality_improvement": 0.15,
  "total_execution_time_seconds": 267.4,
  "agent_execution_times": [45.2, 67.8, 78.1, 76.3],
  "early_stopped": false
}
```

#### Execute Mixture-of-Agents

```http
POST /api/v1/agents/mixture
```

**Request Schema**:
```json
{
  "query": "Multi-perspective analysis of AI regulation",
  "agent_types": ["literature-review", "methodology", "comparative-analysis"],
  "context": {"perspective": "multi-stakeholder"},
  "aggregation_strategy": "consensus",
  "weight_by_confidence": true,
  "consensus_threshold": 0.8,
  "timeout_seconds": 300,
  "max_parallel": 3
}
```

**Response Schema**:
```json
{
  "execution_id": "mixture-exec-789",
  "status": "completed",
  "agent_types": ["literature-review", "methodology", "comparative-analysis"],
  "agent_results": {
    "literature-review": {"output": {...}, "confidence": 0.87},
    "methodology": {"output": {...}, "confidence": 0.82},
    "comparative-analysis": {"output": {...}, "confidence": 0.91}
  },
  "aggregated_result": {"consensus_analysis": "..."},
  "consensus_score": 0.87,
  "agent_weights": {
    "literature-review": 0.33,
    "methodology": 0.31,
    "comparative-analysis": 0.36
  },
  "consensus_achieved": true,
  "parallel_efficiency": 1.8,
  "mixture_quality_score": 0.88,
  "inter_agent_agreement": 0.85
}
```

### Agent-Specific Endpoints

#### Validate Agent Input

```http
POST /api/v1/agents/{agent_type}/validate
```

**Purpose**: Validate query and parameters before execution

**Request**:
```json
{
  "agent_type": "literature-review",
  "query": "Find papers on machine learning in education",
  "parameters": {"max_sources": 100}
}
```

**Response**:
```json
{
  "valid": true,
  "validation_score": 0.92,
  "query_suitability": 0.89,
  "estimated_quality": 0.85,
  "estimated_cost": 0.012,
  "recommendations": ["Consider narrowing domain scope for better results"],
  "validation_issues": []
}
```

#### Agent Performance Metrics

```http
GET /api/v1/agents/{agent_type}/metrics
```

**Response**:
```json
{
  "agent_type": "literature-review",
  "total_executions": 1247,
  "success_rate": 0.94,
  "average_execution_time_ms": 43200,
  "average_quality_score": 0.87,
  "average_cost_per_execution": 0.014,
  "quality_trend_7_days": 0.03,
  "recent_success_rate": 0.96,
  "peak_usage_hour": 14,
  "most_common_domains": ["research", "academic"],
  "complexity_distribution": {
    "simple": 156,
    "moderate": 623,
    "complex": 468
  }
}
```

#### Agent Health Status

```http
GET /api/v1/agents/{agent_type}/health
```

**Response**:
```json
{
  "agent_type": "literature-review",
  "status": "healthy",
  "success_rate_24h": 0.96,
  "average_response_time_ms": 41200,
  "error_rate": 0.04,
  "resource_utilization": 0.67,
  "queue_length": 3,
  "current_issues": [],
  "last_health_check": "2025-09-08T15:45:00Z"
}
```

### Convenience Endpoints

#### Literature Search

```http
POST /api/v1/agents/literature-review/search?query=AI+ethics&max_sources=25&domains=ai,ethics
```

#### Citation Formatting

```http
POST /api/v1/agents/citation/format?sources=["Source1","Source2"]&style=APA
```

#### Synthesis

```http
POST /api/v1/agents/synthesis/combine?synthesis_focus=comprehensive
```

### System Monitoring Endpoints

#### System Statistics

```http
GET /api/v1/agents/system/stats
```

#### Active Executions

```http
GET /api/v1/agents/executions/active
```

#### Health Summary

```http
GET /api/v1/agents/health/summary
```

#### Performance Comparison

```http
GET /api/v1/agents/performance/comparison?metric=quality_score&time_period_hours=24
```

---

## WebSocket Real-Time API

### Agent Execution Streaming

```javascript
// Connect to execution progress stream
const ws = new WebSocket('ws://localhost:8000/ws/query/execution/exec-123');

ws.onmessage = (event) => {
    const update = JSON.parse(event.data);
    console.log('Progress:', update.progress_percentage);
    console.log('Phase:', update.current_phase);
    console.log('Quality:', update.quality_scores);
};
```

### Interactive Agent Sessions

```javascript
// Interactive agent conversation
const ws = new WebSocket('ws://localhost:8000/ws/agents/literature-review/interactive');

// Send query
ws.send(JSON.stringify({
    "action": "execute",
    "query": "Find papers on AI ethics",
    "parameters": {"max_sources": 20}
}));

// Receive real-time results
ws.onmessage = (event) => {
    const result = JSON.parse(event.data);
    if (result.type === 'progress') {
        console.log('Search progress:', result.sources_found);
    } else if (result.type === 'result') {
        console.log('Final results:', result.sources);
    }
};
```

---

## Error Handling

### Standard Error Response

```json
{
  "error": {
    "code": "AGENT_EXECUTION_FAILED",
    "message": "Literature review agent execution failed",
    "details": {
      "agent_type": "literature-review",
      "execution_id": "exec-123",
      "error_category": "timeout",
      "recoverable": true,
      "suggested_action": "Retry with increased timeout"
    },
    "timestamp": "2025-09-08T15:45:00Z"
  }
}
```

### Common Error Codes

- `AGENT_NOT_FOUND`: Specified agent type doesn't exist
- `INVALID_PARAMETERS`: Request parameters don't meet validation requirements
- `EXECUTION_TIMEOUT`: Agent execution exceeded timeout limit
- `CAPACITY_EXCEEDED`: System at maximum concurrent execution capacity
- `ROUTING_FAILED`: MASR routing decision failed
- `QUALITY_THRESHOLD_NOT_MET`: Result quality below required threshold

### Error Recovery Strategies

- **Automatic Retry**: Failed executions automatically retried with exponential backoff
- **Fallback Routing**: MASR provides fallback routing strategies for failures
- **Quality Recovery**: Low-quality results trigger refinement rounds
- **Graceful Degradation**: System maintains core functionality during partial failures

---

## Rate Limiting

### Current Limits (Development)

- **Primary API**: 100 requests/hour per user
- **Bypass API**: 50 requests/hour per user  
- **WebSocket**: 10 concurrent connections per user
- **System Total**: 1000 concurrent executions

### Future Authentication-Aware Limits (Task #18)

Rate limits will be aligned with MASR cost predictions and user roles:

- **Cost-Based Limiting**: Expensive operations have lower rate limits
- **Role-Based Limits**: Higher limits for premium users and service accounts
- **Dynamic Adjustment**: Rate limits adjust based on system load and user behavior

---

## Performance Guidelines

### Optimal Usage Patterns

#### For Best Performance:
- ✅ Use Primary API for production workloads (automatic optimization)
- ✅ Provide relevant context and domain hints for better routing
- ✅ Enable real-time updates for long-running queries
- ✅ Use appropriate timeout values based on query complexity

#### For Cost Optimization:
- ✅ Use `cost_efficient` routing strategy for budget-conscious queries
- ✅ Provide accurate domain hints to avoid unnecessary cross-domain routing
- ✅ Cache results when appropriate for repeated similar queries
- ✅ Monitor usage through metrics endpoints

#### For Quality Optimization:
- ✅ Use `quality_focused` routing strategy for critical analysis
- ✅ Enable refinement rounds for important queries
- ✅ Use Mixture-of-Agents pattern for consensus building
- ✅ Monitor quality scores and confidence metrics

### Performance Expectations

| Query Type | Primary API Latency | Bypass API Latency | Quality Score |
|------------|--------------------|--------------------|---------------|
| Simple | 2.1s | 1.2s | 0.82 |
| Moderate | 3.4s | 2.8s | 0.87 |
| Complex | 5.8s | 4.9s | 0.91 |

---

## Examples and Use Cases

### Production Research Workflow

```python
import asyncio
import httpx

async def comprehensive_research(query: str, domains: list):
    """Production research workflow using Primary API."""
    
    async with httpx.AsyncClient() as client:
        # Start intelligent research
        response = await client.post(
            "http://localhost:8000/api/v1/query/research",
            json={
                "query": query,
                "domains": domains,
                "routing_strategy": "quality_focused",
                "enable_real_time_updates": True
            }
        )
        
        execution_id = response.json()["execution_id"]
        
        # Monitor progress
        while True:
            status = await client.get(
                f"/api/v1/query/execution/{execution_id}/status"
            )
            
            status_data = status.json()
            print(f"Progress: {status_data['progress_percentage']}%")
            
            if status_data["status"] in ["completed", "failed"]:
                break
                
            await asyncio.sleep(5)
        
        # Get final results
        results = await client.get(
            f"/api/v1/query/execution/{execution_id}/results"
        )
        
        return results.json()

# Usage
results = await comprehensive_research(
    "AI impact on educational outcomes",
    ["ai", "education"]
)
```

### Development Testing Workflow

```python
async def test_agent_performance(agent_type: str, test_queries: list):
    """Test specific agent using Bypass API."""
    
    async with httpx.AsyncClient() as client:
        results = []
        
        for query in test_queries:
            response = await client.post(
                f"http://localhost:8000/api/v1/agents/{agent_type}/execute",
                json={
                    "query": query,
                    "enable_refinement": False,  # Faster testing
                    "timeout_seconds": 120
                }
            )
            
            results.append(response.json())
        
        # Analyze performance
        avg_quality = sum(r["quality_score"] for r in results) / len(results)
        avg_time = sum(r["execution_time_seconds"] for r in results) / len(results)
        
        print(f"Agent {agent_type} - Avg Quality: {avg_quality:.3f}, Avg Time: {avg_time:.1f}s")
        
        return results

# Usage
test_results = await test_agent_performance(
    "literature-review",
    ["AI ethics", "Machine learning applications", "Educational technology"]
)
```

### Custom Workflow Example

```python
async def custom_research_workflow(query: str):
    """Custom workflow using Chain-of-Agents pattern."""
    
    async with httpx.AsyncClient() as client:
        # Execute custom chain
        response = await client.post(
            "http://localhost:8000/api/v1/agents/chain",
            json={
                "query": query,
                "agent_chain": ["literature-review", "methodology", "synthesis"],
                "pass_intermediate_results": True,
                "early_stopping": True,
                "quality_threshold": 0.9
            }
        )
        
        chain_result = response.json()
        
        # If quality not sufficient, run mixture for consensus
        if chain_result["chain_quality_score"] < 0.9:
            mixture_response = await client.post(
                "http://localhost:8000/api/v1/agents/mixture",
                json={
                    "query": query,
                    "agent_types": ["literature-review", "comparative-analysis", "synthesis"],
                    "aggregation_strategy": "consensus"
                }
            )
            return mixture_response.json()
        
        return chain_result
```

---

## Integration Examples

### MASR Routing Decision Analysis

```python
async def analyze_routing_decisions(queries: list):
    """Analyze MASR routing decisions for optimization."""
    
    async with httpx.AsyncClient() as client:
        routing_data = []
        
        for query in queries:
            # Get routing recommendation
            recommendation = await client.get(
                "/api/v1/query/routing/recommend",
                params={"query": query}
            )
            
            # Execute with recommended strategy
            execution = await client.post(
                "/api/v1/query/research",
                json={
                    "query": query,
                    "routing_strategy": recommendation.json()["routing_recommendation"]["suggested_strategy"]
                }
            )
            
            routing_data.append({
                "query": query,
                "recommendation": recommendation.json(),
                "execution": execution.json()
            })
        
        return routing_data
```

### Multi-Domain Coordination

```python
async def cross_domain_analysis(query: str, domains: list):
    """Example of cross-domain query handling."""
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8000/api/v1/query/research",
            json={
                "query": query,
                "domains": domains,
                "context": {
                    "cross_domain": True,
                    "require_synthesis": True
                },
                "routing_strategy": "quality_focused"
            }
        )
        
        execution_id = response.json()["execution_id"]
        
        # MASR will automatically coordinate multiple supervisors
        # for cross-domain queries
        
        results = await wait_for_completion(client, execution_id)
        return results
```

---

## Next Steps

### Upcoming API Enhancements (Weeks 2-4)

**Week 2: MASR Dynamic Routing API** (`/api/v1/masr/*`)
- Expose full MASR routing intelligence and cost optimization capabilities
- Strategy evaluation and adaptation endpoints
- Real-time cost prediction and optimization

**Week 3: Hierarchical Supervisor API** (`/api/v1/supervisors/*`)
- Multi-supervisor coordination for complex cross-domain queries
- Supervisor health monitoring and capability management
- Worker pool allocation and performance optimization

**Week 4: TalkHier Protocol API** (`/api/v1/talkhier/*`)
- Structured communication and multi-round refinement
- Interactive WebSocket sessions with consensus building
- Enterprise-grade quality assurance and validation

### Integration with Future Features

- **Authentication Strategy (Task #18)**: Secure access control and cost-aware rate limiting
- **A/B Testing System (Task #16)**: Experiment management and optimization
- **Advanced Patterns**: Enhanced Chain-of-Agents and Mixture-of-Agents implementations

The Agent Framework APIs establish Cerebro as a cutting-edge platform that exposes sophisticated multi-agent capabilities through research-validated, production-ready interfaces that balance automation with control, cost with quality, and sophistication with usability.