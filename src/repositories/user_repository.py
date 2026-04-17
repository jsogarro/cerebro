"""
User repository.

Specialized repository for user operations.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.db.user import User
from src.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    """
    Repository for user operations.

    Provides specialized queries for user management.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize user repository."""
        super().__init__(User, session)

    async def get_by_email(
        self, email: str, include_deleted: bool = False
    ) -> User | None:
        """
        Get user by email.

        Args:
            email: User email
            include_deleted: Include deleted users

        Returns:
            User or None
        """
        query = select(User).where(func.lower(User.email) == email.lower())

        if not include_deleted:
            query = query.where(User.deleted_at.is_(None))

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_by_username(
        self, username: str, include_deleted: bool = False
    ) -> User | None:
        """
        Get user by username.

        Args:
            username: Username
            include_deleted: Include deleted users

        Returns:
            User or None
        """
        query = select(User).where(func.lower(User.username) == username.lower())

        if not include_deleted:
            query = query.where(User.deleted_at.is_(None))

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def update_last_login(self, user_id: UUID) -> User | None:
        """
        Update user's last login time.

        Args:
            user_id: User ID

        Returns:
            Updated user
        """
        user = await self.get(user_id)
        if user:
            user.record_login()
            await self.session.flush()
            await self.session.refresh(user)
        return user

    async def get_active_users(
        self, limit: int | None = None, offset: int | None = None
    ) -> list[User]:
        """
        Get active users.

        Args:
            limit: Maximum results
            offset: Skip results

        Returns:
            List of active users
        """
        return await self.get_many(
            filters={"is_active": True, "is_verified": True},
            limit=limit,
            offset=offset,
            order_by="last_login",
            order_desc=True,
        )

    async def create_with_password(
        self,
        email: str,
        username: str,
        password: str,
        full_name: str | None = None,
        **kwargs: Any,
    ) -> User:
        """
        Create user with hashed password.

        Args:
            email: User email
            username: Username
            password: Plain text password
            full_name: Full name
            **kwargs: Additional user fields

        Returns:
            Created user
        """
        # Check if email already exists
        existing = await self.get_by_email(email)
        if existing:
            raise ValueError(f"User with email {email} already exists")

        # Check if username already exists
        existing = await self.get_by_username(username)
        if existing:
            raise ValueError(f"Username {username} already taken")

        # Create user with hashed password
        user = User.create_with_password(
            email=email,
            username=username,
            password=password,
            full_name=full_name,
            **kwargs,
        )

        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)
        return user

    async def verify_credentials(
        self, email_or_username: str, password: str
    ) -> User | None:
        """
        Verify user credentials.

        Args:
            email_or_username: Email or username
            password: Plain text password

        Returns:
            User if credentials valid, None otherwise
        """
        # Try email first
        user = await self.get_by_email(email_or_username)

        # Try username if email not found
        if not user:
            user = await self.get_by_username(email_or_username)

        # Verify password
        if user and user.verify_password(password) and user.is_active:
            return user

        return None

    async def search_users(
        self,
        query: str,
        role: str | None = None,
        is_active: bool | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[User]:
        """
        Search users.

        Args:
            query: Search query
            role: Filter by role
            is_active: Filter by active status
            limit: Maximum results
            offset: Skip results

        Returns:
            List of matching users
        """
        stmt = self.build_query()

        # Search in email, username, and full name
        if query:
            search_term = f"%{query.lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(User.email).like(search_term),
                    func.lower(User.username).like(search_term),
                    func.lower(User.full_name).like(search_term),
                )
            )

        # Filter by role
        if role:
            stmt = stmt.where(User.role == role)

        # Filter by active status
        if is_active is not None:
            stmt = stmt.where(User.is_active == is_active)

        # Order by last login
        stmt = stmt.order_by(User.last_login.desc().nullslast())

        # Pagination
        stmt = stmt.offset(offset).limit(limit)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_user_statistics(self) -> dict[str, Any]:
        """
        Get user statistics.

        Returns:
            User statistics
        """
        # Total users
        total_query = select(func.count(User.id)).where(User.deleted_at.is_(None))
        result = await self.session.execute(total_query)
        total = result.scalar() or 0

        # Active users
        active_query = select(func.count(User.id)).where(
            and_(User.deleted_at.is_(None), User.is_active)
        )
        result = await self.session.execute(active_query)
        active = result.scalar() or 0

        # Verified users
        verified_query = select(func.count(User.id)).where(
            and_(User.deleted_at.is_(None), User.is_verified)
        )
        result = await self.session.execute(verified_query)
        verified = result.scalar() or 0

        # Superusers
        super_query = select(func.count(User.id)).where(
            and_(User.deleted_at.is_(None), User.is_superuser)
        )
        result = await self.session.execute(super_query)
        superusers = result.scalar() or 0

        return {
            "total_users": total,
            "active_users": active,
            "verified_users": verified,
            "superusers": superusers,
            "inactive_users": total - active,
            "unverified_users": total - verified,
        }

    async def update_password(self, user_id: UUID, new_password: str) -> User | None:
        """
        Update user password.

        Args:
            user_id: User ID
            new_password: New password

        Returns:
            Updated user
        """
        user = await self.get(user_id)
        if user:
            user.update_password(new_password)
            await self.session.flush()
            await self.session.refresh(user)
        return user

    async def toggle_active(self, user_id: UUID) -> User | None:
        """
        Toggle user active status.

        Args:
            user_id: User ID

        Returns:
            Updated user
        """
        user = await self.get(user_id)
        if user:
            if user.is_active:
                user.deactivate()
            else:
                user.activate()
            await self.session.flush()
            await self.session.refresh(user)
        return user

    async def verify_email(self, user_id: UUID) -> User | None:
        """
        Mark user email as verified.

        Args:
            user_id: User ID

        Returns:
            Updated user
        """
        user = await self.get(user_id)
        if user:
            user.verify_email()
            await self.session.flush()
            await self.session.refresh(user)
        return user


__all__ = ["UserRepository"]
