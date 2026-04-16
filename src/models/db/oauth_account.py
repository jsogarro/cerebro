"""
OAuth account database model.

Manages OAuth provider connections for social authentication
(Google, GitHub, etc.).
"""

from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ENUM, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.db.base import BaseModel


class OAuthProvider(str, Enum):
    """Supported OAuth providers."""

    GOOGLE = "google"
    GITHUB = "github"
    MICROSOFT = "microsoft"
    FACEBOOK = "facebook"
    LINKEDIN = "linkedin"
    TWITTER = "twitter"
    APPLE = "apple"
    GITLAB = "gitlab"
    BITBUCKET = "bitbucket"
    OKTA = "okta"
    AUTH0 = "auth0"


class OAuthAccount(BaseModel):
    """
    OAuth account model.

    Links user accounts to external OAuth providers for
    social authentication and authorization.
    """

    __tablename__ = "oauth_accounts"

    user_id: Mapped[Any] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    provider: Mapped[OAuthProvider] = mapped_column(
        ENUM(OAuthProvider, name="oauth_provider"),
        nullable=False,
        index=True,
        comment="OAuth provider name",
    )

    provider_user_id: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="User ID from OAuth provider"
    )

    provider_username: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="Username from OAuth provider"
    )

    provider_email: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="Email from OAuth provider"
    )

    access_token: Mapped[str] = mapped_column(
        String(2048), nullable=False, comment="OAuth access token (encrypted)"
    )

    refresh_token: Mapped[str | None] = mapped_column(
        String(2048), nullable=True, comment="OAuth refresh token (encrypted)"
    )

    id_token: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
        comment="OAuth ID token for OIDC providers (encrypted)",
    )

    token_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        default="Bearer",
        comment="Token type (usually Bearer)",
    )

    access_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="Access token expiration time"
    )

    refresh_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="Refresh token expiration time"
    )

    provider_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, comment="Full profile data from provider"
    )

    profile_picture_url: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="Profile picture URL from provider"
    )

    display_name: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="Display name from provider"
    )

    scopes: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, comment="List of granted OAuth scopes"
    )

    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="Primary OAuth account for user"
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        index=True,
        comment="Whether connection is active",
    )

    is_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether provider account is verified",
    )

    first_connected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default="now()",
        comment="When first connected",
    )

    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Last time used for authentication",
    )

    last_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="Last token refresh time"
    )

    connection_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="Number of times connected"
    )

    last_error: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="Last error message"
    )

    last_error_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="Last error timestamp"
    )

    error_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="Total error count"
    )

    # Relationships
    user = relationship("User", back_populates="oauth_accounts")

    # Constraints and indexes
    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id", name="uq_oauth_provider_user"),
        Index("idx_oauth_account_user_provider", "user_id", "provider", "is_active"),
        Index("idx_oauth_account_provider_email", "provider", "provider_email"),
        Index("idx_oauth_account_token_expiry", "access_token_expires_at"),
    )

    @classmethod
    def create_connection(
        cls,
        user_id: str,
        provider: OAuthProvider,
        provider_user_id: str,
        access_token: str,
        refresh_token: str | None = None,
        id_token: str | None = None,
        expires_in: int | None = None,
        provider_data: dict[str, Any] | None = None,
        scopes: list[str] | None = None,
    ) -> "OAuthAccount":
        """
        Create a new OAuth connection.

        Args:
            user_id: User ID
            provider: OAuth provider
            provider_user_id: Provider's user ID
            access_token: Access token (will be encrypted)
            refresh_token: Refresh token (will be encrypted)
            id_token: ID token for OIDC (will be encrypted)
            expires_in: Token expiration in seconds
            provider_data: Full provider profile data
            scopes: List of granted scopes

        Returns:
            OAuthAccount instance
        """
        # Calculate token expiration
        access_token_expires_at = None
        if expires_in:
            access_token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

        # Extract profile information from provider data
        provider_email = None
        provider_username = None
        display_name = None
        profile_picture_url = None
        is_verified = False

        if provider_data:
            # Common fields across providers
            provider_email = provider_data.get("email")
            is_verified = provider_data.get("email_verified", False)

            # Provider-specific field mapping
            if provider == OAuthProvider.GOOGLE:
                display_name = provider_data.get("name")
                profile_picture_url = provider_data.get("picture")
            elif provider == OAuthProvider.GITHUB:
                provider_username = provider_data.get("login")
                display_name = provider_data.get("name")
                profile_picture_url = provider_data.get("avatar_url")
            elif provider == OAuthProvider.MICROSOFT:
                display_name = provider_data.get("displayName")
                provider_username = provider_data.get("userPrincipalName")
            # Add more provider mappings as needed

        return cls(
            user_id=user_id,
            provider=provider,
            provider_user_id=provider_user_id,
            provider_username=provider_username,
            provider_email=provider_email,
            access_token=access_token,  # Should be encrypted before storage
            refresh_token=refresh_token,  # Should be encrypted before storage
            id_token=id_token,  # Should be encrypted before storage
            access_token_expires_at=access_token_expires_at,
            provider_data=provider_data,
            profile_picture_url=profile_picture_url,
            display_name=display_name,
            scopes=scopes,
            is_verified=is_verified,
        )

    def update_tokens(
        self,
        access_token: str,
        refresh_token: str | None = None,
        expires_in: int | None = None,
    ) -> None:
        """
        Update OAuth tokens.

        Args:
            access_token: New access token
            refresh_token: New refresh token
            expires_in: Token expiration in seconds
        """
        self.access_token = access_token  # Should be encrypted

        if refresh_token:
            self.refresh_token = refresh_token  # Should be encrypted

        if expires_in:
            self.access_token_expires_at = datetime.utcnow() + timedelta(
                seconds=expires_in
            )

        self.last_refreshed_at = datetime.utcnow()

    def record_usage(self) -> None:
        """Record OAuth account usage."""
        self.last_used_at = datetime.utcnow()
        self.connection_count += 1

    def record_error(self, error_message: str) -> None:
        """
        Record an error with this OAuth connection.

        Args:
            error_message: Error message
        """
        self.last_error = error_message[:500]  # Truncate to field length
        self.last_error_at = datetime.utcnow()
        self.error_count += 1

        # Deactivate if too many errors
        if self.error_count >= 10:
            self.is_active = False

    def disconnect(self) -> None:
        """Disconnect OAuth account."""
        self.is_active = False
        self.access_token = "REVOKED"
        self.refresh_token = None
        self.id_token = None

    def set_as_primary(self, session: Any = None) -> None:
        """
        Set this as the primary OAuth account for the user.

        Args:
            session: Database session
        """
        if session:
            # Remove primary flag from other accounts
            session.query(OAuthAccount).filter(
                OAuthAccount.user_id == self.user_id, OAuthAccount.id != self.id
            ).update({"is_primary": False})

        self.is_primary = True

    @property
    def is_token_expired(self) -> bool:
        """Check if access token is expired."""
        if not self.access_token_expires_at:
            return False
        return bool(datetime.utcnow() > self.access_token_expires_at)

    @property
    def needs_refresh(self) -> bool:
        """Check if token needs refresh."""
        if not self.access_token_expires_at:
            return False

        buffer_time = datetime.utcnow() + timedelta(minutes=5)
        return bool(buffer_time > self.access_token_expires_at)

    @property
    def days_since_last_use(self) -> int | None:
        """Get days since last use."""
        if not self.last_used_at:
            return None
        delta = datetime.utcnow() - self.last_used_at
        return delta.days

    @classmethod
    def get_by_provider(
        cls,
        user_id: str,
        provider: OAuthProvider,
        active_only: bool = True,
        session: Any = None,
    ) -> "OAuthAccount | None":
        """
        Get OAuth account by provider.

        Args:
            user_id: User ID
            provider: OAuth provider
            active_only: Only return active accounts
            session: Database session

        Returns:
            OAuthAccount instance or None
        """
        if not session:
            return None

        query = session.query(cls).filter(
            cls.user_id == user_id, cls.provider == provider
        )

        if active_only:
            query = query.filter(cls.is_active == True)

        result: OAuthAccount | None = query.first()
        return result

    @classmethod
    def find_by_provider_id(
        cls, provider: OAuthProvider, provider_user_id: str, session: Any = None
    ) -> "OAuthAccount | None":
        """
        Find OAuth account by provider and provider user ID.

        Args:
            provider: OAuth provider
            provider_user_id: Provider's user ID
            session: Database session

        Returns:
            OAuthAccount instance or None
        """
        if not session:
            return None

        result: OAuthAccount | None = (
            session.query(cls)
            .filter(cls.provider == provider, cls.provider_user_id == provider_user_id)
            .first()
        )
        return result

    def to_dict(self, include_tokens: bool = False) -> dict[str, Any]:
        """
        Convert to dictionary.

        Args:
            include_tokens: Include token information

        Returns:
            Dictionary representation
        """
        data = {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "provider": self.provider.value,
            "provider_username": self.provider_username,
            "provider_email": self.provider_email,
            "display_name": self.display_name,
            "profile_picture_url": self.profile_picture_url,
            "is_primary": self.is_primary,
            "is_active": self.is_active,
            "is_verified": self.is_verified,
            "scopes": self.scopes,
            "first_connected_at": (
                self.first_connected_at.isoformat() if self.first_connected_at is not None else None
            ),
            "last_used_at": (
                self.last_used_at.isoformat() if self.last_used_at is not None else None
            ),
            "connection_count": self.connection_count,
            "is_token_expired": self.is_token_expired,
            "needs_refresh": self.needs_refresh,
            "days_since_last_use": self.days_since_last_use,
        }

        if include_tokens:
            data["has_refresh_token"] = bool(self.refresh_token)
            data["token_expires_at"] = (
                self.access_token_expires_at.isoformat()
                if self.access_token_expires_at
                else None
            )

        return data

    def __repr__(self) -> str:
        """String representation."""
        return f"<OAuthAccount(id={self.id}, user={self.user_id}, provider={self.provider.value}, active={self.is_active})>"
