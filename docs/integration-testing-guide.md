# Integration and E2E Testing Guide

## Overview

The Multi-Agent Research Platform implements a comprehensive testing strategy with integration tests, end-to-end tests, and performance testing to ensure system reliability and quality.

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

The testing infrastructure uses Docker Compose to create isolated test environments with real services:

- **PostgreSQL**: Full database with migrations
- **Redis**: Caching and session storage
- **Temporal**: Workflow orchestration
- **MinIO**: S3-compatible object storage

### Test Factories

Located in `tests/factories/`, these provide consistent test data generation:

#### User Factory
```python
from tests.factories.user_factory import UserFactory

# Create test user
user = await UserFactory.create(
    role="researcher",
    is_verified=True
)

# Create admin user
admin = await UserFactory.create_admin()

# Create user with API key
user_with_key = await UserFactory.create_with_api_key()
```

#### Project Factory
```python
from tests.factories.project_factory import ProjectFactory

# Create research project
project = await ProjectFactory.create(
    user_id=user.id,
    status="in_progress"
)

# Create with mock results
project = await ProjectFactory.create_with_results()
```

#### Agent Factory
```python
from tests.factories.agent_factory import AgentFactory

# Create mock agent
agent = AgentFactory.create_mock_agent("literature_review")

# Mock Gemini response
response = AgentFactory.mock_gemini_response(
    "Analyze this research query..."
)
```

## Integration Tests

### API Integration Tests

Located in `tests/integration/test_api_integration.py`:

#### Authentication Flow Testing
```python
async def test_complete_auth_flow(client):
    # Registration
    response = await client.post("/auth/register", json={
        "email": "test@example.com",
        "password": "SecurePass123!",
        "username": "testuser"
    })
    assert response.status_code == 201
    
    # Email verification
    token = extract_verification_token(response)
    response = await client.get(f"/auth/verify-email?token={token}")
    assert response.status_code == 200
    
    # Login
    response = await client.post("/auth/login", json={
        "email": "test@example.com",
        "password": "SecurePass123!"
    })
    assert response.status_code == 200
    assert "access_token" in response.json()
```

#### Research Workflow Testing
```python
async def test_research_workflow(auth_client):
    # Create project
    project = await auth_client.post("/api/v1/research/projects", json={
        "title": "AI Research",
        "query": "Impact of AI on employment",
        "domains": ["AI", "Economics"]
    })
    
    # Start research
    response = await auth_client.post(
        f"/api/v1/research/projects/{project['id']}/start"
    )
    
    # Monitor progress
    status = await auth_client.get(
        f"/api/v1/research/projects/{project['id']}/status"
    )
    assert status["phase"] in ["planning", "execution", "synthesis"]
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

### Workflow Integration Tests

Testing Temporal and LangGraph workflows:

```python
async def test_temporal_workflow():
    # Start workflow
    workflow_id = await temporal_client.start_workflow(
        "research_workflow",
        project_data
    )
    
    # Wait for completion
    result = await temporal_client.get_workflow_result(workflow_id)
    assert result["status"] == "completed"
    
async def test_langgraph_orchestration():
    # Build graph
    graph = ResearchGraphBuilder().build()
    
    # Execute
    result = await graph.invoke({
        "query": "Test research query"
    })
    assert "research_plan" in result
```

## End-to-End Tests

### Playwright Setup

E2E tests use Playwright for browser automation:

```python
# tests/e2e/conftest.py
import pytest
from playwright.async_api import async_playwright

@pytest.fixture
async def browser():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        yield browser
        await browser.close()
```

### User Journey Tests

Located in `tests/e2e/test_user_journeys.py`:

```python
async def test_new_user_journey(page):
    # Navigate to homepage
    await page.goto("http://localhost:3000")
    
    # Click register
    await page.click("text=Sign Up")
    
    # Fill registration form
    await page.fill("input[name=email]", "newuser@example.com")
    await page.fill("input[name=password]", "SecurePass123!")
    await page.fill("input[name=username]", "newuser")
    
    # Submit
    await page.click("button[type=submit]")
    
    # Verify success
    await page.wait_for_selector("text=Verify your email")
```

### Cross-Browser Testing

```python
@pytest.mark.parametrize("browser_name", ["chromium", "firefox", "webkit"])
async def test_cross_browser(browser_name, playwright):
    browser = await getattr(playwright, browser_name).launch()
    page = await browser.new_page()
    
    # Run tests
    await page.goto("http://localhost:3000")
    assert await page.title() == "Research Platform"
    
    await browser.close()
```

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

```python
from locust import HttpUser, task, between

class ResearchUser(HttpUser):
    wait_time = between(1, 3)
    
    @task(3)
    def list_projects(self):
        self.client.get("/api/v1/research/projects")
    
    @task(1)
    def create_project(self):
        self.client.post("/api/v1/research/projects", json={
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
`.github/workflows/integration-tests.yml`:
```yaml
name: Integration Tests
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15
      redis:
        image: redis:7
    
    steps:
      - uses: actions/checkout@v3
      - name: Run Integration Tests
        run: ./scripts/run_integration_tests.sh
```

#### E2E Tests
`.github/workflows/e2e-tests.yml`:
```yaml
name: E2E Tests
on:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        browser: [chromium, firefox, webkit]
    
    steps:
      - uses: actions/checkout@v3
      - name: Install Playwright
        run: |
          pip install playwright
          playwright install ${{ matrix.browser }}
      - name: Run E2E Tests
        run: ./scripts/run_e2e_tests.sh --browser=${{ matrix.browser }}
```

## Test Scripts

### Integration Test Runner
`scripts/run_integration_tests.sh`:
```bash
#!/bin/bash
# Start services
docker-compose -f docker-compose.test.yml up -d

# Wait for services
./scripts/wait-for-services.sh

# Run migrations
alembic upgrade head

# Run tests
pytest tests/integration -v --cov=src --cov-report=html

# Cleanup
docker-compose -f docker-compose.test.yml down
```

### E2E Test Runner
`scripts/run_e2e_tests.sh`:
```bash
#!/bin/bash
# Start application
docker-compose up -d

# Wait for app
./scripts/wait-for-app.sh

# Run E2E tests
pytest tests/e2e -v --video=on --screenshot=on

# Stop application
docker-compose down
```

## Test Data Management

### Fixtures
Located in `tests/fixtures/`:
- `users.json`: Test user accounts
- `projects.json`: Sample research projects
- `agents.json`: Agent configurations

### Database Seeding
```python
# tests/integration/conftest.py
@pytest.fixture
async def seeded_db(db_session):
    # Load fixtures
    with open("tests/fixtures/users.json") as f:
        users = json.load(f)
    
    # Seed database
    for user_data in users:
        await user_repo.create(**user_data)
    
    yield db_session
    
    # Cleanup
    await db_session.rollback()
```

## Coverage Goals

- **Unit Tests**: 90% coverage
- **Integration Tests**: 85% coverage
- **E2E Tests**: Critical paths 100%
- **Overall**: 85% minimum

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

The testing infrastructure provides comprehensive validation of the Multi-Agent Research Platform, ensuring reliability and quality across all components.