# Repository Pattern Implementation Guide

## Overview

This guide documents the repository pattern implementation in Cerebro, providing examples and best practices for data access operations.

## Repository Pattern Benefits

1. **Separation of Concerns**: Business logic separated from data access
2. **Testability**: Easy to mock repositories for unit testing
3. **Consistency**: Uniform interface across all data entities
4. **Type Safety**: Generic types ensure compile-time safety
5. **Query Optimization**: Centralized location for query optimization

## Base Repository

### Core Interface

```python
from typing import Generic, TypeVar, Optional, List, Dict, Any
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession

ModelType = TypeVar("ModelType")

class BaseRepository(Generic[ModelType]):
    def __init__(self, model: Type[ModelType], session: AsyncSession):
        self.model = model
        self.session = session
```

### Standard CRUD Operations

#### Create
```python
async def create_research_project():
    async with get_transaction() as session:
        repo = ResearchRepository(session)
        
        project = await repo.create(
            title="AI Impact on Employment",
            query="How will AI affect job markets?",
            domains=["AI", "Economics"],
            user_id=user_id
        )
        
        # get_transaction() commits automatically on context exit
        return project
```

#### Read
```python
async def get_project_details(project_id: UUID):
    async with get_transaction() as session:
        repo = ResearchRepository(session)
        
        # Get single record
        project = await repo.get(project_id)
        
        # Get with related data
        project_with_results = await repo.get_with_results(project_id)
        
        # Get multiple with filters
        user_projects = await repo.get_by_user(
            user_id=current_user.id,
            status="in_progress",
            limit=10
        )
```

#### Update
```python
async def update_project_status(project_id: UUID, new_status: str):
    async with get_transaction() as session:
        repo = ResearchRepository(session)
        
        # Simple update
        project = await repo.update(
            project_id,
            {"status": new_status},
            updated_by=current_user.id
        )
        
        # Complex update with validation
        project = await repo.update_status(
            project_id,
            new_status,
            updated_by=current_user.id
        )
        
        # get_transaction() commits automatically on context exit
```

#### Delete
```python
async def delete_project(project_id: UUID):
    async with get_transaction() as session:
        repo = ResearchRepository(session)
        
        # Soft delete (default)
        success = await repo.delete(project_id)
        
        # Hard delete
        success = await repo.delete(project_id, soft=False)
        
        # get_transaction() commits automatically on context exit
```

## Specialized Repository Examples

### ResearchRepository

> **Note:** As of PR #10, `ResearchProject.user_id` is an opaque `String(255)` field (not a typed FK to `users.id`). The multi-tenancy refactor supports external identity providers where user records may not exist in the local `users` table.

```python
class ResearchRepository(BaseRepository[ResearchProject]):
    
    async def search_projects(
        self,
        query: str,
        domains: Optional[List[str]] = None,
        status: Optional[List[ProjectStatus]] = None
    ) -> List[ResearchProject]:
        """Full-text search across projects."""
        
        stmt = select(ResearchProject)
        
        # Text search (case-insensitive contains on title and query)
        if query:
            stmt = stmt.where(
                or_(
                    func.lower(ResearchProject.title).contains(query.lower()),
                    func.lower(ResearchProject.query).contains(query.lower())
                )
            )
        
        # Domain filter (JSON containment, one clause per domain)
        if domains:
            for domain in domains:
                stmt = stmt.where(ResearchProject.domains.contains([domain]))
        
        # Status filter
        if status:
            stmt = stmt.where(ResearchProject.status.in_(status))
        
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
    
    async def get_statistics(
        self,
        user_id: Optional[UUID] = None,
        days: int = 30,
        organization_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Aggregate project statistics across the reporting window."""
        
        since = datetime.now(UTC) - timedelta(days=days)
        
        base = select(ResearchProject).where(
            and_(
                ResearchProject.deleted_at.is_(None),
                ResearchProject.created_at >= since,
            )
        )
        if user_id:
            base = base.where(ResearchProject.user_id == user_id)
        
        # Counts by status
        status_counts = {}
        for status in ProjectStatus:
            count_query = select(func.count(ResearchProject.id)).where(
                and_(
                    ResearchProject.deleted_at.is_(None),
                    ResearchProject.status == status,
                    ResearchProject.created_at >= since,
                )
            )
            result = await self.session.execute(count_query)
            status_counts[status.value] = result.scalar() or 0
        
        # Average quality score and total count
        avg_result = await self.session.execute(
            select(func.avg(ResearchProject.quality_score)).where(
                and_(
                    ResearchProject.deleted_at.is_(None),
                    ResearchProject.quality_score.isnot(None),
                    ResearchProject.created_at >= since,
                )
            )
        )
        total_result = await self.session.execute(
            select(func.count(ResearchProject.id)).where(
                and_(
                    ResearchProject.deleted_at.is_(None),
                    ResearchProject.created_at >= since,
                )
            )
        )
        
        return {
            "total_projects": total_result.scalar() or 0,
            "status_distribution": status_counts,
            "average_quality_score": float(avg_result.scalar() or 0.0),
            "period_days": days,
            "since": since.isoformat(),
        }
```

