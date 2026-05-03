"""
Authentication middleware for request authentication and authorization.

Provides JWT validation, user context injection, and permission checking.
"""

from collections.abc import Callable
from typing import Any

import redis.asyncio as redis
import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from src.auth.jwt_service import JWTService
from src.auth.models import TokenPayload
from src.core.config import settings
from src.models.db.session import get_session
from src.models.db.user import User
from src.repositories.user_repository import UserRepository

logger = structlog.get_logger(__name__)

# Security scheme
security = HTTPBearer(auto_error=False)


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Authentication middleware for all requests.

    Validates JWT tokens and adds user context to requests.
    """

    def __init__(self, app: Any, exclude_paths: list[str] | None = None) -> None:
        """
        Initialize authentication middleware.

        Args:
            app: FastAPI application
            exclude_paths: Paths to exclude from authentication
        """
        super().__init__(app)
        self.exclude_paths = exclude_paths or [
            "/health",
            "/ready",
            "/docs",
            "/openapi.json",
            "/auth/login",
            "/auth/register",
            "/auth/forgot-password",
            "/auth/reset-password",
            "/auth/verify-email",
        ]

    async def dispatch(self, request: Request, call_next: Callable[..., Any]) -> Response:
        """
        Process request with authentication.

        Args:
            request: Incoming request
            call_next: Next middleware or endpoint

        Returns:
            Response from endpoint
        """
        # Skip authentication for excluded paths
        path = request.url.path
        if any(path.startswith(excluded) for excluded in self.exclude_paths):
            response: Response = await call_next(request)
            return response

        # Extract token from Authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            # Allow request to continue without user context
            # Endpoints can enforce authentication as needed
            request.state.user = None
            request.state.token_payload = None
            request.state.organization_id = None
            response = await call_next(request)
            return response

        token = auth_header.replace("Bearer ", "")

        try:
            # Initialize JWT service
            redis_client = await redis.from_url(settings.REDIS_URL)
            jwt_service = JWTService(
                redis_client=redis_client,
                private_key_path=settings.JWT_PRIVATE_KEY_PATH,
                public_key_path=settings.JWT_PUBLIC_KEY_PATH,
            )

            # Validate token
            token_payload = await jwt_service.validate_token(token)

            # Add to request state
            request.state.token_payload = token_payload
            request.state.user_id = token_payload.sub
            request.state.organization_id = token_payload.organization_id

            # Log authenticated request
            logger.debug(
                "Authenticated request",
                user_id=token_payload.sub,
                organization_id=token_payload.organization_id,
                path=path,
                method=request.method,
            )

        except Exception as e:
            logger.warning("Authentication failed", error=str(e), path=path)
            # Allow request to continue without user context
            request.state.user = None
            request.state.token_payload = None
            request.state.organization_id = None

        response = await call_next(request)
        return response


async def get_jwt_service() -> JWTService:
    """
    Get JWT service instance.

    This is a dependency that can be overridden in tests.
    """
    redis_client = await redis.from_url(settings.REDIS_URL)
    return JWTService(
        redis_client=redis_client,
        private_key_path=settings.JWT_PRIVATE_KEY_PATH,
        public_key_path=settings.JWT_PUBLIC_KEY_PATH,
    )


async def get_current_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    jwt_service: JWTService = Depends(get_jwt_service),
) -> TokenPayload:
    """
    Get current token payload from request.

    Args:
        credentials: HTTP authorization credentials
        jwt_service: JWT service for token validation

    Returns:
        Decoded token payload

    Raises:
        HTTPException: If token is invalid or missing
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    try:
        # Validate token using injected JWT service
        token_payload = await jwt_service.validate_token(token)
        return token_payload

    except Exception as e:
        logger.warning("Token validation failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e


async def get_current_user(
    token_payload: TokenPayload = Depends(get_current_token),
    db: AsyncSession = Depends(get_session),
) -> User:
    """
    Get current authenticated user.

    Args:
        token_payload: Decoded token payload
        db: Database session

    Returns:
        Current user object

    Raises:
        HTTPException: If user not found
    """
    user_repo = UserRepository(db)
    user = await user_repo.get(token_payload.sub)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Get current active user.

    Args:
        current_user: Current authenticated user

    Returns:
        Active user object

    Raises:
        HTTPException: If user is not active
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled"
        )

    return current_user


