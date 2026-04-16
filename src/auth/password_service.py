"""
Password service for secure password handling.

Provides password hashing, validation, and management features.
"""

from __future__ import annotations

import hashlib
import secrets
import string
from typing import Any

import httpx
import redis.asyncio as redis
import structlog
from passlib.context import CryptContext

logger = structlog.get_logger(__name__)


class PasswordService:
    """
    Secure password management service.

    Features:
    - Bcrypt password hashing with configurable rounds
    - Password strength validation
    - Password history tracking
    - Breach detection via HaveIBeenPwned API
    - Secure password reset tokens
    - Password generation
    """

    def __init__(
        self,
        redis_client: redis.Redis[Any] | None = None,
        bcrypt_rounds: int = 12,
        min_password_length: int = 12,
        password_history_limit: int = 5,
        check_breaches: bool = True,
    ):
        """
        Initialize password service.

        Args:
            redis_client: Redis client for caching
            bcrypt_rounds: Bcrypt hashing rounds (12-14 recommended)
            min_password_length: Minimum password length
            password_history_limit: Number of previous passwords to track
            check_breaches: Enable breach checking
        """
        self.redis_client = redis_client
        self.bcrypt_rounds = bcrypt_rounds
        self.min_password_length = min_password_length
        self.password_history_limit = password_history_limit
        self.check_breaches = check_breaches

        # Initialize password context
        self.pwd_context = CryptContext(
            schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=bcrypt_rounds
        )

        # Password history prefix in Redis
        self.history_prefix = "password:history:"
        self.reset_token_prefix = "password:reset:"

        # HTTP client for breach checking
        self.http_client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "PasswordService":
        """Async context manager entry."""
        if self.check_breaches:
            self.http_client = httpx.AsyncClient(timeout=5.0)
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        if self.http_client:
            await self.http_client.aclose()

    def hash_password(self, password: str) -> str:
        """
        Hash password using bcrypt.

        Args:
            password: Plain text password

        Returns:
            Hashed password
        """
        return self.pwd_context.hash(password)

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """
        Verify password against hash.

        Args:
            plain_password: Plain text password
            hashed_password: Hashed password

        Returns:
            True if password matches
        """
        return self.pwd_context.verify(plain_password, hashed_password)

    def validate_password_strength(
        self,
        password: str,
        username: str | None = None,
        email: str | None = None,
    ) -> dict[str, Any]:
        """
        Validate password strength.

        Args:
            password: Password to validate
            username: Username for context checking
            email: Email for context checking

        Returns:
            Validation result with score and issues
        """
        issues = []
        score = 100

        # Length check
        if len(password) < self.min_password_length:
            issues.append(
                f"Password must be at least {self.min_password_length} characters"
            )
            score -= 30
        elif len(password) < 16:
            score -= 10

        # Character type checks
        has_upper = any(c.isupper() for c in password)
        has_lower = any(c.islower() for c in password)
        has_digit = any(c.isdigit() for c in password)
        has_special = any(c in string.punctuation for c in password)

        if not has_upper:
            issues.append("Password must contain uppercase letters")
            score -= 15

        if not has_lower:
            issues.append("Password must contain lowercase letters")
            score -= 15

        if not has_digit:
            issues.append("Password must contain digits")
            score -= 15

        if not has_special:
            issues.append("Password must contain special characters")
            score -= 15

        # Common patterns check
        common_patterns = [
            "123456",
            "password",
            "qwerty",
            "abc123",
            "111111",
            "123123",
            "admin",
            "letmein",
        ]

        password_lower = password.lower()
        for pattern in common_patterns:
            if pattern in password_lower:
                issues.append("Password contains common patterns")
                score -= 20
                break

        # Username/email similarity check
        if username and username.lower() in password_lower:
            issues.append("Password is too similar to username")
            score -= 20

        if email:
            email_parts = email.lower().split("@")[0]
            if email_parts in password_lower:
                issues.append("Password is too similar to email")
                score -= 20

        # Sequential characters check
        if self._has_sequential_chars(password):
            issues.append("Password contains sequential characters")
            score -= 10

        # Repeating characters check
        if self._has_repeating_chars(password):
            issues.append("Password contains too many repeating characters")
            score -= 10

        # Calculate entropy
        entropy = self._calculate_entropy(password)
        if entropy < 40:
            issues.append("Password entropy is too low")
            score -= 15

        return {
            "valid": len(issues) == 0,
            "score": max(0, score),
            "issues": issues,
            "entropy": entropy,
            "strength": self._get_strength_label(score),
        }

    async def check_password_breach(self, password: str) -> bool:
        """
        Check if password has been breached using HaveIBeenPwned API.

        Args:
            password: Password to check

        Returns:
            True if password has been breached
        """
        if not self.check_breaches or not self.http_client:
            return False

        try:
            # Calculate SHA-1 hash
            sha1 = hashlib.sha1(password.encode()).hexdigest().upper()
            prefix = sha1[:5]
            suffix = sha1[5:]

            # Query HIBP API
            response = await self.http_client.get(
                f"https://api.pwnedpasswords.com/range/{prefix}",
                headers={"User-Agent": "Research-Platform-Security"},
            )

            if response.status_code == 200:
                # Check if suffix appears in response
                for line in response.text.splitlines():
                    hash_suffix, count = line.split(":")
                    if hash_suffix == suffix:
                        logger.warning(
                            "Password found in breach database", occurrences=count
                        )
                        return True

            return False

        except Exception as e:
            logger.error("Failed to check password breach", error=str(e))
            # Fail open - don't block on API failure
            return False

    async def add_to_password_history(self, user_id: str, password_hash: str) -> None:
        """
        Add password to user's password history.

        Args:
            user_id: User identifier
            password_hash: Hashed password
        """
        if not self.redis_client:
            return

        history_key = f"{self.history_prefix}{user_id}"

        # Add to history list
        await self.redis_client.lpush(history_key, password_hash)

        # Trim to limit
        await self.redis_client.ltrim(history_key, 0, self.password_history_limit - 1)

        # Set expiration (1 year)
        await self.redis_client.expire(history_key, 365 * 24 * 3600)

    async def check_password_history(self, user_id: str, password: str) -> bool:
        """
        Check if password was recently used.

        Args:
            user_id: User identifier
            password: Plain text password to check

        Returns:
            True if password was recently used
        """
        if not self.redis_client:
            return False

        history_key = f"{self.history_prefix}{user_id}"
        history = await self.redis_client.lrange(history_key, 0, -1)

        for old_hash in history:
            if isinstance(old_hash, bytes):
                old_hash = old_hash.decode()

            if self.verify_password(password, old_hash):
                return True

        return False

    def generate_reset_token(self, length: int = 32) -> str:
        """
        Generate secure password reset token.

        Args:
            length: Token length

        Returns:
            Secure random token
        """
        return secrets.token_urlsafe(length)

    async def store_reset_token(
        self, user_id: str, token: str, expires_in: int = 3600
    ) -> None:
        """
        Store password reset token.

        Args:
            user_id: User identifier
            token: Reset token
            expires_in: Expiration time in seconds
        """
        if not self.redis_client:
            return

        token_key = f"{self.reset_token_prefix}{token}"
        await self.redis_client.setex(token_key, expires_in, user_id)

    async def validate_reset_token(self, token: str) -> str | None:
        """
        Validate password reset token.

        Args:
            token: Reset token

        Returns:
            User ID if valid, None otherwise
        """
        if not self.redis_client:
            return None

        token_key = f"{self.reset_token_prefix}{token}"
        user_id = await self.redis_client.get(token_key)

        if user_id:
            # Delete token after use
            await self.redis_client.delete(token_key)
            return user_id.decode() if isinstance(user_id, bytes) else user_id

        return None

    def generate_password(
        self,
        length: int = 16,
        include_symbols: bool = True,
        exclude_ambiguous: bool = True,
    ) -> str:
        """
        Generate secure random password.

        Args:
            length: Password length
            include_symbols: Include special characters
            exclude_ambiguous: Exclude ambiguous characters (0, O, l, 1, etc.)

        Returns:
            Generated password
        """
        # Character sets
        lowercase = string.ascii_lowercase
        uppercase = string.ascii_uppercase
        digits = string.digits
        symbols = string.punctuation if include_symbols else ""

        # Exclude ambiguous characters
        if exclude_ambiguous:
            ambiguous = "0O1lI"
            lowercase = "".join(c for c in lowercase if c not in ambiguous)
            uppercase = "".join(c for c in uppercase if c not in ambiguous)
            digits = "".join(c for c in digits if c not in ambiguous)

        # Combine character sets
        all_chars = lowercase + uppercase + digits + symbols

        # Ensure at least one character from each set
        password = [
            secrets.choice(lowercase),
            secrets.choice(uppercase),
            secrets.choice(digits),
        ]

        if include_symbols:
            password.append(secrets.choice(symbols))

        # Fill remaining length
        for _ in range(len(password), length):
            password.append(secrets.choice(all_chars))

        # Shuffle password
        secrets.SystemRandom().shuffle(password)

        return "".join(password)

    def _has_sequential_chars(self, password: str, max_seq: int = 3) -> bool:
        """Check for sequential characters."""
        for i in range(len(password) - max_seq + 1):
            substr = password[i : i + max_seq]

            # Check ascending
            if all(
                ord(substr[j]) == ord(substr[j - 1]) + 1 for j in range(1, len(substr))
            ):
                return True

            # Check descending
            if all(
                ord(substr[j]) == ord(substr[j - 1]) - 1 for j in range(1, len(substr))
            ):
                return True

        return False

    def _has_repeating_chars(self, password: str, max_repeat: int = 3) -> bool:
        """Check for repeating characters."""
        for i in range(len(password) - max_repeat + 1):
            if len(set(password[i : i + max_repeat])) == 1:
                return True
        return False

    def _calculate_entropy(self, password: str) -> float:
        """Calculate password entropy."""
        charset_size = 0

        if any(c.islower() for c in password):
            charset_size += 26
        if any(c.isupper() for c in password):
            charset_size += 26
        if any(c.isdigit() for c in password):
            charset_size += 10
        if any(c in string.punctuation for c in password):
            charset_size += len(string.punctuation)

        if charset_size == 0:
            return 0

        import math

        return len(password) * math.log2(charset_size)

    def _get_strength_label(self, score: int) -> str:
        """Get password strength label from score."""
        if score >= 80:
            return "strong"
        elif score >= 60:
            return "good"
        elif score >= 40:
            return "fair"
        elif score >= 20:
            return "weak"
        else:
            return "very_weak"
