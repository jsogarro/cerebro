# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with Cerebro AI Brain platform.

**Cerebro** is a multi-modal LLM intelligence system that evolved from a specialized research platform
into a comprehensive AI brain capable of handling diverse domains through intelligent routing,
hierarchical coordination, and self-improvement.

## Essential Commands

### Development Setup
```bash
# Install with development dependencies
uv pip install -e ".[dev]"

# Start API server with hot reload
uvicorn src.api.main:app --reload --port 8000

# Start all services (preferred for development)
docker-compose up -d

# Start with development tools (pgAdmin, Redis Commander)
docker-compose --profile dev-tools up -d
```

### Testing
```bash
# Run all tests with coverage
pytest

# Run specific test file with verbose output
pytest tests/test_api.py -v
pytest tests/test_models.py -v
pytest tests/test_temporal_workflows.py -v

# Run tests with HTML coverage report
pytest --cov=src --cov-report=html

# Run single test method
pytest tests/test_api.py::TestHealthEndpoints::test_health_check -v
```

### Code Quality
```bash
# Format code (required before commits)
black src tests

# Lint code (must pass)
ruff check src tests

# Type checking (must pass)
mypy src

# Run all quality checks
black src tests && ruff check src tests && mypy src
```

### CLI Tool Usage
```bash
# Check API health
research-cli health

# Create research project (legacy)
research-cli projects create \
  --title "AI Impact Study" \
  --query "How does AI affect employment?" \
  --domains "AI,Economics,Labor" \
  --user-id "researcher-001"

# AI Brain Commands (NEW)
# Test dynamic model configuration
python examples/test_dynamic_config.py

# Check AI Brain health and configuration
cerebro-cli brain status
cerebro-cli brain models list
cerebro-cli brain route "Your query here"

# Agent Framework API Commands (LATEST)
# Primary API - Intelligent routing through MASR (RECOMMENDED - 90% usage)
curl -X POST "http://localhost:8000/api/v1/query/research" \
  -H "Content-Type: application/json" \
  -d '{"query": "AI ethics in healthcare", "domains": ["ai", "healthcare"]}'

# MASR Intelligent Routing API (NEW - Week 2 Complete)
# Get intelligent routing decision with cost optimization
curl -X POST "http://localhost:8000/api/v1/masr/route" \
  -H "Content-Type: application/json" \
  -d '{"query": "Analyze climate change impact", "strategy": "quality_focused"}'

# Estimate execution cost with detailed breakdown
curl -X POST "http://localhost:8000/api/v1/masr/estimate-cost" \
  -H "Content-Type: application/json" \
  -d '{"query": "Complex research on AI ethics", "domains": ["ai", "ethics", "philosophy"]}'

# Evaluate different routing strategies
curl -X POST "http://localhost:8000/api/v1/masr/evaluate-strategies" \
  -H "Content-Type: application/json" \
  -d '{"query": "Machine learning in healthcare", "compare_strategies": ["cost_efficient", "quality_focused", "balanced"]}'

# Submit feedback for continuous learning
curl -X POST "http://localhost:8000/api/v1/masr/feedback" \
  -H "Content-Type: application/json" \
  -d '{"routing_id": "uuid-123", "actual_cost": 0.38, "quality_score": 0.92}'

# Get router health and performance metrics
curl -X GET "http://localhost:8000/api/v1/masr/status"

# TalkHier Protocol API (NEW - Week 4 Complete)
# Create multi-round refinement session
curl -X POST "http://localhost:8000/api/v1/talkhier/sessions" \
  -H "Content-Type: application/json" \
  -d '{"protocol": "multi_round", "quality_threshold": 0.85, "max_rounds": 3}'

# Submit refinement round
curl -X POST "http://localhost:8000/api/v1/talkhier/sessions/{session_id}/round" \
  -H "Content-Type: application/json" \
  -d '{"message": "Refine analysis quality", "participants": ["lit-review", "synthesis"]}'

# Get consensus status
curl -X GET "http://localhost:8000/api/v1/talkhier/sessions/{session_id}/consensus"

# Interactive WebSocket session for real-time refinement
wscat -c ws://localhost:8000/ws/talkhier/{session_id}

# Bypass API - Direct agent access (for testing/development - 10% usage)
curl -X POST "http://localhost:8000/api/v1/agents/literature-review/execute" \
  -H "Content-Type: application/json" \
  -d '{"query": "Find papers on machine learning", "parameters": {"max_sources": 25}}'

# Chain-of-Agents execution
curl -X POST "http://localhost:8000/api/v1/agents/chain" \
  -H "Content-Type: application/json" \
  -d '{"query": "Analyze AI impact", "agent_chain": ["literature-review", "synthesis"]}'
```

