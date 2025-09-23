"""
Multi-factor authentication settings database model.

Manages MFA configuration including TOTP, SMS, email, and backup codes.
"""

import secrets
from datetime import datetime
from enum import Enum

import pyotp
from passlib.context import CryptContext
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, UUID
from sqlalchemy.orm import relationship

from src.models.db.base import BaseModel

# Password hashing for backup codes
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class MFAMethod(str, Enum):
    """Supported MFA methods."""

    TOTP = "totp"  # Time-based One-Time Password (Google Authenticator, etc.)
    SMS = "sms"  # SMS text message
    EMAIL = "email"  # Email verification code
    BACKUP_CODES = "backup_codes"  # Pre-generated backup codes
    WEBAUTHN = "webauthn"  # WebAuthn/FIDO2 security keys
    PUSH = "push"  # Push notifications to mobile app


class MFASettings(BaseModel):
    """
    MFA settings model.

    Stores multi-factor authentication configuration and
    recovery options for user accounts.
    """

    __tablename__ = "mfa_settings"

    # Foreign key to user (one-to-one relationship)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # MFA status
    is_enabled = Column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
        comment="Whether MFA is enabled for the user",
    )

    is_enforced = Column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether MFA is enforced (cannot be disabled by user)",
    )

    # Primary MFA method
    primary_method = Column(
        ENUM(MFAMethod, name="mfa_method"), nullable=True, comment="Primary MFA method"
    )

    # Enabled methods
    enabled_methods = Column(
        ARRAY(String), nullable=True, default=[], comment="List of enabled MFA methods"
    )

    # TOTP settings
    totp_secret = Column(
        String(255), nullable=True, comment="TOTP secret key (encrypted)"
    )

    totp_verified = Column(
        Boolean, nullable=False, default=False, comment="Whether TOTP has been verified"
    )

    totp_last_used = Column(
        DateTime(timezone=True), nullable=True, comment="Last time TOTP was used"
    )

    totp_counter = Column(
        Integer,
        nullable=False,
        default=0,
        comment="TOTP counter for preventing replay attacks",
    )

    # SMS settings
    sms_phone_number = Column(
        String(20), nullable=True, comment="Phone number for SMS (encrypted)"
    )

    sms_verified = Column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether SMS number has been verified",
    )

    sms_last_sent = Column(
        DateTime(timezone=True), nullable=True, comment="Last time SMS was sent"
    )

    sms_send_count = Column(
        Integer, nullable=False, default=0, comment="Number of SMS codes sent"
    )

    # Email settings (uses user's email)
    email_verified = Column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether email MFA has been verified",
    )

    email_last_sent = Column(
        DateTime(timezone=True), nullable=True, comment="Last time email code was sent"
    )

    # Backup codes
    backup_codes = Column(JSON, nullable=True, comment="Hashed backup codes")

    backup_codes_generated_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="When backup codes were generated",
    )

    backup_codes_used = Column(
        JSON, nullable=True, default=[], comment="List of used backup code indices"
    )

    # WebAuthn settings
    webauthn_credentials = Column(
        JSON, nullable=True, comment="WebAuthn credential data"
    )

    # Recovery settings
    recovery_email = Column(
        String(255), nullable=True, comment="Alternative email for recovery (encrypted)"
    )

    recovery_phone = Column(
        String(20), nullable=True, comment="Alternative phone for recovery (encrypted)"
    )

    # Security settings
    require_mfa_for_sensitive = Column(
        Boolean,
        nullable=False,
        default=True,
        comment="Require MFA for sensitive operations",
    )

    trusted_devices = Column(
        JSON, nullable=True, default=[], comment="List of trusted device IDs"
    )

    # Usage statistics
    successful_verifications = Column(
        Integer, nullable=False, default=0, comment="Total successful MFA verifications"
    )

    failed_attempts = Column(
        Integer, nullable=False, default=0, comment="Total failed MFA attempts"
    )

    last_verified_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="Last successful MFA verification",
    )

    last_failed_at = Column(
        DateTime(timezone=True), nullable=True, comment="Last failed MFA attempt"
    )

    # Temporary codes for setup/recovery
    temp_setup_code = Column(
        String(255), nullable=True, comment="Temporary code for MFA setup"
    )

    temp_setup_expires = Column(
        DateTime(timezone=True), nullable=True, comment="When temp setup code expires"
    )

    # Relationships
    user = relationship("User", back_populates="mfa_settings", uselist=False)

    # Indexes
    __table_args__ = (
        Index("idx_mfa_settings_enabled", "is_enabled", "primary_method"),
        Index("idx_mfa_settings_phone", "sms_phone_number"),
    )

    @classmethod
    def create_settings(cls, user_id: str) -> "MFASettings":
        """
        Create default MFA settings for a user.

        Args:
            user_id: User ID

        Returns:
            MFASettings instance
        """
        return cls(user_id=user_id)

    def enable_totp(self, issuer: str = "Research Platform") -> tuple[str, str]:
        """
        Enable TOTP authentication.

        Args:
            issuer: Issuer name for TOTP

        Returns:
            Tuple of (secret, provisioning_uri)
        """
        # Generate secret
        secret = pyotp.random_base32()
        self.totp_secret = secret  # Should be encrypted before storage

        # Generate provisioning URI for QR code
        totp = pyotp.TOTP(secret)
        provisioning_uri = totp.provisioning_uri(
            name=self.user.email, issuer_name=issuer
        )

        # Add to enabled methods
        if self.enabled_methods:
            if MFAMethod.TOTP.value not in self.enabled_methods:
                self.enabled_methods.append(MFAMethod.TOTP.value)
        else:
            self.enabled_methods = [MFAMethod.TOTP.value]

        return secret, provisioning_uri

    def verify_totp(self, code: str, window: int = 1) -> bool:
        """
        Verify a TOTP code.

        Args:
            code: TOTP code to verify
            window: Time window tolerance

        Returns:
            True if code is valid
        """
        if not self.totp_secret:
            return False

        totp = pyotp.TOTP(self.totp_secret)

        # Verify with time window
        is_valid = totp.verify(code, valid_window=window)

        if is_valid:
            self.totp_verified = True
            self.totp_last_used = datetime.utcnow()
            self.totp_counter += 1
            self.successful_verifications += 1
            self.last_verified_at = datetime.utcnow()

            # Enable MFA if this is the first verification
            if not self.is_enabled:
                self.is_enabled = True
                self.primary_method = MFAMethod.TOTP
        else:
            self.failed_attempts += 1
            self.last_failed_at = datetime.utcnow()

        return is_valid

    def generate_backup_codes(self, count: int = 10) -> list[str]:
        """
        Generate backup codes.

        Args:
            count: Number of codes to generate

        Returns:
            List of backup codes
        """
        codes = []
        hashed_codes = []

        for _ in range(count):
            # Generate 8-character alphanumeric code
            code = secrets.token_hex(4).upper()
            codes.append(code)

            # Hash for storage
            hashed = pwd_context.hash(code)
            hashed_codes.append(hashed)

        self.backup_codes = hashed_codes
        self.backup_codes_generated_at = datetime.utcnow()
        self.backup_codes_used = []

        # Add to enabled methods
        if self.enabled_methods:
            if MFAMethod.BACKUP_CODES.value not in self.enabled_methods:
                self.enabled_methods.append(MFAMethod.BACKUP_CODES.value)
        else:
            self.enabled_methods = [MFAMethod.BACKUP_CODES.value]

        return codes

    def verify_backup_code(self, code: str) -> bool:
        """
        Verify a backup code.

        Args:
            code: Backup code to verify

        Returns:
            True if code is valid and unused
        """
        if not self.backup_codes:
            return False

        # Check each hashed code
        for i, hashed_code in enumerate(self.backup_codes):
            # Skip already used codes
            if i in (self.backup_codes_used or []):
                continue

            # Verify against hash
            if pwd_context.verify(code, hashed_code):
                # Mark as used
                if self.backup_codes_used:
                    self.backup_codes_used.append(i)
                else:
                    self.backup_codes_used = [i]

                self.successful_verifications += 1
                self.last_verified_at = datetime.utcnow()

                return True

        self.failed_attempts += 1
        self.last_failed_at = datetime.utcnow()
        return False

    def enable_sms(self, phone_number: str) -> str:
        """
        Enable SMS authentication.

        Args:
            phone_number: Phone number for SMS

        Returns:
            Verification code
        """
        self.sms_phone_number = phone_number  # Should be encrypted

        # Generate verification code
        code = str(secrets.randbelow(1000000)).zfill(6)

        # Add to enabled methods
        if self.enabled_methods:
            if MFAMethod.SMS.value not in self.enabled_methods:
                self.enabled_methods.append(MFAMethod.SMS.value)
        else:
            self.enabled_methods = [MFAMethod.SMS.value]

        self.sms_last_sent = datetime.utcnow()
        self.sms_send_count += 1

        return code

    def add_trusted_device(self, device_id: str, device_info: dict) -> None:
        """
        Add a trusted device.

        Args:
            device_id: Device identifier
            device_info: Device information
        """
        trusted_device = {
            "device_id": device_id,
            "added_at": datetime.utcnow().isoformat(),
            "last_used": datetime.utcnow().isoformat(),
            **device_info,
        }

        if self.trusted_devices:
            # Remove if already exists
            self.trusted_devices = [
                d for d in self.trusted_devices if d.get("device_id") != device_id
            ]
            self.trusted_devices.append(trusted_device)
        else:
            self.trusted_devices = [trusted_device]

    def is_trusted_device(self, device_id: str) -> bool:
        """
        Check if device is trusted.

        Args:
            device_id: Device identifier

        Returns:
            True if device is trusted
        """
        if not self.trusted_devices:
            return False

        for device in self.trusted_devices:
            if device.get("device_id") == device_id:
                # Update last used
                device["last_used"] = datetime.utcnow().isoformat()
                return True

        return False

    def disable_mfa(self) -> None:
        """Disable MFA (if not enforced)."""
        if self.is_enforced:
            raise ValueError("MFA is enforced and cannot be disabled")

        self.is_enabled = False
        self.primary_method = None
        self.enabled_methods = []

        # Clear sensitive data
        self.totp_secret = None
        self.totp_verified = False
        self.backup_codes = None
        self.backup_codes_used = []

    @property
    def has_backup_codes(self) -> bool:
        """Check if user has unused backup codes."""
        if not self.backup_codes:
            return False

        used_count = len(self.backup_codes_used or [])
        total_count = len(self.backup_codes)

        return used_count < total_count

    @property
    def backup_codes_remaining(self) -> int:
        """Get number of remaining backup codes."""
        if not self.backup_codes:
            return 0

        used_count = len(self.backup_codes_used or [])
        total_count = len(self.backup_codes)

        return total_count - used_count

    @property
    def requires_setup(self) -> bool:
        """Check if MFA requires setup."""
        return self.is_enabled and not self.enabled_methods

    def to_dict(self, include_sensitive: bool = False) -> dict:
        """
        Convert to dictionary.

        Args:
            include_sensitive: Include sensitive information

        Returns:
            Dictionary representation
        """
        data = {
            "user_id": str(self.user_id),
            "is_enabled": self.is_enabled,
            "is_enforced": self.is_enforced,
            "primary_method": (
                self.primary_method.value if self.primary_method else None
            ),
            "enabled_methods": self.enabled_methods,
            "totp_verified": self.totp_verified,
            "sms_verified": self.sms_verified,
            "email_verified": self.email_verified,
            "has_backup_codes": self.has_backup_codes,
            "backup_codes_remaining": self.backup_codes_remaining,
            "require_mfa_for_sensitive": self.require_mfa_for_sensitive,
            "successful_verifications": self.successful_verifications,
            "failed_attempts": self.failed_attempts,
            "last_verified_at": (
                self.last_verified_at.isoformat() if self.last_verified_at else None
            ),
            "requires_setup": self.requires_setup,
        }

        if include_sensitive:
            data["trusted_devices_count"] = len(self.trusted_devices or [])
            data["has_recovery_email"] = bool(self.recovery_email)
            data["has_recovery_phone"] = bool(self.recovery_phone)

        return data

    def __repr__(self) -> str:
        """String representation."""
        return f"<MFASettings(user_id={self.user_id}, enabled={self.is_enabled}, method={self.primary_method})>"
