# Temporal Workflow Orchestration

## Overview

The Multi-Agent Research Platform uses Temporal workflows to orchestrate complex research processes, coordinating multiple AI agents and managing long-running operations with reliability, fault tolerance, and observability. This document details the workflow architecture, implementation patterns, and operational procedures.

## Architecture

### Temporal Components

```mermaid
graph TB
    subgraph "Research Platform"
        API[FastAPI Server]
        CLI[CLI Client]
        WEB[Web Interface]
    end
    
    subgraph "Temporal Infrastructure"
        TC[Temporal Client]
        TS[Temporal Server]
        TW[Temporal Worker]
        TUI[Temporal Web UI]
    end
    
    subgraph "Workflows"
        RW[Research Workflow]
        PRW[Parallel Research Workflow]
        HW[Hybrid Workflow]
    end
    
    subgraph "Activities"
        LRA[Literature Review Activity]
        DSA[Data Synthesis Activity]
        RGA[Report Generation Activity]
        CRPA[Create Research Plan Activity]
        EATA[Execute Agent Task Activity]
        ARA[Aggregate Results Activity]
    end
    
    subgraph "External Services"
        AGENTS[AI Agents]
        DB[Database]
        GEMINI[Gemini API]
        MCP[MCP Tools]
    end
    
    API --> TC
    CLI --> TC
    WEB --> TC
    
    TC --> TS
    TS --> TW
    
    TW --> RW
    TW --> PRW
    TW --> HW
    
    RW --> LRA
    RW --> DSA
    RW --> RGA
    RW --> CRPA
    RW --> EATA
    RW --> ARA
    
    LRA --> AGENTS
    DSA --> AGENTS
    EATA --> AGENTS
    
    LRA --> DB
    DSA --> DB
    RGA --> DB
    
    AGENTS --> GEMINI
    AGENTS --> MCP
```

### Key Concepts

**Workflows**: Long-running, durable business processes that coordinate activities
**Activities**: Individual units of work that can interact with external services
**Tasks**: Specific work items that can be executed by workers
**Signals**: External events that can modify workflow behavior
**Queries**: Read-only operations to inspect workflow state

## Workflow Implementations

### ResearchWorkflow

The main workflow that orchestrates the complete research process with versioned execution paths.

#### Basic Structure

```python
@workflow.defn
class ResearchWorkflow:
    """Main research workflow with versioning support."""
    
    WORKFLOW_VERSION = 3
    
    def __init__(self) -> None:
        self._progress = WorkflowProgress(
            percentage=0.0,
            current_phase="initialization",
            completed_tasks=[],
            pending_tasks=["literature_review", "data_synthesis", "report_generation"],
            failed_tasks=[]
        )
    
    @workflow.run
    async def run(self, project_data: dict[str, Any]) -> dict[str, Any]:
        """Execute the research workflow pipeline."""
        return await self._run_v3(project_data)
```

#### Workflow Versions

**Version 1 (Basic Pipeline)**:
```
project_data → literature_review → synthesis → report → final_result
```

**Version 2 (With Planning)**:
```
project_data → research_plan → literature_review → synthesis → report → final_result
```

**Version 3 (Full Multi-Agent)**:
```
project_data → research_plan → [agent_tasks] → aggregation → report → final_result
```

#### Implementation Example

