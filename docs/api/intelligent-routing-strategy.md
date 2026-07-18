# Intelligent Routing Strategy

## Overview

Cerebro's Agent Framework APIs implement a **research-informed routing strategy** that prioritizes intelligent orchestration over direct agent access. This approach is based on academic research showing significant performance and cost benefits from learned routing decisions.

## Research Foundation

### "MasRouter: Learning to Route LLMs for Multi-Agent Systems" (2025)

**Key Finding**: Dynamic LLM routing achieves **50-60% cost reduction** compared to static approaches while maintaining or improving quality.

**Implementation in Cerebro**:
- Primary API always routes through MASR (Multi-Agent System Router)
- Every request runs a multi-dimensional complexity analysis and cost-optimization pass
- Cost-quality trade-offs are chosen per request from query characteristics and the selected routing strategy
- Memory-informed and adaptive (Thompson-sampling bandit) routing enhancements exist but are **flag-gated OFF by default** (`MEMORY_INFORMED_ROUTING_ENABLED=False`, `ADAPTIVE_ROUTING_ENABLED=False`); routing does not currently learn across requests unless these flags are enabled

### "LLMs Working in Harmony" (2025)

**Key Findings**: 
- **Chain-of-Agents**: Sequential execution improves quality through building results
- **Mixture-of-Agents**: Parallel execution with aggregation enhances consensus
- **20-25% quality improvement** over single-agent baselines

**Implementation in Cerebro**:
- MASR automatically selects Chain vs Mixture patterns based on query analysis
- Intelligent aggregation strategies for parallel execution results
- Quality-driven early stopping and validation mechanisms

### "Talk Structurally, Act Hierarchically" (2025)

**Key Findings**: Hierarchical coordination with structured communication protocols achieves superior performance through explicit coordination patterns.

**Implementation in Cerebro**:
- Primary API uses hierarchical supervisors for agent coordination
- TalkHier protocol ensures structured communication and quality assurance
- Multi-round refinement and consensus building integrated into routing decisions

## Routing Strategy Architecture

### Primary API: Intelligence-First Routing (design-target ~90% of traffic)

```
User Request → MASR Analysis → (FAST_PATH single LLM call) or (Supervisor Selection → Agent Coordination) → Response
```

> The ~90% Primary / ~10% Bypass split is an **aspirational design target, not a measured usage figure.**

MASR may select a `FAST_PATH` collaboration mode, which makes a **single LLM call that bypasses supervisors entirely**. If the fast-path response fails a minimal quality gate, it silently escalates: the routing decision's mode is mutated to `DIRECT` and the request falls through to full supervisor execution — so one request can incur both a fast-path call and a supervisor run.

#### Benefits (design goals):
- ✅ **Cost Optimization**: intelligent model selection targeting the **50-60% cost reduction** reported in the cited routing research (a research-paper target, not a Cerebro measurement)
- ✅ **Quality Assurance**: coordinated multi-agent execution targeting the **20-25% quality improvement** reported in the cited collaboration research (a research-paper target, not a Cerebro measurement)
- ✅ **Optional Learning**: memory-informed and adaptive routing feedback loops available behind feature flags (OFF by default)
- ✅ **Scalability**: centralized routing enables system-wide optimization

#### When to Use:
- **Production workloads** requiring cost efficiency and reliability
- **General research tasks** where quality and cost optimization matter
- **Learning systems** that should improve from usage patterns
- **Enterprise deployment** requiring predictable performance and costs

### Bypass API: Direct Access (design-target ~10% of traffic)

```
User Request → Direct Agent Execution → Response
```

#### Benefits:
- ✅ **Direct Control**: Manual specification of exact execution patterns
- ✅ **Low Latency**: No routing overhead for simple direct execution
- ✅ **Development Support**: Debugging and testing capabilities
- ✅ **Flexibility**: Custom workflow creation and experimental patterns

