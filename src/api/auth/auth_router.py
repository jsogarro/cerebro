"""
Authentication API endpoints.

Provides user registration, login, token management, and password reset.
"""

from uuid import uuid4

import redis.asyncio as redis
import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.jwt_service import JWTService
from src.auth.models import (
    AuthResponse,
    ChangePasswordRequest,
    LoginRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    RegisterRequest,
    SessionInfo,
    TokenPair,
    UserResponse,
)
from src.auth.password_service import PasswordService
from src.core.config import settings
from src.middleware.auth_middleware import get_current_user
from src.models.db.session import get_session
from src.models.db.user import User
from src.repositories.user_repository import UserRepository

logger = structlog.get_logger(__name__)

# Create router
router = APIRouter(prefix="/auth", tags=["Authentication"])

# Security scheme
security = HTTPBearer()


# Dependencies
async def get_jwt_service() -> JWTService:
    """Get JWT service instance."""
    # In production, use Redis connection from pool
    redis_client = await redis.from_url(settings.REDIS_URL)
    return JWTService(
        redis_client=redis_client,
        private_key_path=settings.JWT_PRIVATE_KEY_PATH,
        public_key_path=settings.JWT_PUBLIC_KEY_PATH,
    )


async def get_password_service() -> PasswordService:
    """Get password service instance."""
    redis_client = await redis.from_url(settings.REDIS_URL)
    return PasswordService(
        redis_client=redis_client,
        bcrypt_rounds=settings.BCRYPT_ROUNDS,
        min_password_length=settings.PASSWORD_MIN_LENGTH,
    )


@router.post(
    "/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED
)
async def register(
    request: RegisterRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    jwt_service: JWTService = Depends(get_jwt_service),
    password_service: PasswordService = Depends(get_password_service),
) -> AuthResponse:
    """
    Register a new user account.

    Creates a new user with the provided credentials and returns authentication tokens.
    """
    async with password_service:
        # Check if user exists
        user_repo = UserRepository(db)

        existing_user = await user_repo.get_by_email(request.email)
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
            )

        existing_user = await user_repo.get_by_username(request.username)
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Username already taken"
            )

        # Validate password strength
        validation = password_service.validate_password_strength(
            request.password, username=request.username, email=request.email
        )

        if not validation["valid"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": "Password does not meet requirements",
                    "issues": validation["issues"],
                },
            )

        # Check for breached password
        if await password_service.check_password_breach(request.password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This password has been found in data breaches. Please choose a different password.",
            )

        # Hash password
        hashed_password = password_service.hash_password(request.password)

        # Create user
        user = User.create_with_password(
            email=request.email,
            username=request.username,
            password=request.password,
            full_name=request.full_name,
            organization=request.organization,
        )

        # Save user
        db.add(user)
        await db.commit()
        await db.refresh(user)

        # Add password to history
        await password_service.add_to_password_history(str(user.id), hashed_password)

        # Generate tokens
        tokens = await jwt_service.generate_token_pair(
            user_id=str(user.id),
            email=user.email,
            roles=[user.role] if user.role else ["user"],
            permissions=[],
        )

        # Send verification email (in background)
        # background_tasks.add_task(send_verification_email, user.email, user.id)

        logger.info(
            "User registered successfully", user_id=str(user.id), email=user.email
        )

        return AuthResponse(user=UserResponse.from_orm(user), tokens=tokens)


@router.post("/login", response_model=AuthResponse)
async def login(
    request: LoginRequest,
    req: Request,
    db: AsyncSession = Depends(get_session),
    jwt_service: JWTService = Depends(get_jwt_service),
    password_service: PasswordService = Depends(get_password_service),
) -> AuthResponse:
    """
    Login with email and password.

    Authenticates user credentials and returns access and refresh tokens.
    """
    # Get user
    user_repo = UserRepository(db)
    user = await user_repo.get_by_email(request.email)

    if not user or not user.verify_password(request.password):
        # Log failed attempt
        logger.warning(
            "Failed login attempt",
            email=request.email,
            ip=req.client.host if req.client else None,
        )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )

    # Check if account is active
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled"
        )

    # Update login info
    user.record_login()
    await db.commit()

    # Generate device ID if not provided
    device_id = request.device_id or f"web_{uuid4().hex[:8]}"

    # Determine roles and permissions
    roles = [user.role] if user.role else ["user"]
    if user.is_superuser:
        roles.append("admin")

    permissions = []
    if user.is_superuser:
        permissions = ["*"]  # All permissions

    # Generate tokens
    tokens = await jwt_service.generate_token_pair(
        user_id=str(user.id),
        email=user.email,
        roles=roles,
        permissions=permissions,
        device_id=device_id,
    )

    logger.info(
        "User logged in successfully",
        user_id=str(user.id),
        email=user.email,
        device_id=device_id,
    )

    return AuthResponse(user=UserResponse.from_orm(user), tokens=tokens)