```python
async def _run_v3(self, project_data: dict[str, Any]) -> dict[str, Any]:
    """Version 3: Full workflow with plan, agent tasks, and aggregation."""
    
    retry_policy = RetryPolicy(
        initial_interval=timedelta(seconds=1),
        maximum_interval=timedelta(seconds=30),
        maximum_attempts=3,
        backoff_coefficient=2,
    )
    
    # Phase 1: Research Plan
    self._update_progress(5.0, "research_planning")
    plan_result = await workflow.execute_activity(
        "create_research_plan_activity",
        project_data,
        start_to_close_timeout=timedelta(minutes=5),
        retry_policy=retry_policy,
    )
    
    # Phase 2: Execute Agent Tasks in Parallel
    self._update_progress(10.0, "executing_agents")
    agent_tasks = [
        {
            "agent_type": "literature_review",
            "task_id": "lit-001",
            "input_data": project_data,
        },
        {
            "agent_type": "comparative_analysis",
            "task_id": "comp-001",
            "input_data": project_data,
        },
        {
            "agent_type": "methodology",
            "task_id": "meth-001",
            "input_data": project_data,
        },
    ]
    
    # Execute agents concurrently
    agent_results = []
    for task in agent_tasks:
        result = await workflow.execute_activity(
            "execute_agent_task_activity",
            task,
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=retry_policy,
        )
        agent_results.append(result)
    
    # Phase 3: Aggregate Results
    self._update_progress(55.0, "aggregating_results")
    aggregated_result = await workflow.execute_activity(
        "aggregate_results_activity",
        {"results": agent_results, "project_context": project_data},
        start_to_close_timeout=timedelta(minutes=5),
        retry_policy=retry_policy,
    )
    
    # Phase 4: Generate Final Report
    report_result = await workflow.execute_activity(
        "report_generation_activity",
        {
            **project_data,
            "aggregated_results": aggregated_result,
            "research_plan": plan_result.get("plan"),
        },
        start_to_close_timeout=timedelta(minutes=5),
        retry_policy=retry_policy,
    )
    
    self._update_progress(100.0, "completed")
    
    return {
        "status": "completed",
        "workflow_version": self.WORKFLOW_VERSION,
        "project_id": project_data.get("id"),
        "title": project_data.get("title"),
        "research_plan": plan_result.get("plan"),
        "aggregated_results": aggregated_result,
        "report": report_result,
        "metadata": {
            "total_agents": len(agent_results),
            "workflow_version": self.WORKFLOW_VERSION,
        },
    }
```

### ParallelResearchWorkflow

Advanced workflow demonstrating parallel execution patterns.

```python
@workflow.defn
class ParallelResearchWorkflow:
    """Workflow with parallel activity execution."""
    
    @workflow.run
    async def run(self, project_data: dict[str, Any]) -> dict[str, Any]:
        """Execute independent activities in parallel."""
        
        retry_policy = RetryPolicy(
            initial_interval=timedelta(seconds=1),
            maximum_interval=timedelta(seconds=30),
            maximum_attempts=3,
        )
        
        # Execute independent activities concurrently
        parallel_tasks = [
            workflow.execute_activity(
                "parallel_activity_1",
                project_data,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry_policy,
            ),
            workflow.execute_activity(
                "parallel_activity_2", 
                project_data,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry_policy,
            )
        ]
        
        # Wait for all parallel tasks
        parallel_results = await asyncio.gather(*parallel_tasks)
        
        return {
            "status": "completed",
            "results": parallel_results,
        }
```

## Activity Implementations

### Core Research Activities

#### literature_review_activity

Discovers and analyzes academic sources.

```python
@activity.defn(name="literature_review_activity")
async def literature_review_activity(project_data: dict[str, Any]) -> dict[str, Any]:
    """Perform literature review for research project."""
    
    # Extract parameters
    depth_level = project_data.get("query", {}).get("depth_level", "comprehensive")
    domains = project_data.get("query", {}).get("domains", [])
    max_sources = project_data.get("scope", {}).get("max_sources", 50)
    
    # Determine source count based on depth
    source_limits = {
        ResearchDepth.SURVEY.value: min(10, max_sources),
        ResearchDepth.COMPREHENSIVE.value: min(50, max_sources),
        ResearchDepth.EXHAUSTIVE.value: min(200, max_sources),
    }
    
    target_sources = source_limits.get(depth_level, 50)
    
    # Simulate literature discovery
    sources = []
    for i in range(target_sources):
        sources.append({
            "id": f"source_{i+1}",
            "title": f"Research Paper {i+1} on {', '.join(domains[:2])}",
            "authors": [f"Author {i+1}"],
            "year": 2024 - (i % 5),
            "content": f"Abstract and content for paper {i+1}",
            "relevance_score": 0.95 - (i * 0.01),
            "domain": domains[i % len(domains)] if domains else "General",
        })
    
    return {
        "sources": sources,
        "status": "completed",
        "total_sources": len(sources),
        "search_domains": domains,
    }
```

#### execute_agent_task_activity

Dispatches tasks to specialized AI agents.