#### When to Use:
- **Development and testing** requiring direct agent access
- **Research and experimentation** with specific agent combinations
- **Custom workflows** with specialized coordination requirements
- **Third-party integration** needing specific agent interaction patterns

## MASR Routing Intelligence

### Query Complexity Analysis

MASR analyzes queries across multiple dimensions to determine optimal routing:

```python
@dataclass
class ComplexityFactors:
    linguistic_complexity: float = 0.0    # Word choice, sentence structure
    reasoning_depth: float = 0.0          # Required analytical thinking
    domain_breadth: float = 0.0           # Cross-domain requirements
    data_requirements: float = 0.0        # External data needed
    output_complexity: float = 0.0        # Expected output sophistication
    time_sensitivity: float = 0.0         # Urgency and latency requirements
    quality_requirements: float = 0.0     # Accuracy and validation needs
```

### Routing Decision Process

1. **Complexity Analysis**: Multi-dimensional query evaluation
2. **Cost Optimization**: Model selection balancing cost, quality, and latency
3. **Collaboration Mode Selection**: Choose optimal agent coordination pattern
4. **Agent Allocation**: Determine specific agents and resource allocation
5. **Performance Prediction**: Estimate cost, quality, and execution time

### Collaboration Modes

The `CollaborationMode` taxonomy is: `FAST_PATH`, `DIRECT`, `PARALLEL`, `HIERARCHICAL`, `DEBATE`, `ENSEMBLE`.

**Fast-Path Mode**: Single LLM call that bypasses supervisors entirely for the simplest queries
- **Use Case**: Trivial, well-scoped questions where full coordination is unnecessary
- **Benefits**: Lowest latency and cost; falls back to `DIRECT` supervisor execution if the response fails a minimal quality gate

**Direct Mode**: Single agent handles simple, well-defined queries
- **Use Case**: Basic questions, simple analysis tasks
- **Benefits**: Fast execution, low cost, minimal coordination overhead

**Parallel Mode**: Multiple agents work simultaneously on different aspects
- **Use Case**: Multi-faceted queries, comprehensive analysis
- **Benefits**: Faster execution than sequential, diverse perspectives

**Hierarchical Mode**: Supervisor coordinates specialist workers
- **Use Case**: Complex, multi-step processes requiring coordination
- **Benefits**: Structured execution, quality assurance, error recovery

**Debate Mode**: Agents discuss and refine responses
- **Use Case**: High-uncertainty topics, controversial subjects
- **Benefits**: Enhanced consensus, reduced bias, improved confidence

**Ensemble Mode**: Multiple approaches combined through voting
- **Use Case**: Critical decisions, maximum quality requirements
- **Benefits**: Highest quality, uncertainty quantification, robustness

## Agent Execution Patterns

### Chain-of-Agents (Sequential Execution)

**Pattern**: `Agent₁ → Agent₂ → Agent₃ → ... → Final Result`

**Research Basis**: "LLMs Working in Harmony" shows sequential agent execution improves quality through iterative refinement.

**Implementation**:
```http
POST /api/v1/agents/chain
{
  "query": "Analyze AI ethics in healthcare",
  "agent_chain": ["literature-review", "methodology", "comparative-analysis", "synthesis"],
  "pass_intermediate_results": true,
  "early_stopping": false
}
```

**Benefits**:
- **Quality Building**: Each agent builds on previous results
- **Structured Workflow**: Clear progression through analysis steps
- **Intermediate Validation**: Quality checks between agents
- **Error Recovery**: Early detection of issues in the chain

**Use Cases**:
- Literature analysis workflows
- Methodology development processes
- Comprehensive research projects
- Multi-step analysis tasks

### Mixture-of-Agents (Parallel Execution)

**Pattern**: `Agent₁ ∥ Agent₂ ∥ Agent₃ → Aggregation → Final Result`

**Research Basis**: "LLMs Working in Harmony" demonstrates parallel agent execution with intelligent aggregation achieves superior consensus.

