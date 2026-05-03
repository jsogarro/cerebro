"""
Base repository implementation.

Provides generic CRUD operations for all repositories.
"""

from datetime import UTC, datetime
from typing import Any, Generic, TypeVar
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import Select
from structlog import get_logger

from src.models.db.base import BaseModel

logger = get_logger()

# Type variable for model classes
ModelType = TypeVar("ModelType", bound=BaseModel)


class BaseRepository(Generic[ModelType]):
    """
    Base repository with generic CRUD operations.

    Provides common database operations for all model types.
    """

    def __init__(self, model: type[ModelType], session: AsyncSession):
        """
        Initialize base repository.

        Args:
            model: SQLAlchemy model class
            session: Async database session
        """
        self.model = model
        self.session = session

    async def create(self, **kwargs: Any) -> ModelType:
        """
        Create new entity.

        Args:
            **kwargs: Entity attributes

        Returns:
            Created entity
        """
        entity = self.model(**kwargs)
        self.session.add(entity)
        await self.session.flush()
        await self.session.refresh(entity)
        return entity

    async def get(
        self,
        id: str | UUID,
        include_deleted: bool = False,
        load_relationships: list[str] | None = None,
        organization_id: str | UUID | None = None,
    ) -> ModelType | None:
        """
        Get entity by ID.

        Args:
            id: Entity ID
            include_deleted: Include soft-deleted entities
            load_relationships: List of relationships to eager load
            organization_id: Tenant organization boundary

        Returns:
            Entity or None if not found
        """
        query = select(self.model).where(self.model.id == id)
        query = self.apply_organization_scope(query, organization_id)

        if not include_deleted:
            query = query.where(self.model.deleted_at.is_(None))

        # Add eager loading for relationships
        if load_relationships:
            for rel in load_relationships:
                query = query.options(selectinload(getattr(self.model, rel)))

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_many(
        self,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
        offset: int | None = None,
        order_by: str | None = None,
        order_desc: bool = False,
        include_deleted: bool = False,
        load_relationships: list[str] | None = None,
        organization_id: str | UUID | None = None,
    ) -> list[ModelType]:
        """
        Get multiple entities with filters.

        Args:
            filters: Filter criteria as dict
            limit: Maximum number of results
            offset: Number of results to skip
            order_by: Field to order by
            order_desc: Order descending if True
            include_deleted: Include soft-deleted entities
            load_relationships: List of relationships to eager load
            organization_id: Tenant organization boundary

        Returns:
            List of entities
        """
        query = select(self.model)
        query = self.apply_organization_scope(query, organization_id)

        # Apply filters
        if filters:
            conditions = []
            for key, value in filters.items():
                if hasattr(self.model, key):
                    if isinstance(value, list):
                        conditions.append(getattr(self.model, key).in_(value))
                    elif value is None:
                        conditions.append(getattr(self.model, key).is_(None))
                    else:
                        conditions.append(getattr(self.model, key) == value)

            if conditions:
                query = query.where(and_(*conditions))

        # Exclude deleted unless specified
        if not include_deleted:
            query = query.where(self.model.deleted_at.is_(None))

        # Add eager loading
        if load_relationships:
            for rel in load_relationships:
                query = query.options(selectinload(getattr(self.model, rel)))

        # Apply ordering
        if order_by and hasattr(self.model, order_by):
            order_column = getattr(self.model, order_by)
            if order_desc:
                query = query.order_by(order_column.desc())
            else:
                query = query.order_by(order_column)
        else:
            # Default ordering by created_at desc
            query = query.order_by(self.model.created_at.desc())

        # Apply pagination
        if offset:
            query = query.offset(offset)
        if limit:
            query = query.limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def update(
        self,
        id: str | UUID,
        data: dict[str, Any],
        updated_by: str | None = None,
        organization_id: str | UUID | None = None,
    ) -> ModelType | None:
        """
        Update entity by ID.

        Args:
            id: Entity ID
            data: Update data
            updated_by: User performing the update
            organization_id: Tenant organization boundary

        Returns:
            Updated entity or None if not found
        """
        entity = await self.get(id, organization_id=organization_id)

        if not entity:
            return None

        # Update fields
        for key, value in data.items():
            if hasattr(entity, key) and key not in ["id", "created_at"]:
                setattr(entity, key, value)

        # Set audit fields
        entity.updated_at = datetime.now(UTC)
        if updated_by:
            entity.updated_by = updated_by

        await self.session.flush()
        await self.session.refresh(entity)
        return entity

    async def delete(
        self,
        id: str | UUID,
        soft: bool = True,
        deleted_by: str | None = None,
        organization_id: str | UUID | None = None,
    ) -> bool:
        """
        Delete entity by ID.

        Args:
            id: Entity ID
            soft: If True, perform soft delete
            deleted_by: User performing the delete
            organization_id: Tenant organization boundary

        Returns:
            True if deleted, False if not found
        """
        entity = await self.get(id, organization_id=organization_id)

        if not entity:
            return False

        if soft:
            # Soft delete
            entity.deleted_at = datetime.now(UTC)
            if deleted_by:
                entity.updated_by = deleted_by
            await self.session.flush()
        else:
            # Hard delete
            await self.session.delete(entity)
            await self.session.flush()

        return True

    async def hard_delete(self, id: str | UUID) -> bool:
        """
        Permanently delete entity.

        Args:
            id: Entity ID

        Returns:
            True if deleted, False if not found
        """
        return await self.delete(id, soft=False)

    async def restore(
        self, id: str | UUID, restored_by: str | None = None
    ) -> ModelType | None:
        """
        Restore soft-deleted entity.

        Args:
            id: Entity ID
            restored_by: User performing the restore

        Returns:
            Restored entity or None if not found
        """
        entity = await self.get(id, include_deleted=True)

        if not entity or not entity.deleted_at:
            return None

        entity.deleted_at = None
        entity.updated_at = datetime.now(UTC)
        if restored_by:
            entity.updated_by = restored_by

        await self.session.flush()
        await self.session.refresh(entity)
        return entity

    async def exists(
        self,
        id: str | UUID | None = None,
        filters: dict[str, Any] | None = None,
        include_deleted: bool = False,
        organization_id: str | UUID | None = None,
    ) -> bool:
        """
        Check if entity exists.

        Args:
            id: Entity ID
            filters: Filter criteria
            include_deleted: Include soft-deleted entities
            organization_id: Tenant organization boundary

        Returns:
            True if exists
        """
        query = select(func.count(self.model.id))
        query = self.apply_organization_scope(query, organization_id)

        if id:
            query = query.where(self.model.id == id)

        if filters:
            for key, value in filters.items():
                if hasattr(self.model, key):
                    query = query.where(getattr(self.model, key) == value)

        if not include_deleted:
            query = query.where(self.model.deleted_at.is_(None))

        result = await self.session.execute(query)
        count = result.scalar()
        assert count is not None
        return count > 0

    async def count(
        self,
        filters: dict[str, Any] | None = None,
        include_deleted: bool = False,
        organization_id: str | UUID | None = None,
    ) -> int:
        """
        Count entities.

        Args:
            filters: Filter criteria
            include_deleted: Include soft-deleted entities
            organization_id: Tenant organization boundary

        Returns:
            Entity count
        """
        query = select(func.count(self.model.id))
        query = self.apply_organization_scope(query, organization_id)

        if filters:
            conditions = []
            for key, value in filters.items():
                if hasattr(self.model, key):
                    if isinstance(value, list):
                        conditions.append(getattr(self.model, key).in_(value))
                    else:
                        conditions.append(getattr(self.model, key) == value)

            if conditions:
                query = query.where(and_(*conditions))

        if not include_deleted:
            query = query.where(self.model.deleted_at.is_(None))

        result = await self.session.execute(query)
        return result.scalar() or 0

    async def bulk_create(self, entities: list[dict[str, Any]]) -> list[ModelType]:
        """
        Create multiple entities.

        Args:
            entities: List of entity data

        Returns:
            List of created entities
        """
        created = []

        for entity_data in entities:
            entity = self.model(**entity_data)
            self.session.add(entity)
            created.append(entity)

        await self.session.flush()

        # Refresh all entities
        for entity in created:
            await self.session.refresh(entity)

        return created

    async def bulk_update(
        self, updates: list[dict[str, Any]], updated_by: str | None = None
    ) -> int:
        """
        Update multiple entities.

        Args:
            updates: List of dicts with 'id' and update data
            updated_by: User performing the update

        Returns:
            Number of updated entities
        """
        updated_count = 0

        for update_data in updates:
            entity_id = update_data.pop("id", None)
            if entity_id:
                entity = await self.update(entity_id, update_data, updated_by)
                if entity:
                    updated_count += 1

        return updated_count

    async def bulk_delete(
        self,
        ids: list[str | UUID],
        soft: bool = True,
        deleted_by: str | None = None,
    ) -> int:
        """
        Delete multiple entities.

        Args:
            ids: List of entity IDs
            soft: If True, perform soft delete
            deleted_by: User performing the delete

        Returns:
            Number of deleted entities
        """
        deleted_count = 0

        for entity_id in ids:
            if await self.delete(entity_id, soft, deleted_by):
                deleted_count += 1

        return deleted_count

    async def search(
        self,
        search_term: str,
        search_fields: list[str],
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
        offset: int | None = None,
        organization_id: str | UUID | None = None,
    ) -> list[ModelType]:
        """
        Search entities by text.

        Args:
            search_term: Search term
            search_fields: Fields to search in
            filters: Additional filters
            limit: Maximum results
            offset: Results to skip
            organization_id: Tenant organization boundary

        Returns:
            List of matching entities
        """
        query = select(self.model)
        query = self.apply_organization_scope(query, organization_id)

        # Build search conditions
        search_conditions = []
        for field in search_fields:
            if hasattr(self.model, field):
                column = getattr(self.model, field)
                search_conditions.append(
                    func.lower(column).contains(search_term.lower())
                )

        if search_conditions:
            query = query.where(or_(*search_conditions))

        # Apply additional filters
        if filters:
            for key, value in filters.items():
                if hasattr(self.model, key):
                    query = query.where(getattr(self.model, key) == value)

        # Exclude deleted
        query = query.where(self.model.deleted_at.is_(None))

        # Apply pagination
        if offset:
            query = query.offset(offset)
        if limit:
            query = query.limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    def build_query(self) -> Select[tuple[ModelType]]:
        """
        Build base query for custom operations.

        Returns:
            SQLAlchemy Select query
        """
        return select(self.model).where(self.model.deleted_at.is_(None))

    def apply_organization_scope(
        self,
        query: Select[Any],
        organization_id: str | UUID | None,
    ) -> Select[Any]:
        """
        Apply tenant organization filtering when a scoped query is requested.

        Repositories stay backward-compatible while API boundaries are migrated:
        callers that have tenant context pass organization_id, and models without
        tenant support fail closed instead of silently ignoring the scope.
        """
        if organization_id is None:
            return query

        if not hasattr(self.model, "organization_id"):
            raise ValueError(
                f"{self.model.__name__} does not support organization scoping"
            )

        model: Any = self.model
        organization_column = model.organization_id
        return query.where(organization_column == organization_id)


__all__ = ["BaseRepository", "ModelType"]
