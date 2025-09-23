# Database and Data Persistence Layer Implementation Plan

## Overview
Implement a comprehensive data persistence layer using SQLAlchemy with async support, Alembic for migrations, and the repository pattern for clean data access.

## Current State Analysis

### What We Have:
- Basic SQLAlchemy configuration in `src/core/config.py`
- Database connection pooling in `src/reliability/connection_pools.py`
- Core domain models in `src/models/research.py`
- Async database session management infrastructure

### What We Need:
- Database models (SQLAlchemy ORM)
- Migration system (Alembic)
- Repository pattern implementation
- Transaction management
- Database indexes and constraints
- Backup and recovery procedures

## Implementation Plan

## Phase 1: Database Models (Day 1 Morning)

### 1.1 Create Base Model Configuration
**File**: `src/models/db/base.py`
```python
- Base declarative class with common fields
- UUID primary keys
- Timestamps (created_at, updated_at)
- Soft delete support (deleted_at)
- Audit fields (created_by, updated_by)
```

### 1.2 Research Project Model
**File**: `src/models/db/research_project.py`
```python
class ResearchProject:
    - id: UUID
    - title: String
    - query: Text
    - domains: JSON (List[str])
    - status: Enum (draft, in_progress, completed, failed)
    - quality_score: Float
    - user_id: UUID (FK)
    - workflow_id: String (optional)
    - metadata: JSON
    - created_at, updated_at, deleted_at
```

### 1.3 Agent Task Model
**File**: `src/models/db/agent_task.py`
```python
class AgentTask:
    - id: UUID
    - project_id: UUID (FK)
    - agent_type: String
    - status: Enum
    - input_data: JSON
    - output_data: JSON (nullable)
    - error_message: Text (nullable)
    - retry_count: Integer
    - started_at, completed_at
    - execution_time_ms: Integer
```

### 1.4 Research Result Model
**File**: `src/models/db/research_result.py`
```python
class ResearchResult:
    - id: UUID
    - project_id: UUID (FK)
    - result_type: String (finding, source, citation, etc.)
    - content: JSON
    - confidence_score: Float
    - agent_type: String (which agent produced it)
    - metadata: JSON
    - created_at
```

### 1.5 User Model
**File**: `src/models/db/user.py`
```python
class User:
    - id: UUID
    - email: String (unique)
    - username: String (unique)
    - hashed_password: String
    - full_name: String (nullable)
    - is_active: Boolean
    - is_superuser: Boolean
    - last_login: DateTime
    - created_at, updated_at
```

### 1.6 Workflow Checkpoint Model
**File**: `src/models/db/workflow_checkpoint.py`
```python
class WorkflowCheckpoint:
    - id: UUID
    - workflow_id: String
    - project_id: UUID (FK)
    - checkpoint_data: JSON
    - phase: String
    - created_at
```

### 1.7 API Key Model (for service accounts)
**File**: `src/models/db/api_key.py`
```python
class APIKey:
    - id: UUID
    - key_hash: String (unique)
    - user_id: UUID (FK)
    - name: String
    - permissions: JSON
    - last_used_at: DateTime
    - expires_at: DateTime (nullable)
    - is_active: Boolean
```

## Phase 2: Alembic Migrations (Day 1 Afternoon)

### 2.1 Initialize Alembic
```bash
alembic init alembic
```

### 2.2 Configure Alembic
**File**: `alembic.ini`
- Set database URL from environment
- Configure naming conventions
- Set up async support

### 2.3 Initial Migration
**File**: `alembic/versions/001_initial_schema.py`
- Create all tables
- Add indexes:
  - project_id + status (for filtering)
  - user_id + created_at (for user queries)
  - workflow_id (for checkpoint queries)
- Add constraints:
  - Foreign keys with CASCADE options
  - Check constraints for enums
  - Unique constraints

### 2.4 Migration Utilities
**File**: `src/models/db/migrations.py`
```python
- run_migrations()
- rollback_migration()
- get_current_revision()
- check_migration_status()
```

