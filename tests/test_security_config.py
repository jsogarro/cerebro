"""Characterization + specification tests for SecurityConfig and the production
JWT-secret validator.

Layered contract:
  - ``SecurityConfig.jwt_secret_key`` remains optional HS256 compatibility.
  - Production uses required RS256 PEM paths validated by ProductionConfig.
  - ``validate_production_jwt_secret`` remains available to legacy HS256
    consumers but is not part of the production startup contract.

Characterization tests (must pass on unmodified code AND after Fix 1) lock in
behavior that survives the refactor. Specification tests describe the new
contract introduced by Fix 1.
"""

from __future__ import annotations

import pytest

from config.base import SecurityConfig

KNOWN_WEAK_DEFAULTS = (
    "change-me-in-production",
    "dev-secret-key",
    "dev-secret-key-not-for-production",
    "staging-secret-key",
    "test-secret-key",
)


class TestSecurityConfigCharacterization:
    """Locked behavior — preserved across the refactor."""

    def test_accepts_custom_jwt_secret_key(self) -> None:
        config = SecurityConfig(jwt_secret_key="a-real-rotated-production-secret")
        assert config.jwt_secret_key == "a-real-rotated-production-secret"

    def test_jwt_algorithm_default_is_hs256(self) -> None:
        config = SecurityConfig(jwt_secret_key="x")
        assert config.jwt_algorithm == "HS256"

    def test_jwt_expiration_defaults_unchanged(self) -> None:
        config = SecurityConfig(jwt_secret_key="x")
        assert config.jwt_expiration_minutes == 60
        assert config.refresh_token_expiration_days == 7

    def test_rate_limit_defaults_unchanged(self) -> None:
        config = SecurityConfig(jwt_secret_key="x")
        assert config.rate_limiting_enabled is True
        assert config.rate_limit_per_minute == 100

    def test_dev_environment_can_use_placeholder_secret(self) -> None:
        # Dev/test environments must still construct SecurityConfig with their
        # explicit placeholder values; weak-default rejection is a production
        # concern, not a model-level constraint.
        config = SecurityConfig(jwt_secret_key="dev-secret-key-not-for-production")
        assert config.jwt_secret_key == "dev-secret-key-not-for-production"


class TestSecurityConfigSpecification:
    """Production-compatible SecurityConfig field contract."""

    def test_allows_rs256_paths_without_shared_secret(self) -> None:
        config = SecurityConfig(
            jwt_algorithm="RS256",
            jwt_private_key_path="/synthetic/private.pem",
            jwt_public_key_path="/synthetic/public.pem",
        )

        assert config.jwt_secret_key is None
        assert config.jwt_private_key_path == "/synthetic/private.pem"
        assert config.jwt_public_key_path == "/synthetic/public.pem"


class TestProductionJwtSecretValidator:
    """Separate production-only guard that rejects known weak defaults."""

    def test_accepts_strong_secret(self) -> None:
        from config.base import validate_production_jwt_secret

        # Should not raise.
        validate_production_jwt_secret("a-real-rotated-production-secret-32chars")

    def test_rejects_empty_string(self) -> None:
        from config.base import validate_production_jwt_secret

        with pytest.raises(ValueError):
            validate_production_jwt_secret("")

    @pytest.mark.parametrize("weak_value", KNOWN_WEAK_DEFAULTS)
    def test_rejects_known_weak_defaults(self, weak_value: str) -> None:
        from config.base import validate_production_jwt_secret

        with pytest.raises(ValueError):
            validate_production_jwt_secret(weak_value)
