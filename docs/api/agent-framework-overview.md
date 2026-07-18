# Agent Framework API Overview

## Introduction

The Cerebro Agent Framework APIs represent a major architectural advancement that transforms Cerebro's sophisticated multi-agent system from internal implementation details into first-class, research-validated API resources. Following cutting-edge academic research, these APIs enable direct interaction with Cerebro's AI Brain capabilities through both intelligent orchestration and direct access patterns.

## Research Foundation

Our API design is built on rigorous academic research from leading institutions:

### Core Research Papers

1. **"LLMs Working in Harmony: A Survey on the Technological Aspects of Building Effective LLM-Based Multi Agent Systems"** (2025)
   - **Influence**: Chain-of-Agents and Mixture-of-Agents execution patterns
   - **Implementation**: Sequential and parallel agent coordination with intelligent aggregation

2. **"MasRouter: Learning to Route LLMs for Multi-Agent Systems"** (2025)
   - **Influence**: Primary routing strategy prioritizing MASR intelligence over direct access
   - **Implementation**: MASR-routed model selection. The paper's 50-60% cost-reduction figure is a research-paper target, not a Cerebro measurement — multi-provider model selection is disabled by default (`MULTI_PROVIDER_ROUTING_ENABLED=False`), so the default runtime is Gemini-only.

3. **"Talk Structurally, Act Hierarchically: A Collaborative Framework for LLM Multi-Agent Systems"** (2025)
   - **Influence**: Hierarchical coordination and structured communication patterns
   - **Implementation**: Supervisor coordination integrated into primary API endpoints

4. **"Routine: A Structural Planning Framework for LLM Agent System in Enterprise"** (2025)
   - **Influence**: Enterprise-grade structured planning and multi-step coordination
   - **Implementation**: Production-ready workflow orchestration with quality assurance

5. **"Data-to-Dashboard: Multi-Agent LLM Framework for Insightful Visualization in Enterprise Analytics"** (2025)
   - **Influence**: Domain-specific agent specialization and coordination patterns
   - **Implementation**: Specialized endpoint design for different agent capabilities

6. **"How we built our multi-agent research system"** (Anthropic Engineering Blog, 2025)
   - **Influence**: Built-in evaluation framework and performance tracking approach
   - **Implementation**: Comprehensive metrics, health monitoring, and continuous improvement

## Architecture Overview

### Two-Tier API Strategy

Based on extensive research analysis, we implemented a **hybrid approach** that prioritizes intelligent orchestration while providing direct access options:

#### Primary API (90% of usage) - Intelligent Orchestration
```
/api/v1/query/*    # MASR-routed for optimal agent selection and cost efficiency
```

- **Routes through MASR**: Every request is routed through MASR for supervisor selection. Note that a query classified as `FAST_PATH` makes a single LLM call that bypasses supervisors (and TalkHier) entirely — not every Primary request receives supervisor/TalkHier coordination.
- **Leverages Full Intelligence**: Non-fast-path requests use hierarchical supervisors and the TalkHier protocol
- **No runtime learning by default**: Adaptive and memory-informed routing are flag-gated OFF (`ADAPTIVE_ROUTING_ENABLED=False`, `MEMORY_INFORMED_ROUTING_ENABLED=False`); in the default runtime no learning occurs and requests do not update future routing.
- **Cost target (not measured)**: The MasRouter paper's 50-60% cost reduction is a cited research target. Multi-provider model selection is OFF by default (`MULTI_PROVIDER_ROUTING_ENABLED=False`); the default runtime is Gemini-only.

#### Bypass API (10% of usage) - Direct Access
```
/api/v1/agents/*   # Direct agent execution for specialized needs
```

- **Direct Execution**: Immediate agent access without routing overhead
- **Development Friendly**: Ideal for debugging, testing, and experimentation
- **Manual Control**: Specify exact execution patterns (Chain, Mixture)
- **Specialized Use Cases**: Custom workflows and third-party integrations

### Research-Informed Design Principles

#### 1. Intelligence-First Architecture
Following "MasRouter" research showing significant cost and performance benefits, we designed the Primary API to **always route through MASR** intelligence:

- **Cost Optimization**: MASR selects a routing strategy per query. The 50-60% cost-reduction figure is a MasRouter-paper target, not a Cerebro measurement — multi-provider model selection is OFF by default and the runtime is Gemini-only.
- **Quality Assurance**: Hierarchical supervisors ensure structured coordination
- **No runtime learning by default**: Adaptive and memory-informed routing are flag-gated OFF; in the default runtime routing does not improve from request history.
- **Resource Efficiency**: Intelligent allocation prevents over-provisioning

#### 2. Pattern-Based Execution
Implementing "LLMs Working in Harmony" research patterns:

- **Chain-of-Agents**: Sequential execution where agents build on previous results
- **Mixture-of-Agents**: Parallel execution with intelligent result aggregation
- **Dynamic Selection**: MASR automatically chooses optimal patterns based on query analysis
- **Quality Enhancement**: The 20-25% improvement figure is a research-paper result, not a Cerebro measurement — worker confidence scores are hardcoded heuristics (e.g. 0.85 on success, `llm_worker_base.py:252`), not a measured quality gain

#### 3. Structured Communication
Following "Talk Structurally, Act Hierarchically" research:

- **Hierarchical Coordination**: Domain supervisors manage specialized workers
- **Structured Dialogue**: TalkHier protocol ensures quality through multi-round refinement
- **Consensus Building**: Automated consensus detection and conflict resolution
- **Quality Assurance**: Built-in validation and quality improvement mechanisms

## API Categories

### 1. Primary Query APIs (`/api/v1/query/*`)

**Intelligent Research Endpoint**:
```http
POST /api/v1/query/research
```
- **Purpose**: General research queries with MASR intelligent routing
- **Benefits**: Automatic agent selection, quality assurance
- **Use Cases**: Academic research, literature analysis, comprehensive investigation
- **Response note**: The immediate HTTP response returns hardcoded placeholders (`selected_agents=[]`, `estimated_cost=0.015`, `estimated_quality=0.85`, `confidence=0.85`, `routing_time_ms=50.0`) — the request executes asynchronously, and real routing data is available via `GET /api/v1/query/execution/{id}/status` and `/results`.

**Analysis-Focused Endpoint**:
```http
POST /api/v1/query/analyze
```
- **Purpose**: Analysis-heavy queries optimized for depth and methodology
- **Benefits**: Specialized routing for analytical tasks, enhanced methodology integration
- **Use Cases**: Data analysis, comparative studies, methodological research

**Synthesis Endpoint**:
```http
POST /api/v1/query/synthesize
```
- **Purpose**: Synthesis and integration tasks with existing materials
- **Benefits**: Optimized for synthesis agents, intelligent source integration
- **Use Cases**: Report generation, literature synthesis, knowledge integration

### 2. Direct Agent APIs (`/api/v1/agents/*`)

**Individual Agent Execution**:
```http
POST /api/v1/agents/{agent_type}/execute
```
- **Agent Types** (10 bypass-callable values): `literature-review`, `citation`, `methodology`, `comparative-analysis`, `synthesis`, `financial-analysis`, `valuation`, `risk-assessment`, `financial-calculator`, `verification`
- **Not bypass-reachable**: Content workers (content-planning, drafting, editing, optimization) and Analytics workers (data-analysis, statistical-modeling, insight-synthesis) are not exposed through the bypass agent API
- **Use Cases**: Direct agent testing, specialized workflows, debugging

**Chain-of-Agents Pattern**:
```http
POST /api/v1/agents/chain
```
- **Purpose**: Sequential agent execution with intermediate result passing
- **Benefits**: Controlled workflow specification, step-by-step validation
- **Use Cases**: Custom workflows, experimental patterns, development testing

**Mixture-of-Agents Pattern**:
```http
POST /api/v1/agents/mixture
```
- **Purpose**: Parallel agent execution with result aggregation
- **Benefits**: Consensus building, multiple perspectives, quality enhancement
- **Use Cases**: Critical decisions, comprehensive analysis, quality validation

### 3. System Intelligence APIs

**Agent Discovery**:
```http
GET /api/v1/agents
```
- **Purpose**: List available agents with capabilities and performance metrics
- **Benefits**: Dynamic agent discovery, capability-based selection

**Performance Monitoring**:
```http
GET /api/v1/agents/{agent_type}/metrics
GET /api/v1/agents/{agent_type}/health
```
- **Purpose**: Real-time performance tracking and health monitoring
- **Benefits**: Performance optimization, debugging, capacity planning

**Routing Intelligence**:
```http
GET /api/v1/query/routing/recommend
GET /api/v1/query/routing/strategies
```
- **Purpose**: Expose routing strategy options
- **Note**: `GET /routing/recommend` returns static, canned recommendations keyed by query length (`query_api.py:625-653`) — it does not run a live MASR routing computation.

## Integration Architecture

### MASR-Hierarchical Integration

The Agent Framework APIs seamlessly integrate with Cerebro's existing MASR-Hierarchical Communication system:

```
Primary API Flow:
Request → MASR Analysis → Supervisor Selection → Worker Coordination → Response

Bypass API Flow:
Request → Direct Agent Execution → Response
```

### WebSocket Real-Time Integration

Real-time capabilities are built into both API tiers:

- **Primary API**: Real-time progress updates through MASR routing and supervisor execution
- **Bypass API**: Direct agent execution progress and Chain/Mixture coordination updates
- **Interactive Sessions**: WebSocket endpoints for multi-round refinement and live coordination

### Performance Characteristics