## Phase 3: Repository Pattern (Day 2 Morning)

### 3.1 Base Repository
**File**: `src/repositories/base.py`
```python
class BaseRepository:
    - __init__(session: AsyncSession)
    - create(entity)
    - get(id)
    - get_many(filters, limit, offset)
    - update(id, data)
    - delete(id) # soft delete
    - hard_delete(id)
    - exists(id)
    - count(filters)
```

### 3.2 Research Repository
**File**: `src/repositories/research_repository.py`
```python
class ResearchRepository(BaseRepository):
    - get_by_user(user_id, status=None)
    - get_in_progress()
    - update_status(project_id, status)
    - update_quality_score(project_id, score)
    - get_with_results(project_id)
    - search(query, domains, user_id)
```

### 3.3 Task Repository
**File**: `src/repositories/task_repository.py`
```python
class TaskRepository(BaseRepository):
    - get_by_project(project_id)
    - get_pending_tasks()
    - get_failed_tasks(max_retries)
    - update_task_status(task_id, status, output=None, error=None)
    - get_task_metrics(project_id)
```

### 3.4 Result Repository
**File**: `src/repositories/result_repository.py`
```python
class ResultRepository(BaseRepository):
    - get_by_project(project_id, result_type=None)
    - get_by_agent(project_id, agent_type)
    - bulk_create(results)
    - get_high_confidence(project_id, min_confidence)
    - aggregate_by_type(project_id)
```

### 3.5 User Repository
**File**: `src/repositories/user_repository.py`
```python
class UserRepository(BaseRepository):
    - get_by_email(email)
    - get_by_username(username)
    - update_last_login(user_id)
    - get_active_users()
    - create_with_password(user_data, password)
```

### 3.6 Checkpoint Repository
**File**: `src/repositories/checkpoint_repository.py`
```python
class CheckpointRepository(BaseRepository):
    - get_latest(workflow_id)
    - get_by_phase(workflow_id, phase)
    - cleanup_old(workflow_id, keep_count)
    - get_recovery_point(project_id)
```

## Phase 4: Advanced Features (Day 2 Afternoon)

### 4.1 Transaction Management
**File**: `src/repositories/transaction.py`
```python
class TransactionManager:
    - begin()
    - commit()
    - rollback()
    - savepoint()
    
@contextmanager
async def transaction(session):
    # Transaction context manager
```

### 4.2 Database Connection Management
**File**: `src/models/db/session.py`
```python
- get_session() - Get database session
- get_transaction() - Get transaction context
- init_db() - Initialize database
- close_db() - Close all connections
```

### 4.3 Query Optimization
**File**: `src/repositories/query_optimizer.py`
```python
class QueryOptimizer:
    - add_eager_loading(query, relationships)
    - add_pagination(query, page, size)
    - add_sorting(query, sort_by, order)
    - add_filtering(query, filters)
    - optimize_n_plus_one(query)
```

### 4.4 Database Health Checks
**File**: `src/models/db/health.py`
```python
- check_connection()
- check_migration_status()
- get_table_sizes()
- get_slow_queries()
- analyze_indexes()
```

### 4.5 Backup and Recovery
**File**: `src/models/db/backup.py`
```python
class BackupManager:
    - create_backup()
    - restore_backup(backup_id)
    - list_backups()
    - cleanup_old_backups()
    - verify_backup(backup_id)
```

## Phase 5: Integration and Testing (Day 3)

### 5.1 API Integration
- Update FastAPI endpoints to use repositories
- Add dependency injection for repositories
- Update existing services to use new data layer

### 5.2 Test Fixtures
**File**: `tests/fixtures/database.py`
```python
- test_db fixture
- test_session fixture
- sample data factories
- cleanup utilities
```

### 5.3 Repository Tests
**Files**: `tests/test_repositories/test_*.py`
- Test all CRUD operations
- Test transactions
- Test query optimization
- Test concurrent access

### 5.4 Migration Tests
**File**: `tests/test_migrations.py`
- Test migration up/down
- Test data integrity
- Test rollback scenarios

