# Repository Pattern Implementation Guide

## Overview

This guide documents the repository pattern implementation in the Multi-Agent Research Platform, providing examples and best practices for data access operations.

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
    def __init__(self, model_class: Type[ModelType], session: AsyncSession):
        self.model_class = model_class
        self.session = session
```

### Standard CRUD Operations

#### Create
```python
async def create_research_project():
    async with get_session() as session:
        repo = ResearchRepository(session)
        
        project = await repo.create(
            title="AI Impact on Employment",
            research_query="How will AI affect job markets?",
            domains=["AI", "Economics"],
            created_by=user_id
        )
        
        await session.commit()
        return project
```

#### Read
```python
async def get_project_details(project_id: UUID):
    async with get_session() as session:
        repo = ResearchRepository(session)
        
        # Get single record
        project = await repo.get(project_id)
        
        # Get with related data
        project_with_tasks = await repo.get_with_tasks(project_id)
        
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
    async with get_session() as session:
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
        
        await session.commit()
```

#### Delete
```python
async def delete_project(project_id: UUID):
    async with get_session() as session:
        repo = ResearchRepository(session)
        
        # Soft delete (default)
        success = await repo.delete(project_id)
        
        # Hard delete
        success = await repo.delete(project_id, soft=False)
        
        await session.commit()
```

## Specialized Repository Examples

### ResearchRepository

```python
class ResearchRepository(BaseRepository[ResearchProject]):
    
    async def search_projects(
        self,
        query: str,
        domains: Optional[List[str]] = None,
        status: Optional[str] = None
    ) -> List[ResearchProject]:
        """Full-text search across projects."""
        
        stmt = select(ResearchProject)
        
        # Text search
        if query:
            stmt = stmt.where(
                or_(
                    ResearchProject.title.ilike(f"%{query}%"),
                    ResearchProject.research_query.ilike(f"%{query}%")
                )
            )
        
        # Domain filter
        if domains:
            stmt = stmt.where(
                ResearchProject.domains.overlap(domains)
            )
        
        # Status filter
        if status:
            stmt = stmt.where(ResearchProject.status == status)
        
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
    
    async def get_statistics(self, project_id: UUID) -> Dict[str, Any]:
        """Get comprehensive project statistics."""
        
        # Get task statistics
        task_stats = await self.session.execute(
            select(
                AgentTask.status,
                func.count(AgentTask.id).label("count")
            )
            .where(AgentTask.project_id == project_id)
            .group_by(AgentTask.status)
        )
        
        # Get result statistics
        result_stats = await self.session.execute(
            select(
                func.count(ResearchResult.id).label("total"),
                func.avg(ResearchResult.confidence_score).label("avg_confidence")
            )
            .where(ResearchResult.project_id == project_id)
        )
        
        return {
            "tasks": {row.status: row.count for row in task_stats},
            "results": result_stats.one()._asdict()
        }
```

### TaskRepository

```python
class TaskRepository(BaseRepository[AgentTask]):
    
    async def get_pending_tasks(
        self,
        agent_type: Optional[str] = None,
        limit: int = 10
    ) -> List[AgentTask]:
        """Get tasks ready for execution."""
        
        query = self.build_query().where(
            AgentTask.status == "pending"
        )
        
        if agent_type:
            query = query.where(AgentTask.agent_type == agent_type)
        
        # Check dependencies
        subquery = select(AgentTask.id).where(
            and_(
                AgentTask.id.in_(select(unnest(AgentTask.dependencies))),
                AgentTask.status != "completed"
            )
        )
        
        query = query.where(
            ~exists(subquery)  # No incomplete dependencies
        )
        
        query = query.order_by(
            AgentTask.priority.desc(),
            AgentTask.created_at
        ).limit(limit)
        
        result = await self.session.execute(query)
        return list(result.scalars().all())
    
    async def retry_task(self, task_id: UUID) -> Optional[AgentTask]:
        """Retry a failed task."""
        
        task = await self.get(task_id)
        
        if not task or task.status != "failed":
            return None
        
        # Check retry limit
        if task.retry_count >= 3:
            task.status = "permanently_failed"
        else:
            task.status = "pending"
            task.retry_count += 1
            task.error_details = None
            task.scheduled_at = datetime.utcnow() + timedelta(
                minutes=2 ** task.retry_count  # Exponential backoff
            )
        
        await self.session.flush()
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
        
        # Update project statistics
        if created_results:
            project_id = created_results[0].project_id
            await self._update_project_stats(project_id)
        
        return created_results
    
    async def merge_duplicates(self, project_id: UUID) -> int:
        """Intelligently merge duplicate results."""
        
        # Group by source and content similarity
        results = await self.get_by_project(project_id)
        
        duplicates = defaultdict(list)
        for result in results:
            # Create signature for deduplication
            signature = self._create_signature(result)
            duplicates[signature].append(result)
        
        merged_count = 0
        for signature, group in duplicates.items():
            if len(group) > 1:
                # Keep highest confidence result
                group.sort(key=lambda x: x.confidence_score or 0, reverse=True)
                keep = group[0]
                
                # Merge metadata from duplicates
                for duplicate in group[1:]:
                    keep.result_metadata = {
                        **keep.result_metadata,
                        **duplicate.result_metadata,
                        "merged_from": [*keep.result_metadata.get("merged_from", []), str(duplicate.id)]
                    }
                    
                    await self.delete(duplicate.id)
                    merged_count += 1
        
        return merged_count
    
    def _create_signature(self, result: ResearchResult) -> str:
        """Create unique signature for deduplication."""
        components = [
            result.result_type,
            result.source_id or "",
            str(result.content.get("title", ""))[:100] if result.content else ""
        ]
        return "|".join(components)