```python
@activity.defn(name="execute_agent_task_activity")
async def execute_agent_task_activity(task_data: dict[str, Any]) -> dict[str, Any]:
    """Execute a specific agent task."""
    
    agent_type = task_data.get("agent_type", "unknown")
    task_id = task_data.get("task_id", "")
    input_data = task_data.get("input_data", {})
    
    try:
        # Create agent using factory
        agent_config = task_data.get("agent_config", {})
        agent = AgentFactory.create_agent(agent_type, agent_config)
        
        # Create agent task
        agent_task = AgentTask(
            id=task_id,
            agent_type=agent_type,
            input_data=input_data,
            context=task_data.get("context", {}),
            timeout=task_data.get("timeout_seconds", 60),
        )
        
        # Execute with retry logic
        result = await agent.execute_with_retry(agent_task)
        
        return {
            "status": result.status,
            "agent_type": agent_type,
            "task_id": task_id,
            "result": result.output,
            "confidence": result.confidence,
            "execution_time": result.execution_time,
            "metadata": result.metadata,
        }
        
    except Exception as e:
        return {
            "status": "failed",
            "error": str(e),
            "agent_type": agent_type,
            "task_id": task_id,
        }
```

### Activity Design Patterns

#### Pure Function Activities

Activities are designed as pure functions that transform inputs to outputs:

```python
@activity.defn(name="data_synthesis_activity")
async def data_synthesis_activity(input_data: dict[str, Any]) -> dict[str, Any]:
    """Synthesize data from literature sources."""
    
    sources = input_data.get("sources", [])
    query_text = input_data.get("query", {}).get("text", "")
    
    # Group sources by domain (pure transformation)
    sources_by_domain = {}
    for source in sources:
        domain = source.get("domain", "General")
        if domain not in sources_by_domain:
            sources_by_domain[domain] = []
        sources_by_domain[domain].append(source)
    
    # Generate insights (deterministic logic)
    key_findings = []
    for domain, domain_sources in sources_by_domain.items():
        key_findings.append(
            f"Analysis of {len(domain_sources)} sources in {domain} reveals significant patterns"
        )
    
    # Create synthesis (pure data transformation)
    synthesis = {
        "summary": f"Comprehensive analysis of '{query_text}' across {len(sources)} sources",
        "domains_analyzed": list(sources_by_domain.keys()),
        "source_distribution": {
            domain: len(domain_sources)
            for domain, domain_sources in sources_by_domain.items()
        },
        "methodology": "Systematic literature review with thematic analysis",
        "confidence_level": "high" if len(sources) > 20 else "moderate",
    }
    
    return {
        "synthesis": synthesis,
        "key_findings": key_findings,
        "status": "completed",
    }
```

## Error Handling and Reliability

### Retry Policies

Standard retry configuration for robust operation:

```python
retry_policy = RetryPolicy(
    initial_interval=timedelta(seconds=1),     # Start with 1 second
    maximum_interval=timedelta(seconds=30),    # Cap at 30 seconds
    maximum_attempts=3,                        # Try up to 3 times
    backoff_coefficient=2,                     # Exponential backoff
)
```

### Timeout Configuration

Activity timeouts prevent infinite execution:

```python
# Different timeouts for different activity types
timeout_configs = {
    "literature_review_activity": timedelta(minutes=10),
    "data_synthesis_activity": timedelta(minutes=15),
    "report_generation_activity": timedelta(minutes=5),
    "execute_agent_task_activity": timedelta(minutes=10),
    "create_research_plan_activity": timedelta(minutes=5),
}
```

### Workflow Error Handling

```python
try:
    # Execute workflow activities
    result = await workflow.execute_activity(...)
    
except ActivityError as e:
    # Handle specific activity failures
    workflow.logger.error(f"Activity failed: {e}")
    raise ApplicationError(f"Research step failed: {e}")
    
except Exception as e:
    # Handle unexpected errors
    workflow.logger.error(f"Workflow failed: {e}")
    self._progress.failed_tasks.append(self._progress.current_phase)
    raise ApplicationError(f"Research workflow failed: {e}")
```

## Progress Tracking and Observability

### Progress Queries

Workflows expose real-time progress through queries:

```python
@workflow.query
def get_progress(self) -> dict[str, Any]:
    """Get current workflow progress."""
    return {
        "percentage": self._progress.percentage,
        "current_phase": self._progress.current_phase,
        "completed_tasks": self._progress.completed_tasks[:],
        "pending_tasks": self._progress.pending_tasks[:],
        "failed_tasks": self._progress.failed_tasks[:],
    }

@workflow.query
def get_version(self) -> int:
    """Get the workflow version."""
    return self.WORKFLOW_VERSION
```

### Progress Updates

Internal progress tracking with external visibility:

```python
def _update_progress(self, percentage: float, phase: str) -> None:
    """Update workflow progress state."""
    self._progress.percentage = percentage
    self._progress.current_phase = phase
    workflow.logger.info(f"Progress: {percentage}% - {phase}")

def _mark_task_completed(self, task: str) -> None:
    """Mark a task as completed."""
    if task in self._progress.pending_tasks:
        self._progress.pending_tasks.remove(task)
        self._progress.completed_tasks.append(task)
```

