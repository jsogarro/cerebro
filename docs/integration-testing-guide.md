# Integration and E2E Testing Guide

## Overview

Cerebro implements a comprehensive testing strategy with integration tests, end-to-end tests, and performance testing to ensure system reliability and quality.

## Testing Architecture

```
┌──────────────────────────────────────────────────────┐
│                    E2E Tests                         │
│         (User Journeys, Cross-browser)               │
├──────────────────────────────────────────────────────┤
│                Integration Tests                      │
│    (API, Database, Workflows, Agents, Security)      │
├──────────────────────────────────────────────────────┤
│                   Unit Tests                         │
│        (Components, Services, Utilities)             │
└──────────────────────────────────────────────────────┘
```

## Test Infrastructure

### Docker-Based Test Environment

The testing infrastructure uses Docker Compose (`tests/integration/docker-compose.test.yml`) to create isolated test environments with real services:

- **PostgreSQL** (`postgres:16-alpine`): Full database with migrations
- **Redis** (`redis:7-alpine`): Caching and session storage

These are the only two services in the test compose file. Execution is in-process (`DirectExecutionService`, asyncio) — there is no external workflow engine or object store to stand up.

### Test Factories

Located in `tests/factories/`, these provide consistent test data generation.
These are synchronous `factory_boy` factories — build objects directly, do not `await` them.

#### User Factory
```python
from tests.factories.user_factory import UserFactory, APIKeyFactory

# Create test user (factory_boy build/create is synchronous)
user = UserFactory(role="researcher", is_verified=True)

# Role helpers
admin = UserFactory.create_admin()
researcher = UserFactory.create_researcher()
viewer = UserFactory.create_viewer()

# API keys are a separate factory, not a UserFactory method
api_key = APIKeyFactory(user_id=user.id)
```

#### Project Factory
```python
from tests.factories.project_factory import (
    ResearchProjectFactory,
    ResearchResultFactory,
)

# Create research project
project = ResearchProjectFactory(user_id=user.id, status="in_progress")

# Create a result attached to a project
result = ResearchResultFactory(project_id=project.id)
```

#### Agent Response Factories
```python
from tests.factories.agent_factory import (
    MockAgentResponseFactory,
    MockGeminiResponseFactory,
    MockMCPToolFactory,
)

# Build a canned agent response payload
agent_response = MockAgentResponseFactory.create_literature_review_response()

# Mock a Gemini response
response = MockGeminiResponseFactory.create_response(
    "Analyze this research query..."
)

# Mock an MCP tool response
tool_response = MockMCPToolFactory.create_academic_search_response()
```

## Integration Tests

### API Integration Tests

Located in `tests/integration/test_api_integration.py`:

Test names in this module include `test_user_registration_flow`,
`test_token_refresh_flow`, `test_password_reset_flow`, and
`test_complete_research_workflow`. Note: the module's own docstring flags that its
research tests POST to `/api/v1/projects/...` while the real research router is
mounted at `/api/v1/research/projects/...`, so those tests hit 404s and are skipped.

#### Authentication Flow Testing
```python
async def test_user_registration_flow(async_client):
    # Registration
    response = await client.post("/api/v1/auth/register", json={
        "email": "test@example.com",
        "password": "SecurePass123!",
        "username": "testuser"
    })
    assert response.status_code == 201
    
    # Email verification (token passed as a query parameter)
    token = extract_verification_token(response)
    response = await client.get(f"/api/v1/auth/verify-email?token={token}")
    assert response.status_code == 200
    
    # Login
    response = await client.post("/api/v1/auth/login", json={
        "email": "test@example.com",
        "password": "SecurePass123!"
    })
    assert response.status_code == 200
    assert "access_token" in response.json()
```

#### Research Workflow Testing
```python
async def test_complete_research_workflow(authenticated_client):
    # NOTE: the real research router is mounted at /api/v1/research/projects/...;
    # use that prefix. Creation kicks off execution — there is no separate /start
    # endpoint on the research router — the DirectExecutionService background task
    # is spawned when the project is created.
    project = await authenticated_client.post("/api/v1/research/projects", json={
        "title": "AI Research",
        "query": "Impact of AI on employment",
        "domains": ["AI", "Economics"]
    })
    
    # Monitor project-level progress
    progress = await authenticated_client.get(
        f"/api/v1/research/projects/{project['id']}/progress"
    )
    assert progress["progress_percentage"] >= 0
```

