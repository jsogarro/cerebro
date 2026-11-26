"""Encryption helpers for memory data stored at rest."""

from __future__ import annotations

import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from src.utils.serialization import deserialize, serialize

ENCRYPTED_PAYLOAD_KEY = "__cerebro_encrypted__"
ENCRYPTED_TEXT_PREFIX = "fernet:"


class MemoryEncryption:
    """Fernet-backed encryption for persisted memory payloads."""

    def __init__(
        self, key: str | bytes | None = None, *, env_fallback: bool = True
    ) -> None:
        self._key = key or (
            os.getenv("MEMORY_ENCRYPTION_KEY") if env_fallback else None
        )
        self._fernet: Fernet | MultiFernet | None = None
        if self._key:
            raw = self._key.decode() if isinstance(self._key, bytes) else self._key
            keys = [Fernet(part.strip().encode()) for part in raw.split(",")]
            self._fernet = keys[0] if len(keys) == 1 else MultiFernet(keys)

    @property
    def enabled(self) -> bool:
        """Return whether memory encryption is configured."""

        return self._fernet is not None

    def encrypt_data(self, data: Any) -> dict[str, str] | Any:
        """Encrypt JSON-serializable data, or return it unchanged if disabled."""

        if self._fernet is None:
            return data
        token = self._fernet.encrypt(serialize(data)).decode("utf-8")
        return {ENCRYPTED_PAYLOAD_KEY: token}

    def decrypt_data(self, data: Any) -> Any:
        """Decrypt data produced by encrypt_data, or return plaintext unchanged."""

        if not self._is_encrypted_payload(data):
            return data
        if self._fernet is None:
            raise ValueError("MEMORY_ENCRYPTION_KEY is required to decrypt memory data")
        token = data[ENCRYPTED_PAYLOAD_KEY].encode("utf-8")
        try:
            return deserialize(self._fernet.decrypt(token))
        except InvalidToken as exc:
            raise ValueError("Invalid encrypted memory payload") from exc

    def encrypt_text(self, value: str | None) -> str | None:
        """Encrypt a string value, or return it unchanged if disabled."""

        if value is None or self._fernet is None:
            return value
        token = self._fernet.encrypt(value.encode()).decode("utf-8")
        return f"{ENCRYPTED_TEXT_PREFIX}{token}"

    def decrypt_text(self, value: str | None) -> str | None:
        """Decrypt a string produced by encrypt_text."""

        if value is None or not value.startswith(ENCRYPTED_TEXT_PREFIX):
            return value
        if self._fernet is None:
            raise ValueError("MEMORY_ENCRYPTION_KEY is required to decrypt memory text")
        token = value.removeprefix(ENCRYPTED_TEXT_PREFIX).encode("utf-8")
        try:
            return self._fernet.decrypt(token).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Invalid encrypted memory text") from exc

    @staticmethod
    def _is_encrypted_payload(data: Any) -> bool:
        return (
            isinstance(data, dict)
            and set(data) == {ENCRYPTED_PAYLOAD_KEY}
            and isinstance(data[ENCRYPTED_PAYLOAD_KEY], str)
        )


class DocumentEncryptionUnavailable(RuntimeError):  # noqa: N818
    """No usable ``DOCUMENT_ENCRYPTION_KEY``; a disabled cipher would store plaintext."""


def document_cipher() -> MemoryEncryption:
    """Return the cipher for uploaded-document columns, or fail closed."""
    from src.core.config import settings

    cipher = MemoryEncryption(
        settings.DOCUMENT_ENCRYPTION_KEY.strip(), env_fallback=False
    )
    if not cipher.enabled:
        raise DocumentEncryptionUnavailable("DOCUMENT_ENCRYPTION_KEY is not set")
    return cipher


__all__ = ["DocumentEncryptionUnavailable", "MemoryEncryption", "document_cipher"]