## High-Level Architecture

### Cerebro AI Brain System Architecture

**Cerebro** now operates as a comprehensive multi-modal intelligence platform with three primary layers:

#### Layer 1: Intelligence Orchestration
- **MASR (Multi-Agent System Router)**: Intelligent query routing and cost optimization
- **Multi-Tier Memory System**: Working, episodic, semantic, and procedural memory
- **Dynamic Model Configuration**: Hot-reloadable model specifications and provider management

#### Layer 2: Foundation Model Integration  
- **Model Providers**: DeepSeek-V3, Llama 3.3 70B, Gemini Pro, and extensible provider system
- **Cost Optimization Engine**: Intelligent model selection balancing cost, quality, and latency
- **Fallback & Reliability**: Multi-provider redundancy and graceful degradation

#### Layer 3: Specialized Agent Domains
- **Research Domain**: Literature review, comparative analysis, methodology, synthesis, citation agents
- **Content Domain**: Content planning, drafting, editing, optimization agents (implemented)
- **Analytics Domain**: Data analysis, statistical modeling, insight synthesis agents (implemented)
- **Finance Domain**: Financial analysis, valuation, risk assessment agents (implemented; LLM-reasoning, no external data). See docs/agent-domains.md
- **Service Domain**: Customer service and support agents (planned)

### Legacy Multi-Agent Research System (Now Research Domain)
The original research platform **continues to function** as a specialized domain within Cerebro:
- **Literature Review Agent**: Searches academic databases, extracts key findings
- **Comparative Analysis Agent**: Compares theories/approaches, creates comparison matrices  
- **Methodology Agent**: Recommends research methods, identifies biases
- **Synthesis Agent**: Integrates findings, creates coherent narratives
- **Citation & Verification Agent**: Verifies sources, formats citations

### Orchestration Layer (Core Architecture)
**MASR Router** + **LangGraph** + **Hierarchical Supervisors** provide the orchestration backbone:
- `MASR Router`: Intelligent query routing with cost optimization and supervisor selection
- `Hierarchical Supervisors`: Coordinate specialized worker teams via TalkHier protocol
- `LangGraph Workflows`: State management, conditional routing, and workflow orchestration
- **Direct execution design**: `API -> MASR -> Supervisor -> Workers -> Response`
- **State management**: LangGraph handles workflow state and coordination
- **Quality assurance**: TalkHier multi-round refinement and consensus building

### Agent Framework API Layer (NEW - September 2025)
**Research-Informed API Design** exposing agents as first-class resources:

#### Primary API (90% usage) - Intelligence-First Routing
- **`/api/v1/query/research`**: MASR-routed research queries with automatic optimization
- **`/api/v1/query/analyze`**: Analysis-focused with methodological emphasis  
- **`/api/v1/query/synthesize`**: Synthesis-optimized with smart coordination
- **Benefits**: 50-60% cost reduction, 20-25% quality improvement through MASR routing

#### Bypass API (10% usage) - Direct Agent Access
- **`/api/v1/agents/{type}/execute`**: Direct agent execution for debugging/testing
- **`/api/v1/agents/chain`**: Manual Chain-of-Agents specification
- **`/api/v1/agents/mixture`**: Manual Mixture-of-Agents specification
- **Benefits**: Direct control, low latency, experimental flexibility

#### Research Foundation
- **"MasRouter: Learning to Route LLMs"**: Intelligent routing for cost optimization
- **"LLMs Working in Harmony"**: Chain-of-Agents and Mixture-of-Agents patterns
- **"Talk Structurally, Act Hierarchically"**: Hierarchical coordination protocols
- **Anthropic Engineering**: Built-in evaluation and performance tracking

### Technology Stack Integration
- **API Layer**: FastAPI with WebSocket support + Agent Framework APIs (Primary/Bypass)
- **Data Layer**: PostgreSQL (structured data) + Redis (caching) + Vector DB (embeddings)
- **AI Integration**: Google Gemini with prompt engineering and response parsing
- **MCP Protocol**: Tool servers for academic databases, citation formatting, statistics
- **CLI**: Rich terminal interface with table/JSON/YAML/CSV output formats

## Key Development Patterns

### Test-Driven Development (Mandatory)
- Write tests FIRST before implementing features
- Maintain >80% code coverage (enforced in CI)
- Test structure: Unit tests → Integration tests → E2E tests
- Mock external services (Gemini API, MASR router) in tests
- **Docs-in-PR rule**: Update `docs/configuration-reference.md` for new settings (verify defaults from `config.py`); update architecture docs for behavior changes

