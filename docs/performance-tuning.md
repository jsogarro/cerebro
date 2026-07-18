# Performance Tuning Guide

This guide provides comprehensive strategies and techniques for optimizing the performance of Cerebro.

## Table of Contents
- [Performance Overview](#performance-overview)
- [Database Optimization](#database-optimization)
- [Caching Strategies](#caching-strategies)
- [API Performance](#api-performance)
- [Agent Optimization](#agent-optimization)
- [Workflow Performance](#workflow-performance)
- [Resource Management](#resource-management)
- [Monitoring and Profiling](#monitoring-and-profiling)
- [Scaling Strategies](#scaling-strategies)
- [Benchmarking](#benchmarking)

## Performance Overview

### Key Performance Metrics

| Metric | Target | Good | Needs Improvement |
|--------|--------|------|-------------------|
| API Response Time | < 200ms | < 500ms | > 1000ms |
| Database Query Time | < 50ms | < 100ms | > 200ms |
| Agent Execution Time | < 30s | < 60s | > 120s |
| Workflow Completion | < 5min | < 10min | > 20min |
| Memory Usage | < 1GB | < 2GB | > 4GB |
| CPU Usage | < 70% | < 85% | > 95% |
| Cache Hit Rate | > 80% | > 60% | < 40% |

### Performance Baseline

Before optimization, establish baseline metrics:

```bash
# API Performance Test
ab -n 1000 -c 10 http://localhost:8000/health

# Database Performance
docker-compose exec postgres psql -U research -d research_db -c \
  "EXPLAIN ANALYZE SELECT * FROM research_projects LIMIT 100;"

# Memory Usage
docker stats --no-stream | grep api

# Agent Performance
research-cli projects create --title "Performance Test" \
  --query "Quick test" --domains "Test"
```

## Database Optimization

### PostgreSQL Configuration

#### Connection Pool Optimization

Pool settings are passed to `create_engine()` in `src/models/db/session.py`
(defaults shown below). Adjust these parameters to tune the connection pool:

```python
# src/models/db/session.py — create_engine()
pool_size = 20          # Base connection pool size
max_overflow = 10       # Additional overflow connections
pool_timeout = 30       # Wait for a connection, in seconds
pool_recycle = 3600     # Recycle connections hourly
pool_pre_ping = True    # Validate connections before use
```

#### Query Optimization

**1. Index Creation:**
```sql
-- Add indexes for frequently queried columns
CREATE INDEX CONCURRENTLY idx_research_projects_user_id 
ON research_projects(user_id);

CREATE INDEX CONCURRENTLY idx_research_projects_status 
ON research_projects(status);

CREATE INDEX CONCURRENTLY idx_research_projects_created_at 
ON research_projects(created_at DESC);

CREATE INDEX CONCURRENTLY idx_agent_tasks_project_status 
ON agent_tasks(project_id, status);

CREATE INDEX CONCURRENTLY idx_research_results_project_type 
ON research_results(project_id, result_type);

-- Composite indexes for common queries
CREATE INDEX CONCURRENTLY idx_projects_user_status_created 
ON research_projects(user_id, status, created_at DESC);
```

**2. Query Performance Analysis:**
```bash
# Enable query statistics
docker-compose exec postgres psql -U research -d research_db -c \
  "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"

# Analyze slow queries
docker-compose exec postgres psql -U research -d research_db -c \
  "SELECT query, calls, total_time, mean_time, rows 
   FROM pg_stat_statements 
   ORDER BY mean_time DESC LIMIT 10;"

# Check index usage
docker-compose exec postgres psql -U research -d research_db -c \
  "SELECT schemaname, tablename, indexname, idx_scan, idx_tup_read, idx_tup_fetch 
   FROM pg_stat_user_indexes 
   ORDER BY idx_scan DESC;"
```

**3. Query Optimization Techniques:**
```python
# Use async operations
async def get_projects_optimized(
    user_id: str, 
    status: Optional[str] = None,
    limit: int = 10,
    offset: int = 0
) -> List[ResearchProject]:
    query = select(ResearchProject).where(
        ResearchProject.user_id == user_id
    )
    
    if status:
        query = query.where(ResearchProject.status == status)
    
    # Use proper ordering and pagination
    query = query.order_by(ResearchProject.created_at.desc())
    query = query.limit(limit).offset(offset)
    
    # Use selectinload for related data
    query = query.options(
        selectinload(ResearchProject.agent_tasks),
        selectinload(ResearchProject.research_results)
    )
    
    result = await session.execute(query)
    return result.scalars().all()

# Batch operations
async def bulk_create_tasks(tasks: List[AgentTask]) -> None:
    # Use bulk insert instead of individual inserts
    await session.bulk_insert_mappings(AgentTask, [
        task.dict() for task in tasks
    ])
    await session.commit()
```

### PostgreSQL Configuration Tuning

```ini
# postgresql.conf optimizations
shared_buffers = 256MB                    # 25% of RAM
effective_cache_size = 1GB                # 75% of RAM
random_page_cost = 1.1                    # For SSD storage
effective_io_concurrency = 200            # For SSD storage
work_mem = 4MB                            # Per connection
maintenance_work_mem = 64MB               # For maintenance ops
checkpoint_completion_target = 0.9         # Spread checkpoints
wal_buffers = 16MB                        # Write-ahead log buffers
default_statistics_target = 100           # Query planner statistics
```

## Caching Strategies

### Redis Configuration

```bash
# redis.conf optimizations
maxmemory 1gb
maxmemory-policy allkeys-lru
save 900 1  # Snapshot every 15 min if 1+ keys changed
save 300 10 # Snapshot every 5 min if 10+ keys changed
save 60 10000 # Snapshot every min if 10000+ keys changed
```

### Application-Level Caching

**1. API Response Caching:**
```python
# src/services/cache/cache_manager.py
from functools import wraps
from typing import Optional, Any
import json
import hashlib

def cache_response(ttl: int = 300, key_prefix: str = "api"):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Generate cache key from function and parameters
            cache_key = f"{key_prefix}:{func.__name__}:{hash_params(args, kwargs)}"
            
            # Try to get from cache
            cached_result = await cache_manager.get(cache_key)
            if cached_result:
                return json.loads(cached_result)
            
            # Execute function and cache result
            result = await func(*args, **kwargs)
            await cache_manager.set(
                cache_key, 
                json.dumps(result, default=str), 
                ttl=ttl
            )
            return result
        return wrapper
    return decorator

# Usage
@cache_response(ttl=600, key_prefix="projects")
async def get_project_details(project_id: str) -> dict:
    # Expensive database operation
    project = await get_project_from_db(project_id)
    return project.dict()
```

**2. Database Query Caching:**
```python
# Cache expensive aggregations
@cache_response(ttl=1800, key_prefix="stats")
async def get_user_statistics(user_id: str) -> dict:
    stats = await session.execute(text("""
        SELECT 
            COUNT(*) as total_projects,
            COUNT(*) FILTER (WHERE status = 'completed') as completed_projects,
            AVG(EXTRACT(EPOCH FROM (completed_at - created_at))) as avg_duration
        FROM research_projects 
        WHERE user_id = :user_id
    """), {"user_id": user_id})
    
    return stats.fetchone()._asdict()
```

**3. Gemini Response Caching:**

Gemini is the default (and, with default flags, the only) runtime provider. An OpenRouter multi-provider layer (DeepSeek for simple tiers, Claude Sonnet for complex) exists but is gated OFF behind `MULTI_PROVIDER_ROUTING_ENABLED=True` **and** a set `OPENROUTER_API_KEY`; when enabled, apply the same caching approach per provider.

```python
# Cache Gemini API responses to reduce costs
class GeminiCacheService:
    def __init__(self, redis_client):
        self.redis = redis_client
        self.ttl = 86400  # 24 hours
    
    async def cached_generate(self, prompt: str, **kwargs) -> str:
        # Create cache key from prompt and parameters
        cache_key = self._create_cache_key(prompt, kwargs)
        
        # Check cache first
        cached_response = await self.redis.get(cache_key)
        if cached_response:
            return cached_response.decode()
        
        # Generate new response
        response = await self.gemini_client.generate(prompt, **kwargs)
        
        # Cache the response
        await self.redis.setex(cache_key, self.ttl, response)
        
        return response
    
    def _create_cache_key(self, prompt: str, kwargs: dict) -> str:
        content = f"{prompt}:{json.dumps(kwargs, sort_keys=True)}"
        return f"gemini:{hashlib.md5(content.encode()).hexdigest()}"
```

### Cache Warming Strategies

There is no task-queue worker in the stack. Warm the cache with an in-process
asyncio background task started from the FastAPI lifespan (the same in-process
model `DirectExecutionService` uses for query execution):

```python
# Background cache warming — in-process asyncio, no external task queue
import asyncio
import contextlib

async def warm_project_cache() -> None:
    """Warm cache for frequently accessed projects."""
    active_projects = await get_active_projects(limit=100)

    for project in active_projects:
        # Pre-load project details, statistics, and related data
        await get_project_details(project.id)
        await get_project_statistics(project.id)
        await get_project_results(project.id)

async def cache_warming_loop(interval_seconds: float = 3600.0) -> None:
    """Re-warm the cache on a fixed interval until cancelled."""
    while True:
        try:
            await warm_project_cache()
        except Exception:  # never let a warming failure kill the loop
            logger.exception("cache warming failed")
        await asyncio.sleep(interval_seconds)

# Start/stop the loop with the app lifespan (src/api/main.py)
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    warm_task = asyncio.create_task(cache_warming_loop())
    try:
        yield
    finally:
        warm_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await warm_task

# For a one-off warm-up tied to a request, use FastAPI BackgroundTasks:
#   background_tasks.add_task(warm_project_cache)
```

## API Performance

### FastAPI Optimization

**1. Async Everywhere:**
```python
# Use async for all I/O operations
@router.post("/projects", response_model=ProjectResponse)
async def create_project(
    project: ProjectCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session)
) -> ProjectResponse:
    # Async database operations
    db_project = await create_project_async(session, project)
    
    # Background task for workflow start
    background_tasks.add_task(start_workflow_async, db_project.id)
    
    return ProjectResponse.from_orm(db_project)
```

**2. Response Compression:**
```python
# Enable compression middleware
from fastapi.middleware.gzip import GZipMiddleware

app.add_middleware(GZipMiddleware, minimum_size=1000)
```

**3. Connection Pooling:**
```python
# HTTP client with connection pooling
import httpx

class OptimizedHTTPClient:
    def __init__(self):
        self.client = httpx.AsyncClient(
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=100,
                keepalive_expiry=30
            ),
            timeout=httpx.Timeout(30.0)
        )
    
    async def __aenter__(self):
        return self.client
    
    async def __aexit__(self, *args):
        await self.client.aclose()
```

### Request/Response Optimization

**1. Pagination:**
```python
from pydantic import BaseModel, validator
from typing import List, Optional

class PaginationParams(BaseModel):
    page: int = 1
    size: int = 10
    
    @validator('size')
    def validate_size(cls, v):
        if v > 100:
            raise ValueError('Page size cannot exceed 100')
        return v

class PaginatedResponse(BaseModel):
    items: List[Any]
    total: int
    page: int
    size: int
    pages: int
    
    @property
    def has_next(self) -> bool:
        return self.page < self.pages
    
    @property
    def has_prev(self) -> bool:
        return self.page > 1
```

**2. Field Selection:**
```python
# Allow clients to specify which fields to return
from typing import Set
from pydantic import Field

class ProjectResponse(BaseModel):
    id: str
    title: str
    status: str
    created_at: datetime
    
    # Optional fields
    description: Optional[str] = None
    results: Optional[List[dict]] = None
    
    class Config:
        fields = {
            'results': {'exclude': True}  # Exclude by default
        }

@router.get("/projects/{project_id}")
async def get_project(
    project_id: str,
    fields: Optional[str] = Query(None, description="Comma-separated fields")
) -> ProjectResponse:
    project = await get_project_from_db(project_id)
    
    if fields:
        # Return only requested fields
        field_set = set(fields.split(','))
        return ProjectResponse(**{
            k: v for k, v in project.dict().items() 
            if k in field_set
        })
    
    return ProjectResponse.from_orm(project)
```

## Agent Optimization

### Parallel Agent Execution

Parallel execution is now handled through hierarchical supervisors coordinating worker agents:

```python
# Supervisors coordinate parallel worker execution
import asyncio
from typing import List, Dict, Any

# Worker coordination within supervisors
class SupervisorWorkflow:
    async def parallel_worker_execution(self, workers: List[WorkerDefinition]):
        """Execute multiple workers in parallel through LangGraph."""
        tasks = [worker.execute() for worker in workers]
        return await asyncio.gather(*tasks)
    
    async def execute_agents_parallel(
        self, 
        agents: List[BaseAgent], 
        input_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute multiple agents in parallel where possible"""
        
        # Group agents by dependencies
        independent_agents = [
            agent for agent in agents 
            if not agent.dependencies
        ]
        dependent_agents = [
            agent for agent in agents 
            if agent.dependencies
        ]
        
        results = {}
        
        # Execute independent agents in parallel
        if independent_agents:
            tasks = [
                self._execute_agent_async(agent, input_data)
                for agent in independent_agents
            ]
            
            parallel_results = await asyncio.gather(*tasks)
            results.update(dict(zip(
                [agent.name for agent in independent_agents],
                parallel_results
            )))
        
        # Execute dependent agents sequentially
        for agent in dependent_agents:
            # Merge previous results as input
            enhanced_input = {**input_data, **results}
            agent_result = await self._execute_agent_async(agent, enhanced_input)
            results[agent.name] = agent_result
        
        return results
    
    async def _execute_agent_async(self, agent: BaseAgent, input_data: Dict[str, Any]) -> Any:
        """Execute single agent asynchronously"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor, 
            agent.execute, 
            input_data
        )
```

### Agent Resource Management

```python
# Resource-aware agent execution
class ResourceManager:
    def __init__(self):
        self.cpu_limit = 0.8  # 80% CPU
        self.memory_limit = 0.8  # 80% Memory
        self.active_agents = {}
    
    async def can_execute_agent(self, agent_type: str) -> bool:
        """Check if system has resources for agent execution"""
        cpu_usage = await self.get_cpu_usage()
        memory_usage = await self.get_memory_usage()
        
        if cpu_usage > self.cpu_limit or memory_usage > self.memory_limit:
            return False
        
        # Check agent-specific limits
        max_concurrent = self.get_agent_concurrent_limit(agent_type)
        current_count = len([
            a for a in self.active_agents.values() 
            if a['type'] == agent_type
        ])
        
        return current_count < max_concurrent
    
    def get_agent_concurrent_limit(self, agent_type: str) -> int:
        """Get maximum concurrent executions for agent type"""
        limits = {
            'literature_review': 3,
            'comparative_analysis': 2,
            'methodology': 2,
            'synthesis': 1,
            'citation': 4
        }
        return limits.get(agent_type, 1)
```

### Gemini API Optimization

```python
# Optimize Gemini API usage
class OptimizedGeminiService:
    def __init__(self):
        self.rate_limiter = AsyncLimiter(60, 60)  # 60 requests per minute
        self.batch_size = 5  # Batch multiple requests
        self.request_queue = asyncio.Queue()
        self.response_cache = {}
    
    async def generate_batch(self, prompts: List[str]) -> List[str]:
        """Batch multiple prompts for efficiency"""
        
        # Check cache first
        cached_results = []
        uncached_prompts = []
        
        for prompt in prompts:
            cache_key = hashlib.md5(prompt.encode()).hexdigest()
            if cache_key in self.response_cache:
                cached_results.append(self.response_cache[cache_key])
            else:
                uncached_prompts.append(prompt)
        
        # Generate responses for uncached prompts
        if uncached_prompts:
            async with self.rate_limiter:
                # Combine prompts intelligently
                combined_prompt = self._combine_prompts(uncached_prompts)
                response = await self.client.generate(combined_prompt)
                individual_responses = self._split_response(response, len(uncached_prompts))
                
                # Cache responses
                for prompt, response in zip(uncached_prompts, individual_responses):
                    cache_key = hashlib.md5(prompt.encode()).hexdigest()
                    self.response_cache[cache_key] = response
                
                cached_results.extend(individual_responses)
        
        return cached_results
    
    def _combine_prompts(self, prompts: List[str]) -> str:
        """Intelligently combine multiple prompts"""
        combined = "Please respond to each of the following prompts separately:\n\n"
        for i, prompt in enumerate(prompts, 1):
            combined += f"Prompt {i}: {prompt}\n\n"
        combined += "Please format your response as 'Response {i}: [your response]'"
        return combined
```

## Workflow Performance

### In-Process Execution Optimization

Cerebro has **no external workflow engine** — Temporal was removed and replaced by
`DirectExecutionService` (`src/api/services/direct_execution_service.py`), an
in-process asyncio engine. A query request returns immediately after the service
spawns an asyncio background task (`_execute_research_workflow`); MASR routing,
supervisor coordination, and worker execution all run inside that task. Tune the
in-process engine, not a task queue.

**1. Cap concurrent executions:**
```python
# src/api/services/direct_execution_service.py
# The service refuses new work past max_concurrent_executions (raises RuntimeError
# at capacity) so the event loop is not oversubscribed. Size it to the LLM
# provider's rate limit and available memory rather than raising it blindly.
if len(self.active_executions) >= self.max_concurrent_executions:
    raise RuntimeError("Execution capacity reached")
```

**2. Bound domain parallelism with a semaphore:**
```python
# Multi-domain queries fan out per-domain subqueries concurrently, but are bounded
# by asyncio.Semaphore(self.max_domain_parallelism) (default 4) and gathered with
# return_exceptions=True so one failed domain does not abort the others.
semaphore = asyncio.Semaphore(self.max_domain_parallelism)

async def run_domain(subquery):
    async with semaphore:
        return await self._execute_domain_supervisor(subquery, context)

domain_results = await asyncio.gather(
    *(run_domain(sq) for sq in subqueries),
    return_exceptions=True,  # partial success: keep succeeding domains
)
```
Raising `max_domain_parallelism` increases throughput for wide multi-domain
queries but multiplies concurrent LLM calls — keep it under the provider quota.

**3. Retry with tenacity / `src/reliability`, not workflow retry policies:**
```python
# Transient failures are handled in-process. Simple cases use tenacity decorators;
# richer policies live in src/reliability/retry_strategies.py
# (RetryPolicy, ExponentialBackoff, CircuitBreaker, with_retry, with_circuit_breaker).
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
)
async def call_provider(request):
    return await provider.generate(request)
```
Wrap flaky downstream calls with `with_circuit_breaker` from
`src/reliability/retry_strategies.py` to shed load fast when a provider is
degraded instead of retrying into a rate-limit wall.

**Caution — retry scope:** decorate the narrowest failing call (a single provider
request), never the whole background workflow. A historical bug placed
`@retry(stop_after_attempt(3))` on `_execute_research_workflow` and a
naive/aware datetime subtraction in its `finally` block tripped the retry,
re-running the entire pipeline three times (fixed with `datetime.now(UTC)`).

### Parallel Worker Optimization

```python
# Inside a supervisor, run independent workers concurrently and tolerate partial
# failure. This is plain asyncio — no workflow-engine activities.
async def parallel_literature_search(query: str, domains: list[str]) -> dict:
    """Optimized literature search with parallel sources."""

    search_tasks = [
        search_google_scholar(query, domains),
        search_pubmed(query, domains),
        search_arxiv(query, domains),
    ]

    # Execute searches in parallel; don't fail if one source fails
    results = await asyncio.gather(*search_tasks, return_exceptions=True)

    valid_results = [r for r in results if not isinstance(r, Exception)]

    combined_results = deduplicate_papers(valid_results)
    ranked_results = rank_papers_by_relevance(combined_results, query)

    return {
        "papers": ranked_results[:50],  # Limit to top 50
        "search_stats": {
            "total_sources": len(valid_results),
            "total_papers": len(combined_results),
            "top_papers": len(ranked_results),
        },
    }
```

## Resource Management

### Memory Optimization

```python
# Memory-efficient data processing
import gc
from typing import Iterator, List

class MemoryEfficientProcessor:
    def __init__(self, batch_size: int = 1000):
        self.batch_size = batch_size
    
    def process_large_dataset(self, data_source: Iterator) -> Iterator:
        """Process large datasets in batches to control memory usage"""
        batch = []
        
        for item in data_source:
            batch.append(item)
            
            if len(batch) >= self.batch_size:
                # Process batch
                yield from self.process_batch(batch)
                
                # Clear batch and force garbage collection
                batch.clear()
                gc.collect()
        
        # Process remaining items
        if batch:
            yield from self.process_batch(batch)
    
    def process_batch(self, batch: List) -> Iterator:
        """Process a single batch of items"""
        for item in batch:
            yield self.process_item(item)
            
    def process_item(self, item) -> dict:
        """Process single item efficiently"""
        # Minimize object creation
        result = {
            'id': item.get('id'),
            'processed_data': self.transform_data(item.get('data'))
        }
        return result
```

### CPU Optimization

```python
# CPU-intensive operations optimization
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor
import numpy as np

class CPUOptimizedProcessor:
    def __init__(self):
        self.cpu_count = mp.cpu_count()
        self.process_pool = ProcessPoolExecutor(max_workers=self.cpu_count)
    
    async def parallel_analysis(self, data_chunks: List) -> List:
        """Distribute CPU-intensive work across processes"""
        
        # Divide work into chunks
        chunk_size = len(data_chunks) // self.cpu_count
        work_chunks = [
            data_chunks[i:i + chunk_size] 
            for i in range(0, len(data_chunks), chunk_size)
        ]
        
        # Submit work to process pool
        loop = asyncio.get_event_loop()
        tasks = [
            loop.run_in_executor(
                self.process_pool,
                self.analyze_chunk,
                chunk
            )
            for chunk in work_chunks
        ]
        
        # Wait for all tasks to complete
        results = await asyncio.gather(*tasks)
        
        # Combine results
        return [item for sublist in results for item in sublist]
    
    @staticmethod
    def analyze_chunk(chunk: List) -> List:
        """CPU-intensive analysis of data chunk"""
        # Use NumPy for vectorized operations
        data_array = np.array([item['values'] for item in chunk])
        
        # Vectorized operations are much faster
        means = np.mean(data_array, axis=1)
        stds = np.std(data_array, axis=1)
        
        # Return processed results
        return [
            {
                'id': chunk[i]['id'],
                'mean': means[i],
                'std': stds[i]
            }
            for i in range(len(chunk))
        ]
```

## Monitoring and Profiling

### Performance Monitoring

```python
# Real-time performance monitoring
import time
import psutil
from prometheus_client import Counter, Histogram, Gauge
from functools import wraps

# Metrics
REQUEST_COUNT = Counter('http_requests_total', 'Total HTTP requests', ['method', 'endpoint'])
REQUEST_DURATION = Histogram('http_request_duration_seconds', 'HTTP request duration')
MEMORY_USAGE = Gauge('memory_usage_bytes', 'Memory usage in bytes')
CPU_USAGE = Gauge('cpu_usage_percent', 'CPU usage percentage')

def monitor_performance(func):
    """Decorator to monitor function performance"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        start_time = time.time()
        
        try:
            result = await func(*args, **kwargs)
            REQUEST_COUNT.labels(method='POST', endpoint=func.__name__).inc()
            return result
        except Exception as e:
            REQUEST_COUNT.labels(method='POST', endpoint=f"{func.__name__}_error").inc()
            raise
        finally:
            duration = time.time() - start_time
            REQUEST_DURATION.observe(duration)
    
    return wrapper

# System metrics collection
async def collect_system_metrics():
    """Collect and export system metrics"""
    while True:
        # Memory usage
        memory = psutil.virtual_memory()
        MEMORY_USAGE.set(memory.used)
        
        # CPU usage
        cpu_percent = psutil.cpu_percent(interval=1)
        CPU_USAGE.set(cpu_percent)
        
        await asyncio.sleep(10)  # Collect every 10 seconds
```

### Application Profiling

```python
# Profile application performance
import cProfile
import pstats
from line_profiler import LineProfiler

class PerformanceProfiler:
    def __init__(self):
        self.profiler = cProfile.Profile()
        self.line_profiler = LineProfiler()
    
    def profile_function(self, func):
        """Profile a specific function"""
        def wrapper(*args, **kwargs):
            self.profiler.enable()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                self.profiler.disable()
        
        return wrapper
    
    def save_profile(self, filename: str):
        """Save profiling results"""
        stats = pstats.Stats(self.profiler)
        stats.sort_stats('cumulative')
        stats.dump_stats(filename)
    
    def print_top_functions(self, limit: int = 20):
        """Print top time-consuming functions"""
        stats = pstats.Stats(self.profiler)
        stats.sort_stats('cumulative')
        stats.print_stats(limit)

# Usage
profiler = PerformanceProfiler()

@profiler.profile_function
async def expensive_operation():
    # Your expensive operation here
    pass
```

### Query Analysis

```bash
# PostgreSQL query analysis
cat > analyze_queries.sql << EOF
-- Enable query logging
ALTER SYSTEM SET log_statement = 'all';
ALTER SYSTEM SET log_min_duration_statement = 1000; -- Log queries > 1s

-- Analyze query performance
SELECT 
    query,
    calls,
    total_time,
    mean_time,
    min_time,
    max_time,
    stddev_time,
    rows,
    100.0 * shared_blks_hit / nullif(shared_blks_hit + shared_blks_read, 0) AS hit_percent
FROM pg_stat_statements 
ORDER BY total_time DESC 
LIMIT 20;

-- Check index usage
SELECT 
    schemaname,
    tablename,
    indexname,
    idx_scan,
    idx_tup_read,
    idx_tup_fetch
FROM pg_stat_user_indexes 
WHERE idx_scan > 0
ORDER BY idx_scan DESC;

-- Check table statistics
SELECT 
    schemaname,
    tablename,
    n_tup_ins,
    n_tup_upd,
    n_tup_del,
    n_live_tup,
    n_dead_tup,
    last_vacuum,
    last_autovacuum,
    last_analyze,
    last_autoanalyze
FROM pg_stat_user_tables
ORDER BY n_live_tup DESC;
EOF

docker-compose exec postgres psql -U research -d research_db -f analyze_queries.sql
```

## Scaling Strategies

### Horizontal Scaling

```yaml
# docker-compose.yml - Multiple API instances
version: '3.8'

services:
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
    depends_on:
      - api1
      - api2
      - api3

  api1:
    build: .
    environment:
      - INSTANCE_ID=api1
    depends_on:
      - postgres
      - redis

  api2:
    build: .
    environment:
      - INSTANCE_ID=api2
    depends_on:
      - postgres
      - redis

  api3:
    build: .
    environment:
      - INSTANCE_ID=api3
    depends_on:
      - postgres
      - redis
```

```nginx
# nginx.conf - Load balancing
upstream api_servers {
    server api1:8000 weight=1;
    server api2:8000 weight=1;
    server api3:8000 weight=1;
}

server {
    listen 80;
    
    location / {
        proxy_pass http://api_servers;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        
        # Connection pooling
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        
        # Timeouts
        proxy_connect_timeout 30s;
        proxy_send_timeout 30s;
        proxy_read_timeout 30s;
    }
}
```

### Kubernetes Scaling

```yaml
# k8s/hpa.yaml - Horizontal Pod Autoscaler
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: research-api-hpa
  namespace: research-platform
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: research-api
  minReplicas: 3
  maxReplicas: 20
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: 80
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 60
      policies:
      - type: Percent
        value: 100
        periodSeconds: 15
    scaleDown:
      stabilizationWindowSeconds: 300
      policies:
      - type: Percent
        value: 10
        periodSeconds: 60
```

## Benchmarking

### Load Testing

```python
# load_test.py - Locust load testing
from locust import HttpUser, task, between
import random
import uuid

class ResearchPlatformUser(HttpUser):
    wait_time = between(1, 3)
    
    def on_start(self):
        """Setup user session"""
        self.api_key = "your-test-api-key"
        self.headers = {"Authorization": f"Bearer {self.api_key}"}
    
    @task(3)
    def get_projects(self):
        """Get user projects - most common operation"""
        self.client.get("/api/v1/research/projects", headers=self.headers)
    
    @task(2)
    def get_project_details(self):
        """Get specific project details"""
        # Use a known project ID for testing
        project_id = "test-project-id"
        self.client.get(f"/api/v1/research/projects/{project_id}", headers=self.headers)
    
    @task(1)
    def create_project(self):
        """Create new project - less frequent but important"""
        project_data = {
            "title": f"Load Test Project {uuid.uuid4()}",
            "query": {
                "text": "Test query for load testing",
                "domains": ["AI", "Testing"],
                "depth_level": "survey"
            },
            "user_id": f"test-user-{random.randint(1, 100)}"
        }
        
        self.client.post(
            "/api/v1/research/projects",
            json=project_data,
            headers=self.headers
        )
    
    @task(1)
    def health_check(self):
        """Health check endpoint"""
        self.client.get("/health")
```

### Performance Testing Script

```bash
#!/bin/bash
# performance_test.sh

echo "Starting Performance Tests..."

# API Load Test
echo "1. API Load Test"
locust -f load_test.py --host=http://localhost:8000 \
       --users=50 --spawn-rate=5 --run-time=300s --headless

# Database Performance Test
echo "2. Database Performance Test"
docker-compose exec postgres pgbench -U research -d research_db \
       -c 10 -j 2 -T 60 -r

# Redis Performance Test
echo "3. Redis Performance Test"
docker-compose exec redis redis-benchmark -t set,get -n 100000 -q

# Memory Stress Test
echo "4. Memory Stress Test"
docker run --rm -it progrium/stress \
       --vm 2 --vm-bytes 1G --vm-hang 60 --timeout 60s

# Generate Performance Report
echo "5. Generating Performance Report"
python3 scripts/generate_performance_report.py
```

### Performance Metrics Collection

```python
# scripts/generate_performance_report.py
import json
import time
import requests
import psutil
from datetime import datetime, timedelta

class PerformanceReporter:
    def __init__(self):
        self.api_url = "http://localhost:8000"
        self.metrics = {
            "timestamp": datetime.now().isoformat(),
            "api_performance": {},
            "database_performance": {},
            "system_performance": {}
        }
    
    def test_api_performance(self):
        """Test API response times"""
        endpoints = [
            "/health",
            "/api/v1/research/projects",
            "/metrics"
        ]
        
        for endpoint in endpoints:
            times = []
            for _ in range(10):
                start = time.time()
                try:
                    response = requests.get(f"{self.api_url}{endpoint}", timeout=30)
                    end = time.time()
                    if response.status_code == 200:
                        times.append(end - start)
                except:
                    pass
            
            if times:
                self.metrics["api_performance"][endpoint] = {
                    "avg_response_time": sum(times) / len(times),
                    "min_response_time": min(times),
                    "max_response_time": max(times),
                    "success_rate": len(times) / 10
                }
    
    def collect_system_metrics(self):
        """Collect system performance metrics"""
        self.metrics["system_performance"] = {
            "cpu_percent": psutil.cpu_percent(interval=1),
            "memory_percent": psutil.virtual_memory().percent,
            "disk_usage": psutil.disk_usage('/').percent,
            "load_average": psutil.getloadavg(),
            "network_io": psutil.net_io_counters()._asdict()
        }
    
    def generate_report(self):
        """Generate comprehensive performance report"""
        self.test_api_performance()
        self.collect_system_metrics()
        
        # Save metrics
        with open(f"performance_report_{int(time.time())}.json", "w") as f:
            json.dump(self.metrics, f, indent=2, default=str)
        
        # Print summary
        print("\n" + "="*50)
        print("PERFORMANCE REPORT SUMMARY")
        print("="*50)
        
        for endpoint, metrics in self.metrics["api_performance"].items():
            print(f"{endpoint}:")
            print(f"  Avg Response Time: {metrics['avg_response_time']:.3f}s")
            print(f"  Success Rate: {metrics['success_rate']:.1%}")
        
        print(f"\nSystem:")
        print(f"  CPU Usage: {self.metrics['system_performance']['cpu_percent']:.1f}%")
        print(f"  Memory Usage: {self.metrics['system_performance']['memory_percent']:.1f}%")
        print(f"  Disk Usage: {self.metrics['system_performance']['disk_usage']:.1f}%")

if __name__ == "__main__":
    reporter = PerformanceReporter()
    reporter.generate_report()
```

This comprehensive performance tuning guide provides strategies for optimizing every layer of Cerebro, from database queries to API responses to agent execution. Regular monitoring and benchmarking ensure that performance improvements are effective and sustainable.