#### Primary API Benefits
- **Cost Reduction**: 50-60% is a MasRouter research-paper target, not a Cerebro measurement — multi-provider model selection is OFF by default (Gemini-only runtime)
- **Quality Improvement**: 20-25% is a "LLMs Working in Harmony" research-paper figure, not a measured Cerebro gain
- **Learning**: No runtime learning by default — adaptive and memory-informed routing are flag-gated OFF
- **Optimization**: Automatic resource allocation and supervisor selection

#### Bypass API Benefits
- **Direct Control**: Manual specification of execution patterns
- **Low Latency**: No routing overhead for simple direct execution
- **Development Support**: Debugging and testing capabilities
- **Flexibility**: Custom workflow creation and experimental patterns

## Usage Recommendations

### When to Use Primary API (Recommended for 90% of cases)

✅ **Production Workloads**: All production queries should use Primary API for cost and quality optimization

✅ **General Research**: Academic research, literature analysis, content generation

✅ **Cost Optimization**: When budget efficiency is important

✅ **Quality Critical**: When highest quality results are required

### When to Use Bypass API (Specialized use cases)

🔧 **Development & Testing**: Direct agent testing, debugging, performance analysis

🔬 **Research & Experimentation**: Testing new agent combinations or execution patterns

🎛️ **Custom Workflows**: Specific requirements that need manual agent coordination

🔌 **Third-Party Integration**: External systems with specific agent interaction requirements

## Internal Tool Registry

### Overview

The Agent Framework includes a **tool registry** system that allows worker agents to declare and use deterministic, pure-internal tools. Tools are scoped strictly to **internal-only** operations (no network, no external APIs, no MCP) and provide validated, structured results that agents can reference in their reasoning.

### Design Principles

1. **Pure-Internal**: Tools are deterministic functions with no external dependencies
2. **Validated Inputs**: All parameters validated via Pydantic models
3. **Structured Results**: Tools return `ToolResult` with success/error indication
4. **Safe Execution**: Tools never raise exceptions — errors become structured error results
5. **Injection-Safe**: Arithmetic evaluation uses AST parsing with strict whitelisting (no `eval()`)

### AgentTool Interface

Each tool implements the `AgentTool` base class with:
- `name`: Unique identifier (e.g., `arithmetic`, `datetime_info`)
- `description`: One-line summary
- `params_model`: Pydantic model for input validation
- `_execute_impl`: The actual computation (async or sync)

### Built-in Internal Tools

1. **`arithmetic`**: Safe expression evaluation with AST parsing
   - Supports: `+`, `-`, `*`, `/`, `**`, `//`, `%`, unary `+`/`-`
   - Guards: division by zero, huge exponents, code injection
   - Example: `{"expression": "(2 + 3) * 4 / 2"}` → `{"result": 10.0}`

2. **`datetime_info`**: Current date/time, day-of-week, date arithmetic (UTC-aware)
   - Operations: `current`, `day_of_week`, `add_days`, `diff_days`
   - Example: `{"operation": "add_days", "date_iso": "2024-01-15", "days": 10}` → `{"result_date": "2024-01-25"}`

3. **`unit_conversion`**: Deterministic table-driven conversions
   - Supports: length, mass, temperature, data size
   - Example: `{"value": 1000, "from_unit": "m", "to_unit": "km"}` → `{"result": 1.0}`

### Worker Agent Registration

Workers declare tools by overriding `_register_tools()`:

```python
class DataAnalysisAgent(LLMWorkerAgentBase):
    def _register_tools(self, registry: ToolRegistry) -> None:
        from src.agents.tools import ArithmeticTool
        registry.register(ArithmeticTool())
```

When a worker has registered tools, the execution framework automatically appends an "Available tools:" block to the prompt with tool names and descriptions.

### Scope and Limitations

- **Strictly Internal**: No network, no filesystem, no subprocess, no external APIs
- **No MCP Integration**: MCP tools are explicitly out of scope (product decision)
- **Precompute-Style**: Tools currently inject metadata into prompts; full LLM tool-calling loop is not implemented

## Next Steps

The Agent Framework APIs establish the foundation for:

1. **A/B Testing System (Task #16)**: APIs provide evaluation endpoints for experiment tracking
2. **Authentication Strategy (Task #18)**: Secure access control for agent endpoints
3. **Advanced Features**: Enhanced Chain-of-Agents, Mixture-of-Agents, and TalkHier protocols
4. **Production Deployment**: Enterprise-grade stability and monitoring features

## Conclusion

The Agent Framework APIs transform Cerebro from a powerful but internally-focused system into a comprehensive, research-validated platform that exposes sophisticated multi-agent capabilities as first-class resources. By following cutting-edge academic research and prioritizing intelligent orchestration, these APIs enable both powerful automation and fine-grained control while maintaining cost efficiency and quality assurance.

The research-informed routing strategy ensures that users benefit from Cerebro's full intelligence by default while providing the flexibility needed for specialized use cases, establishing a new standard for multi-agent system APIs that balance sophistication with usability.