### Direct Execution Design
Research execution follows **direct MASR routing and supervisor coordination**:
```python
# Direct execution through MASR and supervisors
async def research_execution(project_data):
    # Step 1: MASR intelligent routing
    routing_decision = await masr_router.route(project_data.query)
    
    # Step 2: Supervisor coordination
    supervisor_result = await supervisor_bridge.execute_routing_decision(
        routing_decision, agent_task, supervisor_registry
    )
    
    # Step 3: Return results with real-time updates
    return supervisor_result.agent_result.output
```

### Async/Await Throughout
- All I/O operations use async/await
- Database sessions are async (AsyncSession)
- HTTP client uses httpx AsyncClient
- Temporal activities and workflows are async

### Configuration Management
- **Pydantic Settings** with environment variable loading
- Development vs. production configurations via `ENVIRONMENT` variable
- CLI configuration via `.env.cli` files and command-line overrides
- Docker Compose handles service configuration

### Repository Pattern
Data access follows repository pattern:
- `ResearchRepository`: CRUD operations for research projects
- `TaskRepository`: Temporal task management
- `ResultRepository`: Research result storage
- Clean separation between API, business logic, and data access

## Development Workflow

### Local Development Environment
1. **Prerequisites**: Python 3.11+, Docker, uv package manager
2. **Service Dependencies**: 
   - PostgreSQL (port 5432)
   - Redis (port 6379) 
   - MASR Router (port 9100)
   - MCP Server (port 9000)
3. **API Access**: http://localhost:8000 (API), http://localhost:8000/docs (Swagger)

### Environment Setup
```bash
# Copy environment templates
cp .env.example .env
cp .env.cli.example .env.cli

# Required environment variables
GEMINI_API_KEY=your-gemini-api-key
DATABASE_URL=postgresql+asyncpg://research:research123@localhost:5432/research_db
REDIS_URL=redis://localhost:6379/0
MASR_SERVICE_URL=http://localhost:9100
ENABLE_DIRECT_EXECUTION=true
```

### Testing Strategy
- **Unit Tests**: Test individual components with mocked dependencies
- **Integration Tests**: Test component interactions with test database
- **E2E Tests**: Full workflow testing via API endpoints
- **Direct Execution Testing**: Test MASR routing and supervisor coordination
- **CLI Testing**: Mock API responses for CLI command testing

### Agent Development
When implementing new agents, follow this pattern:
1. Inherit from `BaseAgent` abstract class
2. Implement `execute()` and `validate_result()` methods
3. Add agent-specific prompts to `src/services/prompts/agent_prompts.py`
4. Create corresponding tests with mocked Gemini responses
5. Register agent in `AgentFactory`

### Direct Execution Development
Direct execution services use **MASR routing and supervisor coordination**:
- Implement proper retry policies using tenacity decorators
- Use MASR for intelligent routing and cost optimization
- Coordinate workers through hierarchical supervisors with TalkHier protocol
- Track progress through direct execution status and WebSocket events
- Handle errors at supervisor level with graceful degradation

### CLI-First Development
All API functionality must be accessible via CLI:
- API endpoints → CLI commands relationship is 1:1
- Support multiple output formats (table, JSON, YAML, CSV)
- Implement proper error handling and user feedback
- Add shell completion support

## Important Context

### Current Development Status  
- **Foundation Complete**: Core models, API structure, Docker setup, MASR routing, Hierarchical supervision
- **Recently Completed**: MASR-Hierarchical integration, CI/CD pipeline, Temporal removal, Agent Framework APIs Week 1
- **Architecture Enhanced**: Research-informed API design exposing agents as first-class resources
- **API Innovation**: Two-tier strategy (Primary MASR routing + Bypass direct access) following academic research
- **Next Phase**: Complete Agent Framework APIs (Weeks 2-4), A/B Testing System, Enhanced monitoring

### Performance Considerations
- **Caching Strategy**: Redis for Gemini responses, database query results
- **Rate Limiting**: Respect Gemini API quotas with exponential backoff
- **Parallel Execution**: Use hierarchical supervisors with parallel worker coordination
- **Database Optimization**: Connection pooling, appropriate indexes

### Security Requirements
- No hardcoded API keys or secrets in code
- JWT authentication planned for production
- Request validation and sanitization
- Audit logging for all research operations
- Rate limiting per user/API key

### Monitoring & Observability
- **OpenTelemetry**: Trace all requests, MASR routing, supervisor coordination, database queries
- **Prometheus Metrics**: Custom metrics for research projects, agent performance
- **Structured Logging**: Use structlog throughout codebase
- **Health Checks**: Multiple endpoints (`/health`, `/ready`, `/live`)