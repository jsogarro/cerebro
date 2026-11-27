"""Production JWT key provisioning and multi-worker compatibility contracts."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from src.auth.jwt_service import JWTService
from src.core.config import settings

REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_COMPOSE = REPO_ROOT / "docker-compose.production.yml"


def _write_synthetic_keypair(directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_path = directory / "jwt_private.pem"
    public_path = directory / "jwt_public.pem"
    private_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_path, public_path


def test_production_compose_requires_one_read_only_api_keypair() -> None:
    content = PRODUCTION_COMPOSE.read_text(encoding="utf-8")
    compose = yaml.safe_load(content)
    api = compose["services"]["api"]

    assert "JWT_PRIVATE_KEY_FILE:?" in content
    assert "JWT_PUBLIC_KEY_FILE:?" in content
    assert "JWT_PRIVATE_KEY_PATH=/run/secrets/jwt_private.pem" in api["environment"]
    assert "JWT_PUBLIC_KEY_PATH=/run/secrets/jwt_public.pem" in api["environment"]
    assert api["secrets"] == [
        {"source": "jwt_private_key", "target": "jwt_private.pem"},
        {"source": "jwt_public_key", "target": "jwt_public.pem"},
    ]
    assert compose["secrets"]["jwt_private_key"]["file"].startswith(
        "${JWT_PRIVATE_KEY_FILE:?"
    )
    assert compose["secrets"]["jwt_public_key"]["file"].startswith(
        "${JWT_PUBLIC_KEY_FILE:?"
    )


@pytest.mark.asyncio
async def test_two_production_workers_share_signing_and_verification_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_path, public_path = _write_synthetic_keypair(tmp_path)
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")

    issuer = JWTService(
        private_key_path=str(private_path),
        public_key_path=str(public_path),
    )
    verifier = JWTService(
        private_key_path=str(private_path),
        public_key_path=str(public_path),
    )

    token_pair = await issuer.generate_token_pair(
        user_id="worker-shared-user",
        email="worker@example.test",
    )
    # This contract proves cross-worker cryptographic verification only. The
    # revocation-store contract is exercised separately by JWT auth tests.
    payload = await verifier.validate_token(
        token_pair.access_token, verify_blacklist=False
    )

    assert payload.sub == "worker-shared-user"


def test_production_jwt_service_fails_closed_without_key_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")

    with pytest.raises(RuntimeError, match="private key file is required"):
        JWTService(
            private_key_path=str(tmp_path / "missing-private.pem"),
            public_key_path=str(tmp_path / "missing-public.pem"),
        )


def test_production_jwt_service_rejects_mismatched_keypair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_private, _ = _write_synthetic_keypair(tmp_path / "first")
    _, second_public = _write_synthetic_keypair(tmp_path / "second")
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")

    with pytest.raises(RuntimeError, match="invalid or mismatched"):
        JWTService(
            private_key_path=str(first_private),
            public_key_path=str(second_public),
        )