### Database Integration Tests

Located in `tests/integration/test_database_integration.py`:

#### Transaction Testing
```python
async def test_transaction_rollback(db_session):
    async with db_session.begin() as transaction:
        # Create project
        project = await project_repo.create(
            title="Test Project"
        )
        
        # Simulate error
        raise Exception("Simulated error")
        
    # Verify rollback
    project = await project_repo.get(project.id)
    assert project is None
```

#### Repository Integration
```python
async def test_complex_query(db_session):
    # Test aggregation
    stats = await result_repo.get_statistics(project_id)
    assert stats["total_results"] > 0
    assert stats["average_confidence"] > 0.5
    
    # Test relationship loading
    project = await project_repo.get_with_tasks(project_id)
    assert len(project.tasks) > 0
```

### Execution Workflow Integration Tests

Execution runs in-process via `DirectExecutionService` (asyncio background task) — there is
no Temporal engine, and there is no top-level LangGraph orchestrator (`src/orchestration/` was
removed; LangGraph now lives only inside the domain supervisors). Integration tests exercise the
real flow by submitting a query and polling the execution status endpoint until it completes:

```python
async def test_research_execution_workflow(client):
    # Submit a MASR-routed research query. The immediate response returns
    # placeholder routing fields plus an execution/project id; real routing
    # data becomes available later via the status endpoint.
    submit = await client.post("/api/v1/query/research", json={
        "query": "Impact of AI on employment",
        "domains": ["ai", "economics"]
    })
    assert submit.status_code == 200
    execution_id = submit.json()["execution_id"]

    # Poll execution status until the background task finishes.
    for _ in range(30):
        status = await client.get(
            f"/api/v1/query/execution/{execution_id}/status"
        )
        assert status.status_code == 200
        if status.json()["status"] in ("completed", "failed"):
            break
        await asyncio.sleep(1)

    assert status.json()["status"] == "completed"

    # Final results (including real routing metadata) are on the results endpoint.
    results = await client.get(
        f"/api/v1/query/execution/{execution_id}/results"
    )
    assert results.status_code == 200
```

## End-to-End Tests

### Python E2E Tests (API-level, no browser)

The Python E2E suite in `tests/e2e/` does **not** use Playwright or a browser.
`tests/e2e/conftest.py` is effectively empty (no Playwright fixture), and the tests are
plain `httpx` API tests exercising the live docker-compose stack over
`http://localhost:8000`. The files are:

- `test_auth_e2e.py` — full authentication lifecycle (register, login, refresh, logout)
- `test_research_project_e2e.py` — research project API flows
- `test_tenant_isolation_e2e.py` — multi-tenant isolation
- `test_websocket_e2e.py` — WebSocket endpoints

They require docker-compose running (`docker compose up -d`) and are run with, e.g.,
`pytest tests/e2e/ --no-cov -v`. A representative shape:

```python
async def test_auth_e2e(async_client):
    # Register + login against the running API (no browser involved)
    resp = await async_client.post("/api/v1/auth/register", json={
        "email": "newuser@example.com",
        "password": "SecurePass123!",
        "username": "newuser",
    })
    assert resp.status_code in (200, 201)
```

### Browser E2E (JavaScript / Playwright)

The only Playwright specs live on the web frontend as JS specs under
`cerebro/web/tests/e2e/` and are executed with `npx playwright test` (not pytest).
See the note in `.github/workflows/e2e-tests.yml` for the current status of that job.

## Performance Testing

### API Performance Benchmarks

```python
@pytest.mark.benchmark
async def test_api_performance(benchmark, auth_client):
    result = await benchmark(
        auth_client.get,
        "/api/v1/research/projects"
    )
    assert result.status_code == 200
    assert benchmark.stats["mean"] < 0.2  # 200ms
```

### Database Performance

