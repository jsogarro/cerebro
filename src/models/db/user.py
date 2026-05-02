"""
User database model.

Represents users of the research platform.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from passlib.context import CryptContext
from sqlalchemy import Boolean, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.db.base import BaseModel

if TYPE_CHECKING:
    pass

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class User(BaseModel):
    """
    User model.

    Stores user account information including authentication
    credentials and profile data.
    """

    __tablename__ = "users"

    # Authentication fields
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)

    username: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)

    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    # Profile fields
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    organization: Mapped[str | None] = mapped_column(String(255), nullable=True)

    role: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )

    # Account status
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    is_superuser: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    is_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # Activity tracking
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Account limits
    max_projects: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )

    api_rate_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Relationships removed: user_id is now a plain string, not a FK

    api_keys = relationship(
        "APIKey", back_populates="user", lazy="dynamic", cascade="all, delete-orphan"
    )

    # Authentication relationships
    password_history = relationship(
        "PasswordHistory",
        back_populates="user",
        lazy="dynamic",
        cascade="all, delete-orphan",
        order_by="desc(PasswordHistory.created_at)",
    )

    sessions = relationship(
        "UserSession",
        back_populates="user",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    audit_logs = relationship("AuditLog", back_populates="user", lazy="dynamic")

    oauth_accounts = relationship(
        "OAuthAccount",
        back_populates="user",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    mfa_settings = relationship(
        "MFASettings",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

    security_alerts = relationship(
        "SecurityAlert",
        back_populates="user",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    # Indexes
    __table_args__ = (
        Index("idx_user_active", "is_active", "is_verified"),
        Index("idx_user_login", "last_login", "is_active"),
    )

    @classmethod
    def create_with_password(
        cls,
        email: str,
        username: str,
        password: str,
        full_name: str | None = None,
        **kwargs: Any,
    ) -> "User":
        """
        Create a new user with hashed password.

        Args:
            email: User email
            username: Username
            password: Plain text password
            full_name: Full name
            **kwargs: Additional user fields

        Returns:
            User instance
        """
        hashed_password = pwd_context.hash(password)

        return cls(
            email=email,
            username=username,
            hashed_password=hashed_password,
            full_name=full_name,
            **kwargs,
        )

    def verify_password(self, password: str) -> bool:
        """
        Verify password against hash.

        Args:
            password: Plain text password

        Returns:
            True if password matches
        """
        return pwd_context.verify(password, self.hashed_password)

    def update_password(self, new_password: str) -> None:
        """
        Update user password.

        Args:
            new_password: New plain text password
        """
        self.hashed_password = pwd_context.hash(new_password)
        self.updated_at = datetime.now(UTC)

    def record_login(self) -> None:
        """Record a successful login."""
        self.last_login = datetime.now(UTC)
        self.login_count += 1

    def activate(self) -> None:
        """Activate user account."""
        self.is_active = True
        self.updated_at = datetime.now(UTC)

    def deactivate(self) -> None:
        """Deactivate user account."""
        self.is_active = False
        self.updated_at = datetime.now(UTC)

    def verify_email(self) -> None:
        """Mark email as verified."""
        self.is_verified = True
        self.updated_at = datetime.now(UTC)

    def grant_superuser(self) -> None:
        """Grant superuser privileges."""
        self.is_superuser = True
        self.updated_at = datetime.now(UTC)

    def revoke_superuser(self) -> None:
        """Revoke superuser privileges."""
        self.is_superuser = False
        self.updated_at = datetime.now(UTC)

    @property
    def can_create_project(self) -> bool:
        """Check if user can create new projects."""
        if not self.is_active:
            return False

        if self.max_projects is None:
            return True

        # Without FK relationship, assume can create
        return True

    @property
    def display_name(self) -> str:
        """Get display name for user."""
        return str(self.full_name or self.username)

    def to_dict(self, include_sensitive: bool = False) -> dict[str, Any]:
        """
        Convert to dictionary.

        Args:
            include_sensitive: Include sensitive fields

        Returns:
            Dictionary representation
        """
        data: dict[str, Any] = {
            "id": str(self.id),
            "email": self.email,
            "username": self.username,
            "full_name": self.full_name,
            "organization": self.organization,
            "role": self.role,
            "is_active": self.is_active,
            "is_verified": self.is_verified,
            "is_superuser": self.is_superuser,
            "last_login": self.last_login.isoformat() if self.last_login else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "display_name": self.display_name,
            "can_create_project": self.can_create_project,
        }

        if include_sensitive:
            data["login_count"] = self.login_count
            data["max_projects"] = self.max_projects
            data["api_rate_limit"] = self.api_rate_limit

        return data

    def __repr__(self) -> str:
        """String representation."""
        return f"<User(id={self.id}, username='{self.username}', email='{self.email}')>"
