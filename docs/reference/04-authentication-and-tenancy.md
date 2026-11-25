# Authentication and tenancy

Callers authenticate with RS256-signed JWTs issued by the `/api/v1/auth` routes and checked on every request by `AuthMiddleware`. The token's `organization_id` claim is the tenant boundary: tenant-scoped routes read it, set it on the database session, and filter every repository query by it. Routes are listed in [HTTP API](03-http-api.md).

## Summary

| Feature | Status | Entry point | Source |
|---|---|---|---|
| Default-deny request authentication | Active | every request | `src/middleware/auth_middleware.py` (`AuthMiddleware`, `PUBLIC_ROUTE_ALLOWLIST`) |
| Route-level token dependency | Active | `Depends(get_current_token)` | `src/middleware/auth_middleware.py` (`get_current_token`) |
| WebSocket token authentication | Active | `?token=` query parameter | `src/api/websocket/auth.py` (`require_authenticated_websocket`) |
| Anonymous WebSocket project access | Off by default | `DEV_ALLOW_ANONYMOUS_WEBSOCKETS` | `src/api/websocket/auth.py` (`verify_project_access`) |
| RS256 access and refresh tokens | Active | `/api/v1/auth/login`, `/register`, `/refresh` | `src/auth/jwt_service.py` (`JWTService`) |
| Token and session revocation | Active | `/api/v1/auth/logout`, `/revoke-all`, `/sessions/{device_id}` | `src/auth/jwt_service.py` (`revoke_token`, `revoke_all_user_tokens`) |
| Password policy and history | Active | register, reset, change | `src/auth/password_service.py` (`PasswordService`) |
| Failed-login lockout | Active | `POST /api/v1/auth/login` | `src/security/login_throttle.py` (`LoginThrottle`) |
| Tenant context and RLS setting | Active | `get_tenant_context` | `src/middleware/tenant_context.py` |
| Repository tenant scoping | Active | repositories | `src/repositories/base.py` (`apply_organization_scope`), `src/repositories/tenant_scope.py` (`scope_to_organization`) |
| Postgres row-level security policies | Active | `tenant_isolation_<table>` policies, created on Postgres only | `alembic/versions/` (see [Tenancy](#tenancy)) |
| User data erasure | Active | `DELETE /api/v1/users/{user_id}/gdpr` | `src/api/routes/users.py` (`delete_user_gdpr`) |
| Memory-tier purge during erasure | Not wired | `app.state.memory_system` | `src/api/routes/users.py` (`get_memory_system`) |
| Role, permission and superuser dependencies | Not wired | `require_roles`, `require_permissions`, `require_superuser` | `src/middleware/auth_middleware.py` |
| MFA, OAuth, session limits, breach-check setting | Not wired | settings only | `src/core/config.py` (`Settings`) |

## Request authentication

`AuthMiddleware` (`src/middleware/auth_middleware.py`) runs on every HTTP request.

1. It clears `request.state.user`, `user_id`, `token_payload` and `organization_id`.
2. If the request's exact `(method, path)` pair is a key of `PUBLIC_ROUTE_ALLOWLIST`, it passes the request on. The allowlist is listed in [HTTP API](03-http-api.md#authentication-requirement). `/metrics` is not in it.
3. Otherwise it reads the `Authorization` header. A header that is missing, is not the `Bearer` scheme, or has no token returns `401` with code `AUTHENTICATION_REQUIRED` and a `WWW-Authenticate: Bearer` header.
4. It resolves the shared `JWTService` and calls `validate_token`. A `JWTError` returns `401` ("Invalid authentication token"). An unavailable service, key store or revocation store (`AuthenticationServiceUnavailableError`, `JWTServiceUnavailableError`, or any other exception) returns `503` with code `AUTHENTICATION_SERVICE_UNAVAILABLE`.
5. On success it stores the identity on the request (`user_id` is the token `sub`) and calls the next layer.

The constructor argument `exclude_paths` is accepted and ignored: access is defined only by the exact allowlist, so a newly mounted route is protected by default.

`get_current_token` is a FastAPI dependency that reuses the payload set by the middleware and otherwise validates the bearer credential itself (`401` for a missing or invalid token, `503` when the service is unavailable). Domain routers attach it at router or route level; see [HTTP API](03-http-api.md#routers).

### WebSocket authentication

Mounted WebSocket routes depend on `require_authenticated_websocket` (`src/api/websocket/auth.py`). It reads only the `token` query parameter (a leading `Bearer ` is stripped) and validates it through the same `JWTService` as HTTP, so the key pair and revocation checks are shared. An `Authorization` header alone does not authenticate a WebSocket. It always calls `authenticate_websocket_principal` with `allow_anonymous=False`: a missing token closes the connection with code 1008 and a failed or unavailable service closes with 1008 or 1011 (`WebSocketException`). The validated principal (`user_id`, `organization_id`, client type) is stored on `websocket.state.websocket_principal`.

The run event stream (`/ws/runs/{run_id}`) additionally requires an `organization_id` claim (`resolve_run_stream_entitlement`).

> **Off by default.** With `ENVIRONMENT=development` and `DEV_ALLOW_ANONYMOUS_WEBSOCKETS=true`, `verify_project_access` returns `True` for any project without checking ownership. Enable with `DEV_ALLOW_ANONYMOUS_WEBSOCKETS=true`. It requires both settings; it does not admit a connection without a token, because every mounted route already requires one.

Without that opt-in, `verify_project_access` allows a project subscription only when the caller has a user id, an organization claim and a database session, and the project belongs to the caller within the caller's organization (`ResearchRepository.get_for_user`). The code states that the application-level organization filter is the enforcement here, because the policies are not forced for the table owner (see [Tenancy](#tenancy)).

## Tokens

- **Algorithm.** `JWTService.algorithm` is the literal `RS256`. The `JWT_ALGORITHM` setting is not read.
- **Keys.** `get_jwt_service` passes `JWT_PRIVATE_KEY_PATH` and `JWT_PUBLIC_KEY_PATH` (defaults `/secrets/jwt_private.pem` and `/secrets/jwt_public.pem`). If the private key file exists it is loaded. If it does not and `ENVIRONMENT` is not `production`, the service generates an RSA-2048 pair, tries to write it to the configured path, and falls back to an in-memory key with a warning when the path is not writable. When `ENVIRONMENT` is `production`, a missing, unreadable, invalid or mismatched key file raises `RuntimeError`. The public key must match the private key in every environment.
- **Lifetimes.** `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` (default 15) and `JWT_REFRESH_TOKEN_EXPIRE_DAYS` (default 7).
- **Access token claims.** `sub`, `email`, `roles`, `permissions`, `organization_id`, `jti`, `iat`, `iat_ns`, `device_id`, `exp`, `token_type` (`access`).
- **Refresh token claims.** `sub`, `jti` (`refresh_<access jti>`), `iat`, `iat_ns`, `exp`, `token_type` (`refresh`), `device_id`, `organization_id`. It carries no `email`, `roles` or `permissions`, so a token pair produced by refresh has an empty `email` claim and empty `roles` and `permissions` lists.
- **Token pair.** `access_token`, `refresh_token`, `token_type` (`Bearer`) and `expires_in` in seconds.
- **Redis keys.** `refresh:token:<refresh jti>` (session record: `user_id`, `device_id`, `organization_id`, `created_at`; expires with the refresh token), `blacklist:token:<jti>` (revoked access token, expires with the token), `blacklist:user:<user id>` (user-wide revocation marker), `password:history:<user id>`, `password:reset:<token>` and `email:verification:<token>`.
- **Validation.** `validate_token` checks the signature and expiry, that `token_type` is `access`, that `jti` exists, that the `jti` is not blacklisted, and that the token was issued after the user-wide revocation marker (compared by `iat_ns`). If Redis cannot be reached, validation raises `JWTServiceUnavailableError`, which surfaces as `503`; it never admits a token it could not check.
- **Refresh.** `POST /api/v1/auth/refresh` validates the refresh token, requires the user to exist and be active (`401` otherwise), rejects a `device_id` that differs from the token's, and consumes the `refresh:token:<jti>` record with `GETDEL`, so each refresh token works once. A missing record returns `401`.
- **Sessions.** Revoking the refresh record (`logout`, `DELETE /sessions/{device_id}`, `revoke-all`) ends the session: its refresh token cannot be exchanged.
- **One service per process.** `get_jwt_service` builds one `JWTService` (one Redis client and parsed key pair) and caches it; the application lifespan closes it through `close_jwt_service`.

## Auth endpoints

All routes are in `src/api/auth/auth_router.py`, mounted at `/api/v1/auth`.

| Method | Path | Request model | Response model | Behaviour |
|---|---|---|---|---|
| `POST` | `/register` | `RegisterRequest` | `AuthResponse` (201) | Rejects an existing email or username (`409`; the unique indexes `uq_users_email_lower` and `uq_users_username_lower` from migration `b5d7e9f1a3c2` make the match case-insensitive), checks password strength and the breach list (`400`), creates the user with a new organization id, stores an email verification token, records the password in history, commits, and returns the user and a token pair with `roles` `[user.role]` (or `["user"]`) and no `device_id`. |
| `POST` | `/login` | `LoginRequest` | `AuthResponse` | Counts the attempt against the lockout (`429` with `Retry-After` when locked), checks email and password (`401`), requires an active account (`403`), clears the lockout counter, records the login, assigns an organization id if the user has none, and returns a token pair. Superusers receive the extra role `admin` and permissions `["*"]`. |
| `POST` | `/refresh` | `RefreshRequest` | `TokenPair` | See [Tokens](#tokens). |
| `POST` | `/logout` | none | 204 | Blacklists the presented access token for its remaining lifetime and deletes the paired refresh record (`503` if revocation fails). |
| `POST` | `/forgot-password` | `PasswordResetRequest` | `{"message": ...}` (202) | Persists a one-hour reset token for an active account. The response is identical for unknown and inactive accounts. No email is sent: the application has no email provider. `503` when the token store is unavailable. |
| `POST` | `/reset-password` | `PasswordResetConfirm` | `{"message": ...}` | Consumes the reset token (`400` invalid or expired), checks strength, history and the breach list, sets the password, then revokes every token of the user. |
| `POST` | `/change-password` | `ChangePasswordRequest` | `{"message": ...}` | Requires the current password (`401` if wrong), applies the same checks, sets the password and revokes every token of the user, including the one presented. |
| `GET` | `/verify-email` | query `token` | `{"message": ...}` | Consumes a 24-hour verification token and marks the user verified (`400` invalid or expired). Login does not require a verified email. |
| `GET` | `/sessions` | none | `list[SessionInfo]` | Lists the caller's refresh records. |
| `DELETE` | `/sessions/{device_id}` | none | 204 | Deletes the refresh record of the caller's session for that `device_id` (`404` if there is none). An access token already issued stays valid until it expires. |
| `POST` | `/revoke-all` | none | 204 | Writes the user-wide revocation marker and deletes the user's refresh records (`503` if the store is unavailable). |
| `GET` | `/me` | none | `UserResponse` | Returns the caller's user record (`404` if the user does not exist). |

**`RegisterRequest`.** `email`, `username` (3 to 50 characters; letters, digits, `_` and `-`; stored lower-case), `password`, `confirm_password` (must equal `password`), `full_name` (optional, up to 255), `organization` (optional free text, up to 255), `accept_terms` (must be `true`). `organization` is stored on the user as profile text and has no tenancy effect; `accept_terms` is validated and not stored.

**`LoginRequest`.** `email`, `password`, `device_id` (optional; the route generates `web_<8 hex characters>` when absent), `remember_me`. `remember_me` is declared and never read, so it changes nothing. The password field applies the full complexity rules, so a login password that breaks them is rejected with `422` before any lookup.

**Other models.** `RefreshRequest` (`refresh_token`, optional `device_id`), `PasswordResetRequest` (`email`), `PasswordResetConfirm` (`token`, `new_password`, `confirm_password`), `ChangePasswordRequest` (`current_password`, `new_password`, `confirm_password`; the new password must differ from the current one).

**`SessionInfo`.** `device_id`, `created_at`, `last_activity`, `ip_address`, `user_agent`. A refresh record stores only the user, device, organization and creation time, so `last_activity`, `ip_address` and `user_agent` are always `null`.

## Passwords and login protection

- **Account hash.** The account password is hashed with the module-level bcrypt context in `src/models/db/user.py`, created without a rounds argument. `BCRYPT_ROUNDS` (default 12) applies only to `PasswordService.hash_password`, which produces the password-history entry written at registration.
- **Length and complexity.** The request models require at least 12 characters with an upper-case letter, a lower-case letter, a digit and one of `!@#$%^&*()_+-=[]{}|;:,.<>?`. `PasswordService.validate_password_strength` then runs for register, reset and change. It rejects, with the listed issues returned in a `400` detail: fewer than `PASSWORD_MIN_LENGTH` characters (default 12), a missing character class (special characters are `string.punctuation`), a common pattern (`123456`, `password`, `qwerty`, `abc123`, `111111`, `123123`, `admin`, `letmein`), a username or email local part contained in the password, three sequential or three repeated characters, and entropy below 40.
- **Breach check.** `PasswordService.check_password_breach` sends the first five characters of the password's SHA-1 to `api.pwnedpasswords.com` and rejects a match (`400`). It is always enabled: `get_password_service` does not pass the `CHECK_PASSWORD_BREACHES` setting, and the constructor default is `True`. An error from the service is treated as "not breached".
- **History.** Each user has a Redis list `password:history:<user id>` trimmed to `password_history_limit` entries (the constructor default, 5, because `get_password_service` does not pass `PASSWORD_HISTORY_LIMIT`) with a one-year expiry. Reset and change reject a password that matches an entry (`400`). Register, reset and change append the new hash. Without a Redis client the history operations raise instead of reporting an unchecked password as unused.
- **Lockout.** `LoginThrottle` counts every login attempt for a SHA-256 of the normalized email (`strip().casefold()`), whether or not the account exists, in a fixed window that starts at the first attempt. After `LOGIN_MAX_FAILED_ATTEMPTS` (default 5), further attempts in the window return `429` with `Retry-After` set to the seconds left in the window (`LOGIN_LOCKOUT_SECONDS`, default 900). The window is never extended by further attempts. A successful login clears the counter. Counters are in Redis under the `login_fail` prefix with an in-process fallback.

## Roles and permissions

> **Not wired.** `require_roles`, `require_permissions` and `require_superuser` are defined in `src/middleware/auth_middleware.py` and re-exported by `src/middleware/__init__.py`. No route depends on them. The module also defines `optional_user` and a second class named `RateLimitMiddleware` that nothing imports; the rate limiter that runs is the one in `src/api/middleware/rate_limiter.py`.

Tokens carry `roles` and `permissions` lists (see [Auth endpoints](#auth-endpoints) for what login and register put in them). Most routes require authentication only. The checks that read the user record are:

- `get_current_user` loads the user named by the token subject (`404` if missing). `/change-password`, `/sessions`, `/sessions/{device_id}`, `/revoke-all` and `/me` use it.
- `get_current_active_user` adds `403` ("Account is disabled") for an inactive user. The erasure route uses it.
- The superuser flag (`User.is_superuser`) is read by the erasure route.

## Tenancy

**Claim.** `organization_id` is a claim in the access token. Every account gets one: `register` creates the user with a new UUID organization id, and `login` assigns a new UUID to a user that has none (`provisioned_organization_id`). The id is never derived from the free-text `organization` field. Nothing in the application adds a user to an existing organization.

**Context.** `src/middleware/tenant_context.py` provides:

- `require_organization_id` — returns the claim or raises `403` ("Tenant organization claim is required").
- `get_tenant_identity` — resolves `TenantContext(user_id, organization_id)` from the token without opening a database session.
- `get_tenant_context` — the same, and also takes a database session and calls `set_postgres_tenant_context`, which runs `SELECT set_config('app.current_org_id', :organization_id, true)` so the setting lasts for the current transaction. On a non-Postgres dialect it does nothing.

**Row-level security.** Migrations enable row-level security and create a policy `tenant_isolation_<table>` (`organization_id = current_setting('app.current_org_id', true)::uuid`) on `research_projects` (`b7a9c2d4e8f1`), on the six run-lifecycle tables `agent_runs`, `agent_run_tasks`, `agent_task_attempts`, `agent_run_config_snapshots`, `agent_run_events` and `agent_run_event_outbox` (`c3f5a1d7b209`), and on the six tool-boundary tables `agent_capability_grants`, `agent_capability_approvals`, `agent_artifacts`, `agent_tool_invocations`, `agent_evidence` and `agent_claim_supports` (`5d04cec6c232`). The organization columns come from `9d8c7b6a5e4f`. The migrations use `ENABLE ROW LEVEL SECURITY` without `FORCE ROW LEVEL SECURITY`, so the policies do not bind the table owner; the organization filter in the repositories is the enforcement the application relies on. See [data model](14-data-model-and-persistence.md).

**Repository scoping.** `BaseRepository.apply_organization_scope` filters a query on a tenant-scoped model to one organization. It fails closed: a tenant-scoped model queried without an organization raises `ValueError` (unless the repository declares `allows_unscoped_access`), and a model with no organization column raises `ValueError` when an organization is supplied. `scope_to_organization` in `src/repositories/tenant_scope.py` is the shared read filter of the tool-boundary repositories (artifacts, evidence, claim supports, tool invocations, capability grants and approvals); it leaves the query unscoped when given `None` (boot-time scans), while the write path (`normalize_organization_id`) raises `MissingOrganizationContextError` without an organization.

**Cross-tenant reads.** A project, execution, run or report in another organization is reported as `404`, the same as one that does not exist.

## GDPR erasure

`DELETE /api/v1/users/{user_id}/gdpr` (`delete_user_gdpr`) requires an active account. The caller must be the user named in the path or a superuser; otherwise `403`. The sequence is:

1. Revoke every token of the user (`revoke_all_user_tokens`). If the token store is unavailable the route returns `503` and deletes nothing.
2. Delete, in order, the user's `generated_reports` (rows owned by the user or attached to the user's projects; `report_formats` follow through the database cascade), `research_results` and `workflow_checkpoints` of the user's projects, the user's `research_projects`, and the `users` row.
3. Return `{"user_id", "deleted": {users, research_projects, generated_reports, research_results, workflow_checkpoints, memory}}` with row counts.

> **Not wired.** `MultiTierMemorySystem.purge_user_data` is called only when `app.state.memory_system` is a `MultiTierMemorySystem` (`get_memory_system`). Nothing in `src` assigns `app.state.memory_system`, so the `memory` entry of the response is always `{"working_memory": 0, "episodic_memory": 0}`.

## Settings with no effect

> **Not wired.** These settings are declared in `src/core/config.py` (`Settings`) and read nowhere in `src`: `ENABLE_MFA`, `MFA_ISSUER`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `SESSION_SECRET_KEY`, `SESSION_EXPIRE_HOURS`, `MAX_SESSIONS_PER_USER`, `CHECK_PASSWORD_BREACHES`, `PASSWORD_HISTORY_LIMIT`, `JWT_ALGORITHM`, `RATE_LIMIT_REQUESTS` and `RATE_LIMIT_PERIOD`. There are no MFA or OAuth routes, no session-count limit and no session lifetime other than the token lifetimes.

`SECRET_KEY` is validated at startup (at least 32 characters; the placeholder value is rejected when `ENVIRONMENT` is `production`) and is not used for token signing, which uses the RSA key pair.

The `oauth_accounts` table (migration `5bf6a31d1957`) and its `OAuthAccount` model exist, and no route or service reads or writes them. The tables `mfa_settings`, `user_sessions`, `password_history` and `api_keys` are dropped by migration `f8c9d0e1a2b3`; password history lives in Redis and sessions are refresh records in Redis. See [configuration](02-configuration.md) for the full settings reference.

## Source map

- `src/middleware/auth_middleware.py` — `PUBLIC_ROUTE_ALLOWLIST`, `AuthMiddleware`, `get_jwt_service`, `get_current_token`, `get_current_user`, `get_current_active_user`, and the unused role, permission and superuser dependencies.
- `src/middleware/tenant_context.py` — `TenantContext`, `get_tenant_context`, `get_tenant_identity`, `require_organization_id`, `set_postgres_tenant_context`.
- `src/api/auth/auth_router.py` — `/api/v1/auth` routes, `provisioned_organization_id`, `get_login_throttle`.
- `src/auth/jwt_service.py` — token issue, validation, refresh, revocation, key loading.
- `src/auth/password_service.py` — password policy, history, breach check, reset and verification tokens.
- `src/auth/models.py` — request, response and `TokenPayload` models.
- `src/security/login_throttle.py` — failed-login lockout.
- `src/api/websocket/auth.py` — WebSocket authentication and project access.
- `src/api/routes/users.py` — GDPR erasure.
- `src/repositories/base.py` — `apply_organization_scope`.
- `src/repositories/tenant_scope.py` — `scope_to_organization`, `normalize_organization_id`.
- `src/models/db/user.py` — `User`, password hashing context, `organization_id` column.
- `alembic/versions/` — tenant columns, policies and the authentication-table removal (`9d8c7b6a5e4f`, `b7a9c2d4e8f1`, `c3f5a1d7b209`, `5d04cec6c232`, `f8c9d0e1a2b3`, `b5d7e9f1a3c2`).
- `src/core/config.py` — JWT, password, login lockout and WebSocket development settings.