**Implementation**:
```http
POST /api/v1/agents/mixture
{
  "query": "Evaluate AI impact on education",
  "agent_types": ["literature-review", "methodology", "comparative-analysis"],
  "aggregation_strategy": "consensus",
  "weight_by_confidence": true,
  "consensus_threshold": 0.8
}
```

**Benefits**:
- **Parallel Efficiency**: Faster than sequential execution
- **Diverse Perspectives**: Multiple agent viewpoints on same query
- **Consensus Building**: Intelligent aggregation with conflict resolution
- **Quality Enhancement**: Multiple validations improve accuracy

**Use Cases**:
- Critical decision making
- Comprehensive analysis requiring multiple perspectives
- Quality validation and consensus building
- Time-sensitive research with parallel processing needs

## Performance Characteristics

> Cerebro does not yet publish benchmarked latency, cost, or concurrency figures for either API tier. The two tiers differ structurally as described below; concrete numbers should come from a measured evaluation, not this document.

### Primary API

- **Cost**: intended to be lower than naive direct model access via MASR model selection (see the cited-research targets above)
- **Quality**: coordinated multi-agent execution and a verification QA gate
- **Overhead**: adds routing, supervisor coordination, and (for multi-domain queries) concurrent supervisor fan-out

### Bypass API

- **Cost**: variable, determined by the agent/model the caller selects
- **Quality**: depends on manual agent selection
- **Overhead**: no MASR routing; direct agent execution

### WebSocket Real-Time Updates

Both API tiers support real-time progress tracking:

- **Connection Types**: Project-specific, agent-specific, system-wide
- **Update Frequency**: Real-time with sub-second latency
- **Event Types**: Execution progress, quality metrics, error notifications
- **Scalability**: Redis pub/sub enables horizontal scaling

## Integration with Cerebro Architecture

### MASR-Hierarchical System Integration

The Agent Framework APIs build on Cerebro's existing MASR-Hierarchical Communication Integration:

- **MASRouter**: The in-process `MASRouter` class provides intelligent routing decisions for the Primary API
- **MASRSupervisorBridge**: Maps MASR routing decisions to the appropriate domain supervisor (Research, Content, Analytics, Finance)
- **Domain Supervisors**: Each runs an internal LangGraph `StateGraph` to coordinate its worker team; LangGraph exists only inside supervisors
- **Multi-domain execution**: There is no dedicated "orchestrator" class — cross-domain queries fan out **inline** in `DirectExecutionService._execute_research_workflow`, running per-domain supervisor calls concurrently under an `asyncio.Semaphore`

### Memory System Integration

Cerebro's configuration defines a four-tier memory design (working / episodic / semantic / procedural), but this is currently **config-only and not implemented**. `src/memory` is a stub: `WorkingMemoryManager` stores to a plain in-process dict, `EpisodicMemoryService.get_recent_context` returns an empty list, and the semantic/procedural services return empties. The backing stores implied by config (Redis / Postgres / Qdrant / JSON) are not wired up.

- **Working Memory** (design): short-term context and conversation state — currently a plain dict
- **Episodic Memory** (design): historical interaction patterns — currently returns empty
- **Semantic Memory** (design): domain knowledge and embeddings — not implemented
- **Procedural Memory** (design): successful workflow patterns — not implemented

Do not rely on cross-request memory in the default build.

### Foundation Model Integration

**Default runtime is Gemini-only** (`GEMINI_DEFAULT_MODEL=gemini-pro`). `DEEPSEEK_ENABLED`, `LLAMA_ENABLED`, and `OPENROUTER_ENABLED` all default to `False`.

- **Multi-Provider Support (flag-gated OFF)**: A multi-provider routing layer exists but is gated behind the master switch `MULTI_PROVIDER_ROUTING_ENABLED=False`, which additionally requires `OPENROUTER_API_KEY` to be set. When enabled, requests route through **OpenRouter** to tier-mapped models — simple queries to a DeepSeek tier and more complex queries to a Claude Sonnet tier — not to a DeepSeek/Llama/Gemini trio.
- **Dynamic Model Selection**: With the flag on, MASR maps a query's complexity tier to a provider/model; with the flag off, all calls go to Gemini.
- **Fallback Strategies**: Provider fallback is available within the multi-provider layer when enabled.
- **Cost Tracking**: `LLMCostDriftMiddleware` compares estimated vs. actual provider cost and emits Prometheus drift metrics.

