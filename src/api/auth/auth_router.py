"""
Authentication API endpoints.

Provides user registration, login, token management, and password reset.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import UUID, uuid4

import redis.asyncio as redis
import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth.tokens_router import router as tokens_router
from src.api.client_ip import client_ip
from src.api.middleware.rate_limiter import (
    RedisRateLimitStore,
    ResilientRateLimitStore,
)
from src.auth import mfa
from src.auth.jwt_service import (
    JWTService,
    JWTServiceUnavailableError,
    RefreshIdentity,
)
from src.auth.models import (
    AuthResponse,
    ChangePasswordRequest,
    LoginRequest,
    MfaActivateResponse,
    MfaCodeRequest,
    MfaEnrollRequest,
    MfaEnrollResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    RegisterRequest,
    SessionInfo,
    TokenPair,
    UserResponse,
)
from src.auth.password_service import (
    PasswordService,
    PasswordTokenStoreUnavailableError,
)
from src.auth.pat_service import user_claims
from src.core.config import settings
from src.core.memory_encryption import DocumentEncryptionUnavailable
from src.middleware.auth_middleware import (
    get_current_token,
    get_current_user,
    get_jwt_service,
)
from src.models.db.session import get_session
from src.models.db.user import User
from src.repositories.user_repository import UserRepository
from src.security.login_throttle import LoginThrottle

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])
router.include_router(tokens_router)

# Security scheme
security = HTTPBearer()


# Dependencies
async def get_password_service(
    jwt_service: JWTService = Depends(get_jwt_service),
) -> AsyncIterator[PasswordService]:
    """Yield a password service over the app's shared Redis client.

    Only a client this request opened itself is closed here; the shared one
    outlives every request.
    """
    shared = jwt_service.redis_client
    redis_client = (
        shared if shared is not None else await redis.from_url(settings.REDIS_URL)
    )
    try:
        yield PasswordService(
            redis_client=redis_client,
            bcrypt_rounds=settings.BCRYPT_ROUNDS,
            min_password_length=settings.PASSWORD_MIN_LENGTH,
            password_history_limit=settings.PASSWORD_HISTORY_LIMIT,
            check_breaches=settings.CHECK_PASSWORD_BREACHES,
        )
    finally:
        if shared is None:
            await redis_client.close()


_login_throttle: LoginThrottle | None = None


def get_login_throttle() -> LoginThrottle:
    """Get the process-wide failed-login throttle (overridable in tests)."""
    global _login_throttle
    if _login_throttle is None:
        _login_throttle = LoginThrottle(
            ResilientRateLimitStore(
                RedisRateLimitStore(settings.REDIS_URL, key_prefix="login_fail")
            ),
            max_failures=settings.LOGIN_MAX_FAILED_ATTEMPTS,
            lockout_seconds=settings.LOGIN_LOCKOUT_SECONDS,
        )
    return _login_throttle


def provisioned_organization_id(user: User) -> UUID:
    """Return the user's tenant organization, assigning one if it has none.

    Every tenant-scoped surface fails closed without an ``organization_id``
    claim, so a user with no organization can reach none of them. Users
    created before the boundary existed have ``organization_id`` NULL; they
    are given one here, on the next authentication, rather than by a
    backfill migration that would have to invent the same value anyway.

    The organization is a fresh UUID, never derived from the free-text
    ``organization`` profile field. Deriving it from client-supplied text
    would let anyone join an existing tenant by typing its name; joining an
    existing organization is an administrative action this module does not
    provide.

    The caller is responsible for committing: a newly assigned organization
    must be durable before it is minted into a token.
    """
    if user.organization_id is None:
        user.organization_id = uuid4()
        logger.info("Provisioned tenant organization for user", user_id=str(user.id))
    return user.organization_id


def _claims_for(user: User) -> tuple[list[str], list[str]]:
    """Roles and permissions an account carries in its tokens."""
    return user_claims(user)


async def _current_identity(
    db: AsyncSession, user_id: str, mfa_verified: bool
) -> RefreshIdentity:
    """Resolve the claims a refresh may mint, from storage.

    A storage failure is unavailability, never a reason to mint from what the
    old token said. Refresh does not provision an organization: an account
    without one has lost its tenant and must sign in again. A session that
    never passed a second factor cannot outlive the account enrolling one.
    """
    try:
        user = await UserRepository(db).get(user_id)
    except Exception as exc:
        raise JWTServiceUnavailableError("Account store unavailable") from exc
    if user is None or not user.is_active or user.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        )
    if user.totp_enabled and not mfa_verified:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        )
    roles, permissions = _claims_for(user)
    return RefreshIdentity(
        user_id=str(user.id),
        email=user.email,
        organization_id=str(user.organization_id),
        roles=roles,
        permissions=permissions,
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

        if await password_service.check_password_breach(request.password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This password has been found in data breaches. Please choose a different password.",
            )

        # Hash password
        hashed_password = password_service.hash_password(request.password)

        # Create user. Every account gets its own tenant organization at
        # creation: the token's organization claim is what every
        # tenant-scoped surface authorizes against, and an account without
        # one can reach none of them.
        user = User.create_with_password(
            email=request.email,
            username=request.username,
            password=request.password,
            full_name=request.full_name,
            organization=request.organization,
            organization_id=uuid4(),
        )

        # Save the user enough to obtain its UUID before issuing a
        # verification token. The token is persisted for the configured
        # delivery integration; this route does not claim that an email was
        # sent because Cerebro does not provide an email provider.
        db.add(user)
        try:
            await db.flush()
        except IntegrityError as exc:
            # The pre-checks above skip soft-deleted rows and race with
            # concurrent registrations; the unique indexes are the authority.
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email or username already registered",
            ) from exc
        verification_token = password_service.generate_verification_token()
        try:
            await password_service.store_verification_token(
                str(user.id), verification_token
            )
        except PasswordTokenStoreUnavailableError as exc:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Email verification service unavailable",
            ) from exc

        # Add to password history before committing the account so a missing
        # authentication store cannot leave a partially provisioned user.
        await password_service.add_to_password_history(str(user.id), hashed_password)

        await db.commit()
        await db.refresh(user)

        # Generate tokens
        tokens = await jwt_service.generate_token_pair(
            user_id=str(user.id),
            email=user.email,
            roles=[user.role] if user.role else ["user"],
            permissions=[],
            organization_id=str(provisioned_organization_id(user)),
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
    throttle: LoginThrottle = Depends(get_login_throttle),
) -> AuthResponse:
    """
    Login with email and password.

    Authenticates user credentials and returns access and refresh tokens.
    Repeated failures lock the account identifier for a while, whether or not
    the account exists.
    """
    retry_after = await throttle.begin(request.email)
    if retry_after:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    user_repo = UserRepository(db)
    user = await user_repo.get_by_email(request.email)

    if not user or not user.verify_password(request.password):
        if user:
            # The audit trail keys failed-login alerts on the account, not on
            # the submitted address.
            req.state.audit_subject_id = user.id
        logger.warning(
            "Failed login attempt",
            email=request.email,
            ip=client_ip(req),
        )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled"
        )

    try:
        second_factor_ok = not user.totp_enabled or bool(
            request.totp_code
            and await mfa.verify_second_factor(db, user, request.totp_code)
        )
    except mfa.MfaUnavailableError as exc:
        raise _mfa_unavailable(req, user) from exc
    if not second_factor_ok:
        req.state.audit_subject_id = user.id
        logger.warning("Failed MFA code at login", ip=client_ip(req))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid MFA code is required",
        )

    # Update login info. Accounts created before the tenant boundary
    # existed have no organization; give them one here so the token they
    # are about to receive can carry the claim every scoped surface needs.
    await throttle.clear(request.email)
    user.record_login()
    organization_id = provisioned_organization_id(user)
    await db.commit()
    await db.refresh(user)  # Refresh to keep user attached after commit

    # Generate device ID if not provided
    device_id = request.device_id or f"web_{uuid4().hex[:8]}"

    roles, permissions = _claims_for(user)

    # Generate tokens
    tokens = await jwt_service.generate_token_pair(
        user_id=str(user.id),
        email=user.email,
        roles=roles,
        permissions=permissions,
        device_id=device_id,
        organization_id=str(organization_id),
        mfa_verified=user.totp_enabled,
    )

    logger.info(
        "User logged in successfully",
        user_id=str(user.id),
        email=user.email,
        device_id=device_id,
    )

    return AuthResponse(user=UserResponse.from_orm(user), tokens=tokens)


def _mfa_unavailable(req: Request, user: User) -> HTTPException:
    """A stored secret that cannot be decrypted is an outage, never a bad code."""
    req.state.audit_subject_id = user.id
    logger.error("MFA secret cannot be decrypted; check DOCUMENT_ENCRYPTION_KEY")
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="MFA verification unavailable",
    )


async def _throttled_check(
    user: User,
    req: Request,
    throttle: LoginThrottle,
    check: Callable[[], Awaitable[bool]],
    failure: str,
) -> None:
    """Throttle an MFA-management check and raise 401 (audited) when it fails."""
    key = f"mfa:{user.id}"
    retry_after = await throttle.begin(key)
    if retry_after:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed MFA attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )
    try:
        valid = await check()
    except mfa.MfaUnavailableError as exc:
        raise _mfa_unavailable(req, user) from exc
    if not valid:
        req.state.audit_subject_id = user.id
        logger.warning("Failed MFA check", ip=client_ip(req))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=failure)
    await throttle.clear(key)


def _require_enrolment_enabled() -> None:
    """New enrolment is switchable; enforcement and ``/disable`` never are."""
    if not settings.ENABLE_MFA:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="MFA enrolment is disabled"
        )


@router.post("/mfa/enroll", response_model=MfaEnrollResponse)
async def mfa_enroll(
    request: MfaEnrollRequest,
    req: Request,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    throttle: LoginThrottle = Depends(get_login_throttle),
) -> MfaEnrollResponse:
    """Generate a pending TOTP secret; it takes effect once a code verifies it."""
    _require_enrolment_enabled()
    if user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="MFA is already enabled"
        )

    async def password_ok() -> bool:
        return user.verify_password(request.current_password)

    await _throttled_check(user, req, throttle, password_ok, "Invalid password")
    try:
        secret = mfa.new_secret()
        user.totp_secret_enc = mfa.encrypt_secret(secret)
    except DocumentEncryptionUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MFA secret storage unavailable",
        ) from exc
    user.totp_last_step = None
    await db.commit()
    return MfaEnrollResponse(
        secret=secret, provisioning_uri=mfa.provisioning_uri(secret, user.email)
    )


@router.post("/mfa/verify", response_model=MfaActivateResponse)
async def mfa_verify(
    request: MfaCodeRequest,
    req: Request,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    throttle: LoginThrottle = Depends(get_login_throttle),
    jwt_service: JWTService = Depends(get_jwt_service),
) -> MfaActivateResponse:
    """Activate MFA with a code from the pending secret and issue recovery codes.

    Every existing session ends, this one included: tokens minted before the
    second factor existed must not outlive it.
    """
    _require_enrolment_enabled()
    if user.totp_enabled or user.totp_secret_enc is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="No MFA enrolment is pending"
        )
    await _throttled_check(
        user,
        req,
        throttle,
        lambda: mfa.consume_totp(db, user, request.code.strip()),
        "Invalid MFA code",
    )
    codes = mfa.new_recovery_codes()
    await mfa.store_recovery_codes(db, user, codes)
    user.totp_enabled = True
    await _revoke_sessions(jwt_service, user)
    await db.commit()
    return MfaActivateResponse(recovery_codes=codes)


@router.post("/mfa/disable", status_code=status.HTTP_204_NO_CONTENT)
async def mfa_disable(
    request: MfaCodeRequest,
    req: Request,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    throttle: LoginThrottle = Depends(get_login_throttle),
    jwt_service: JWTService = Depends(get_jwt_service),
) -> None:
    """Turn MFA off; a valid TOTP or recovery code is required. Ends all sessions."""
    if not user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="MFA is not enabled"
        )
    await _throttled_check(
        user,
        req,
        throttle,
        lambda: mfa.verify_second_factor(db, user, request.code),
        "Invalid MFA code",
    )
    await mfa.store_recovery_codes(db, user, [])
    user.totp_enabled = False
    user.totp_secret_enc = None
    user.totp_last_step = None
    await _revoke_sessions(jwt_service, user)
    await db.commit()


async def _revoke_sessions(jwt_service: JWTService, user: User) -> None:
    """Revoke before committing, so an outage leaves MFA state unchanged."""
    try:
        await jwt_service.revoke_all_user_tokens(str(user.id))
    except JWTServiceUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from exc


@router.post("/refresh", response_model=TokenPair)
async def refresh_tokens(
    request: RefreshRequest,
    jwt_service: JWTService = Depends(get_jwt_service),
    db: AsyncSession = Depends(get_session),
) -> TokenPair:
    """
    Refresh authentication tokens.

    Uses a valid refresh token to generate new access and refresh tokens. The
    token's user must still exist and be active.
    """
    try:
        refresh_payload = await jwt_service.validate_token(
            request.refresh_token, token_type="refresh"
        )
        identity = await _current_identity(db, refresh_payload.sub, refresh_payload.mfa)
        new_tokens = await jwt_service.refresh_tokens(
            request.refresh_token, identity=identity, device_id=request.device_id
        )

        logger.info("Tokens refreshed successfully")
        return new_tokens

    except HTTPException:
        raise
    except JWTServiceUnavailableError as e:
        logger.warning("Token refresh service unavailable", error=type(e).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from e
    except Exception as e:
        logger.warning("Token refresh failed", error=type(e).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        ) from e


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(get_current_token)],
)
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
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        )


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(
    request: PasswordResetRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    password_service: PasswordService = Depends(get_password_service),
) -> dict[str, str]:
    """
    Request password reset.

    Accepts a password recovery request and persists a secure token. Email
    delivery is intentionally outside this application because no provider is
    configured here.
    """
    async with password_service:
        try:
            # Check availability before looking up the email so an outage
            # cannot reveal whether an account exists.
            await password_service.ensure_token_store_available()
        except PasswordTokenStoreUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Password recovery service unavailable",
            ) from exc

        user_repo = UserRepository(db)
        user = await user_repo.get_by_email(request.email)

        # Keep the response generic for unknown or inactive accounts. A real
        # token is persisted for active accounts; no delivery success is
        # claimed because no email provider is wired into this application.
        if user and user.is_active:
            token = password_service.generate_reset_token()

            try:
                await password_service.store_reset_token(
                    str(user.id),
                    token,
                    expires_in=3600,  # 1 hour
                )
            except PasswordTokenStoreUnavailableError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Password recovery service unavailable",
                ) from exc

            logger.info(
                "Password reset requested", user_id=str(user.id), email=user.email
            )

    return {"message": "If the email exists, the recovery request was accepted"}


@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(
    request: PasswordResetConfirm,
    db: AsyncSession = Depends(get_session),
    password_service: PasswordService = Depends(get_password_service),
    jwt_service: JWTService = Depends(get_jwt_service),
) -> dict[str, str]:
    """
    Reset password with token.

    Validates the reset token and updates the user's password.
    """
    async with password_service:
        try:
            user_id = await password_service.validate_reset_token(request.token)
        except PasswordTokenStoreUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Password recovery service unavailable",
            ) from exc

        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired reset token",
            )

        user_repo = UserRepository(db)
        user = await user_repo.get(user_id)

        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
            )

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

        if await password_service.check_password_history(user_id, request.new_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password was recently used. Please choose a different password.",
            )

        if await password_service.check_password_breach(request.new_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This password has been found in data breaches. Please choose a different password.",
            )

        user.update_password(request.new_password)

        await password_service.add_to_password_history(user_id, user.hashed_password)

        await db.commit()
        # A reset is account recovery: every existing session must end.
        await jwt_service.revoke_all_user_tokens(str(user.id))

        logger.info("Password reset successfully", user_id=user_id)

        return {"message": "Password reset successfully"}


@router.post("/change-password", status_code=status.HTTP_200_OK)
async def change_password(
    request: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    password_service: PasswordService = Depends(get_password_service),
    jwt_service: JWTService = Depends(get_jwt_service),
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

        user_id = str(current_user.id)
        if await password_service.check_password_history(user_id, request.new_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password was recently used. Please choose a different password.",
            )

        if await password_service.check_password_breach(request.new_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This password has been found in data breaches. Please choose a different password.",
            )

        current_user.update_password(request.new_password)

        await password_service.add_to_password_history(
            user_id, current_user.hashed_password
        )

        await db.commit()
        await jwt_service.revoke_all_user_tokens(user_id)

        logger.info("Password changed successfully", user_id=user_id)

        return {"message": "Password changed successfully"}


@router.get("/verify-email", status_code=status.HTTP_200_OK)
async def verify_email(
    token: str,
    db: AsyncSession = Depends(get_session),
    password_service: PasswordService = Depends(get_password_service),
) -> dict[str, str]:
    """
    Verify email address with token.

    Confirms the user's email address using the verification token.
    """
    async with password_service:
        try:
            user_id = await password_service.validate_verification_token(token)
        except PasswordTokenStoreUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Email verification service unavailable",
            ) from exc

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token",
        )

    try:
        parsed_user_id = UUID(user_id)
    except (AttributeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token",
        ) from exc

    user = await UserRepository(db).verify_email(parsed_user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token",
        )

    await db.commit()

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
            created_at=str(session.get("created_at"))
            if session.get("created_at")
            else "",
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
    sessions = await jwt_service.get_active_sessions(str(current_user.id))

    # Find and revoke matching session
    for session in sessions:
        if session.get("device_id") == device_id:
            # Extract token ID from key and revoke
            key = session.get("key", "")
            if key.startswith("refresh:token:"):
                await jwt_service.revoke_refresh_session(
                    key.removeprefix("refresh:token:")
                )
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
    try:
        count = await jwt_service.revoke_all_user_tokens(str(current_user.id))
    except JWTServiceUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from exc

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