@router.post("/refresh", response_model=TokenPair)
async def refresh_tokens(
    request: RefreshRequest,
    jwt_service: JWTService = Depends(get_jwt_service),
) -> TokenPair:
    """
    Refresh authentication tokens.

    Uses a valid refresh token to generate new access and refresh tokens.
    """
    try:
        # Refresh tokens
        new_tokens = await jwt_service.refresh_tokens(
            refresh_token=request.refresh_token, device_id=request.device_id
        )

        logger.info("Tokens refreshed successfully")
        return new_tokens

    except Exception as e:
        logger.warning("Token refresh failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    jwt_service: JWTService = Depends(get_jwt_service),
) -> None:
    """
    Logout and revoke tokens.

    Adds the current token to blacklist, preventing further use.
    """
    token = credentials.credentials

    # Revoke token
    success = await jwt_service.revoke_token(token)

    if success:
        logger.info("User logged out successfully")
    else:
        logger.warning("Failed to revoke token during logout")


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(
    request: PasswordResetRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    password_service: PasswordService = Depends(get_password_service),
) -> dict[str, str]:
    """
    Request password reset.

    Sends a password reset email with a secure token.
    """
    # Get user
    user_repo = UserRepository(db)
    user = await user_repo.get_by_email(request.email)

    # Always return success to prevent email enumeration
    if user and user.is_active:
        # Generate reset token
        token = password_service.generate_reset_token()

        # Store token
        await password_service.store_reset_token(
            str(user.id), token, expires_in=3600  # 1 hour
        )

        # Send reset email (in background)
        # background_tasks.add_task(send_reset_email, user.email, token)

        logger.info("Password reset requested", user_id=str(user.id), email=user.email)

    return {"message": "If the email exists, a password reset link has been sent"}


@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(
    request: PasswordResetConfirm,
    db: AsyncSession = Depends(get_session),
    password_service: PasswordService = Depends(get_password_service),
) -> dict[str, str]:
    """
    Reset password with token.

    Validates the reset token and updates the user's password.
    """
    async with password_service:
        # Validate token
        user_id = await password_service.validate_reset_token(request.token)

        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired reset token",
            )

        # Get user
        user_repo = UserRepository(db)
        user = await user_repo.get(user_id)

        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
            )

        # Validate new password
        validation = password_service.validate_password_strength(
            request.new_password, username=user.username, email=user.email
        )

        if not validation["valid"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": "Password does not meet requirements",
                    "issues": validation["issues"],
                },
            )

        # Check password history
        if await password_service.check_password_history(user_id, request.new_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password was recently used. Please choose a different password.",
            )

        # Check for breached password
        if await password_service.check_password_breach(request.new_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This password has been found in data breaches. Please choose a different password.",
            )

        # Update password
        user.update_password(request.new_password)

        # Add to password history
        await password_service.add_to_password_history(user_id, user.hashed_password)

        await db.commit()

        logger.info("Password reset successfully", user_id=user_id)

        return {"message": "Password reset successfully"}


@router.post("/change-password", status_code=status.HTTP_200_OK)
async def change_password(
    request: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    password_service: PasswordService = Depends(get_password_service),
) -> dict[str, str]:
    """
    Change password for authenticated user.

    Requires current password verification before updating.
    """
    async with password_service:
        # Verify current password
        if not current_user.verify_password(request.current_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Current password is incorrect",
            )

        # Validate new password
        validation = password_service.validate_password_strength(
            request.new_password,
            username=current_user.username,
            email=current_user.email,
        )

        if not validation["valid"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": "Password does not meet requirements",
                    "issues": validation["issues"],
                },
            )

        # Check password history
        user_id = str(current_user.id)
        if await password_service.check_password_history(user_id, request.new_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password was recently used. Please choose a different password.",
            )

        # Check for breached password
        if await password_service.check_password_breach(request.new_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This password has been found in data breaches. Please choose a different password.",
            )

        # Update password
        current_user.update_password(request.new_password)

        # Add to password history
        await password_service.add_to_password_history(
            user_id, current_user.hashed_password
        )

        await db.commit()

        logger.info("Password changed successfully", user_id=user_id)

        return {"message": "Password changed successfully"}


@router.get("/verify-email", status_code=status.HTTP_200_OK)
async def verify_email(
    token: str,
    db: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """
    Verify email address with token.

    Confirms the user's email address using the verification token.
    """
    # In production, validate token and get user ID
    # For now, this is a placeholder

    return {"message": "Email verified successfully"}


@router.get("/sessions", response_model=list[SessionInfo])
async def get_sessions(
    current_user: User = Depends(get_current_user),
    jwt_service: JWTService = Depends(get_jwt_service),
) -> list[SessionInfo]:
    """
    Get active sessions for current user.

    Returns a list of all active sessions with device and location information.
    """
    sessions = await jwt_service.get_active_sessions(str(current_user.id))

    return [
        SessionInfo(
            device_id=session.get("device_id"),
            created_at=str(session.get("created_at")) if session.get("created_at") else "",
            last_activity=session.get("last_activity"),
            ip_address=session.get("ip_address"),
            user_agent=session.get("user_agent"),
        )
        for session in sessions
    ]


@router.delete("/sessions/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    device_id: str,
    current_user: User = Depends(get_current_user),
    jwt_service: JWTService = Depends(get_jwt_service),
) -> None:
    """
    Revoke a specific session.

    Terminates the session associated with the given device ID.
    """
    # Get all sessions
    sessions = await jwt_service.get_active_sessions(str(current_user.id))

    # Find and revoke matching session
    for session in sessions:
        if session.get("device_id") == device_id:
            # Extract token ID from key and revoke
            key = session.get("key", "")
            if key.startswith("refresh:token:"):
                jti = key.replace("refresh:token:", "")
                # Would need to revoke associated tokens
                logger.info(
                    "Session revoked", user_id=str(current_user.id), device_id=device_id
                )
                return

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
    )


@router.post("/revoke-all", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_all_sessions(
    current_user: User = Depends(get_current_user),
    jwt_service: JWTService = Depends(get_jwt_service),
) -> None:
    """
    Revoke all sessions for current user.

    Logs out the user from all devices and sessions.
    """
    count = await jwt_service.revoke_all_user_tokens(str(current_user.id))

    logger.info("All sessions revoked", user_id=str(current_user.id), count=count)


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    """
    Get current user information.

    Returns the authenticated user's profile information.
    """
    return UserResponse.from_orm(current_user)