### Workflow Monitoring

Monitor workflow execution with detailed logging:

```python
# At workflow start
workflow.logger.info(
    f"Starting research workflow v{self.WORKFLOW_VERSION} for project: {project_data.get('title')}"
)

# During activity execution
workflow.logger.info(f"Executing phase: {phase_name}")

# At completion
workflow.logger.info(
    f"Research workflow completed for project: {project_data.get('title')}"
)
```

## Workflow Versioning

### Version Management

Workflows support versioning for backward compatibility:

```python
class ResearchWorkflow:
    WORKFLOW_VERSION = 3
    
    @workflow.run
    async def run(self, project_data: dict[str, Any]) -> dict[str, Any]:
        """Route to appropriate version."""
        version = project_data.get("workflow_version", self.WORKFLOW_VERSION)
        
        if version == 1:
            return await self._run_v1(project_data)
        elif version == 2:
            return await self._run_v2(project_data)
        elif version == 3:
            return await self._run_v3(project_data)
        else:
            # Default to latest version
            return await self._run_v3(project_data)
```

### Migration Strategies

When updating workflows:

1. **Add new version method** (`_run_v4`)
2. **Update `WORKFLOW_VERSION` constant**
3. **Test new version thoroughly**
4. **Deploy with backward compatibility**
5. **Monitor for version-specific issues**

## Temporal Client Integration

### Client Configuration

```python
from temporalio.client import Client, TLSConfig
from temporalio.worker import Worker

# Create Temporal client
client = await Client.connect(
    target_host=settings.TEMPORAL_HOST,
    namespace=settings.TEMPORAL_NAMESPACE,
    tls=TLSConfig(
        server_root_ca_cert=settings.TEMPORAL_TLS_CERT,
        domain=settings.TEMPORAL_TLS_DOMAIN,
    ) if settings.TEMPORAL_TLS_ENABLED else False,
)
```

### Starting Workflows

```python
async def start_research_workflow(
    client: Client,
    project_data: dict[str, Any],
    workflow_id: str | None = None,
) -> str:
    """Start a research workflow."""
    
    if not workflow_id:
        workflow_id = f"research-{project_data['id']}-{int(time.time())}"
    
    # Start workflow execution
    handle = await client.start_workflow(
        ResearchWorkflow.run,
        project_data,
        id=workflow_id,
        task_queue="research-task-queue",
        execution_timeout=timedelta(hours=2),
        retry_policy=RetryPolicy(maximum_attempts=1),  # No workflow retries
    )
    
    return handle.id
```

### Querying Workflow State

```python
async def get_workflow_progress(
    client: Client,
    workflow_id: str,
) -> dict[str, Any]:
    """Get workflow progress."""
    
    handle = client.get_workflow_handle(workflow_id)
    
    try:
        progress = await handle.query(ResearchWorkflow.get_progress)
        return progress
    except WorkflowQueryFailedError as e:
        raise WorkflowNotFoundError(f"Workflow {workflow_id} not found") from e
```

### Workflow Termination

```python
async def cancel_workflow(
    client: Client,
    workflow_id: str,
    reason: str = "User requested cancellation",
) -> bool:
    """Cancel a running workflow."""
    
    try:
        handle = client.get_workflow_handle(workflow_id)
        await handle.cancel()
        return True
    except Exception as e:
        logger.error(f"Failed to cancel workflow {workflow_id}: {e}")
        return False
```

## Worker Configuration

### Worker Setup

```python
async def run_worker():
    """Run Temporal worker."""
    
    # Import all activities
    from src.temporal.activities.research_activities import (
        literature_review_activity,
        data_synthesis_activity,
        report_generation_activity,
        create_research_plan_activity,
        execute_agent_task_activity,
        aggregate_results_activity,
    )
    
    # Create worker
    worker = Worker(
        client,
        task_queue="research-task-queue",
        workflows=[ResearchWorkflow, ParallelResearchWorkflow],
        activities=[
            literature_review_activity,
            data_synthesis_activity,
            report_generation_activity,
            create_research_plan_activity,
            execute_agent_task_activity,
            aggregate_results_activity,
        ],
        max_concurrent_activities=10,
        max_concurrent_workflows=5,
    )
    
    # Start worker
    await worker.run()
```

### Activity Worker Pool