### TaskRepository

```python
class TaskRepository(BaseRepository[AgentTask]):
    
    async def get_pending_tasks(
        self,
        limit: int = 10,
        agent_type: Optional[str] = None
    ) -> List[AgentTask]:
        """Get pending tasks ready for execution.

        Dependency gating is a separate concern handled by
        ``get_ready_tasks(project_id)`` (which uses ``task.can_start``);
        this method only selects unstarted tasks by status.
        """
        
        query = self.build_query().where(
            AgentTask.status.in_([TaskStatus.PENDING, TaskStatus.QUEUED])
        )
        
        if agent_type:
            query = query.where(AgentTask.agent_type == agent_type)
        
        query = query.order_by(
            AgentTask.priority.desc(),
            AgentTask.created_at.asc()
        ).limit(limit)
        
        result = await self.session.execute(query)
        return list(result.scalars().all())
    
    async def mark_for_retry(self, task_id: UUID) -> Optional[AgentTask]:
        """Mark a failed task for retry."""
        
        task = await self.get(task_id)
        
        if not task or task.status != TaskStatus.FAILED:
            return None
        
        # task.retry() sets status to RETRYING, increments retry_count,
        # and clears error_message/output_data/timestamps.
        task.retry()
        await self.session.flush()
        await self.session.refresh(task)
        return task
```

### ResultRepository

```python
class ResultRepository(BaseRepository[ResearchResult]):
    
    async def bulk_create(
        self,
        results: List[Dict[str, Any]]
    ) -> List[ResearchResult]:
        """Efficiently create multiple results."""
        
        # Use PostgreSQL's INSERT ... RETURNING
        stmt = insert(ResearchResult).values(results).returning(ResearchResult)
        
        result = await self.session.execute(stmt)
        created_results = list(result.scalars().all())
        
        await self.session.flush()
        return created_results
    
    async def merge_duplicates(self, project_id: UUID) -> int:
        """Merge duplicate results based on (source_id, result_type)."""
        
        results = await self.get_by_project(project_id)
        
        # Group by source_id + result_type; results with no source_id are skipped
        duplicates = defaultdict(list)
        for result in results:
            if result.source_id:
                key = (result.source_id, result.result_type)
                duplicates[key].append(result)
        
        merged_count = 0
        for _key, group in duplicates.items():
            if len(group) > 1:
                # Keep highest confidence result
                group.sort(key=lambda x: x.confidence_score or 0, reverse=True)
                keep = group[0]
                
                # Record provenance and soft-delete each duplicate
                for duplicate in group[1:]:
                    if duplicate.result_metadata:
                        keep.add_metadata("merged_from", str(duplicate.id))
                    
                    await self.delete(duplicate.id, soft=True)
                    merged_count += 1
        
        if merged_count > 0:
            await self.session.flush()
        
        return merged_count
```

### APIKeyRepository

```python
class APIKeyRepository(BaseRepository[APIKey]):
    
    async def create_key(
        self,
        user_id: UUID,
        name: str,
        permissions: List[str],
        expires_in_days: Optional[int] = None,
        description: Optional[str] = None,
        rate_limit: Optional[int] = None,
        allowed_ips: Optional[List[str]] = None
    ) -> Tuple[APIKey, str]:
        """Create API key and return it with raw key (shown once)."""
        
        # Generate secure key and its hash (raw_key, key_hash)
        raw_key, key_hash = generate_api_key()
        
        # Calculate expiration
        expires_at = None
        if expires_in_days:
            expires_at = datetime.now(UTC) + timedelta(days=expires_in_days)
        
        # Create key record
        api_key = await self.create(
            key_hash=key_hash,
            user_id=user_id,
            name=name,
            description=description,
            permissions=permissions,
            rate_limit=rate_limit,
            allowed_ips=allowed_ips,
            expires_at=expires_at,
            is_active=True
        )
        
        return api_key, raw_key  # Return raw key only once
    
    async def validate_key(
        self,
        raw_key: str,
        required_permission: Optional[str] = None,
        ip_address: Optional[str] = None
    ) -> Optional[APIKey]:
        """Validate API key with permission and IP checks.

        Every failure path returns None silently; the repository does
        not emit audit logs.
        """
        
        # Hash the provided key
        key_hash = APIKey.hash_key(raw_key)
        
        # Find key by hash
        api_key = await self.get_by_key_hash(key_hash)
        
        if not api_key:
            return None
        
        # Validate key status
        if not api_key.is_valid:
            return None
        
        # Check permission
        if required_permission and not api_key.has_permission(required_permission):
            return None
        
        # Check IP restrictions
        if ip_address and not api_key.is_valid_ip(ip_address):
            return None
        
        # Record usage
        await self.record_usage(api_key.id, ip_address)
        
        return api_key
```

