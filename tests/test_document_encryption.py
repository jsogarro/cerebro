import pytest
from cryptography.fernet import Fernet

from src.core.config import settings
from src.core.memory_encryption import (
    DocumentEncryptionUnavailable,
    MemoryEncryption,
    document_cipher,
)

KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()


def test_empty_key_does_not_fall_back_to_the_memory_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEMORY_ENCRYPTION_KEY", KEY_A)
    assert MemoryEncryption(key="").enabled
    assert not MemoryEncryption(key="", env_fallback=False).enabled


def test_rotation_with_a_comma_separated_key_list() -> None:
    old = MemoryEncryption(KEY_A).encrypt_text("secret")
    rotated = MemoryEncryption(f"{KEY_B},{KEY_A}")
    assert rotated.decrypt_text(old) == "secret"
    new = rotated.encrypt_text("fresh")
    assert MemoryEncryption(KEY_B).decrypt_text(new) == "fresh"
    with pytest.raises(ValueError):
        MemoryEncryption(KEY_A).decrypt_text(new)


@pytest.mark.parametrize("key", ["", "   "])
def test_document_cipher_fails_closed_on_a_blank_key(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    monkeypatch.setenv("MEMORY_ENCRYPTION_KEY", KEY_A)
    monkeypatch.setattr(settings, "DOCUMENT_ENCRYPTION_KEY", key)
    with pytest.raises(DocumentEncryptionUnavailable):
        document_cipher()


def test_document_cipher_encrypts_with_the_document_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_ENCRYPTION_KEY", KEY_A)
    token = document_cipher().encrypt_text("page text")
    assert token != "page text"
    assert MemoryEncryption(KEY_A).decrypt_text(token) == "page text"