```python
# Worker configuration for high throughput
worker_config = {
    "max_concurrent_activities": 20,
    "max_concurrent_workflows": 10,
    "max_cached_workflows": 100,
    "max_heartbeat_throttle_interval": timedelta(seconds=60),
    "default_heartbeat_throttle_interval": timedelta(seconds=30),
}
```

## Deployment and Operations

### Docker Configuration

```dockerfile
# Dockerfile.worker
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src/ src/
CMD ["python", "-m", "src.temporal.worker"]
```

### Docker Compose

```yaml
version: '3.8'
services:
  temporal:
    image: temporalio/auto-setup:1.22
    environment:
      - DB=postgresql
      - DB_PORT=5432
      - POSTGRES_USER=temporal
      - POSTGRES_PWD=temporal
      - POSTGRES_SEEDS=postgres
    ports:
      - "7233:7233"
    depends_on:
      - postgres
  
  temporal-ui:
    image: temporalio/ui:2.21
    environment:
      - TEMPORAL_ADDRESS=temporal:7233
    ports:
      - "8080:8080"
    depends_on:
      - temporal
  
  worker:
    build:
      context: .
      dockerfile: docker/Dockerfile.worker
    environment:
      - TEMPORAL_HOST=temporal:7233
      - DATABASE_URL=postgresql+asyncpg://research:research123@postgres:5432/research_db
    depends_on:
      - temporal
      - postgres
    deploy:
      replicas: 3
```

### Kubernetes Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: temporal-worker
spec:
  replicas: 5
  selector:
    matchLabels:
      app: temporal-worker
  template:
    metadata:
      labels:
        app: temporal-worker
    spec:
      containers:
      - name: worker
        image: research-platform/worker:latest
        env:
        - name: TEMPORAL_HOST
          value: "temporal.temporal.svc.cluster.local:7233"
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: database-secret
              key: url
        resources:
          requests:
            memory: "512Mi"
            cpu: "250m"
          limits:
            memory: "1Gi"
            cpu: "500m"
```

## Monitoring and Observability

### Metrics Collection

```python
from prometheus_client import Counter, Histogram, Gauge

# Workflow metrics
workflow_executions = Counter(
    'temporal_workflow_executions_total',
    'Total workflow executions',
    ['workflow_type', 'status']
)

workflow_duration = Histogram(
    'temporal_workflow_duration_seconds',
    'Workflow execution duration',
    ['workflow_type']
)

active_workflows = Gauge(
    'temporal_active_workflows',
    'Number of active workflows',
    ['workflow_type']
)

# Activity metrics
activity_executions = Counter(
    'temporal_activity_executions_total',
    'Total activity executions',
    ['activity_name', 'status']
)

activity_duration = Histogram(
    'temporal_activity_duration_seconds',
    'Activity execution duration',
    ['activity_name']
)
```

### Health Checks

```python
async def check_temporal_health() -> dict[str, Any]:
    """Check Temporal cluster health."""
    try:
        # Test workflow execution
        test_workflow_id = f"health-check-{int(time.time())}"
        
        handle = await client.start_workflow(
            "health_check_workflow",
            {},
            id=test_workflow_id,
            task_queue="health-check-queue",
            execution_timeout=timedelta(seconds=30),
        )
        
        result = await handle.result()
        
        return {
            "status": "healthy",
            "workflow_execution": "ok",
            "response_time_ms": result.get("duration", 0) * 1000,
        }
        
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
        }
```

### Logging Configuration

```python
import structlog

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

# Workflow logging
@workflow.defn
class ResearchWorkflow:
    def __init__(self):
        self.logger = structlog.get_logger().bind(
            workflow_type="research",
            workflow_version=self.WORKFLOW_VERSION
        )
```

## Testing Strategies

### Unit Testing Workflows

```python
import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

@pytest.mark.asyncio
async def test_research_workflow():
    """Test research workflow execution."""
    
    async with WorkflowEnvironment() as env:
        # Create test worker
        worker = Worker(
            env.client,
            task_queue="test-queue",
            workflows=[ResearchWorkflow],
            activities=[
                literature_review_activity,
                data_synthesis_activity,
                report_generation_activity,
            ],
        )
        
        async with worker:
            # Test workflow execution
            result = await env.client.execute_workflow(
                ResearchWorkflow.run,
                {
                    "id": "test-project",
                    "title": "Test Research",
                    "query": {
                        "text": "Test query",
                        "domains": ["Test"],
                        "depth_level": "survey"
                    }
                },
                id="test-workflow",
                task_queue="test-queue",
            )
            
            assert result["status"] == "completed"
            assert "research_output" in result