## Advanced Patterns

### Transaction Management

```python
async def complex_operation(project_id: UUID):
    """Example of transaction management."""
    
    # get_transaction() opens a transaction for the whole block:
    # it commits on clean exit and rolls back on any exception.
    async with get_transaction() as session:
        research_repo = ResearchRepository(session)
        task_repo = TaskRepository(session)
        result_repo = ResultRepository(session)
        
        try:
            # Multiple operations in single transaction
            project = await research_repo.get(project_id)
            
            # Create tasks
            tasks = []
            for agent_type in ["literature", "synthesis", "citation"]:
                task = await task_repo.create(
                    project_id=project_id,
                    agent_type=agent_type,
                    task_type="research",
                    priority=1
                )
                tasks.append(task)
            
            # Update project status
            await research_repo.update_status(
                project_id,
                ProjectStatus.IN_PROGRESS
            )
            
            # Create initial results
            await result_repo.bulk_create([
                {
                    "project_id": project_id,
                    "task_id": task.id,
                    "result_type": "initial",
                    "content": {}
                }
                for task in tasks
            ])
            
            # Transaction commits here if no exception
            
        except Exception as e:
            # Transaction automatically rolls back
            logger.error(f"Transaction failed: {e}")
            raise
```

### Query Optimization

```python
class OptimizedRepository(BaseRepository):
    
    async def get_with_relations(self, id: UUID):
        """Eager load related data to avoid N+1 queries."""
        
        query = (
            select(ResearchProject)
            .options(
                selectinload(ResearchProject.tasks),
                selectinload(ResearchProject.results),
                selectinload(ResearchProject.checkpoints)
            )
            .where(ResearchProject.id == id)
        )
        
        result = await self.session.execute(query)
        return result.scalar_one_or_none()
    
    async def bulk_update_optimized(self, updates: List[Dict]):
        """Bulk update using PostgreSQL's UPDATE ... FROM VALUES."""
        
        if not updates:
            return 0
        
        # Build VALUES clause
        values = []
        for update in updates:
            values.append(f"('{update['id']}'::uuid, '{update['status']}')")
        
        # Execute bulk update
        sql = text(f"""
            UPDATE research_projects
            SET status = v.status,
                updated_at = NOW()
            FROM (VALUES {','.join(values)}) AS v(id, status)
            WHERE research_projects.id = v.id
        """)
        
        result = await self.session.execute(sql)
        return result.rowcount
```

### Caching Integration

```python
class CachedRepository(BaseRepository):
    
    def __init__(self, model, session, cache):
        super().__init__(model, session)
        self.cache = cache
    
    async def get(self, id: UUID) -> Optional[ModelType]:
        """Get with caching."""
        
        # Check cache first
        cache_key = f"{self.model.__name__}:{id}"
        cached = await self.cache.get(cache_key)
        
        if cached:
            return self.deserialize(cached)
        
        # Fetch from database
        result = await super().get(id)
        
        if result:
            # Cache the result
            await self.cache.set(
                cache_key,
                self.serialize(result),
                expire=3600  # 1 hour TTL
            )
        
        return result
    
    async def update(self, id: UUID, data: Dict, **kwargs):
        """Update with cache invalidation."""
        
        result = await super().update(id, data, **kwargs)
        
        if result:
            # Invalidate cache
            cache_key = f"{self.model.__name__}:{id}"
            await self.cache.delete(cache_key)
        
        return result
```

## Testing Repositories

### Unit Testing

```python
@pytest.mark.asyncio
async def test_research_repository(test_db):
    """Test research repository operations."""
    
    async with test_db as session:
        repo = ResearchRepository(session)
        
        # Test create
        project = await repo.create(
            title="Test Project",
            query="Test query",
            domains=["Test"],
            user_id=str(uuid.uuid4())
        )
        assert project.id is not None
        assert project.title == "Test Project"
        
        # Test get
        retrieved = await repo.get(project.id)
        assert retrieved.id == project.id
        
        # Test update
        updated = await repo.update(
            project.id,
            {"status": "completed"}
        )
        assert updated.status == "completed"
        
        # Test delete
        success = await repo.delete(project.id)
        assert success
        
        # Verify soft delete
        deleted = await repo.get(project.id)
        assert deleted is None
```

