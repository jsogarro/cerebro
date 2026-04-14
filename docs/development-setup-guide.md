# Development Setup and Contribution Guide

## Overview

This guide provides comprehensive instructions for setting up a development environment, understanding the codebase structure, and contributing to the Multi-Agent Research Platform. The platform uses modern Python development practices with async/await, dependency injection, and functional programming principles.

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
git clone https://github.com/your-org/multi-agent-research-platform.git
cd multi-agent-research-platform
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

Edit `.env` with your configuration:

```bash
# API Configuration
GEMINI_API_KEY=your-gemini-api-key-here
DATABASE_URL=postgresql+asyncpg://research:research123@localhost:5432/research_db
REDIS_URL=redis://localhost:6379/0
TEMPORAL_HOST=localhost:7233

# Development Settings
ENVIRONMENT=development
DEBUG=true
LOG_LEVEL=INFO

# Security
SECRET_KEY=your-secret-key-here
JWT_SECRET_KEY=your-jwt-secret-here
```

### 4. Start Services

```bash
# Start all services with Docker Compose
docker-compose up -d

# Or start with development tools
docker-compose --profile dev-tools up -d

# Verify services are running
docker-compose ps
```

### 5. Initialize Database

```bash
# Run database migrations
alembic upgrade head

# Create test data (optional)
python scripts/create_test_data.py
```

### 6. Verify Installation

```bash
# Run health checks
python -m src.cli.main health

# Run basic tests
pytest tests/test_health.py -v

# Start API server
uvicorn src.api.main:app --reload --port 8000
```

## Development Environment

### Directory Structure

```
multi-agent-research-platform/
├── .github/                    # GitHub workflows and templates
├── docs/                       # Documentation
├── scripts/                    # Development and deployment scripts
├── src/                        # Main source code
│   ├── agents/                 # AI agent implementations
│   │   ├── base.py            # Base agent class
│   │   ├── factory.py         # Agent factory
│   │   ├── models.py          # Agent data models
│   │   ├── integrations/      # External integrations
│   │   ├── literature_review_agent.py
│   │   ├── comparative_analysis_agent.py
│   │   ├── methodology_agent.py
│   │   ├── synthesis_agent.py
│   │   └── citation_agent.py
│   ├── api/                    # FastAPI application
│   │   ├── main.py            # API entry point
│   │   ├── auth/              # Authentication
│   │   ├── routes/            # API endpoints
│   │   ├── services/          # Business logic services
│   │   └── websocket/         # WebSocket handlers
│   ├── cli/                    # Command-line interface
│   │   ├── main.py            # CLI entry point
│   │   ├── commands/          # CLI commands
│   │   ├── formatters.py      # Output formatters
│   │   └── websocket_client.py
│   ├── core/                   # Core utilities
│   │   ├── config.py          # Configuration management
│   │   ├── logging.py         # Logging setup
│   │   └── security.py        # Security utilities
│   ├── models/                 # Data models
│   │   ├── research_project.py
│   │   ├── report.py
│   │   └── websocket_messages.py
│   ├── orchestration/          # LangGraph orchestration
│   │   ├── research_orchestrator.py
│   │   ├── graph_builder.py
│   │   ├── state.py
│   │   ├── nodes/             # Workflow nodes
│   │   └── edges.py           # Conditional routing
│   ├── repositories/           # Data access layer
│   │   ├── base.py
│   │   ├── research_repository.py
│   │   └── report_repository.py
│   ├── services/               # Business services
│   │   ├── report_generator.py
│   │   ├── gemini_service.py
│   │   └── prompts/
│   ├── temporal/               # Temporal workflows
│   │   ├── client.py
│   │   ├── worker.py
│   │   ├── workflows/
│   │   └── activities/
│   └── utils/                  # Utility functions
├── tests/                      # Test suite
│   ├── conftest.py            # Test configuration
│   ├── test_agents.py
│   ├── test_api.py
│   ├── test_models.py
│   ├── test_orchestration.py
│   ├── test_temporal_workflows.py
│   └── integration/           # Integration tests
├── docker/                     # Docker configurations
├── alembic/                    # Database migrations
├── requirements.txt            # Python dependencies
├── pyproject.toml             # Project configuration
├── docker-compose.yml         # Service definitions
└── README.md
```

