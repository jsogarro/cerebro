"""Satisfy ``get_current_token`` on a test app with a real token payload."""

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI

from src.auth.models import TokenPayload
from src.middleware.auth_middleware import get_current_token


def allow_authenticated(app: FastAPI) -> TokenPayload:
    """Override ``get_current_token`` so routes see an authenticated caller."""
    now = datetime.now(UTC)
    payload = TokenPayload(
        sub="test-user",
        email="test@example.com",
        organization_id="test-org",
        jti="test-jti",
        iat=now,
        exp=now + timedelta(minutes=5),
    )
    app.dependency_overrides[get_current_token] = lambda: payload
    return payload
