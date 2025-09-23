# Integration Testing Suite - Complete Implementation Guide

## ✅ Completed Components

### 1. Test Infrastructure (Phase 1) ✅
- **Enhanced Configuration** (`tests/integration/conftest.py`)
  - Docker Compose integration for PostgreSQL, Redis, Temporal
  - Async fixtures for database sessions, HTTP clients
  - Authentication fixtures with role-based users
  - Test data seeding and cleanup

- **Test Factories** (`tests/factories/`)
  - `user_factory.py`: User generation with roles, sessions, API keys
  - `project_factory.py`: Research projects with full lifecycle
  - `agent_factory.py`: Mock agent responses and Gemini integration

- **Test Utilities** (`tests/utils/`)
  - `docker_utils.py`: Container management for integration tests
  - `db_utils.py`: Database operations, transactions, assertions
  - `auth_utils.py`: JWT tokens, OAuth mocks, permissions
  - `temporal_utils.py`: Workflow testing environment

### 2. Integration Tests (Phase 2) ✅
- **API Integration** (`test_api_integration.py`)
  - Complete authentication flows (registration, login, OAuth)
  - RBAC and authorization testing
  - Full research workflow from creation to results
  - Error handling and edge cases

- **Database Integration** (`test_database_integration.py`)
  - Transaction management and rollbacks
  - Repository pattern testing
  - Complex queries and aggregations
  - Constraint validation and cascade operations
  - Performance testing with bulk operations

## 🚧 Remaining Implementation Tasks

### 3. Workflow Integration Tests
```python
# tests/integration/test_workflow_integration.py
"""
Key test scenarios:
- Temporal workflow execution with real workers
- LangGraph orchestration and state management
- Checkpoint/resume functionality
- Parallel workflow execution
- Error recovery and compensation
"""
```

### 4. Agent Integration Tests
```python
# tests/integration/test_agent_integration.py
"""
Key test scenarios:
- Multi-agent coordination and communication
- MCP tool integration (academic search, citations)
- Agent result aggregation and synthesis
- Gemini API integration with rate limiting
- Agent failure and retry mechanisms
"""
```

### 5. Security Integration Tests
```python
# tests/integration/test_security_integration.py
"""
Key test scenarios:
- Complete security pipeline (auth → authz → audit)
- Rate limiting under load
- CORS and security headers
- SQL injection and XSS prevention
- Audit logging completeness
"""
```

### 6. E2E Test Setup (Phase 3)
```python
# tests/e2e/conftest.py
"""
Playwright configuration:
- Browser contexts for different user roles
- Page object models for UI components
- WebSocket connection handling
- Screenshot and video recording on failure
"""
```

### 7. User Journey Tests
```python
# tests/e2e/test_user_journeys.py
"""
Complete user journeys:
1. New User Journey:
   - Registration → Email verification → First project
   
2. Research Creation Journey:
   - Login → Create project → Monitor progress → Download report
   
3. Collaboration Journey:
   - Share project → Review results → Export data
"""
```

### 8. Performance Tests (Phase 4)
```python
# tests/performance/test_performance.py
"""
Performance benchmarks:
- API response times (p50, p95, p99)
- Database query performance
- Workflow execution speed
- Memory usage patterns
- Concurrent user handling
"""
```

### 9. Load Testing with Locust
```python
# tests/load/locustfile.py
from locust import HttpUser, task, between

class ResearchPlatformUser(HttpUser):
    wait_time = between(1, 3)
    
    @task(3)
    def list_projects(self):
        self.client.get("/api/v1/projects")
    
    @task(1)
    def create_project(self):
        self.client.post("/api/v1/projects", json={...})
    
    @task(2)
    def get_project_status(self):
        self.client.get(f"/api/v1/projects/{project_id}/status")
```

## 📜 Test Runner Scripts

### Integration Test Runner
```bash
#!/bin/bash
# scripts/run_integration_tests.sh

echo "Starting integration test environment..."
docker-compose -f tests/integration/docker-compose.test.yml up -d

echo "Waiting for services..."
sleep 10

echo "Running integration tests..."
pytest tests/integration/ \
  --cov=src \
  --cov-report=html \
  --cov-report=term \
  -v

echo "Cleaning up..."
docker-compose -f tests/integration/docker-compose.test.yml down -v
```

