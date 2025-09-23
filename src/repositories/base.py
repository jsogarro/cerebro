"""
Base repository implementation.

Provides generic CRUD operations for all repositories.
"""

import logging
from datetime import datetime
from typing import Any, Generic, TypeVar
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import Select

from src.models.db.base import BaseModel

logger = logging.getLogger(__name__)

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

    async def create(self, **kwargs) -> ModelType:
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
    ) -> ModelType | None:
        """
        Get entity by ID.

        Args:
            id: Entity ID
            include_deleted: Include soft-deleted entities
            load_relationships: List of relationships to eager load

        Returns:
            Entity or None if not found
        """
        query = select(self.model).where(self.model.id == id)

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

        Returns:
            List of entities
        """
        query = select(self.model)

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
    ) -> ModelType | None:
        """
        Update entity by ID.

        Args:
            id: Entity ID
            data: Update data
            updated_by: User performing the update

        Returns:
            Updated entity or None if not found
        """
        entity = await self.get(id)

        if not entity:
            return None

        # Update fields
        for key, value in data.items():
            if hasattr(entity, key) and key not in ["id", "created_at"]:
                setattr(entity, key, value)

        # Set audit fields
        entity.updated_at = datetime.utcnow()
        if updated_by:
            entity.updated_by = updated_by

        await self.session.flush()
        await self.session.refresh(entity)
        return entity

    async def delete(
        self, id: str | UUID, soft: bool = True, deleted_by: str | None = None
    ) -> bool:
        """
        Delete entity by ID.

        Args:
            id: Entity ID
            soft: If True, perform soft delete
            deleted_by: User performing the delete

        Returns:
            True if deleted, False if not found
        """
        entity = await self.get(id)

        if not entity:
            return False

        if soft:
            # Soft delete
            entity.deleted_at = datetime.utcnow()
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
        entity.updated_at = datetime.utcnow()
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
    ) -> bool:
        """
        Check if entity exists.

        Args:
            id: Entity ID
            filters: Filter criteria
            include_deleted: Include soft-deleted entities

        Returns:
            True if exists
        """
        query = select(func.count(self.model.id))

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
        return count > 0

    async def count(
        self, filters: dict[str, Any] | None = None, include_deleted: bool = False
    ) -> int:
        """
        Count entities.

        Args:
            filters: Filter criteria
            include_deleted: Include soft-deleted entities

        Returns:
            Entity count
        """
        query = select(func.count(self.model.id))

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
    ) -> list[ModelType]:
        """
        Search entities by text.

        Args:
            search_term: Search term
            search_fields: Fields to search in
            filters: Additional filters
            limit: Maximum results
            offset: Results to skip

        Returns:
            List of matching entities
        """
        query = select(self.model)

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

    def build_query(self) -> Select:
        """
        Build base query for custom operations.

        Returns:
            SQLAlchemy Select query
        """
        return select(self.model).where(self.model.deleted_at.is_(None))


__all__ = ["BaseRepository", "ModelType"]