```python
async def test_bulk_insert_performance(benchmark, db_session):
    items = [generate_item() for _ in range(1000)]
    
    elapsed = await benchmark(
        result_repo.bulk_create,
        items
    )
    
    assert elapsed < 2.0  # 2 seconds for 1000 items
```

## Load Testing

### Locust Configuration

Located in `tests/load/locustfile.py`:

The file defines `ResearchPlatformUser` (and `AdminUser`), whose tasks hit
`/api/v1/projects`, `/api/v1/projects/{id}/status|/start|/results`, and `/api/v1/admin/*`:

```python
from locust import HttpUser, task, between

class ResearchPlatformUser(HttpUser):
    wait_time = between(1, 3)
    
    @task(3)
    def list_projects(self):
        self.client.get("/api/v1/projects")
    
    @task(1)
    def create_project(self):
        self.client.post("/api/v1/projects", json={
            "title": f"Load Test {uuid4()}",
            "query": "Test query"
        })
```

### Running Load Tests

```bash
# Web UI
locust -f tests/load/locustfile.py --host=http://localhost:8000

# Headless
locust -f tests/load/locustfile.py \
    --host=http://localhost:8000 \
    --users=100 \
    --spawn-rate=10 \
    --run-time=5m \
    --headless
```

## CI/CD Integration

### GitHub Actions Workflows

#### Integration Tests
`.github/workflows/integration-tests.yml` runs `pytest tests/integration/` directly
(it does **not** invoke `scripts/run_integration_tests.sh`) against service containers
`postgres:16-alpine` and `redis:7-alpine`, enforcing `--cov-fail-under=25` in CI:
```yaml
name: Integration Tests
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  workflow_dispatch:

jobs:
  integration-tests:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16-alpine
      redis:
        image: redis:7-alpine
    
    steps:
      - uses: actions/checkout@v4
      - name: Run Integration Tests
        run: |
          pytest tests/integration/ \
            --cov=src --cov-fail-under=25 -v
```

#### E2E Tests
`.github/workflows/e2e-tests.yml` — the browser-matrix job (`matrix.browser: [chromium, firefox]`,
no webkit) is currently **disabled** via `if: false` because no pytest-playwright tests exist;
when enabled it runs `pytest tests/e2e/ --browser=...` directly, not `scripts/run_e2e_tests.sh`.
The job that actually runs is the browserless `e2e-python` job (httpx API tests):
```yaml
name: E2E Tests
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  workflow_dispatch:

jobs:
  e2e-tests:
    runs-on: ubuntu-latest
    # Disabled: no pytest-playwright tests exist yet.
    if: false
    strategy:
      matrix:
        browser: [chromium, firefox]
    
    steps:
      - uses: actions/checkout@v4
      - name: Install Playwright browsers
        run: playwright install --with-deps ${{ matrix.browser }}
      - name: Run E2E Tests
        run: pytest tests/e2e/ --browser=${{ matrix.browser }}
```

## Test Scripts

### Integration Test Runner
`scripts/run_integration_tests.sh` starts the compose stack, inlines its own readiness
loops (`pg_isready`, `redis-cli ping`, plus a wait for a Temporal container that no longer
exists in the compose file), then runs pytest with a **local** `--cov-fail-under=80`
(higher than the CI gate of 25). There is no `wait-for-services.sh` helper:
```bash
#!/bin/bash
set -e
COMPOSE_FILE="tests/integration/docker-compose.test.yml"

# Start services
docker-compose -f "$COMPOSE_FILE" up -d

# Wait for PostgreSQL / Redis with inline loops
for i in {1..30}; do
    docker-compose -f "$COMPOSE_FILE" exec -T postgres \
        pg_isready -U test_user -d test_research_db && break
    sleep 1
done
# (a similar redis-cli ping loop and a legacy Temporal wait loop follow)

# Run migrations
alembic upgrade head || echo "Migrations skipped (may not exist)"

# Run tests (local threshold is 80%)
pytest "$COMPOSE_FILE"/../ --cov=src --cov-fail-under=80 -v

# Cleanup via an EXIT trap: docker-compose -f "$COMPOSE_FILE" down -v
```