### E2E Test Runner
```bash
#!/bin/bash
# scripts/run_e2e_tests.sh

echo "Installing Playwright browsers..."
playwright install chromium firefox

echo "Starting application..."
docker-compose up -d

echo "Running E2E tests..."
pytest tests/e2e/ \
  --browser chromium \
  --browser firefox \
  --screenshot on \
  --video on \
  -v

docker-compose down
```

## 🔄 GitHub Actions CI/CD

### Integration Tests Workflow
```yaml
# .github/workflows/integration-tests.yml
name: Integration Tests

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_PASSWORD: test
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
      
      redis:
        image: redis:7
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    
    steps:
    - uses: actions/checkout@v3
    
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'
    
    - name: Install dependencies
      run: |
        pip install uv
        uv pip install -e ".[dev]"
    
    - name: Run integration tests
      run: |
        pytest tests/integration/ --cov=src --cov-report=xml
    
    - name: Upload coverage
      uses: codecov/codecov-action@v3
```

### E2E Tests Workflow
```yaml
# .github/workflows/e2e-tests.yml
name: E2E Tests

on:
  push:
    branches: [main]
  schedule:
    - cron: '0 0 * * *'  # Daily

jobs:
  test:
    runs-on: ubuntu-latest
    
    steps:
    - uses: actions/checkout@v3
    
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'
    
    - name: Install dependencies
      run: |
        pip install uv
        uv pip install -e ".[dev]"
        playwright install chromium
    
    - name: Start services
      run: docker-compose up -d
    
    - name: Run E2E tests
      run: pytest tests/e2e/ --browser chromium
    
    - name: Upload test artifacts
      if: failure()
      uses: actions/upload-artifact@v3
      with:
        name: e2e-artifacts
        path: |
          test-results/
          screenshots/
          videos/
```

## 📊 Test Coverage Goals

| Component | Target Coverage | Current | Status |
|-----------|----------------|---------|--------|
| API Routes | 90% | - | 🚧 |
| Database Layer | 85% | - | 🚧 |
| Workflows | 80% | - | 🚧 |
| Agents | 85% | - | 🚧 |
| Security | 95% | - | 🚧 |
| E2E Flows | 75% | - | 🚧 |

## 🎯 Next Steps

1. **Immediate Priority**:
   - Complete workflow integration tests
   - Implement agent integration tests
   - Set up basic E2E tests

2. **Secondary Priority**:
   - Performance benchmarking
   - Load testing configuration
   - CI/CD pipeline setup

3. **Ongoing**:
   - Maintain test coverage above 80%
   - Regular performance regression testing
   - Security vulnerability scanning

## 💡 Testing Best Practices

1. **Test Isolation**: Each test should be independent
2. **Data Cleanup**: Always clean up test data
3. **Mocking**: Mock external services appropriately
4. **Assertions**: Use specific, meaningful assertions
5. **Documentation**: Document complex test scenarios
6. **Performance**: Keep test execution time reasonable

## 🔧 Troubleshooting

### Common Issues:
1. **Docker containers not starting**: Check port conflicts
2. **Database connection errors**: Verify connection strings
3. **Temporal timeouts**: Increase workflow timeouts
4. **Flaky tests**: Add proper waits and retries

### Debug Commands:
```bash
# View container logs
docker-compose -f tests/integration/docker-compose.test.yml logs

# Connect to test database
psql postgresql://test_user:test_password@localhost:5433/test_research_db

# Monitor Redis
redis-cli -p 6380 MONITOR

# Check Temporal workflows
tctl --address localhost:7234 workflow list
```

## 📚 Resources

- [Pytest Documentation](https://docs.pytest.org/)
- [Playwright Python](https://playwright.dev/python/)
- [Locust Documentation](https://docs.locust.io/)
- [Docker Compose for Testing](https://docs.docker.com/compose/)