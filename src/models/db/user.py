"""
User database model.

Represents users of the research platform.
"""

from datetime import datetime

from passlib.context import CryptContext
from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String
from sqlalchemy.orm import relationship

from src.models.db.base import BaseModel

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
    email = Column(String(255), nullable=False, unique=True, index=True)

    username = Column(String(100), nullable=False, unique=True, index=True)

    hashed_password = Column(String(255), nullable=False)

    # Profile fields
    full_name = Column(String(255), nullable=True)

    organization = Column(String(255), nullable=True)

    role = Column(
        String(50), nullable=True, comment="User role (researcher, admin, etc.)"
    )

    # Account status
    is_active = Column(Boolean, nullable=False, default=True, index=True)

    is_superuser = Column(Boolean, nullable=False, default=False)

    is_verified = Column(
        Boolean, nullable=False, default=False, comment="Email verification status"
    )

    # Activity tracking
    last_login = Column(DateTime(timezone=True), nullable=True)

    login_count = Column(Integer, nullable=False, default=0)

    # Account limits
    max_projects = Column(
        Integer, nullable=True, comment="Maximum number of concurrent projects"
    )

    api_rate_limit = Column(Integer, nullable=True, comment="API calls per hour limit")

    # Relationships
    research_projects = relationship(
        "ResearchProject",
        back_populates="user",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

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
        **kwargs,
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
        self.updated_at = datetime.utcnow()

    def record_login(self) -> None:
        """Record a successful login."""
        self.last_login = datetime.utcnow()
        self.login_count += 1

    def activate(self) -> None:
        """Activate user account."""
        self.is_active = True
        self.updated_at = datetime.utcnow()

    def deactivate(self) -> None:
        """Deactivate user account."""
        self.is_active = False
        self.updated_at = datetime.utcnow()

    def verify_email(self) -> None:
        """Mark email as verified."""
        self.is_verified = True
        self.updated_at = datetime.utcnow()

    def grant_superuser(self) -> None:
        """Grant superuser privileges."""
        self.is_superuser = True
        self.updated_at = datetime.utcnow()

    def revoke_superuser(self) -> None:
        """Revoke superuser privileges."""
        self.is_superuser = False
        self.updated_at = datetime.utcnow()

    @property
    def can_create_project(self) -> bool:
        """Check if user can create new projects."""
        if not self.is_active:
            return False

        if self.max_projects is None:
            return True

        # Check current project count
        active_projects = (
            self.research_projects.filter_by(deleted_at=None)
            .filter(ResearchProject.status.in_(["draft", "in_progress"]))
            .count()
        )

        return active_projects < self.max_projects

    @property
    def display_name(self) -> str:
        """Get display name for user."""
        return self.full_name or self.username

    def to_dict(self, include_sensitive: bool = False) -> dict:
        """
        Convert to dictionary.

        Args:
            include_sensitive: Include sensitive fields

        Returns:
            Dictionary representation
        """
        data = {
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
