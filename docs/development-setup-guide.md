# Development Setup and Contribution Guide

## Overview

This guide covers setting up a development environment, understanding the
codebase structure, and contributing to **Cerebro**, a general
research-workflow workbench backed by a multi-agent LLM runtime. Cerebro routes
natural-language queries through a Multi-Agent System Router (MASR) to
hierarchical domain supervisors, which coordinate specialist LLM workers.
Execution is in process through `DirectExecutionService`.

> **Naming note:** the product is **Cerebro**, but the pre-rebrand infra identity **"research-platform"** is still used verbatim in deployment artifacts — the FastAPI title (`Research Platform API`), the k8s namespace and image names (`research-platform-api`), the default database (`research_db`), and the CLI entrypoints (`research-platform`, `research-cli`). Keep those infra names as-is; use "Cerebro" for the product.

The platform uses modern async Python: `async`/`await` throughout, dependency injection via FastAPI, and an async SQLAlchemy repository layer.

## Prerequisites

### System Requirements

- **Python**: 3.11 or higher
- **Docker**: 20.10+ with Docker Compose v2
- **Git**: 2.30+
- **Memory**: 8GB RAM minimum, 16GB recommended
- **Storage**: 20GB free space
- **OS**: macOS, Linux, or Windows with WSL2

### Required Tools

```bash
# Install uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install Docker and Docker Compose
# Follow platform-specific instructions at https://docs.docker.com/

# Install development tools
brew install git pre-commit redis postgresql  # macOS
sudo apt-get install git pre-commit redis postgresql  # Ubuntu
```

## Quick Start

### 1. Clone Repository

```bash
git clone https://github.com/your-org/cerebro.git
cd cerebro
```

### 2. Environment Setup

```bash
# Copy environment templates
cp .env.example .env
cp .env.cli.example .env.cli

# Install dependencies with uv
uv pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install
```

### 3. Configure Environment

