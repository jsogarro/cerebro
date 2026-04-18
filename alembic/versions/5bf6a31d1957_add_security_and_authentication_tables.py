"""Add security and authentication tables

Revision ID: 5bf6a31d1957
Revises: 4141aabbf97d
Create Date: 2025-08-17

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '5bf6a31d1957'
down_revision: str | None = '4141aabbf97d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add security and authentication tables."""
    
    # Create ENUMs
    op.execute("CREATE TYPE audit_event_type AS ENUM ('login_success', 'login_failed', 'logout', 'password_change', 'password_reset_request', 'password_reset_complete', 'account_created', 'account_activated', 'account_deactivated', 'account_deleted', 'account_locked', 'account_unlocked', 'email_verified', 'email_changed', 'mfa_enabled', 'mfa_disabled', 'mfa_verified', 'mfa_failed', 'mfa_backup_used', 'oauth_connected', 'oauth_disconnected', 'oauth_login', 'session_created', 'session_refreshed', 'session_revoked', 'session_expired', 'api_key_created', 'api_key_revoked', 'api_key_used', 'permission_granted', 'permission_revoked', 'role_assigned', 'role_removed', 'suspicious_activity', 'rate_limit_exceeded', 'invalid_token', 'unauthorized_access', 'data_accessed', 'data_modified', 'data_deleted', 'data_exported')")
    op.execute("CREATE TYPE audit_severity AS ENUM ('info', 'warning', 'error', 'critical')")
    op.execute("CREATE TYPE oauth_provider AS ENUM ('google', 'github', 'microsoft', 'facebook', 'linkedin', 'twitter', 'apple', 'gitlab', 'bitbucket', 'okta', 'auth0')")
    op.execute("CREATE TYPE mfa_method AS ENUM ('totp', 'sms', 'email', 'backup_codes', 'webauthn', 'push')")
    op.execute("CREATE TYPE alert_type AS ENUM ('suspicious_login', 'failed_login_attempts', 'new_device_login', 'new_location_login', 'password_changed', 'password_reset', 'account_locked', 'account_unlocked', 'account_deactivated', 'privilege_escalation', 'mfa_disabled', 'mfa_method_changed', 'mfa_bypass_attempt', 'concurrent_sessions', 'session_hijacking', 'unusual_activity', 'api_rate_limit', 'api_key_compromised', 'unauthorized_api_access', 'bulk_data_access', 'sensitive_data_access', 'data_exfiltration', 'brute_force_attack', 'sql_injection_attempt', 'xss_attempt', 'system_breach')")
    op.execute("CREATE TYPE alert_severity AS ENUM ('low', 'medium', 'high', 'critical')")
    op.execute("CREATE TYPE alert_status AS ENUM ('new', 'acknowledged', 'investigating', 'resolved', 'false_positive', 'escalated')")
    
    # Create password_history table
    op.create_table('password_history',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('hashed_password', sa.String(length=255), nullable=False, comment='Bcrypt hashed password'),
        sa.Column('changed_by', sa.String(length=255), nullable=True, comment='Who initiated the password change (user, admin, system)'),
        sa.Column('change_reason', sa.String(length=500), nullable=True, comment='Reason for password change (expired, reset, voluntary)'),
        sa.Column('ip_address', sa.String(length=45), nullable=True, comment='IP address from which password was changed'),
        sa.Column('user_agent', sa.String(length=500), nullable=True, comment='User agent string when password was changed'),
        sa.Column('password_strength', sa.Integer(), nullable=True, comment='Password strength score (0-100)'),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True, comment='When this password expired (if applicable)'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_password_history_expires', 'password_history', ['expires_at'], unique=False)
    op.create_index('idx_password_history_user_created', 'password_history', ['user_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_password_history_deleted_at'), 'password_history', ['deleted_at'], unique=False)
    op.create_index(op.f('ix_password_history_user_id'), 'password_history', ['user_id'], unique=False)
    
    # Create user_sessions table
    op.create_table('user_sessions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('session_token', sa.String(length=255), nullable=False, comment='Unique session token'),
        sa.Column('refresh_token', sa.String(length=255), nullable=True, comment='Refresh token for session renewal'),
        sa.Column('session_type', sa.String(length=50), nullable=False, comment='Session type (web, api, mobile, cli)'),
        sa.Column('device_id', sa.String(length=255), nullable=True, comment='Unique device identifier'),
        sa.Column('device_name', sa.String(length=255), nullable=True, comment="Device name (e.g., 'iPhone 12')"),
        sa.Column('device_type', sa.String(length=50), nullable=True, comment='Device type (desktop, mobile, tablet)'),
        sa.Column('os_name', sa.String(length=100), nullable=True, comment='Operating system name'),
        sa.Column('os_version', sa.String(length=50), nullable=True, comment='Operating system version'),
        sa.Column('browser_name', sa.String(length=100), nullable=True, comment='Browser name'),
        sa.Column('browser_version', sa.String(length=50), nullable=True, comment='Browser version'),
        sa.Column('user_agent', sa.String(length=500), nullable=True, comment='Full user agent string'),
        sa.Column('ip_address', sa.String(length=45), nullable=False, comment='Client IP address'),
        sa.Column('country', sa.String(length=100), nullable=True, comment='Country from IP geolocation'),
        sa.Column('region', sa.String(length=100), nullable=True, comment='Region/state from IP geolocation'),
        sa.Column('city', sa.String(length=100), nullable=True, comment='City from IP geolocation'),
        sa.Column('latitude', sa.String(length=20), nullable=True, comment='Latitude from IP geolocation'),
        sa.Column('longitude', sa.String(length=20), nullable=True, comment='Longitude from IP geolocation'),
        sa.Column('last_activity', sa.DateTime(timezone=True), server_default='now()', nullable=False, comment='Last activity timestamp'),
        sa.Column('last_ip_address', sa.String(length=45), nullable=True, comment='Last known IP address'),
        sa.Column('request_count', sa.Integer(), nullable=False, comment='Total requests in this session'),
        sa.Column('is_active', sa.Boolean(), nullable=False, comment='Whether session is active'),
        sa.Column('is_suspicious', sa.Boolean(), nullable=False, comment='Flag for suspicious activity'),
        sa.Column('mfa_verified', sa.Boolean(), nullable=False, comment='Whether MFA was verified for this session'),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False, comment='Session expiration time'),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True, comment='When session was revoked'),
        sa.Column('revoke_reason', sa.String(length=255), nullable=True, comment='Reason for session revocation'),
        sa.Column('metadata', postgresql.JSON(astext_type=sa.Text()), nullable=True, comment='Additional session metadata'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('refresh_token'),
        sa.UniqueConstraint('session_token')
    )
    op.create_index('idx_user_session_active', 'user_sessions', ['user_id', 'is_active', 'expires_at'], unique=False)
    op.create_index('idx_user_session_activity', 'user_sessions', ['last_activity', 'is_active'], unique=False)
    op.create_index('idx_user_session_device', 'user_sessions', ['device_id', 'user_id'], unique=False)
    op.create_index(op.f('ix_user_sessions_deleted_at'), 'user_sessions', ['deleted_at'], unique=False)
    op.create_index(op.f('ix_user_sessions_expires_at'), 'user_sessions', ['expires_at'], unique=False)
    op.create_index(op.f('ix_user_sessions_is_active'), 'user_sessions', ['is_active'], unique=False)
    op.create_index(op.f('ix_user_sessions_refresh_token'), 'user_sessions', ['refresh_token'], unique=False)
    op.create_index(op.f('ix_user_sessions_session_token'), 'user_sessions', ['session_token'], unique=False)
    op.create_index(op.f('ix_user_sessions_user_id'), 'user_sessions', ['user_id'], unique=False)
    
    # Create audit_logs table
    op.create_table('audit_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.Column('event_type', postgresql.ENUM('login_success', 'login_failed', 'logout', 'password_change', 'password_reset_request', 'password_reset_complete', 'account_created', 'account_activated', 'account_deactivated', 'account_deleted', 'account_locked', 'account_unlocked', 'email_verified', 'email_changed', 'mfa_enabled', 'mfa_disabled', 'mfa_verified', 'mfa_failed', 'mfa_backup_used', 'oauth_connected', 'oauth_disconnected', 'oauth_login', 'session_created', 'session_refreshed', 'session_revoked', 'session_expired', 'api_key_created', 'api_key_revoked', 'api_key_used', 'permission_granted', 'permission_revoked', 'role_assigned', 'role_removed', 'suspicious_activity', 'rate_limit_exceeded', 'invalid_token', 'unauthorized_access', 'data_accessed', 'data_modified', 'data_deleted', 'data_exported', name='audit_event_type'), nullable=False, comment='Type of audit event'),
        sa.Column('severity', postgresql.ENUM('info', 'warning', 'error', 'critical', name='audit_severity'), nullable=False, comment='Event severity level'),
        sa.Column('event_category', sa.String(length=50), nullable=True, comment='Event category for grouping'),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('username', sa.String(length=100), nullable=True, comment='Username at time of event (denormalized)'),
        sa.Column('email', sa.String(length=255), nullable=True, comment='Email at time of event (denormalized)'),
        sa.Column('actor_id', postgresql.UUID(as_uuid=True), nullable=True, comment='ID of user who performed action (for admin actions)'),
        sa.Column('actor_username', sa.String(length=100), nullable=True, comment='Username of actor'),
        sa.Column('resource_type', sa.String(length=50), nullable=True, comment='Type of resource affected'),
        sa.Column('resource_id', sa.String(length=255), nullable=True, comment='ID of resource affected'),
        sa.Column('resource_name', sa.String(length=255), nullable=True, comment='Name/description of resource'),
        sa.Column('action', sa.String(length=100), nullable=False, comment='Action performed'),
        sa.Column('description', sa.Text(), nullable=True, comment='Detailed event description'),
        sa.Column('result', sa.String(length=50), nullable=True, comment='Result of action (success, failure, partial)'),
        sa.Column('error_message', sa.Text(), nullable=True, comment='Error message if action failed'),
        sa.Column('ip_address', sa.String(length=45), nullable=True, comment='Client IP address'),
        sa.Column('user_agent', sa.String(length=500), nullable=True, comment='User agent string'),
        sa.Column('request_id', sa.String(length=100), nullable=True, comment='Request correlation ID'),
        sa.Column('session_id', sa.String(length=255), nullable=True, comment='Session ID if applicable'),
        sa.Column('country', sa.String(length=100), nullable=True, comment='Country from IP geolocation'),
        sa.Column('city', sa.String(length=100), nullable=True, comment='City from IP geolocation'),
        sa.Column('metadata', postgresql.JSON(astext_type=sa.Text()), nullable=True, comment='Additional event metadata'),
        sa.Column('is_suspicious', sa.Boolean(), nullable=False, comment='Flag for suspicious activity'),
        sa.Column('requires_review', sa.Boolean(), nullable=False, comment='Flag for manual review required'),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True, comment='When event was reviewed'),
        sa.Column('reviewed_by', sa.String(length=255), nullable=True, comment='Who reviewed the event'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_audit_log_ip', 'audit_logs', ['ip_address', 'created_at'], unique=False)
    op.create_index('idx_audit_log_resource', 'audit_logs', ['resource_type', 'resource_id'], unique=False)
    op.create_index('idx_audit_log_suspicious', 'audit_logs', ['is_suspicious', 'requires_review'], unique=False)
    op.create_index('idx_audit_log_timestamp', 'audit_logs', ['created_at'], unique=False)
    op.create_index('idx_audit_log_user_event', 'audit_logs', ['user_id', 'event_type', 'created_at'], unique=False)
    op.create_index(op.f('ix_audit_logs_deleted_at'), 'audit_logs', ['deleted_at'], unique=False)
    op.create_index(op.f('ix_audit_logs_event_category'), 'audit_logs', ['event_category'], unique=False)
    op.create_index(op.f('ix_audit_logs_event_type'), 'audit_logs', ['event_type'], unique=False)
    op.create_index(op.f('ix_audit_logs_ip_address'), 'audit_logs', ['ip_address'], unique=False)
    op.create_index(op.f('ix_audit_logs_is_suspicious'), 'audit_logs', ['is_suspicious'], unique=False)
    op.create_index(op.f('ix_audit_logs_request_id'), 'audit_logs', ['request_id'], unique=False)
    op.create_index(op.f('ix_audit_logs_requires_review'), 'audit_logs', ['requires_review'], unique=False)
    op.create_index(op.f('ix_audit_logs_resource_id'), 'audit_logs', ['resource_id'], unique=False)
    op.create_index(op.f('ix_audit_logs_resource_type'), 'audit_logs', ['resource_type'], unique=False)
    op.create_index(op.f('ix_audit_logs_severity'), 'audit_logs', ['severity'], unique=False)
    op.create_index(op.f('ix_audit_logs_user_id'), 'audit_logs', ['user_id'], unique=False)
    
    # Create oauth_accounts table
    op.create_table('oauth_accounts',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('provider', postgresql.ENUM('google', 'github', 'microsoft', 'facebook', 'linkedin', 'twitter', 'apple', 'gitlab', 'bitbucket', 'okta', 'auth0', name='oauth_provider'), nullable=False, comment='OAuth provider name'),
        sa.Column('provider_user_id', sa.String(length=255), nullable=False, comment='User ID from OAuth provider'),
        sa.Column('provider_username', sa.String(length=255), nullable=True, comment='Username from OAuth provider'),
        sa.Column('provider_email', sa.String(length=255), nullable=True, comment='Email from OAuth provider'),
        sa.Column('access_token', sa.String(length=2048), nullable=False, comment='OAuth access token (encrypted)'),
        sa.Column('refresh_token', sa.String(length=2048), nullable=True, comment='OAuth refresh token (encrypted)'),
        sa.Column('id_token', sa.String(length=2048), nullable=True, comment='OAuth ID token for OIDC providers (encrypted)'),
        sa.Column('token_type', sa.String(length=50), nullable=True, comment='Token type (usually Bearer)'),
        sa.Column('access_token_expires_at', sa.DateTime(timezone=True), nullable=True, comment='Access token expiration time'),
        sa.Column('refresh_token_expires_at', sa.DateTime(timezone=True), nullable=True, comment='Refresh token expiration time'),
        sa.Column('provider_data', postgresql.JSON(astext_type=sa.Text()), nullable=True, comment='Full profile data from provider'),
        sa.Column('profile_picture_url', sa.String(length=500), nullable=True, comment='Profile picture URL from provider'),
        sa.Column('display_name', sa.String(length=255), nullable=True, comment='Display name from provider'),
        sa.Column('scopes', postgresql.JSON(astext_type=sa.Text()), nullable=True, comment='List of granted OAuth scopes'),
        sa.Column('is_primary', sa.Boolean(), nullable=False, comment='Primary OAuth account for user'),
        sa.Column('is_active', sa.Boolean(), nullable=False, comment='Whether connection is active'),
        sa.Column('is_verified', sa.Boolean(), nullable=False, comment='Whether provider account is verified'),
        sa.Column('first_connected_at', sa.DateTime(timezone=True), server_default='now()', nullable=False, comment='When first connected'),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True, comment='Last time used for authentication'),
        sa.Column('last_refreshed_at', sa.DateTime(timezone=True), nullable=True, comment='Last token refresh time'),
        sa.Column('connection_count', sa.Integer(), nullable=False, comment='Number of times connected'),
        sa.Column('last_error', sa.String(length=500), nullable=True, comment='Last error message'),
        sa.Column('last_error_at', sa.DateTime(timezone=True), nullable=True, comment='Last error timestamp'),
        sa.Column('error_count', sa.Integer(), nullable=False, comment='Total error count'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider', 'provider_user_id', name='uq_oauth_provider_user')
    )
    op.create_index('idx_oauth_account_provider_email', 'oauth_accounts', ['provider', 'provider_email'], unique=False)
    op.create_index('idx_oauth_account_token_expiry', 'oauth_accounts', ['access_token_expires_at'], unique=False)
    op.create_index('idx_oauth_account_user_provider', 'oauth_accounts', ['user_id', 'provider', 'is_active'], unique=False)
    op.create_index(op.f('ix_oauth_accounts_deleted_at'), 'oauth_accounts', ['deleted_at'], unique=False)
    op.create_index(op.f('ix_oauth_accounts_is_active'), 'oauth_accounts', ['is_active'], unique=False)
    op.create_index(op.f('ix_oauth_accounts_provider'), 'oauth_accounts', ['provider'], unique=False)
    op.create_index(op.f('ix_oauth_accounts_user_id'), 'oauth_accounts', ['user_id'], unique=False)
    
    # Create mfa_settings table
    op.create_table('mfa_settings',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('is_enabled', sa.Boolean(), nullable=False, comment='Whether MFA is enabled for the user'),
        sa.Column('is_enforced', sa.Boolean(), nullable=False, comment='Whether MFA is enforced (cannot be disabled by user)'),
        sa.Column('primary_method', postgresql.ENUM('totp', 'sms', 'email', 'backup_codes', 'webauthn', 'push', name='mfa_method'), nullable=True, comment='Primary MFA method'),
        sa.Column('enabled_methods', postgresql.ARRAY(sa.String()), nullable=True, comment='List of enabled MFA methods'),
        sa.Column('totp_secret', sa.String(length=255), nullable=True, comment='TOTP secret key (encrypted)'),
        sa.Column('totp_verified', sa.Boolean(), nullable=False, comment='Whether TOTP has been verified'),
        sa.Column('totp_last_used', sa.DateTime(timezone=True), nullable=True, comment='Last time TOTP was used'),
        sa.Column('totp_counter', sa.Integer(), nullable=False, comment='TOTP counter for preventing replay attacks'),
        sa.Column('sms_phone_number', sa.String(length=20), nullable=True, comment='Phone number for SMS (encrypted)'),
        sa.Column('sms_verified', sa.Boolean(), nullable=False, comment='Whether SMS number has been verified'),
        sa.Column('sms_last_sent', sa.DateTime(timezone=True), nullable=True, comment='Last time SMS was sent'),
        sa.Column('sms_send_count', sa.Integer(), nullable=False, comment='Number of SMS codes sent'),
        sa.Column('email_verified', sa.Boolean(), nullable=False, comment='Whether email MFA has been verified'),
        sa.Column('email_last_sent', sa.DateTime(timezone=True), nullable=True, comment='Last time email code was sent'),
        sa.Column('backup_codes', postgresql.JSON(astext_type=sa.Text()), nullable=True, comment='Hashed backup codes'),
        sa.Column('backup_codes_generated_at', sa.DateTime(timezone=True), nullable=True, comment='When backup codes were generated'),
        sa.Column('backup_codes_used', postgresql.JSON(astext_type=sa.Text()), nullable=True, comment='List of used backup code indices'),
        sa.Column('webauthn_credentials', postgresql.JSON(astext_type=sa.Text()), nullable=True, comment='WebAuthn credential data'),
        sa.Column('recovery_email', sa.String(length=255), nullable=True, comment='Alternative email for recovery (encrypted)'),
        sa.Column('recovery_phone', sa.String(length=20), nullable=True, comment='Alternative phone for recovery (encrypted)'),
        sa.Column('require_mfa_for_sensitive', sa.Boolean(), nullable=False, comment='Require MFA for sensitive operations'),
        sa.Column('trusted_devices', postgresql.JSON(astext_type=sa.Text()), nullable=True, comment='List of trusted device IDs'),
        sa.Column('successful_verifications', sa.Integer(), nullable=False, comment='Total successful MFA verifications'),
        sa.Column('failed_attempts', sa.Integer(), nullable=False, comment='Total failed MFA attempts'),
        sa.Column('last_verified_at', sa.DateTime(timezone=True), nullable=True, comment='Last successful MFA verification'),
        sa.Column('last_failed_at', sa.DateTime(timezone=True), nullable=True, comment='Last failed MFA attempt'),
        sa.Column('temp_setup_code', sa.String(length=255), nullable=True, comment='Temporary code for MFA setup'),
        sa.Column('temp_setup_expires', sa.DateTime(timezone=True), nullable=True, comment='When temp setup code expires'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id')
    )
    op.create_index('idx_mfa_settings_enabled', 'mfa_settings', ['is_enabled', 'primary_method'], unique=False)
    op.create_index('idx_mfa_settings_phone', 'mfa_settings', ['sms_phone_number'], unique=False)
    op.create_index(op.f('ix_mfa_settings_deleted_at'), 'mfa_settings', ['deleted_at'], unique=False)
    op.create_index(op.f('ix_mfa_settings_is_enabled'), 'mfa_settings', ['is_enabled'], unique=False)
    op.create_index(op.f('ix_mfa_settings_user_id'), 'mfa_settings', ['user_id'], unique=False)
    
    # Create security_alerts table
    op.create_table('security_alerts',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.Column('alert_type', postgresql.ENUM('suspicious_login', 'failed_login_attempts', 'new_device_login', 'new_location_login', 'password_changed', 'password_reset', 'account_locked', 'account_unlocked', 'account_deactivated', 'privilege_escalation', 'mfa_disabled', 'mfa_method_changed', 'mfa_bypass_attempt', 'concurrent_sessions', 'session_hijacking', 'unusual_activity', 'api_rate_limit', 'api_key_compromised', 'unauthorized_api_access', 'bulk_data_access', 'sensitive_data_access', 'data_exfiltration', 'brute_force_attack', 'sql_injection_attempt', 'xss_attempt', 'system_breach', name='alert_type'), nullable=False, comment='Type of security alert'),
        sa.Column('severity', postgresql.ENUM('low', 'medium', 'high', 'critical', name='alert_severity'), nullable=False, comment='Alert severity level'),
        sa.Column('status', postgresql.ENUM('new', 'acknowledged', 'investigating', 'resolved', 'false_positive', 'escalated', name='alert_status'), nullable=False, comment='Current alert status'),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('username', sa.String(length=100), nullable=True, comment='Username at time of alert (denormalized)'),
        sa.Column('email', sa.String(length=255), nullable=True, comment='Email at time of alert (denormalized)'),
        sa.Column('title', sa.String(length=255), nullable=False, comment='Alert title'),
        sa.Column('description', sa.Text(), nullable=False, comment='Detailed alert description'),
        sa.Column('ip_address', sa.String(length=45), nullable=True, comment='Source IP address'),
        sa.Column('user_agent', sa.String(length=500), nullable=True, comment='User agent string'),
        sa.Column('request_path', sa.String(length=500), nullable=True, comment='Request path/endpoint'),
        sa.Column('request_method', sa.String(length=10), nullable=True, comment='HTTP request method'),
        sa.Column('country', sa.String(length=100), nullable=True, comment='Country from IP geolocation'),
        sa.Column('city', sa.String(length=100), nullable=True, comment='City from IP geolocation'),
        sa.Column('is_known_location', sa.Boolean(), nullable=False, comment='Whether location is known for user'),
        sa.Column('risk_score', sa.Integer(), nullable=True, comment='Calculated risk score (0-100)'),
        sa.Column('confidence_score', sa.Integer(), nullable=True, comment='Alert confidence score (0-100)'),
        sa.Column('is_automated', sa.Boolean(), nullable=False, comment='Whether alert was automatically generated'),
        sa.Column('related_alerts', postgresql.JSON(astext_type=sa.Text()), nullable=True, comment='IDs of related alerts'),
        sa.Column('affected_resources', postgresql.JSON(astext_type=sa.Text()), nullable=True, comment='List of affected resources'),
        sa.Column('evidence', postgresql.JSON(astext_type=sa.Text()), nullable=True, comment='Supporting evidence/logs'),
        sa.Column('actions_taken', postgresql.JSON(astext_type=sa.Text()), nullable=True, comment='List of actions taken'),
        sa.Column('auto_remediated', sa.Boolean(), nullable=False, comment='Whether automatically remediated'),
        sa.Column('remediation_steps', sa.Text(), nullable=True, comment='Recommended remediation steps'),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True, comment='When alert was acknowledged'),
        sa.Column('acknowledged_by', sa.String(length=255), nullable=True, comment='Who acknowledged the alert'),
        sa.Column('investigated_at', sa.DateTime(timezone=True), nullable=True, comment='When investigation started'),
        sa.Column('investigated_by', sa.String(length=255), nullable=True, comment='Who investigated the alert'),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True, comment='When alert was resolved'),
        sa.Column('resolved_by', sa.String(length=255), nullable=True, comment='Who resolved the alert'),
        sa.Column('resolution_notes', sa.Text(), nullable=True, comment='Resolution notes'),
        sa.Column('notifications_sent', postgresql.JSON(astext_type=sa.Text()), nullable=True, comment='List of sent notifications'),
        sa.Column('email_sent', sa.Boolean(), nullable=False, comment='Whether email notification was sent'),
        sa.Column('sms_sent', sa.Boolean(), nullable=False, comment='Whether SMS notification was sent'),
        sa.Column('escalated', sa.Boolean(), nullable=False, comment='Whether alert was escalated'),
        sa.Column('escalated_at', sa.DateTime(timezone=True), nullable=True, comment='When alert was escalated'),
        sa.Column('escalated_to', sa.String(length=255), nullable=True, comment='Who/where alert was escalated to'),
        sa.Column('metadata', postgresql.JSON(astext_type=sa.Text()), nullable=True, comment='Additional alert metadata'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_security_alert_escalated', 'security_alerts', ['escalated', 'status'], unique=False)
    op.create_index('idx_security_alert_ip', 'security_alerts', ['ip_address', 'created_at'], unique=False)
    op.create_index('idx_security_alert_status_severity', 'security_alerts', ['status', 'severity'], unique=False)
    op.create_index('idx_security_alert_user_type', 'security_alerts', ['user_id', 'alert_type', 'created_at'], unique=False)
    op.create_index(op.f('ix_security_alerts_alert_type'), 'security_alerts', ['alert_type'], unique=False)
    op.create_index(op.f('ix_security_alerts_deleted_at'), 'security_alerts', ['deleted_at'], unique=False)
    op.create_index(op.f('ix_security_alerts_escalated'), 'security_alerts', ['escalated'], unique=False)
    op.create_index(op.f('ix_security_alerts_ip_address'), 'security_alerts', ['ip_address'], unique=False)
    op.create_index(op.f('ix_security_alerts_severity'), 'security_alerts', ['severity'], unique=False)
    op.create_index(op.f('ix_security_alerts_status'), 'security_alerts', ['status'], unique=False)
    op.create_index(op.f('ix_security_alerts_user_id'), 'security_alerts', ['user_id'], unique=False)


def downgrade() -> None:
    """Remove security and authentication tables."""
    
    # Drop tables
    op.drop_table('security_alerts')
    op.drop_table('mfa_settings')
    op.drop_table('oauth_accounts')
    op.drop_table('audit_logs')
    op.drop_table('user_sessions')
    op.drop_table('password_history')
    
    # Drop ENUMs
    op.execute('DROP TYPE IF EXISTS alert_status')
    op.execute('DROP TYPE IF EXISTS alert_severity')
    op.execute('DROP TYPE IF EXISTS alert_type')
    op.execute('DROP TYPE IF EXISTS mfa_method')
    op.execute('DROP TYPE IF EXISTS oauth_provider')
    op.execute('DROP TYPE IF EXISTS audit_severity')
    op.execute('DROP TYPE IF EXISTS audit_event_type')