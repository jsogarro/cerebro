"""Remove unreachable duplicate authentication schema.

Revision ID: f8c9d0e1a2b3
Revises: c4a8d1e2f307
Create Date: 2026-08-31

The application owns password history in Redis and authenticates sessions with
JWTs.  The four tables removed here were only represented by dormant ORM
models.  The downgrade deliberately reproduces the parent migration's schema
so the schema change is reversible; rows removed by the upgrade cannot be
recovered by a schema downgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "f8c9d0e1a2b3"
down_revision = "c4a8d1e2f307"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop authentication tables with no live application owner."""
    op.drop_index(op.f("ix_mfa_settings_user_id"), table_name="mfa_settings")
    op.drop_index(op.f("ix_mfa_settings_is_enabled"), table_name="mfa_settings")
    op.drop_index(op.f("ix_mfa_settings_deleted_at"), table_name="mfa_settings")
    op.drop_index("idx_mfa_settings_phone", table_name="mfa_settings")
    op.drop_index("idx_mfa_settings_enabled", table_name="mfa_settings")
    op.drop_table("mfa_settings")

    op.drop_index(op.f("ix_user_sessions_user_id"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_session_token"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_refresh_token"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_is_active"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_expires_at"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_deleted_at"), table_name="user_sessions")
    op.drop_index("idx_user_session_device", table_name="user_sessions")
    op.drop_index("idx_user_session_activity", table_name="user_sessions")
    op.drop_index("idx_user_session_active", table_name="user_sessions")
    op.drop_table("user_sessions")

    op.drop_index(op.f("ix_password_history_user_id"), table_name="password_history")
    op.drop_index(op.f("ix_password_history_deleted_at"), table_name="password_history")
    op.drop_index("idx_password_history_user_created", table_name="password_history")
    op.drop_index("idx_password_history_expires", table_name="password_history")
    op.drop_table("password_history")

    op.drop_index(op.f("ix_api_keys_user_id"), table_name="api_keys")
    op.drop_index(op.f("ix_api_keys_last_used_at"), table_name="api_keys")
    op.drop_index(op.f("ix_api_keys_key_hash"), table_name="api_keys")
    op.drop_index(op.f("ix_api_keys_is_active"), table_name="api_keys")
    op.drop_index(op.f("ix_api_keys_expires_at"), table_name="api_keys")
    op.drop_index(op.f("ix_api_keys_deleted_at"), table_name="api_keys")
    op.drop_index("idx_apikey_user_active", table_name="api_keys")
    op.drop_index("idx_apikey_last_used", table_name="api_keys")
    op.drop_index("idx_apikey_expires", table_name="api_keys")
    op.drop_table("api_keys")

    op.execute("DROP TYPE mfa_method")


def downgrade() -> None:
    """Restore the exact authentication schema from the parent revision."""
    op.execute(
        "CREATE TYPE mfa_method AS ENUM "
        "('totp', 'sms', 'email', 'backup_codes', 'webauthn', 'push')"
    )

    op.create_table(
        "api_keys",
        sa.Column(
            "key_hash",
            sa.String(length=255),
            nullable=False,
            comment="SHA256 hash of the API key",
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "name",
            sa.String(length=255),
            nullable=False,
            comment="Descriptive name for the key",
        ),
        sa.Column(
            "description",
            sa.String(length=1000),
            nullable=True,
            comment="Detailed description of key purpose",
        ),
        sa.Column(
            "permissions",
            sa.JSON(),
            nullable=False,
            comment="List of permission strings",
        ),
        sa.Column(
            "rate_limit",
            sa.Integer(),
            nullable=True,
            comment="Requests per hour limit (null = use user default)",
        ),
        sa.Column(
            "allowed_ips",
            sa.JSON(),
            nullable=True,
            comment="List of allowed IP addresses/ranges",
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_ip", sa.String(length=45), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Key expiration time (null = never expires)",
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the key was revoked",
        ),
        sa.Column(
            "revoked_reason",
            sa.String(length=500),
            nullable=True,
            comment="Reason for revocation",
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_apikey_expires", "api_keys", ["expires_at", "is_active"])
    op.create_index("idx_apikey_last_used", "api_keys", ["last_used_at"])
    op.create_index("idx_apikey_user_active", "api_keys", ["user_id", "is_active"])
    op.create_index(op.f("ix_api_keys_deleted_at"), "api_keys", ["deleted_at"])
    op.create_index(op.f("ix_api_keys_expires_at"), "api_keys", ["expires_at"])
    op.create_index(op.f("ix_api_keys_is_active"), "api_keys", ["is_active"])
    op.create_index(op.f("ix_api_keys_key_hash"), "api_keys", ["key_hash"], unique=True)
    op.create_index(op.f("ix_api_keys_last_used_at"), "api_keys", ["last_used_at"])
    op.create_index(op.f("ix_api_keys_user_id"), "api_keys", ["user_id"])

    op.create_table(
        "password_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "hashed_password",
            sa.String(length=255),
            nullable=False,
            comment="Bcrypt hashed password",
        ),
        sa.Column(
            "changed_by",
            sa.String(length=255),
            nullable=True,
            comment="Who initiated the password change (user, admin, system)",
        ),
        sa.Column(
            "change_reason",
            sa.String(length=500),
            nullable=True,
            comment="Reason for password change (expired, reset, voluntary)",
        ),
        sa.Column(
            "ip_address",
            sa.String(length=45),
            nullable=True,
            comment="IP address from which password was changed",
        ),
        sa.Column(
            "user_agent",
            sa.String(length=500),
            nullable=True,
            comment="User agent string when password was changed",
        ),
        sa.Column(
            "password_strength",
            sa.Integer(),
            nullable=True,
            comment="Password strength score (0-100)",
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When this password expired (if applicable)",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_password_history_expires", "password_history", ["expires_at"])
    op.create_index(
        "idx_password_history_user_created",
        "password_history",
        ["user_id", "created_at"],
    )
    op.create_index(
        op.f("ix_password_history_deleted_at"), "password_history", ["deleted_at"]
    )
    op.create_index(
        op.f("ix_password_history_user_id"), "password_history", ["user_id"]
    )

    op.create_table(
        "user_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "session_token",
            sa.String(length=255),
            nullable=False,
            comment="Unique session token",
        ),
        sa.Column(
            "refresh_token",
            sa.String(length=255),
            nullable=True,
            comment="Refresh token for session renewal",
        ),
        sa.Column(
            "session_type",
            sa.String(length=50),
            nullable=False,
            comment="Session type (web, api, mobile, cli)",
        ),
        sa.Column(
            "device_id",
            sa.String(length=255),
            nullable=True,
            comment="Unique device identifier",
        ),
        sa.Column(
            "device_name",
            sa.String(length=255),
            nullable=True,
            comment="Device name (e.g., 'iPhone 12')",
        ),
        sa.Column(
            "device_type",
            sa.String(length=50),
            nullable=True,
            comment="Device type (desktop, mobile, tablet)",
        ),
        sa.Column(
            "os_name",
            sa.String(length=100),
            nullable=True,
            comment="Operating system name",
        ),
        sa.Column(
            "os_version",
            sa.String(length=50),
            nullable=True,
            comment="Operating system version",
        ),
        sa.Column(
            "browser_name",
            sa.String(length=100),
            nullable=True,
            comment="Browser name",
        ),
        sa.Column(
            "browser_version",
            sa.String(length=50),
            nullable=True,
            comment="Browser version",
        ),
        sa.Column(
            "user_agent",
            sa.String(length=500),
            nullable=True,
            comment="Full user agent string",
        ),
        sa.Column(
            "ip_address",
            sa.String(length=45),
            nullable=False,
            comment="Client IP address",
        ),
        sa.Column(
            "country",
            sa.String(length=100),
            nullable=True,
            comment="Country from IP geolocation",
        ),
        sa.Column(
            "region",
            sa.String(length=100),
            nullable=True,
            comment="Region/state from IP geolocation",
        ),
        sa.Column(
            "city",
            sa.String(length=100),
            nullable=True,
            comment="City from IP geolocation",
        ),
        sa.Column(
            "latitude",
            sa.String(length=20),
            nullable=True,
            comment="Latitude from IP geolocation",
        ),
        sa.Column(
            "longitude",
            sa.String(length=20),
            nullable=True,
            comment="Longitude from IP geolocation",
        ),
        sa.Column(
            "last_activity",
            sa.DateTime(timezone=True),
            server_default="now()",
            nullable=False,
            comment="Last activity timestamp",
        ),
        sa.Column(
            "last_ip_address",
            sa.String(length=45),
            nullable=True,
            comment="Last known IP address",
        ),
        sa.Column(
            "request_count",
            sa.Integer(),
            nullable=False,
            comment="Total requests in this session",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            comment="Whether session is active",
        ),
        sa.Column(
            "is_suspicious",
            sa.Boolean(),
            nullable=False,
            comment="Flag for suspicious activity",
        ),
        sa.Column(
            "mfa_verified",
            sa.Boolean(),
            nullable=False,
            comment="Whether MFA was verified for this session",
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Session expiration time",
        ),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When session was revoked",
        ),
        sa.Column(
            "revoke_reason",
            sa.String(length=255),
            nullable=True,
            comment="Reason for session revocation",
        ),
        sa.Column(
            "metadata",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=True,
            comment="Additional session metadata",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("refresh_token"),
        sa.UniqueConstraint("session_token"),
    )
    op.create_index(
        "idx_user_session_active",
        "user_sessions",
        ["user_id", "is_active", "expires_at"],
    )
    op.create_index(
        "idx_user_session_activity",
        "user_sessions",
        ["last_activity", "is_active"],
    )
    op.create_index(
        "idx_user_session_device", "user_sessions", ["device_id", "user_id"]
    )
    op.create_index(
        op.f("ix_user_sessions_deleted_at"), "user_sessions", ["deleted_at"]
    )
    op.create_index(
        op.f("ix_user_sessions_expires_at"), "user_sessions", ["expires_at"]
    )
    op.create_index(op.f("ix_user_sessions_is_active"), "user_sessions", ["is_active"])
    op.create_index(
        op.f("ix_user_sessions_refresh_token"), "user_sessions", ["refresh_token"]
    )
    op.create_index(
        op.f("ix_user_sessions_session_token"), "user_sessions", ["session_token"]
    )
    op.create_index(op.f("ix_user_sessions_user_id"), "user_sessions", ["user_id"])

    op.create_table(
        "mfa_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            nullable=False,
            comment="Whether MFA is enabled for the user",
        ),
        sa.Column(
            "is_enforced",
            sa.Boolean(),
            nullable=False,
            comment="Whether MFA is enforced (cannot be disabled by user)",
        ),
        sa.Column(
            "primary_method",
            postgresql.ENUM(
                "totp",
                "sms",
                "email",
                "backup_codes",
                "webauthn",
                "push",
                name="mfa_method",
                create_type=False,
            ),
            nullable=True,
            comment="Primary MFA method",
        ),
        sa.Column(
            "enabled_methods",
            postgresql.ARRAY(sa.String()),
            nullable=True,
            comment="List of enabled MFA methods",
        ),
        sa.Column(
            "totp_secret",
            sa.String(length=255),
            nullable=True,
            comment="TOTP secret key (encrypted)",
        ),
        sa.Column(
            "totp_verified",
            sa.Boolean(),
            nullable=False,
            comment="Whether TOTP has been verified",
        ),
        sa.Column(
            "totp_last_used",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Last time TOTP was used",
        ),
        sa.Column(
            "totp_counter",
            sa.Integer(),
            nullable=False,
            comment="TOTP counter for preventing replay attacks",
        ),
        sa.Column(
            "sms_phone_number",
            sa.String(length=20),
            nullable=True,
            comment="Phone number for SMS (encrypted)",
        ),
        sa.Column(
            "sms_verified",
            sa.Boolean(),
            nullable=False,
            comment="Whether SMS number has been verified",
        ),
        sa.Column(
            "sms_last_sent",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Last time SMS was sent",
        ),
        sa.Column(
            "sms_send_count",
            sa.Integer(),
            nullable=False,
            comment="Number of SMS codes sent",
        ),
        sa.Column(
            "email_verified",
            sa.Boolean(),
            nullable=False,
            comment="Whether email MFA has been verified",
        ),
        sa.Column(
            "email_last_sent",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Last time email code was sent",
        ),
        sa.Column(
            "backup_codes",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=True,
            comment="Hashed backup codes",
        ),
        sa.Column(
            "backup_codes_generated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When backup codes were generated",
        ),
        sa.Column(
            "backup_codes_used",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=True,
            comment="List of used backup code indices",
        ),
        sa.Column(
            "webauthn_credentials",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=True,
            comment="WebAuthn credential data",
        ),
        sa.Column(
            "recovery_email",
            sa.String(length=255),
            nullable=True,
            comment="Alternative email for recovery (encrypted)",
        ),
        sa.Column(
            "recovery_phone",
            sa.String(length=20),
            nullable=True,
            comment="Alternative phone for recovery (encrypted)",
        ),
        sa.Column(
            "require_mfa_for_sensitive",
            sa.Boolean(),
            nullable=False,
            comment="Require MFA for sensitive operations",
        ),
        sa.Column(
            "trusted_devices",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=True,
            comment="List of trusted device IDs",
        ),
        sa.Column(
            "successful_verifications",
            sa.Integer(),
            nullable=False,
            comment="Total successful MFA verifications",
        ),
        sa.Column(
            "failed_attempts",
            sa.Integer(),
            nullable=False,
            comment="Total failed MFA attempts",
        ),
        sa.Column(
            "last_verified_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Last successful MFA verification",
        ),
        sa.Column(
            "last_failed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Last failed MFA attempt",
        ),
        sa.Column(
            "temp_setup_code",
            sa.String(length=255),
            nullable=True,
            comment="Temporary code for MFA setup",
        ),
        sa.Column(
            "temp_setup_expires",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When temp setup code expires",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(
        "idx_mfa_settings_enabled", "mfa_settings", ["is_enabled", "primary_method"]
    )
    op.create_index("idx_mfa_settings_phone", "mfa_settings", ["sms_phone_number"])
    op.create_index(op.f("ix_mfa_settings_deleted_at"), "mfa_settings", ["deleted_at"])
    op.create_index(op.f("ix_mfa_settings_is_enabled"), "mfa_settings", ["is_enabled"])
    op.create_index(op.f("ix_mfa_settings_user_id"), "mfa_settings", ["user_id"])