### Integration Testing

```python
@pytest.mark.integration
async def test_complex_workflow(postgres_db):
    """Test complete workflow with real database."""
    
    async with postgres_db as session:
        research_repo = ResearchRepository(session)
        task_repo = TaskRepository(session)
        result_repo = ResultRepository(session)
        
        # Create project (user_id is required — ResearchProject.user_id is NOT NULL)
        project = await research_repo.create(
            title="Integration Test",
            query="Test query",
            domains=["Test"],
            user_id=str(uuid.uuid4())
        )
        
        # Create tasks
        tasks = []
        for i in range(5):
            task = await task_repo.create(
                project_id=project.id,
                agent_type=f"agent_{i}",
                task_type="test"
            )
            tasks.append(task)
        
        # Create results
        results = await result_repo.bulk_create([
            {
                "project_id": project.id,
                "task_id": task.id,
                "result_type": "test",
                "content": {"data": f"result_{i}"}
            }
            for i, task in enumerate(tasks)
        ])
        
        # Verify statistics — per-project result stats live on ResultRepository
        stats = await result_repo.get_statistics(project.id)
        assert stats["total_results"] == 5
        
        # Test cleanup — a HARD delete is required to cascade to child rows;
        # the default soft delete only sets deleted_at on the project row,
        # leaving AgentTask/ResearchResult children in place.
        await research_repo.delete(project.id, soft=False)
        
        # Verify cascade delete (delete-orphan fires on ORM hard delete)
        remaining_tasks = await task_repo.get_by_project(project.id)
        assert len(remaining_tasks) == 0
```

## Best Practices

### 1. Session Lifecycle
- Always use context managers
- Keep sessions short-lived
- Don't share sessions between requests

### 2. Error Handling
```python
async def safe_operation():
    try:
        async with get_transaction() as session:
            repo = ResearchRepository(session)
            return await repo.create(...)
    except IntegrityError as e:
        logger.error(f"Duplicate entry: {e}")
        raise ValueError("Project already exists")
    except DatabaseError as e:
        logger.error(f"Database error: {e}")
        raise ServiceUnavailable("Database temporarily unavailable")
```

### 3. Pagination
```python
async def paginated_results(page: int = 1, per_page: int = 20):
    async with get_transaction() as session:
        repo = ResearchRepository(session)
        
        offset = (page - 1) * per_page
        
        results = await repo.get_many(
            filters={"status": "active"},
            limit=per_page,
            offset=offset,
            order_by="created_at",
            order_desc=True
        )
        
        total = await repo.count({"status": "active"})
        
        return {
            "results": results,
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page
        }
```

### 4. Audit Logging
```python
class AuditedRepository(BaseRepository):
    
    async def create(self, **kwargs):
        # Add audit fields
        kwargs["created_by"] = get_current_user_id()
        kwargs["created_at"] = datetime.now(UTC)
        
        result = await super().create(**kwargs)
        
        # Log creation
        await self.audit_log("create", result.id, kwargs)
        
        return result
    
    async def update(self, id, data, **kwargs):
        # Add audit fields
        data["updated_by"] = get_current_user_id()
        data["updated_at"] = datetime.now(UTC)
        
        # Get old values for comparison
        old = await self.get(id)
        
        result = await super().update(id, data, **kwargs)
        
        # Log changes
        if result:
            changes = self.diff(old, result)
            await self.audit_log("update", id, changes)
        
        return result
```

## Common Patterns

### Upsert Pattern
```python
async def upsert_result(project_id: UUID, source_id: str, content: Dict):
    async with get_transaction() as session:
        repo = ResultRepository(session)
        
        # Try to find existing
        existing = await repo.get_by_source(project_id, source_id)
        
        if existing:
            # Update existing
            return await repo.update(
                existing.id,
                {"content": content, "updated_at": datetime.now(UTC)}
            )
        else:
            # Create new
            return await repo.create(
                project_id=project_id,
                source_id=source_id,
                content=content
            )
```

### Batch Processing
```python
async def process_batch(items: List[Dict], batch_size: int = 100):
    async with get_transaction() as session:
        repo = ResultRepository(session)
        
        results = []
        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]
            
            # Process batch
            batch_results = await repo.bulk_create(batch)
            results.extend(batch_results)
            
            # Flush periodically (get_transaction() commits once on exit)
            await session.flush()
        
        return results
```

This comprehensive guide provides everything needed to work with the repository pattern in the platform.