Edit `.env` with non-secret project configuration, or put shared local secrets
in `~/.env`. Existing exported variables win over `~/.env`, which wins over the
repository `.env`, which wins over defaults. Explicit empty exports are kept.
Cerebro runs **Gemini-only by default**; the OpenRouter multi-provider tier is
flag-gated off (see [LLM Providers](#llm-providers)). Provider keys are optional
for startup.

```bash
# API Configuration
GEMINI_API_KEY=your-gemini-api-key-here
DATABASE_URL=postgresql+asyncpg://research:research123@localhost:5432/research_db
REDIS_URL=redis://localhost:6379/0

# Development Settings
ENVIRONMENT=development
DEBUG=true            # enables /docs and /redoc (both OFF in production)
LOG_LEVEL=INFO
DEV_ALLOW_ANONYMOUS_WEBSOCKETS=false  # explicit local-only opt-in

# Security (JWT is RS256 with PEM key files — see the Auth section)
JWT_PRIVATE_KEY_PATH=/secrets/jwt_private.pem
JWT_PUBLIC_KEY_PATH=/secrets/jwt_public.pem
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=15
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7

# Optional: enable OpenRouter multi-provider routing (both required)
# MULTI_PROVIDER_ROUTING_ENABLED=true
# OPENROUTER_API_KEY=your-openrouter-key
```

### 4. Start Services

```bash
# Start the default services with home/project dotenv support
./scripts/compose.sh up -d

# Or start with development tools (pgAdmin, Redis Commander)
./scripts/compose.sh --profile dev-tools up -d

# Verify services are running
./scripts/compose.sh ps

# Optional legacy standalone MASR diagnostics service (not the query path)
./scripts/compose.sh --profile legacy-masr-service up -d masr-router
```

The wrapper parses neither file itself. It passes absolute project-then-home
`--env-file` arguments to Compose, and shell exports remain highest priority.
Plain `docker compose` does not load `~/.env` automatically.

### 5. Initialize Database

```bash
# Run database migrations
alembic upgrade head
```

### 6. Verify Installation

```bash
# Run health checks
research-cli health

# Run basic tests (health-endpoint tests live in tests/test_api.py)
pytest tests/test_api.py::TestHealthEndpoints -v

# Start API server
uvicorn src.api.main:app --reload --port 8000
```

When `DEBUG=true`, interactive API docs are served at `http://localhost:8000/docs` (Swagger) and `/redoc`. Both are disabled when `DEBUG=false` (the production default).

## Development Environment

### Directory Structure

```
cerebro/
├── .github/                    # GitHub workflows and templates
├── docs/                       # Documentation
├── scripts/                    # Development and ops scripts
├── src/                        # Main source code
│   ├── agents/                 # Agent implementations + 17-type registry
│   │   ├── base.py            # BaseAgent abstract class
│   │   ├── factory.py         # AgentFactory (catalog for the bypass API)
│   │   ├── llm_worker_base.py # LLMWorkerAgentBase — shared worker scaffold
│   │   ├── models.py          # Agent data models
│   │   ├── literature_review_agent.py     # Research (5)
│   │   ├── comparative_analysis_agent.py
│   │   ├── methodology_agent.py
│   │   ├── synthesis_agent.py
│   │   ├── citation_agent.py
│   │   ├── content_agents.py              # Content (4)
│   │   ├── analytics_agents.py            # Analytics (3)
│   │   ├── finance_agents.py              # Finance (3)
│   │   ├── financial_calculator_agent.py  # deterministic tool agent
│   │   ├── verification_agent.py          # cross-cutting QA gate
│   │   ├── schemas/           # Structured Pydantic schemas (research)
│   │   ├── supervisors/       # Domain supervisors (Research/Content/Analytics/Finance)
│   │   ├── tools/             # finance_math.py + deterministic tool registry
│   │   └── integrations/      # External integrations
│   ├── ai_brain/               # Routing and intelligence
│   │   ├── router/            # MASRouter (masr.py), query_analyzer,
│   │   │                      #   query_decomposer, cost_optimizer
│   │   ├── compaction/        # Context compaction
│   │   ├── config/            # Model/provider configuration
│   │   ├── experimentation/   # A/B experimentation (routers unmounted)
│   │   ├── integration/       # Integration glue
│   │   ├── learning/          # Adaptive/episodic routing (flag-gated OFF)
│   │   ├── memory/            # Memory-informed routing hooks
│   │   └── providers/         # LLM provider clients
│   ├── api/                    # FastAPI application
│   │   ├── main.py            # App entry point, router mounting, middleware
│   │   ├── routes/            # API endpoints (only some are mounted)
│   │   ├── services/          # direct_execution_service.py + supervisor/talkhier services
│   │   ├── middleware/        # Rate limiting, cost-drift, idempotency
│   │   └── websocket/         # WebSocket handlers + auth
│   ├── cli/                    # Command-line interface (research-cli)
│   │   ├── main.py            # CLI entry point (click)
│   │   ├── commands/          # CLI command groups
│   │   ├── formatters.py      # table/json/yaml/csv output
│   │   └── websocket_client.py
│   ├── core/                   # config.py, observability.py, tracing.py, telemetry.py, pii_redactor.py
│   ├── auth/                    # JWT service + auth helpers
│   ├── middleware/             # Auth (no-op), rate limiting, cost-drift, idempotency
│   ├── models/                 # API/Pydantic models + db/ (SQLAlchemy models)
│   ├── repositories/           # Async SQLAlchemy repository layer
│   ├── memory/                 # Multi-tier memory system
│   ├── mcp/                    # MCP tool servers/clients
│   ├── services/               # Cross-cutting services (e.g. report generation)
│   ├── prompts/                # PromptManager + YAML templates
│   ├── templates/             # Prompt/report templates
│   ├── costs/                  # Cost accounting/optimization
│   ├── improvement/           # Self-improvement / adaptive-eval harness
│   ├── benchmarks/            # Benchmark harnesses
│   ├── research_platform/     # Pre-rebrand research-platform modules
│   ├── qa/                     # MAST failure labeler (mast.py is the only wired piece)
│   ├── reliability/            # Circuit breaker, retry, service registry
│   ├── security/               # ContentSanitizer (only wired export)
│   └── utils/                  # Utility functions
├── tests/                      # Test suite
├── docker/                     # Docker configs (Dockerfile.masr, entrypoint.sh, init.sql, nginx/)
├── alembic/                    # Database migrations
├── pyproject.toml             # Project configuration + [project.scripts]
├── docker-compose.yml         # Service definitions
└── README.md
```

> There is no `src/temporal/` package and no top-level `src/orchestration/` subsystem. Cerebro's execution engine is the in-process `DirectExecutionService`; the earlier Temporal-based orchestrator was removed. LangGraph still exists, but **only inside domain supervisors** (each builds an internal `StateGraph`), not as a top-level orchestrator.

### Key Architectural Patterns

#### Request Flow

```
Client -> FastAPI -> DirectExecutionService (asyncio background task)
       -> MASRouter -> MASRSupervisorBridge
       -> domain supervisors -> workers -> verification QA gate
```

`POST /api/v1/query/research` hands off to `DirectExecutionService`, which spawns an asyncio background task and returns immediately. The immediate HTTP response contains **hardcoded placeholders** (`selected_agents=[]`, `estimated_cost=0.015`, `estimated_quality=0.85`, `confidence=0.85`, `routing_time_ms=50.0`) — real routing data is only available afterward via the execution status/results endpoints (see [Execution Status](#execution-status-endpoints)).

- **MASRouter** (`src/ai_brain/router/masr.py`) — the FastAPI lifespan owns one settings-backed in-process router shared by direct execution, the mounted MASR API, and active TalkHier sessions. The standalone port-9100 container is legacy-only, requires the `legacy-masr-service` profile, and is not on the query path.
- **MASRSupervisorBridge** maps MASR routing decisions to supervisors.
- **Domain supervisors** — Research, Content, Analytics, Finance. Each runs an internal LangGraph `StateGraph`.

#### Repository Pattern

Data access is abstracted through async SQLAlchemy repositories over the DB models in `src/models/db/`:

```python
# src/repositories/base.py
class BaseRepository(Generic[ModelType]):
    """Base repository with generic CRUD operations."""

    def __init__(self, model: type[ModelType], session: AsyncSession):
        self.model = model
        self.session = session

    async def create(self, **kwargs) -> ModelType:
        entity = self.model(**kwargs)
        self.session.add(entity)
        # Note: create() flushes and refreshes — it does NOT commit;
        # the caller (or the request session) owns the transaction.
        await self.session.flush()
        await self.session.refresh(entity)
        return entity

    async def get(self, id: str | UUID) -> ModelType | None:
        query = select(self.model).where(self.model.id == id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()
```

Concrete repositories include `ResearchRepository`, `ReportRepository`, `UserRepository`, `CheckpointRepository` (`WorkflowCheckpoint`), `ResultRepository` (`ResearchResult`), and `TaskRepository`. **`TaskRepository` wraps the `AgentTask` DB model** — it is ordinary agent-task persistence, not workflow-engine task management.

#### Dependency Injection

Services and repositories are injected through FastAPI's dependency system:

```python
async def get_database_session():
    async with AsyncSessionLocal() as session:
        yield session

async def get_research_repository(session: AsyncSession = Depends(get_database_session)):
    return ResearchRepository(session)

@router.post("/projects")
async def create_project(
    request: CreateProjectRequest,
    repo: ResearchRepository = Depends(get_research_repository),
):
    return await repo.create(**request.dict())
```

#### Agent Scaffold

All domain workers subclass **`LLMWorkerAgentBase`** (`src/agents/llm_worker_base.py`). Agents are **LLM-reasoning (prompt-driven)**, not coded decision engines. Note that reported confidence scores are hardcoded heuristics (`0.85` on success, `0.3` on empty output, `0.8` on the fast path) — not real quality signals.

```python
class LiteratureReviewAgent(LLMWorkerAgentBase):
    agent_type = "literature_review"

    def _build_prompt(self, task: AgentTask) -> str:
        ...
```

### Configuration Management

Configuration is a single Pydantic `Settings(BaseSettings)` in `src/core/config.py`, loaded from `.env` (`case_sensitive=True`, `extra='ignore'`), exposed as the module singleton `settings` and via `get_settings()`.

```python
# src/core/config.py (abridged — see docs/configuration-reference.md for the full list)
class Settings(BaseSettings):
    """Application settings from environment variables."""

    DEBUG: bool = False  # /docs and /redoc served only when True

    DATABASE_URL: str = "postgresql+asyncpg://research:research123@localhost:5432/research_db"
    REDIS_URL: str = "redis://localhost:6379/0"
    GEMINI_API_KEY: str | None = None
    GEMINI_DEFAULT_MODEL: str = "gemini-pro"

    # Multi-provider routing (OpenRouter) — OFF by default
    MULTI_PROVIDER_ROUTING_ENABLED: bool = False
    OPENROUTER_API_KEY: str | None = None

    # Auth — RS256 with PEM key files
    JWT_ALGORITHM: str = "RS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore")

settings = Settings()
```

Before `settings` is constructed, `src/core/environment.py` parses the
repository `.env` and then `~/.env` without executing them. It never replaces a
key that was already present in the process.

#### Multiple Environment Support

```bash
# Development
ENVIRONMENT=development
DEBUG=true
DATABASE_URL=postgresql+asyncpg://research:research123@localhost:5432/research_db

# Testing
ENVIRONMENT=testing
DEBUG=true
DATABASE_URL=postgresql+asyncpg://test:test123@localhost:5432/research_test_db

# Production
ENVIRONMENT=production
DEBUG=false
DATABASE_URL=postgresql+asyncpg://prod_user:secure_pass@db.example.com:5432/research_prod_db
```

In production, `config.py` validators enforce `SECRET_KEY` length (≥32 chars) and reject default DB credentials.

### LLM Providers

- **Default runtime is Gemini only** (`GEMINI_ENABLED=True`, `GEMINI_DEFAULT_MODEL='gemini-pro'`).
- **OpenRouter multi-provider routing** (DeepSeek for the `simple` tier / Claude Sonnet for the `complex` tier) is **flag-gated OFF**. It activates only when **both** `MULTI_PROVIDER_ROUTING_ENABLED=True` **and** `OPENROUTER_API_KEY` are set.
- `DEEPSEEK_ENABLED`, `LLAMA_ENABLED`, and `OPENROUTER_ENABLED` all default to `False`. Do not assume DeepSeek or Llama are active out of the box.

## Development Workflow

### Code Quality Standards

Formatting and linting are handled by **Ruff**; type checking by **mypy**. CI runs `ruff check`, `ruff format --check`, and `mypy`.

#### 1. Code Formatting

```bash
# Format code (ruff format also sorts imports — no separate isort step)
ruff format src tests
```

#### 2. Linting

```bash
# Lint with Ruff
ruff check src tests

# Fix auto-fixable issues
ruff check --fix src tests
```

#### 3. Type Checking

```bash
# Type check with mypy
mypy src
```

### Testing Strategy

#### Test Structure

```
tests/
├── conftest.py              # Shared test configuration
├── unit/                    # Unit tests
├── integration/             # Integration tests (postgres + redis)
├── e2e/                     # End-to-end tests
├── security/                # Security tests
├── regression/             # Golden-dataset regression
└── fixtures/                # Test data
```

#### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=html

# Run a specific test file
pytest tests/test_literature_review_agent.py -v

# Run a specific test
pytest tests/test_literature_review_agent.py::TestLiteratureReviewAgent::test_execute_literature_review -v

# Run tests matching a pattern
pytest -k "test_agent" -v

# Run tests with specific markers
pytest -m "integration" -v
```

> **CI coverage gate:** CI enforces `coverage report --fail-under=25`. Do not assume a higher enforced floor — 25% is the actual gate.

#### Test Configuration

```python
# conftest.py
import pytest
import asyncio
from httpx import AsyncClient
from src.api.main import app

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture
async def async_client():
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client

@pytest.fixture
def sample_research_project():
    """Provide a sample research project for testing."""
    return {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "title": "AGI and Society Research",
        "user_id": "test-user-123",
        "status": "pending",
        "query": {
            "text": "What are the implications of artificial general intelligence on society?",
            "domains": ["AI", "Ethics", "Sociology"],
            "depth_level": "comprehensive",
        },
    }
```

#### Example Tests

```python
# tests/test_literature_review_agent.py
@pytest.mark.asyncio
async def test_literature_review_agent():
    """Test literature review agent execution."""
    config = AgentConfig(gemini_config=mock_gemini_config)
    agent = LiteratureReviewAgent(config)

    task = AgentTask(
        id=uuid4(),
        agent_type="literature_review",
        task_type="research",
        research_query="US equity risk factors",
        input_data={},
    )

    with patch.object(agent.gemini_service, "generate_content") as mock_gemini:
        mock_gemini.return_value = {"content": "Mocked response"}
        result = await agent.execute(task)

        assert result.agent_type == "literature_review"
        assert result.status == "completed"
        assert result.confidence_score > 0.0

# tests/integration/test_api_integration.py
@pytest.mark.asyncio
async def test_create_research_project(async_client, sample_research_project):
    """Test research project creation API."""
    response = await async_client.post("/api/v1/research/projects", json=sample_research_project)

    assert response.status_code == 201
    data = response.json()
    assert data["title"] == sample_research_project["title"]
    assert data["status"] == "pending"
    assert "id" in data
```

### Mocking External Services

External LLM calls should be mocked in unit tests. Cerebro's runtime provider is Gemini, so mock the Gemini service:

```python
# Define mocks inline per test module (conftest.py also provides a `mock_gemini_client` fixture)
class MockGeminiService:
    """Mock Gemini service for testing."""

    async def generate_content(self, prompt: str, **kwargs) -> dict:
        return {
            "content": f"Mocked response for: {prompt[:50]}...",
            "confidence": 0.95,
            "metadata": {"tokens_used": 100},
        }
```

### Pre-commit Hooks

Pre-commit hooks run **Ruff** (lint + format) plus the standard hygiene hooks (do not add `black` or `isort` — `ruff format` supersedes both). **mypy is not a pre-commit hook** — it runs only in CI (`.github/workflows/ci.yml`), so run `mypy src` yourself before pushing:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.0
    hooks:
      - id: ruff
        args: [--fix, --exit-non-zero-on-fix]
      - id: ruff-format

  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-added-large-files
        args: [--maxkb=500]
      - id: check-merge-conflict
      - id: detect-private-key
```

### Git Workflow

#### Branch Naming Convention

```bash
feature/agent-improvements
bugfix/timeout-handling
docs/setup-guide
refactor/repository-pattern
```

#### Commit Message Format

```
type(scope): short description

Longer description if needed

- Details about changes
- Breaking changes noted
- Issue references (#123)
```

Examples:

```bash
feat(agents): add valuation agent precompute for DCF

Wire the deterministic finance-math tool into ValuationAgent so exact
DCF figures are injected into the LLM prompt.

Closes #123

fix(query): stop returning placeholder routing metrics as live data

Document that the immediate /research response fields are placeholders;
surface real routing via the execution status endpoint.

Fixes #456
```

## Testing Infrastructure

### Test Database Setup

```bash
# Create test database
createdb research_test_db

# Set test environment
export DATABASE_URL=postgresql+asyncpg://test:test123@localhost:5432/research_test_db

# Run migrations on the test database
alembic upgrade head
```

### Performance Testing

```python
# Illustrative pytest perf test — the repo's load tests live in tests/load/locustfile.py (Locust)
@pytest.mark.performance
async def test_agent_execution_performance():
    """Test agent execution performance under load."""
    agent = LiteratureReviewAgent(test_config)
    tasks = [create_test_task() for _ in range(10)]

    start_time = time.time()
    results = await asyncio.gather(*[agent.execute(task) for task in tasks])
    execution_time = time.time() - start_time

    assert execution_time < 30.0
    assert all(result.status == "completed" for result in results)
    assert len(results) == 10
```

## Contributing Guidelines

### Getting Started

1. **Fork the repository** on GitHub
2. **Clone your fork** locally
3. **Create a feature branch** from `main`
4. **Make your changes** following coding standards
5. **Add tests** for new functionality
6. **Run the test suite** to ensure nothing breaks
7. **Update documentation** as needed
8. **Submit a pull request**

### Development Process

```bash
# Fork and clone
git clone https://github.com/your-username/cerebro.git
cd cerebro

# Add upstream remote
git remote add upstream https://github.com/original-org/cerebro.git

# Create feature branch
git checkout -b feature/your-feature-name

# Install development dependencies
uv pip install -e ".[dev]"
pre-commit install

# Make changes, then run quality checks
ruff format src tests
ruff check src tests
mypy src
pytest

# Commit and push
git add .
git commit -m "feat(component): add new feature"
git push origin feature/your-feature-name
```

### Code Review Process

#### Pull Request Requirements

1. **Clear description** of changes and motivation
2. **All tests passing** in CI/CD pipeline (lint, mypy, test matrix, `--fail-under=25` coverage gate)
3. **Documentation updated** for new features
4. **No merge conflicts** with main branch
5. **Approved by a maintainer** or core contributor

#### Review Checklist

- [ ] Code follows project style guidelines (ruff format + ruff check + mypy)
- [ ] Tests are comprehensive and meaningful
- [ ] Documentation is clear and complete
- [ ] Breaking changes are clearly marked
- [ ] Performance impact is considered
- [ ] Security implications are addressed

## Debugging and Troubleshooting

### Common Issues

#### 1. Database Connection Issues

```bash
# Check database status
pg_ctl status

# Restart PostgreSQL
brew services restart postgresql  # macOS
sudo systemctl restart postgresql  # Linux

# Test connection
psql -h localhost -U research -d research_db
```

#### 2. Redis Connection Issues

```bash
# Check Redis status
redis-cli ping

# Restart Redis
brew services restart redis  # macOS
sudo systemctl restart redis  # Linux
```

#### 3. Execution Appears Stuck

Because `DirectExecutionService` runs the pipeline as an asyncio background task, the immediate `/research` response is not the result. Poll the execution status endpoint to see real progress and routing:

```bash
curl "http://localhost:8000/api/v1/query/execution/{execution_id}/status"
```

If executions never leave `pending`, check that the Gemini API key is set and that Postgres/Redis are reachable — the background task fails silently to the caller and records its error in the execution status.

### Debugging Tools

#### 1. Logging Configuration

Cerebro uses **structlog** directly — there is no central `configure_logging()` function or `src/core/logging.py` module. Modules obtain a logger via `structlog.get_logger()` and log with bound key/value context:

```python
import structlog

logger = structlog.get_logger(__name__)
logger.info("Processing request", project_id="123", user_id="user456")
```

#### 2. Debug Mode

```bash
# Enable debug mode (also serves /docs and /redoc)
export DEBUG=true
export LOG_LEVEL=DEBUG

# Run with verbose logging
uvicorn src.api.main:app --reload --log-level debug

# CLI debug mode
research-cli --verbose projects list
```

#### 3. Database Debugging

```python
import logging
logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)

from sqlalchemy import text
result = await session.execute(text("SELECT version()"))
print(result.scalar())
```

### Observability

- **Prometheus metrics** are exposed at `/metrics` (`src/core/observability.py`): `llm_call_duration_seconds`, `llm_tokens_total`, `llm_cost_usd_total`, `llm_request_cost_drift_ratio`, `llm_cost_drift_events_total`.
- **Structured logging** via structlog throughout.
- **Langfuse tracing** is opt-in (`LANGFUSE_ENABLED`, default `False`). There is no OpenTelemetry backbone and no Grafana/Loki/Jaeger/Sentry wiring.

## Execution Status Endpoints

Because query execution is asynchronous, use these endpoints to observe and control a run:

```bash
# Real routing/result status (poll this after POST /research)
curl "http://localhost:8000/api/v1/query/execution/{execution_id}/status"

# Final results
curl "http://localhost:8000/api/v1/query/execution/{execution_id}/results"

# Resume a checkpointed execution — the path param is the PROJECT id, not the execution_id.
# The route (POST /execution/{project_id}/resume) parses this as a project UUID and loads
# that project's latest recoverable checkpoint; passing an execution_id here returns
# 404 "No recoverable checkpoint found".
curl -X POST "http://localhost:8000/api/v1/query/execution/{project_id}/resume"
```

## Documentation Standards

### Code Documentation

```python
async def create_research_project(
    title: str,
    query: ResearchQuery,
    user_id: str,
    scope: ResearchScope | None = None,
) -> ResearchProject:
    """
    Create a new research project and start its asynchronous execution.

    Args:
        title: Project title (max 200 characters).
        query: Research query with domains and parameters.
        user_id: ID of the user creating the project.
        scope: Optional research scope parameters.

    Returns:
        Created research project with assigned ID and initial status.

    Raises:
        ValidationError: If project parameters are invalid.

    Example:
        >>> query = ResearchQuery(
        ...     text="DCF valuation for a large-cap software company",
        ...     domains=["finance"],
        ... )
        >>> project = await create_research_project(
        ...     title="Equity Valuation Study",
        ...     query=query,
        ...     user_id="researcher-001",
        ... )
        >>> print(project.id)
        'proj-550e8400-e29b-41d4-a716-446655440000'
    """
```

### API Documentation

Endpoints are auto-documented via FastAPI's OpenAPI schema, served at `/docs` and `/redoc` **only when `DEBUG=True`**:

```python
@router.post(
    "/projects",
    response_model=ResearchProject,
    status_code=status.HTTP_201_CREATED,
    summary="Create research project",
    description="Create a new research project and start its execution.",
    responses={
        201: {"description": "Project created successfully"},
        400: {"description": "Invalid project parameters"},
        500: {"description": "Internal server error"},
    },
)
async def create_research_project(
    request: CreateResearchProjectRequest,
) -> ResearchProject:
    """Create a new research project."""
```

## Security Considerations

### Environment Variables

```bash
# Never commit sensitive data. Use .env files (added to .gitignore).
GEMINI_API_KEY=your-secret-key
DATABASE_PASSWORD=secure-password
```

### Input Validation

```python
from pydantic import BaseModel, field_validator, Field

class CreateProjectRequest(BaseModel):
    """Request model with validation."""

    title: str = Field(..., min_length=1, max_length=200)
    query: str = Field(..., min_length=10, max_length=1000)
    domains: list[str] = Field(..., min_length=1, max_length=10)

    @field_validator("domains")
    @classmethod
    def validate_domains(cls, v):
        allowed = {"research", "content", "analytics", "finance"}
        for domain in v:
            if domain not in allowed:
                raise ValueError(f"Invalid domain: {domain}")
        return v
```

### Authentication

Cerebro uses **JWT with RS256** (asymmetric), backed by PEM key files — not a shared HMAC secret. Access tokens expire in **15 minutes**, refresh tokens in **7 days**. Passwords are hashed with bcrypt (12 rounds) and must be at least 12 characters.

```python
from fastapi import Depends, HTTPException

async def get_current_token(token: str = Depends(bearer_scheme)):
    """Validate a JWT using the RS256 public key."""
    payload = jwt_service.validate_token(token.credentials)  # RS256, /secrets/jwt_public.pem
    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    return payload
```

> **Auth reality check:** the request-level `AuthMiddleware` is a no-op — it sets `request.state.user = None` and validates nothing. Real authentication is enforced per-endpoint via `Depends(...)`. As a consequence, `/api/v1/query/*`, `/api/v1/agents/*`, and `/api/v1/masr/*` are effectively unauthenticated in the current build. Do not describe the whole API as JWT-gated. The middleware stack order is: CORS → Idempotency → RateLimit → LLMCostDriftMiddleware → Auth (no-op).

### Rate Limiting

A single global rate limiter is applied (`MAX_REQUESTS_PER_MINUTE=100`, `ENABLE_RATE_LIMITING=True`). There are no per-tier, per-endpoint, or burst configurations.

---

This guide provides what you need to develop against **Cerebro** — from initial setup through debugging, observability, and security. For the exhaustive settings list, see `docs/configuration-reference.md`; for the agent domains and registry, see `docs/agent-domains.md`.