def require_roles(roles: list[str]) -> Callable[..., Any]:
    """
    Dependency to require specific roles.

    Args:
        roles: List of required roles (user must have at least one)

    Returns:
        Dependency function
    """

    async def role_checker(
        token_payload: TokenPayload = Depends(get_current_token),
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        """Check if user has required roles."""
        # Superusers bypass role checks
        if current_user.is_superuser:
            return current_user

        # Check if user has any of the required roles
        if not token_payload.has_any_role(roles):
            logger.warning(
                "Access denied - insufficient roles",
                user_id=str(current_user.id),
                required_roles=roles,
                user_roles=token_payload.roles,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {', '.join(roles)}",
            )

        return current_user

    return role_checker


def require_permissions(permissions: list[str]) -> Callable[..., Any]:
    """
    Dependency to require specific permissions.

    Args:
        permissions: List of required permissions (user must have all)

    Returns:
        Dependency function
    """

    async def permission_checker(
        token_payload: TokenPayload = Depends(get_current_token),
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        """Check if user has required permissions."""
        # Superusers have all permissions
        if current_user.is_superuser or "*" in token_payload.permissions:
            return current_user

        # Check if user has all required permissions
        missing_permissions = [
            perm for perm in permissions if not token_payload.has_permission(perm)
        ]

        if missing_permissions:
            logger.warning(
                "Access denied - insufficient permissions",
                user_id=str(current_user.id),
                required_permissions=permissions,
                missing_permissions=missing_permissions,
                user_permissions=token_payload.permissions,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permissions: {', '.join(missing_permissions)}",
            )

        return current_user

    return permission_checker


def require_superuser() -> Callable[..., Any]:
    """
    Dependency to require superuser privileges.

    Returns:
        Dependency function
    """

    async def superuser_checker(
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        """Check if user is superuser."""
        if not current_user.is_superuser:
            logger.warning(
                "Access denied - superuser required", user_id=str(current_user.id)
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Superuser privileges required",
            )

        return current_user

    return superuser_checker


def optional_user() -> Callable[..., Any]:
    """
    Dependency for optional authentication.

    Returns user if authenticated, None otherwise.

    Returns:
        Dependency function
    """

    async def optional_user_getter(
        credentials: HTTPAuthorizationCredentials | None = Depends(security),
        db: AsyncSession = Depends(get_session),
        jwt_service: JWTService = Depends(get_jwt_service),
    ) -> User | None:
        """Get user if authenticated."""
        if not credentials:
            return None

        try:
            # Validate token using injected JWT service
            token_payload = await jwt_service.validate_token(credentials.credentials)

            # Get user
            user_repo = UserRepository(db)
            user = await user_repo.get(token_payload.sub)

            return user if user and user.is_active else None

        except Exception:
            return None

    return optional_user_getter


class RateLimitMiddleware:
    """
    Rate limiting middleware based on user or IP.

    Integrates with authentication to apply user-specific limits.
    """

    def __init__(
        self,
        requests_per_minute: int = 60,
        requests_per_hour: int = 1000,
    ):
        """
        Initialize rate limit middleware.

        Args:
            requests_per_minute: Max requests per minute
            requests_per_hour: Max requests per hour
        """
        self.requests_per_minute = requests_per_minute
        self.requests_per_hour = requests_per_hour

    async def __call__(
        self,
        request: Request,
        current_user: User | None = Depends(optional_user()),
    ) -> None:
        """
        Check rate limits for request.

        Args:
            request: Incoming request
            current_user: Authenticated user if available

        Raises:
            HTTPException: If rate limit exceeded
        """
        # Get identifier (user ID or IP)
        if current_user:
            identifier = f"user:{current_user.id}"
            # Use user-specific limits if configured
            limit = current_user.api_rate_limit or self.requests_per_hour
        else:
            # Use IP address for anonymous users
            client_ip = request.client.host if request.client else "unknown"
            identifier = f"ip:{client_ip}"
            limit = self.requests_per_minute

        # In production, implement actual rate limiting with Redis
        # For now, just log the check
        logger.debug("Rate limit check", identifier=identifier, limit=limit)