### 5.5 Performance Tests
**File**: `tests/test_db_performance.py`
- Benchmark query performance
- Test connection pooling
- Test bulk operations
- Load testing

## Database Schema Diagram

```
┌─────────────────┐     ┌─────────────────┐
│      User       │     │    APIKey       │
├─────────────────┤     ├─────────────────┤
│ id (PK)         │←────│ user_id (FK)    │
│ email           │     │ key_hash        │
│ username        │     │ permissions     │
│ hashed_password │     └─────────────────┘
└─────────────────┘
         │
         │ 1:N
         ↓
┌─────────────────┐     ┌─────────────────┐
│ ResearchProject │     │ WorkflowCheckpt │
├─────────────────┤     ├─────────────────┤
│ id (PK)         │←────│ project_id (FK) │
│ user_id (FK)    │     │ workflow_id     │
│ title           │     │ checkpoint_data │
│ query           │     └─────────────────┘
│ status          │
│ quality_score   │
└─────────────────┘
         │
         │ 1:N
    ┌────┴────┐
    ↓         ↓
┌─────────────────┐     ┌─────────────────┐
│   AgentTask     │     │ ResearchResult  │
├─────────────────┤     ├─────────────────┤
│ id (PK)         │     │ id (PK)         │
│ project_id (FK) │     │ project_id (FK) │
│ agent_type      │     │ result_type     │
│ status          │     │ content         │
│ input_data      │     │ confidence      │
│ output_data     │     └─────────────────┘
└─────────────────┘
```

## Configuration Updates

### Environment Variables
```env
DATABASE_URL=postgresql+asyncpg://user:pass@localhost/db
DATABASE_POOL_SIZE=20
DATABASE_MAX_OVERFLOW=10
DATABASE_POOL_TIMEOUT=30
DATABASE_ECHO=false
DATABASE_ECHO_POOL=false
ALEMBIC_CONFIG=alembic.ini
```

### Database Indexes
1. `idx_project_user_status` - (user_id, status, created_at DESC)
2. `idx_task_project_status` - (project_id, status)
3. `idx_result_project_type` - (project_id, result_type)
4. `idx_checkpoint_workflow` - (workflow_id, created_at DESC)
5. `idx_user_email` - (email) UNIQUE
6. `idx_apikey_hash` - (key_hash) UNIQUE

## Success Criteria

### Functionality
- ✅ All models created with proper relationships
- ✅ Migrations run successfully
- ✅ Repositories provide clean data access
- ✅ Transactions work correctly
- ✅ Connection pooling optimized

### Performance
- ✅ Queries execute in < 100ms
- ✅ Bulk operations optimized
- ✅ N+1 queries eliminated
- ✅ Proper indexing in place

### Reliability
- ✅ Automatic reconnection on connection loss
- ✅ Transaction rollback on errors
- ✅ Data integrity maintained
- ✅ Backup/recovery procedures work

### Testing
- ✅ >80% test coverage
- ✅ All repositories tested
- ✅ Migration tests pass
- ✅ Performance benchmarks met

## Risk Mitigation

### Risks:
1. **Migration failures** - Test migrations thoroughly
2. **Performance issues** - Add indexes, optimize queries
3. **Connection pool exhaustion** - Monitor and tune pool size
4. **Data integrity issues** - Use transactions, add constraints

### Mitigation Strategies:
1. Test migrations on copy of production data
2. Profile queries before deployment
3. Monitor connection pool metrics
4. Implement comprehensive error handling

## Timeline

### Day 1:
- Morning: Create all database models
- Afternoon: Set up Alembic and create initial migration

### Day 2:
- Morning: Implement repository pattern
- Afternoon: Add advanced features (transactions, optimization)

### Day 3:
- Morning: Integration with existing code
- Afternoon: Testing and documentation

Total Estimated Time: 3 days

## Next Steps After Completion

1. Update API endpoints to use repositories
2. Migrate existing data if any
3. Set up database monitoring
4. Create backup schedule
5. Document database schema