### Key Architectural Patterns

#### 1. Repository Pattern

Data access is abstracted through repositories:

```python
# src/repositories/base.py
class BaseRepository[T]:
    """Base repository with common CRUD operations."""
    
    def __init__(self, session: AsyncSession, model_class: type[T]):
        self.session = session
        self.model_class = model_class
    
    async def create(self, **kwargs) -> T:
        """Create new entity."""
        entity = self.model_class(**kwargs)
        self.session.add(entity)
        await self.session.commit()
        return entity
    
    async def get_by_id(self, entity_id: str) -> T | None:
        """Get entity by ID."""
        result = await self.session.execute(
            select(self.model_class).where(self.model_class.id == entity_id)
        )
        return result.scalar_one_or_none()
```

#### 2. Dependency Injection

Services are injected through FastAPI's dependency system:

```python
# src/api/dependencies.py
async def get_database_session():
    """Get database session."""
    async with AsyncSessionLocal() as session:
        yield session

async def get_research_repository(session: AsyncSession = Depends(get_database_session)):
    """Get research repository."""
    return ResearchRepository(session)

# Usage in routes
@router.post("/projects")
async def create_project(
    request: CreateProjectRequest,
    repo: ResearchRepository = Depends(get_research_repository)
):
    return await repo.create(**request.dict())
```

#### 3. Async/Await Throughout

All I/O operations use async/await:

```python
# Example: Agent execution
class BaseAgent(ABC):
    @abstractmethod
    async def execute(self, task: AgentTask) -> AgentResult:
        """Execute agent task asynchronously."""
        pass

# Example: Database operations
class ResearchRepository:
    async def create_project(self, **kwargs) -> ResearchProject:
        """Create project asynchronously."""
        pass
```

### Configuration Management

#### Environment-Based Configuration

```python
# src/core/config.py
class Settings(BaseSettings):
    """Application settings from environment variables."""
    
    # API Configuration
    APP_NAME: str = "Research Platform"
    VERSION: str = "1.0.0"
    DEBUG: bool = False
    
    # Database
    DATABASE_URL: str
    
    # External Services
    GEMINI_API_KEY: str
    REDIS_URL: str = "redis://localhost:6379/0"
    TEMPORAL_HOST: str = "localhost:7233"
    
    # Security
    SECRET_KEY: str
    JWT_SECRET_KEY: str
    JWT_EXPIRE_MINUTES: int = 30
    
    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
```

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

## Development Workflow

### Code Quality Standards

#### 1. Code Formatting

```bash
# Format code with Black
black src tests

# Sort imports with isort
isort src tests

# Run all formatting
black src tests && isort src tests
```

#### 2. Linting

```bash
# Lint with Ruff
ruff check src tests

# Fix auto-fixable issues
ruff check --fix src tests

# Run specific rules
ruff check --select E,W,F src tests
```

#### 3. Type Checking

```bash
# Type check with mypy
mypy src

# Strict type checking
mypy --strict src

# Generate type coverage report
mypy --html-report mypy-report src
```

### Testing Strategy

#### Test Structure

```
tests/
├── conftest.py              # Shared test configuration
├── unit/                    # Unit tests
│   ├── test_agents.py
│   ├── test_models.py
│   ├── test_services.py
│   └── test_utils.py
├── integration/             # Integration tests
│   ├── test_api_endpoints.py
│   ├── test_database.py
│   ├── test_temporal_workflows.py
│   └── test_agent_integration.py
├── e2e/                     # End-to-end tests
│   ├── test_complete_workflows.py
│   └── test_cli_commands.py
└── fixtures/                # Test data
    ├── test_projects.json
    └── mock_responses.json
```

#### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=html

# Run specific test file
pytest tests/test_agents.py -v

# Run specific test
pytest tests/test_agents.py::TestLiteratureReviewAgent::test_execute_success -v

# Run tests matching pattern
pytest -k "test_agent" -v

