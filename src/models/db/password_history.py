"""
Password history database model.

Tracks password changes to prevent password reuse and enforce
password history policies.
"""

from datetime import datetime, timedelta
from typing import Optional

from passlib.context import CryptContext
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.models.db.base import BaseModel

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class PasswordHistory(BaseModel):
    """
    Password history model.

    Tracks historical passwords for each user to prevent reuse
    and enforce password policies.
    """

    __tablename__ = "password_history"

    # Foreign key to user
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Hashed password
    hashed_password = Column(
        String(255), nullable=False, comment="Bcrypt hashed password"
    )

    # Password metadata
    changed_by = Column(
        String(255),
        nullable=True,
        comment="Who initiated the password change (user, admin, system)",
    )

    change_reason = Column(
        String(500),
        nullable=True,
        comment="Reason for password change (expired, reset, voluntary)",
    )

    ip_address = Column(
        String(45), nullable=True, comment="IP address from which password was changed"
    )

    user_agent = Column(
        String(500),
        nullable=True,
        comment="User agent string when password was changed",
    )

    # Password strength metrics
    password_strength = Column(
        Integer, nullable=True, comment="Password strength score (0-100)"
    )

    expires_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="When this password expired (if applicable)",
    )

    # Relationships
    user = relationship("User", back_populates="password_history")

    # Indexes
    __table_args__ = (
        Index("idx_password_history_user_created", "user_id", "created_at"),
        Index("idx_password_history_expires", "expires_at"),
    )

    @classmethod
    def create_entry(
        cls,
        user_id: str,
        password: str,
        changed_by: str | None = None,
        change_reason: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        password_strength: int | None = None,
        expires_in_days: int | None = None,
    ) -> "PasswordHistory":
        """
        Create a new password history entry.

        Args:
            user_id: User ID
            password: Plain text password (will be hashed)
            changed_by: Who initiated the change
            change_reason: Reason for change
            ip_address: Client IP address
            user_agent: Client user agent
            password_strength: Password strength score
            expires_in_days: Days until password expires

        Returns:
            PasswordHistory instance
        """
        hashed_password = pwd_context.hash(password)

        expires_at = None
        if expires_in_days:
            expires_at = datetime.utcnow() + timedelta(days=expires_in_days)

        return cls(
            user_id=user_id,
            hashed_password=hashed_password,
            changed_by=changed_by,
            change_reason=change_reason,
            ip_address=ip_address,
            user_agent=user_agent,
            password_strength=password_strength,
            expires_at=expires_at,
        )

    def verify_password(self, password: str) -> bool:
        """
        Check if a password matches this historical password.

        Args:
            password: Plain text password to check

        Returns:
            True if password matches
        """
        return pwd_context.verify(password, self.hashed_password)

    @property
    def is_expired(self) -> bool:
        """Check if this password has expired."""
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at

    @property
    def age_in_days(self) -> int:
        """Get age of password in days."""
        delta = datetime.utcnow() - self.created_at
        return delta.days

    @classmethod
    def check_password_reuse(
        cls, user_id: str, password: str, history_count: int = 5, session=None
    ) -> bool:
        """
        Check if password has been used recently.

        Args:
            user_id: User ID
            password: Plain text password to check
            history_count: Number of historical passwords to check
            session: Database session

        Returns:
            True if password was used recently
        """
        if not session:
            return False

        # Get recent password history
        recent_passwords = (
            session.query(cls)
            .filter(cls.user_id == user_id, cls.deleted_at.is_(None))
            .order_by(cls.created_at.desc())
            .limit(history_count)
            .all()
        )

        # Check if password matches any recent password
        for entry in recent_passwords:
            if entry.verify_password(password):
                return True

        return False

    @classmethod
    def get_last_change(cls, user_id: str, session=None) -> Optional["PasswordHistory"]:
        """
        Get the most recent password change for a user.

        Args:
            user_id: User ID
            session: Database session

        Returns:
            Most recent PasswordHistory entry or None
        """
        if not session:
            return None

        return (
            session.query(cls)
            .filter(cls.user_id == user_id, cls.deleted_at.is_(None))
            .order_by(cls.created_at.desc())
            .first()
        )

    def to_dict(self) -> dict:
        """
        Convert to dictionary.

        Returns:
            Dictionary representation (excludes sensitive data)
        """
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "changed_by": self.changed_by,
            "change_reason": self.change_reason,
            "password_strength": self.password_strength,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "is_expired": self.is_expired,
            "age_in_days": self.age_in_days,
        }

    def __repr__(self) -> str:
        """String representation."""
        return f"<PasswordHistory(id={self.id}, user_id={self.user_id}, created_at={self.created_at})>"