```

### Integration Testing

```python
@pytest.mark.integration
async def test_workflow_with_real_agents():
    """Test workflow with actual agent integration."""
    
    # Use real Temporal cluster for integration tests
    client = await Client.connect("localhost:7233")
    
    project_data = {
        "id": "integration-test",
        "title": "Integration Test Project",
        "query": {
            "text": "Test query for integration",
            "domains": ["AI", "Testing"],
            "depth_level": "survey"
        }
    }
    
    # Start workflow
    workflow_id = f"integration-test-{int(time.time())}"
    handle = await client.start_workflow(
        ResearchWorkflow.run,
        project_data,
        id=workflow_id,
        task_queue="research-task-queue",
    )
    
    # Monitor progress
    while True:
        progress = await handle.query(ResearchWorkflow.get_progress)
        if progress["percentage"] >= 100:
            break
        await asyncio.sleep(1)
    
    # Get final result
    result = await handle.result()
    assert result["status"] == "completed"
```

## Best Practices

### Workflow Design

1. **Keep workflows deterministic** - No random operations or system calls
2. **Use activities for external interactions** - Database, API calls, file I/O
3. **Design for idempotency** - Activities should be safely retryable
4. **Minimize workflow state** - Store only essential data
5. **Use proper timeouts** - Prevent infinite execution
6. **Version workflows carefully** - Maintain backward compatibility

### Activity Design

1. **Pure functions when possible** - Deterministic input/output transformations
2. **Handle failures gracefully** - Return meaningful error information
3. **Use appropriate timeouts** - Based on expected execution time
4. **Implement heartbeats for long operations** - Keep Temporal informed
5. **Log comprehensively** - Aid in debugging and monitoring

### Error Handling

1. **Use typed exceptions** - Distinguish between retryable and non-retryable errors
2. **Implement circuit breakers** - For external service failures
3. **Configure appropriate retry policies** - Based on failure characteristics
4. **Log failures with context** - Include relevant debugging information

### Performance Optimization

1. **Use parallel execution** - For independent activities
2. **Batch operations when possible** - Reduce activity overhead
3. **Optimize worker configuration** - Based on workload characteristics
4. **Monitor resource usage** - CPU, memory, network
5. **Use appropriate task queues** - Separate concerns and priorities

## Troubleshooting

### Common Issues

#### Workflow Stuck

```bash
# Check workflow status
temporal workflow show --workflow-id research-123

# Describe workflow execution
temporal workflow describe --workflow-id research-123

# Check worker status
temporal task-queue describe --task-queue research-task-queue
```

#### Activity Timeouts

```python
# Increase timeout for specific activities
await workflow.execute_activity(
    "slow_activity",
    input_data,
    start_to_close_timeout=timedelta(minutes=30),  # Increased timeout
    retry_policy=retry_policy,
)
```

#### Worker Connection Issues

```bash
# Check worker logs
kubectl logs -l app=temporal-worker --tail=100

# Verify Temporal server connectivity
temporal cluster health

# Check task queue
temporal task-queue list-partition --task-queue research-task-queue
```

### Debugging Tools

#### Temporal Web UI

Access at `http://localhost:8080` to:
- View workflow execution history
- Monitor task queues
- Inspect workflow state
- Debug failed executions

#### CLI Commands

```bash
# List workflows
temporal workflow list --query "WorkflowType='ResearchWorkflow'"

# Get workflow result
temporal workflow show --workflow-id research-123 --follow

# Terminate workflow
temporal workflow terminate --workflow-id research-123 --reason "Debug"

# Reset workflow
temporal workflow reset --workflow-id research-123 --event-id 10
```

## Future Enhancements

### Planned Features

1. **Workflow Scheduling** - Cron-based research automation
2. **Signal Handling** - Dynamic workflow modification
3. **Child Workflows** - Nested research processes
4. **Local Activities** - High-performance local operations
5. **Workflow Search** - Advanced workflow querying
6. **Batch Processing** - Multi-project workflows

### Performance Improvements

1. **Activity Batching** - Reduce Temporal overhead
2. **Workflow Compression** - Optimize large state handling
3. **Custom Serialization** - Improve data transfer efficiency
4. **Worker Autoscaling** - Dynamic resource allocation
5. **Caching Strategies** - Reduce redundant operations

This comprehensive documentation provides the foundation for understanding, operating, and extending the Temporal workflow orchestration system in the Multi-Agent Research Platform.