## Developer Experience

### Primary API Usage Pattern (Recommended)

```python
import httpx

# Intelligent research query
response = await httpx.post(
    "http://localhost:8000/api/v1/query/research",
    json={
        "query": "What are the ethical implications of AI in healthcare?",
        "domains": ["ai", "healthcare", "ethics"],
        "routing_strategy": "quality_focused",  # Optional: let MASR decide
        "enable_real_time_updates": True
    }
)

execution_id = response.json()["execution_id"]

# Get real-time progress
progress = await httpx.get(f"/api/v1/query/execution/{execution_id}/status")

# Get final results
results = await httpx.get(f"/api/v1/query/execution/{execution_id}/results")
```

### Bypass API Usage Pattern (Specialized)

```python
# Direct agent execution
response = await httpx.post(
    "http://localhost:8000/api/v1/agents/literature-review/execute",
    json={
        "query": "Find papers on AI ethics",
        "parameters": {"max_sources": 50},
        "enable_refinement": True
    }
)

# Chain-of-Agents execution
chain_response = await httpx.post(
    "http://localhost:8000/api/v1/agents/chain",
    json={
        "query": "Comprehensive AI ethics analysis",
        "agent_chain": ["literature-review", "methodology", "synthesis"],
        "pass_intermediate_results": True
    }
)
```

### WebSocket Real-Time Interaction

The live WebSocket routes are `/ws`, `/ws/projects/{project_id}`, and `/ws/cli/{project_id}`. Interactive refinement is served over `/api/v1/talkhier/interactive`. (There is no `/ws/query/interactive` route.)

```javascript
// Connect to a project's execution stream
const ws = new WebSocket('ws://localhost:8000/ws/projects/' + projectId);

ws.onmessage = (event) => {
    const update = JSON.parse(event.data);
    console.log('Execution progress:', update.progress_percentage);
    console.log('Current phase:', update.current_phase);
    console.log('Quality score:', update.quality_scores);
};
```

## Benefits Summary

### Research-Validated Approach

- **Academic Foundation**: Design decisions grounded in the cited routing and collaboration research
- **Research Targets**: Aims for the improvements those papers report — not yet independently benchmarked in Cerebro
- **Production-Oriented**: Follows established multi-agent orchestration patterns

### Cost and Performance Optimization

- **Intelligent Routing**: MASR model selection targeting the **50-60% cost reduction** cited in the routing research (a research-paper target, not a Cerebro measurement)
- **Quality Enhancement**: Coordinated execution targeting the **20-25% improvement** cited in the collaboration research (a research-paper target, not a Cerebro measurement)
- **Design Intent**: Graceful degradation and provider fallback (fallback active only when multi-provider routing is enabled)

### Developer and Enterprise Benefits

- **Two-Tier Access**: Intelligence by default, direct control when needed
- **Real-Time Updates**: Complete visibility into execution progress
- **Enterprise Ready**: Built-in monitoring, error handling, and performance tracking
- **Future Proof**: Foundation for A/B testing and continuous improvement

## Conclusion

The Agent Framework APIs represent a paradigm shift in multi-agent system design, moving from treating agents as internal implementation details to exposing them as sophisticated, research-validated first-class resources. By following cutting-edge academic research and prioritizing intelligent orchestration, these APIs establish a new standard for multi-agent system accessibility while maintaining the cost efficiency and quality assurance essential for production deployment.

The two-tier strategy (Primary Intelligence + Bypass Direct) provides the best of both worlds: automatic optimization for production workloads and direct control for specialized needs, creating a platform that serves both enterprise users and researchers effectively.