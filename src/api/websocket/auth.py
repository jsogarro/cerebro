"""
WebSocket authentication utilities.

This module provides authentication and authorization for WebSocket connections.
"""


from jose import JWTError, jwt
from structlog import get_logger

from src.core.config import settings

logger = get_logger()


class WebSocketAuthError(Exception):
    """WebSocket authentication error."""

    def __init__(self, message: str, code: int = 1008):
        self.message = message
        self.code = code  # WebSocket close code
        super().__init__(message)


async def verify_websocket_token(token: str | None) -> str | None:
    """
    Verify JWT token for WebSocket authentication.

    Args:
        token: JWT token string

    Returns:
        User ID if token is valid, None otherwise

    Raises:
        WebSocketAuthError: If authentication fails
    """
    if not token:
        # For development/testing, allow anonymous connections
        if settings.ENVIRONMENT == "development":
            logger.warning(
                "Allowing anonymous WebSocket connection in development mode"
            )
            return None

        raise WebSocketAuthError("Authentication token required")

    try:
        # Remove 'Bearer ' prefix if present
        if token.startswith("Bearer "):
            token = token[7:]

        # Decode JWT token
        # Note: In production, load the public key from settings.JWT_PUBLIC_KEY_PATH
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,  # Use secret key for now, will update with proper key management
            algorithms=["HS256"],  # Will update to RS256 with proper key management
        )

        user_id = payload.get("sub")
        if not user_id or not isinstance(user_id, str):
            raise WebSocketAuthError("Invalid token: missing user ID")

        logger.info(
            "WebSocket authentication successful",
            user_id=user_id,
        )

        return str(user_id)

    except JWTError as e:
        logger.warning(
            "WebSocket JWT authentication failed",
            error=str(e),
        )
        raise WebSocketAuthError("Invalid authentication token") from e

    except Exception as e:
        logger.error(
            "WebSocket authentication error",
            error=str(e),
        )
        raise WebSocketAuthError("Authentication failed") from e


async def verify_project_access(user_id: str | None, project_id: str) -> bool:
    """
    Verify that a user has access to a specific project.

    Args:
        user_id: User ID (None for anonymous users)
        project_id: Project ID to check access for

    Returns:
        True if user has access, False otherwise
    """
    # For development/testing, allow access to all projects
    if settings.ENVIRONMENT == "development":
        return True

    # TODO: Implement proper project access control
    # This would typically check database for user-project relationships
    # For now, authenticated users can access any project
    return user_id is not None


def extract_client_type(user_agent: str | None) -> str:
    """
    Extract client type from User-Agent header.

    Args:
        user_agent: User-Agent header value

    Returns:
        Client type string ("cli", "web", etc.)
    """
    if not user_agent:
        return "unknown"

    user_agent_lower = user_agent.lower()

    if "research-cli" in user_agent_lower:
        return "cli"
    elif "python" in user_agent_lower:
        return "api"
    elif "websocket" in user_agent_lower:
        return "websocket"
    else:
        return "web"


async def authenticate_websocket_connection(
    token: str | None = None,
    user_agent: str | None = None,
) -> tuple[str | None, str]:
    """
    Authenticate a WebSocket connection and determine client type.

    Args:
        token: Authentication token
        user_agent: User-Agent header

    Returns:
        Tuple of (user_id, client_type)

    Raises:
        WebSocketAuthError: If authentication fails
    """
    # Authenticate user
    user_id = await verify_websocket_token(token)

    # Determine client type
    client_type = extract_client_type(user_agent)

    logger.info(
        "WebSocket connection authenticated",
        user_id=user_id,
        client_type=client_type,
    )

    return user_id, client_type