### E2E Test Runner
`scripts/run_e2e_tests.sh` starts docker-compose, inlines a `curl /health` readiness loop
(no `wait-for-app.sh` helper), and loops over chromium + firefox:
```bash
#!/bin/bash
set -e
BASE_URL="http://localhost:8000"
BROWSERS="chromium firefox"

# Start application
docker-compose up -d

# Wait for app via inline health-check loop
for i in {1..30}; do
    curl -s "$BASE_URL/health" > /dev/null 2>&1 && break
    sleep 2
done

# Run E2E tests per browser
for BROWSER in $BROWSERS; do
    pytest tests/e2e --browser=$BROWSER --video=on --screenshot=on -v || true
done

# Cleanup via an EXIT trap: docker-compose down
```

## Test Data Management

### Fixtures
`tests/fixtures/` contains a single file, `golden_dataset.json`. There are no
`users.json`/`projects.json`/`agents.json` files; test data is generated in code via the
factories above and the fixtures in `tests/integration/conftest.py`.

`tests/integration/conftest.py` provides the container and session fixtures — including
`docker_compose`, `postgres_container`, `redis_container`, `test_engine`, `db_session`,
`redis_client`, `jwt_service`, and `authenticated_client` (there is no `seeded_db` fixture;
in-code seeding is done by `seed_test_data`).

### Database Seeding
```python
# tests/integration/conftest.py — seeding is done in code, not from JSON files
@pytest_asyncio.fixture
async def seed_test_data(db_session):
    # Build rows with the factories, persist them, then hand back the session
    user = UserFactory(role="researcher")
    db_session.add(user)
    await db_session.commit()

    yield db_session
```

## Coverage Goals

**Enforced CI gate**: `coverage report --fail-under=25` (`.github/workflows/ci.yml`). A build fails
only when total coverage drops below **25%**. That is the actual gate — do not assume a higher
threshold is enforced.

The figures below are **aspirational targets**, not enforced thresholds:

- **Unit Tests**: 90% coverage (aspirational)
- **Integration Tests**: 85% coverage (aspirational)
- **E2E Tests**: Critical paths 100% (aspirational)
- **Overall**: 85% (aspirational)

### Checking Coverage

```bash
# Run with coverage
pytest --cov=src --cov-report=html

# View report
open htmlcov/index.html
```

## Best Practices

### Test Organization
- Group related tests in classes
- Use descriptive test names
- One assertion per test when possible
- Use fixtures for common setup

### Async Testing
```python
# Use pytest-asyncio
@pytest.mark.asyncio
async def test_async_operation():
    result = await async_function()
    assert result is not None

# Use async fixtures
@pytest_asyncio.fixture
async def async_client():
    async with AsyncClient() as client:
        yield client
```

### Test Isolation
- Each test should be independent
- Use transactions for database tests
- Mock external services
- Clean up after tests

### Performance
- Use pytest-xdist for parallel execution
- Cache Docker images
- Use in-memory databases for unit tests
- Optimize fixture scope

## Troubleshooting

### Common Issues

1. **Docker services not starting**
   ```bash
   docker-compose ps  # Check status
   docker-compose logs <service>  # View logs
   ```

2. **Test database conflicts**
   ```bash
   # Reset test database
   docker-compose down -v
   docker-compose up -d
   ```

3. **Flaky tests**
   - Add retries for network operations
   - Use explicit waits in E2E tests
   - Check for race conditions

4. **Slow tests**
   - Use pytest profiling: `pytest --profile`
   - Optimize database queries
   - Use parallel execution

## Monitoring Test Health

### Test Metrics Dashboard
Track key metrics:
- Test execution time
- Failure rate
- Coverage trends
- Flaky test detection

### Alerts
Set up alerts for:
- Coverage drops below threshold
- Test execution time increases
- Consistent test failures

## Next Steps

1. **Expand test coverage** for edge cases
2. **Add visual regression testing** for UI
3. **Implement mutation testing** for test quality
4. **Set up test environments** for staging
5. **Add security testing** (OWASP ZAP)
6. **Implement contract testing** for microservices

The testing infrastructure provides comprehensive validation of Cerebro, ensuring reliability and quality across all components.