# Run tests with specific markers
pytest -m "integration" -v
pytest -m "slow" -v
```

#### Test Configuration

```python
# conftest.py
import pytest
import asyncio
from httpx import AsyncClient
from src.api.main import app

@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture
async def async_client():
    """Create async HTTP client for API testing."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client

@pytest.fixture
async def test_project():
    """Create test research project."""
    return {
        "title": "Test Research Project",
        "query": {
            "text": "How does AI impact healthcare?",
            "domains": ["AI", "Healthcare"]
        },
        "user_id": "test-user"
    }
```

#### Example Tests

```python
# tests/unit/test_agents.py
@pytest.mark.asyncio
async def test_literature_review_agent():
    """Test literature review agent execution."""
    
    # Create agent
    config = AgentConfig(gemini_config=mock_gemini_config)
    agent = LiteratureReviewAgent(config)
    
    # Create test task
    task = AgentTask(
        id=uuid4(),
        agent_type="literature_review",
        task_type="research",
        research_query="AI in healthcare",
        input_data={}
    )
    
    # Mock external dependencies
    with patch.object(agent.gemini_service, 'generate_content') as mock_gemini:
        mock_gemini.return_value = {"content": "Mocked response"}
        
        # Execute agent
        result = await agent.execute(task)
        
        # Assertions
        assert result.agent_type == "literature_review"
        assert result.status == "completed"
        assert result.confidence_score > 0.0

# tests/integration/test_api_endpoints.py
@pytest.mark.asyncio
async def test_create_research_project(async_client, test_project):
    """Test research project creation API."""
    
    response = await async_client.post("/research/projects", json=test_project)
    
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == test_project["title"]
    assert data["status"] == "pending"
    assert "id" in data
```

### Pre-commit Hooks

Pre-commit hooks ensure code quality before commits:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.4.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-added-large-files
      - id: check-merge-conflict

  - repo: https://github.com/psf/black
    rev: 23.1.0
    hooks:
      - id: black
        language_version: python3.11

  - repo: https://github.com/pycqa/isort
    rev: 5.12.0
    hooks:
      - id: isort

  - repo: https://github.com/charliermarsh/ruff-pre-commit
    rev: v0.0.254
    hooks:
      - id: ruff
        args: [--fix, --exit-non-zero-on-fix]

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.0.1
    hooks:
      - id: mypy
        additional_dependencies: [types-all]
```

### Git Workflow

#### Branch Naming Convention

```bash
# Feature branches
feature/agent-improvements
feature/api-authentication
feature/websocket-streaming

# Bug fixes
bugfix/memory-leak-fix
bugfix/timeout-handling

# Documentation
docs/api-documentation
docs/setup-guide

# Refactoring
refactor/repository-pattern
refactor/async-improvements
```

#### Commit Message Format

```
type(scope): short description

Longer description if needed

- Details about changes
- Breaking changes noted
- Issue references (#123)

Co-authored-by: Name <email@example.com>
```

Examples:
```bash
feat(agents): add comparative analysis agent

Implement new agent for comparing research approaches
with parallel execution support.

- Add ComparativeAnalysisAgent class
- Integrate with agent factory
- Add comprehensive tests
- Update documentation

Closes #123

fix(api): resolve timeout handling in project creation

Increase timeout for long-running project initialization
and add proper error handling for timeout scenarios.

- Increase timeout from 30s to 60s
- Add timeout-specific error responses
- Add retry logic for transient failures

Fixes #456
```

## Testing Infrastructure

### Test Database Setup

```bash
# Create test database
createdb research_test_db

# Set test environment
export DATABASE_URL=postgresql+asyncpg://test:test123@localhost:5432/research_test_db

# Run migrations on test database
alembic -c alembic.test.ini upgrade head
```

### Mocking External Services

```python
# tests/mocks.py
class MockGeminiService:
    """Mock Gemini API service for testing."""
    
    async def generate_content(self, prompt: str, **kwargs) -> dict:
        return {
            "content": f"Mocked response for: {prompt[:50]}...",
            "confidence": 0.95,
            "metadata": {"tokens_used": 100}
        }

class MockTemporalClient:
    """Mock Temporal client for testing."""
    
    async def start_workflow(self, workflow_type, input_data, **kwargs):
        return MockWorkflowHandle(f"test-workflow-{uuid4()}")
```

### Performance Testing

```python
# tests/performance/test_agent_performance.py
@pytest.mark.performance
async def test_agent_execution_performance():
    """Test agent execution performance under load."""
    
    agent = LiteratureReviewAgent(test_config)
    tasks = [create_test_task() for _ in range(10)]
    
    start_time = time.time()
    
    # Execute tasks concurrently
    results = await asyncio.gather(*[agent.execute(task) for task in tasks])
    
    execution_time = time.time() - start_time
    
    # Performance assertions
    assert execution_time < 30.0  # Should complete within 30 seconds
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

#### 1. Setting Up for Contribution

```bash
# Fork and clone
git clone https://github.com/your-username/multi-agent-research-platform.git
cd multi-agent-research-platform

# Add upstream remote
git remote add upstream https://github.com/original-org/multi-agent-research-platform.git

# Create feature branch
git checkout -b feature/your-feature-name

# Install development dependencies
uv pip install -e ".[dev]"
pre-commit install
```

#### 2. Making Changes

```bash
# Make your changes
# ... edit files ...

# Run tests frequently
pytest tests/relevant_test.py

# Check code quality
black src tests
ruff check src tests
mypy src

# Commit changes
git add .
git commit -m "feat(component): add new feature"
```

#### 3. Submitting Changes

```bash
# Sync with upstream
git fetch upstream
git rebase upstream/main

# Push to your fork
git push origin feature/your-feature-name

# Create pull request on GitHub
```

### Code Review Process

#### Pull Request Requirements

1. **Clear description** of changes and motivation
2. **All tests passing** in CI/CD pipeline
3. **Code coverage** maintained or improved
4. **Documentation updated** for new features
5. **No merge conflicts** with main branch
6. **Approved by maintainer** or core contributor

#### Review Checklist

- [ ] Code follows project style guidelines
- [ ] Tests are comprehensive and meaningful
- [ ] Documentation is clear and complete
- [ ] Breaking changes are clearly marked
- [ ] Performance impact is considered
- [ ] Security implications are addressed

### Issue Guidelines

#### Bug Reports

```markdown
## Bug Report

**Description**
Clear description of the bug

**Steps to Reproduce**
1. Step one
2. Step two
3. Step three

**Expected Behavior**
What should happen

**Actual Behavior**
What actually happens

**Environment**
- OS: [e.g. macOS 13.0]
- Python: [e.g. 3.11.2]
- Version: [e.g. 1.0.0]

**Additional Context**
Screenshots, logs, etc.
```

#### Feature Requests

```markdown
## Feature Request

**Problem Statement**
What problem does this solve?

**Proposed Solution**
How should this be implemented?

**Alternatives Considered**
Other approaches considered

**Additional Context**
Use cases, examples, etc.
```

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

#### 3. Temporal Connection Issues

```bash
# Check Temporal server
temporal operator list

# Restart Temporal
docker-compose restart temporal
```

### Debugging Tools

#### 1. Logging Configuration

```python
# src/core/logging.py
import structlog

def configure_logging(level: str = "INFO"):
    """Configure structured logging."""
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer()
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

# Usage in modules
logger = structlog.get_logger(__name__)
logger.info("Processing request", project_id="123", user_id="user456")
```

#### 2. Debug Mode

```bash
# Enable debug mode
export DEBUG=true
export LOG_LEVEL=DEBUG

# Run with verbose logging
uvicorn src.api.main:app --reload --log-level debug

# CLI debug mode
research-cli --verbose projects list
```

#### 3. Database Debugging

```python
# Enable SQL logging
import logging
logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)

# Database query debugging
from sqlalchemy import text
result = await session.execute(text("SELECT version()"))
print(result.scalar())
```

### Performance Profiling

#### 1. API Performance

```python
# Add timing middleware
from time import time
from fastapi import Request

@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time()
    response = await call_next(request)
    process_time = time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    return response
```

#### 2. Memory Profiling

```bash
# Install memory profiler
pip install memory-profiler

# Profile memory usage
@profile
def memory_intensive_function():
    # Function code here
    pass

# Run with profiling
python -m memory_profiler script.py
```

#### 3. Async Profiling

```python
# Profile async operations
import asyncio
import cProfile

async def profile_async_function():
    # Async function to profile
    pass

# Profile execution
cProfile.run('asyncio.run(profile_async_function())')
```

## Documentation Standards

### Code Documentation

#### Docstring Format

```python
async def create_research_project(
    title: str,
    query: ResearchQuery,
    user_id: str,
    scope: ResearchScope | None = None
) -> ResearchProject:
    """
    Create a new research project.
    
    This function initializes a new research project with the provided
    parameters and starts the associated workflow.
    
    Args:
        title: Project title (max 200 characters)
        query: Research query with domains and parameters
        user_id: ID of the user creating the project
        scope: Optional research scope parameters
        
    Returns:
        Created research project with assigned ID and initial status
        
    Raises:
        ValidationError: If project parameters are invalid
        WorkflowError: If workflow initialization fails
        
    Example:
        >>> query = ResearchQuery(
        ...     text="How does AI impact healthcare?",
        ...     domains=["AI", "Healthcare"]
        ... )
        >>> project = await create_research_project(
        ...     title="AI Healthcare Study",
        ...     query=query,
        ...     user_id="researcher-001"
        ... )
        >>> print(project.id)
        'proj-550e8400-e29b-41d4-a716-446655440000'
    """
```

#### Type Hints

```python
from typing import Any, Dict, List, Optional, Union
from datetime import datetime
from uuid import UUID

# Use specific types
def process_results(
    results: dict[str, Any],
    timestamp: datetime,
    project_id: UUID
) -> list[str]:
    """Process results with proper type hints."""
    pass

# Use generic types appropriately
from typing import TypeVar, Generic

T = TypeVar('T')

class Repository(Generic[T]):
    """Generic repository pattern."""
    
    async def get_by_id(self, entity_id: str) -> T | None:
        """Get entity by ID."""
        pass
```

### API Documentation

API endpoints are automatically documented using FastAPI's built-in OpenAPI:

```python
@router.post(
    "/projects",
    response_model=ResearchProject,
    status_code=status.HTTP_201_CREATED,
    summary="Create research project",
    description="Create a new research project and start the associated workflow",
    responses={
        201: {"description": "Project created successfully"},
        400: {"description": "Invalid project parameters"},
        500: {"description": "Internal server error"}
    }
)
async def create_research_project(
    request: CreateResearchProjectRequest,
) -> ResearchProject:
    """Create a new research project."""
```

## Security Considerations

### Environment Variables

```bash
# Never commit sensitive data
# Use .env files (added to .gitignore)
GEMINI_API_KEY=your-secret-key
DATABASE_PASSWORD=secure-password
JWT_SECRET_KEY=your-jwt-secret
```

### Input Validation

```python
from pydantic import BaseModel, validator, Field

class CreateProjectRequest(BaseModel):
    """Request model with validation."""
    
    title: str = Field(..., min_length=1, max_length=200)
    query: str = Field(..., min_length=10, max_length=1000)
    domains: list[str] = Field(..., min_items=1, max_items=10)
    
    @validator('domains')
    def validate_domains(cls, v):
        """Validate domain names."""
        allowed_domains = {'AI', 'Healthcare', 'Finance', 'Education'}
        for domain in v:
            if domain not in allowed_domains:
                raise ValueError(f'Invalid domain: {domain}')
        return v
```

### Authentication

```python
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer

security = HTTPBearer()

async def get_current_user(token: str = Depends(security)):
    """Get current authenticated user."""
    try:
        payload = jwt.decode(token.credentials, SECRET_KEY, algorithms=["HS256"])
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return user_id
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
```

This comprehensive development setup guide provides everything needed to contribute effectively to the Multi-Agent Research Platform, from initial setup through advanced debugging and security considerations.