```

### APIKeyRepository

```python
class APIKeyRepository(BaseRepository[APIKey]):
    
    async def create_key(
        self,
        user_id: UUID,
        name: str,
        permissions: List[str],
        expires_in_days: Optional[int] = None
    ) -> Tuple[APIKey, str]:
        """Create API key and return it with raw key (shown once)."""
        
        # Generate secure key
        raw_key = f"gar_{secrets.token_urlsafe(32)}"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        
        # Calculate expiration
        expires_at = None
        if expires_in_days:
            expires_at = datetime.utcnow() + timedelta(days=expires_in_days)
        
        # Create key record
        api_key = await self.create(
            key_hash=key_hash,
            user_id=user_id,
            name=name,
            permissions=permissions,
            expires_at=expires_at
        )
        
        # Log key creation
        await self._audit_log(
            action="api_key_created",
            user_id=user_id,
            details={"key_id": str(api_key.id), "name": name}
        )
        
        return api_key, raw_key  # Return raw key only once
    
    async def validate_key(
        self,
        raw_key: str,
        required_permission: Optional[str] = None,
        ip_address: Optional[str] = None
    ) -> Optional[APIKey]:
        """Validate API key with permission and IP checks."""
        
        # Hash the provided key
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        
        # Find key by hash
        api_key = await self.get_by_key_hash(key_hash)
        
        if not api_key:
            await self._audit_log(
                action="invalid_api_key",
                details={"ip": ip_address}
            )
            return None
        
        # Validate key status
        if not api_key.is_valid:
            return None
        
        # Check permission
        if required_permission and not api_key.has_permission(required_permission):
            await self._audit_log(
                action="permission_denied",
                user_id=api_key.user_id,
                details={"permission": required_permission}
            )
            return None
        
        # Check IP restrictions
        if ip_address and not api_key.is_valid_ip(ip_address):
            await self._audit_log(
                action="ip_denied",
                user_id=api_key.user_id,
                details={"ip": ip_address}
            )
            return None
        
        # Record usage
        api_key.record_use(ip_address)
        await self.session.flush()
        
        return api_key
```

## Advanced Patterns

### Transaction Management

```python
async def complex_operation(project_id: UUID):
    """Example of transaction management."""
    
    async with get_session() as session:
        async with session.begin():  # Start transaction
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
                    "in_progress"
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
    
    def __init__(self, model_class, session, cache):
        super().__init__(model_class, session)
        self.cache = cache
    
    async def get(self, id: UUID) -> Optional[ModelType]:
        """Get with caching."""
        
        # Check cache first
        cache_key = f"{self.model_class.__name__}:{id}"
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
            cache_key = f"{self.model_class.__name__}:{id}"
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
            research_query="Test query",
            domains=["Test"],
            created_by=uuid.uuid4()
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
        
        # Create project
        project = await research_repo.create(
            title="Integration Test",
            research_query="Test query",
            domains=["Test"]
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
        
        # Verify statistics
        stats = await research_repo.get_statistics(project.id)
        assert stats["results"]["total"] == 5
        
        # Test cleanup
        await research_repo.delete(project.id)
        
        # Verify cascade delete
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
        async with get_session() as session:
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
    async with get_session() as session:
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
        kwargs["created_at"] = datetime.utcnow()
        
        result = await super().create(**kwargs)
        
        # Log creation
        await self.audit_log("create", result.id, kwargs)
        
        return result
    
    async def update(self, id, data, **kwargs):
        # Add audit fields
        data["updated_by"] = get_current_user_id()
        data["updated_at"] = datetime.utcnow()
        
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
    async with get_session() as session:
        repo = ResultRepository(session)
        
        # Try to find existing
        existing = await repo.get_by_source(project_id, source_id)
        
        if existing:
            # Update existing
            return await repo.update(
                existing.id,
                {"content": content, "updated_at": datetime.utcnow()}
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
    async with get_session() as session:
        repo = ResultRepository(session)
        
        results = []
        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]
            
            # Process batch
            batch_results = await repo.bulk_create(batch)
            results.extend(batch_results)
            
            # Commit periodically
            await session.commit()
        
        return results
```

This comprehensive guide provides everything needed to work with the repository pattern in the platform.