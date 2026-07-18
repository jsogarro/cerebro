# Security and Authentication (Cerebro)

> **Canonical security document.** `docs/security-implementation.md` is superseded by this file; consult this document for the authoritative account of what Cerebro's security and authentication layer actually does.

Cerebro is a multi-agent LLM research platform (current focus: financial research, US equities). Its infrastructure identity is still the pre-rebrand **"research-platform"** — FastAPI title `Research Platform API`, DB `research_db`, and the `research-platform` k8s namespace/images. Those legacy names are kept verbatim in infra artifacts; the product is **Cerebro**.

This document is split into two clearly separated parts:

- **Part 1 — Implemented** — verified against the codebase and safe to rely on.
- **Part 2 — Not implemented / aspirational** — DB models or dead modules exist, but the runtime behavior does **not**. Do not describe these as live security controls.

---

# Part 1 — Implemented (verified)

## 1.1 JWT authentication

Cerebro authenticates with JWT tokens signed using **RS256** (RSA + SHA-256).

- **Algorithm**: `JWT_ALGORITHM = "RS256"` (`src/core/config.py:88`).
- **Access token**: 15 minutes (`JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 15`, `config.py:89`).
- **Refresh token**: 7 days (`JWT_REFRESH_TOKEN_EXPIRE_DAYS = 7`, `config.py:90`).
- **Keys**: PEM files at `/secrets/jwt_private.pem` and `/secrets/jwt_public.pem` (`JWT_PRIVATE_KEY_PATH` / `JWT_PUBLIC_KEY_PATH`, `config.py:91-92`). If the key path is unset or does not exist, `JWTService` **auto-generates a fresh RSA-2048 key pair** at startup (`src/auth/jwt_service.py:63,88`). When the parent directory is not writable (e.g. the production `/secrets/` mount on a dev machine), it logs a loud WARNING and falls back to an ephemeral in-memory key.
- **Revocation**: a **Redis-based token blacklist** keyed on the token `jti` (`blacklist:token:` prefix, `jwt_service.py:67`) is checked on every validation (`jwt_service.py:295`). Logout and revoke-all add tokens to the blacklist for immediate invalidation.

### Token payload (representative)

```json
{
  "sub": "user_uuid",
  "email": "user@example.com",
  "roles": ["user"],
  "permissions": [],
  "organization_id": "org_uuid",
  "token_type": "access",
  "device_id": "device_fingerprint",
  "iat": 1634567890,
  "exp": 1634568790,
  "jti": "unique_token_id"
}
```

The base payload (`src/auth/jwt_service.py:193-202`) carries `roles` and `permissions` lists: login populates `roles=[user.role]` (or `["user"]`) plus `"admin"` for superusers, and `permissions=["*"]` for superusers (`src/api/auth/auth_router.py:200-206`). There is **no** `session_id` claim. Note, however, that runtime enforcement **ignores** these lists — no live route calls `require_roles`/`require_permissions` — so authorization still reduces to the single `is_superuser` flag; see §1.6.

## 1.2 Password security

- **Hashing**: bcrypt with **12 rounds** (`BCRYPT_ROUNDS = 12`, `config.py:95`).
- **Minimum length**: **12 characters** (`PASSWORD_MIN_LENGTH = 12`, `config.py:96`).
- **Complexity**: both registration **and login** enforce the same policy. `LoginRequest.password` is `min_length=12` and runs a validator requiring uppercase, lowercase, digit, and special character (`src/auth/models.py:75-82`) — identical to `RegisterRequest`. There is no separate, weaker 8-character login path.
- **Password history**: `PASSWORD_HISTORY_LIMIT = 5` (`config.py:97`), enforced via **Redis lists** — `PasswordService.add_to_password_history` / `check_password_history` (`src/auth/password_service.py:254-300`) use `lpush`/`ltrim`/`lrange` with a 1-year TTL, called from `auth_router.py:136/355/372/419/436`. The `PasswordHistory` DB model (`src/models/db/password_history.py`) exists but is **never read or written** by any service.
- **Breach checks**: `CHECK_PASSWORD_BREACHES = True` (`config.py:98`).

## 1.3 Session management (partial — see caveats)

- **Storage**: Redis-backed session records under the `refresh:token:*` keys, governed by the **7-day refresh-token TTL**.
- **Revoke-all**: revoking all of a user's sessions/tokens in one call **is** implemented (see §1.7).
- **Concurrent session limit — NOT enforced**: `MAX_SESSIONS_PER_USER = 5` and `SESSION_EXPIRE_HOURS = 24` are **dead config** — defined only at `config.py:102-103` and referenced nowhere else in `src/` or tests. No 5-session limit and no 24-hour session expiry is enforced; session records instead expire with the 7-day refresh TTL.
- **Per-`device_id` termination — NOT enforced**: `DELETE /sessions/{device_id}` is a **stub** (`src/api/auth/auth_router.py:500-518`). It finds the matching session, assigns an unused `_jti`, logs `"Session revoked"`, and returns **without revoking anything** (in-code comment: `# Would need to revoke associated tokens`). Only revoke-all actually revokes tokens.

## 1.4 Per-endpoint authentication (not a gateway)

Authentication is enforced **per endpoint** via FastAPI dependencies — `Depends(get_current_user)` / `Depends(get_current_token)` — which validate the JWT through `JWTService.validate_token` (RS256 signature + blacklist check).

There is **no blanket API-gateway auth**. See §2.1 for the known gap this creates.

## 1.5 Middleware stack

Middleware is added in `src/api/main.py:181-204` in this order:

```
CORS → Idempotency → RateLimit → LLMCostDriftMiddleware → Auth (no-op)
```

- **CORS** (`config.CORS_ORIGINS`, credentials allowed).
- **Idempotency** — Redis-backed (`ResilientIdempotencyStore` over `RedisIdempotencyStore`), dedupes replayed requests.
- **Rate limiting** — Redis-backed flat limiter: **100 requests/minute** (`MAX_REQUESTS_PER_MINUTE = 100`, `ENABLE_RATE_LIMITING = True`). Implemented in `src/api/middleware/rate_limiter.py`. A single global limiter — **no tiers, no burst, no per-endpoint configuration.**
- **LLMCostDriftMiddleware** — cost-drift observability, not a security control.
- **AuthMiddleware** — **a no-op** (see §2.1).

## 1.6 Authorization: single `is_superuser` flag

Authorization is a **single boolean**, `is_superuser` (`src/auth/models.py:323`). There is **no `Role` enum** and no role/permission matrix in the runtime. The multi-role catalog previously documented is aspirational — see §2.4.

## 1.7 Authentication endpoints (live)

All auth routes are mounted under the `/api/v1/auth` prefix.

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/auth/register` | User registration |
| POST | `/api/v1/auth/login` | User login |
| POST | `/api/v1/auth/refresh` | Refresh access token |
| POST | `/api/v1/auth/logout` | Logout and revoke token |
| POST | `/api/v1/auth/forgot-password` | Request password reset |
| POST | `/api/v1/auth/reset-password` | Complete password reset |
| POST | `/api/v1/auth/change-password` | Change password |
| GET  | `/api/v1/auth/verify-email` | Verify email address |
| GET  | `/api/v1/auth/sessions` | List active sessions |
| DELETE | `/api/v1/auth/sessions/{device_id}` | Terminate a session by device |
| POST | `/api/v1/auth/revoke-all` | Revoke all sessions/tokens for the user |
| GET  | `/api/v1/auth/me` | Get current user |

(Source: `src/api/auth/auth_router.py` — router prefix `/auth`, mounted under `/api/v1`.)

## 1.8 GDPR data deletion (the only compliance endpoint)

| Method | Endpoint | Description |
|--------|----------|-------------|
| DELETE | `/api/v1/users/{user_id}/gdpr` | GDPR "right to erasure" — delete a user's data |

(`src/api/routes/users.py:30`.) This single delete endpoint is the **only** implemented compliance feature — see §2.9.

## 1.9 Environment variables (implemented defaults)

```env
# JWT
JWT_ALGORITHM=RS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=15
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7
JWT_PRIVATE_KEY_PATH=/secrets/jwt_private.pem   # auto-generated if missing
JWT_PUBLIC_KEY_PATH=/secrets/jwt_public.pem     # auto-generated if missing

# Password security
BCRYPT_ROUNDS=12
PASSWORD_MIN_LENGTH=12
PASSWORD_HISTORY_LIMIT=5
CHECK_PASSWORD_BREACHES=true

# Sessions (defined in config but NOT enforced at runtime — see §1.3)
SESSION_EXPIRE_HOURS=24
MAX_SESSIONS_PER_USER=5

# Rate limiting (flat, global)
MAX_REQUESTS_PER_MINUTE=100
ENABLE_RATE_LIMITING=true

# MFA (scaffolding only — see Part 2)
ENABLE_MFA=false
```

### RSA key generation (optional — keys auto-generate if absent)

```bash
mkdir -p secrets
openssl genrsa -out secrets/jwt_private.pem 2048
openssl rsa -in secrets/jwt_private.pem -pubout -out secrets/jwt_public.pem
chmod 600 secrets/jwt_private.pem
chmod 644 secrets/jwt_public.pem
```

---

# Part 2 — Not implemented / aspirational

Everything below exists only as **DB-model scaffolding, dead/test-only code, or design intent**. None of it is wired into the request path. Do not represent it as a live security control.

## 2.1 No API-gateway authentication (known gap)

`AuthMiddleware` (`src/middleware/auth_middleware.py`) is **effectively a no-op**: it initializes `request.state.user/token_payload/organization_id = None` and validates nothing; its `exclude_paths` list is unused. Because auth is enforced only where an endpoint declares `Depends(get_current_*)`, the primary product routes are **effectively unauthenticated**:

- `/api/v1/query/*`
- `/api/v1/agents/*`
- `/api/v1/masr/*`

Only the auth routes and the research routes declare auth dependencies — research is protected via `get_tenant_context` → `get_current_token` (`src/middleware/tenant_context.py:56-58`). Notably, the GDPR delete endpoint is **also unauthenticated**: `delete_user_gdpr` (`src/api/routes/users.py:30-35`) declares only `Depends(get_session)` and `Depends(get_memory_system)` — no `get_current_user`/token — so anyone can delete any user's data. `reports.py` has **zero** auth dependencies (it does not even import `Depends`, `src/api/routes/reports.py:12`). **This is a known gap, not a designed public API.**

## 2.2 OAuth2 — scaffolding only

There are **no OAuth2 routes mounted anywhere.** An `oauth_account` DB model exists as scaffolding, but no provider-login, callback, link, or unlink endpoints are served. Google/GitHub/Microsoft/etc. OAuth is **planned, not implemented**.

## 2.3 Multi-Factor Authentication (MFA) — scaffolding only

`ENABLE_MFA` defaults to **False** (`config.py:114`). An `mfa_settings` DB model exists, but **no MFA endpoints are mounted** and no TOTP/SMS/WebAuthn/backup-code flow is wired. MFA is **planned, not implemented**.

## 2.4 Role-based access control — not implemented

The catalog of roles (Superuser / Admin / Researcher / Viewer / Guest) and a resource:action permission matrix is **aspirational**. The runtime has **no `Role` enum** and no permission engine — authorization is the single `is_superuser` boolean (§1.6).

## 2.5 Security headers (CSP / HSTS / X-Frame-Options) — not applied

**No security-headers middleware is in the application stack** (`src/api/main.py` adds only CORS, Idempotency, RateLimit, CostDrift, Auth). `src/security/headers.py` exists but is **test-only dead code** — it is not in any request path. Content-Security-Policy, Strict-Transport-Security, X-Frame-Options, X-Content-Type-Options, etc. are **not set** on responses.

## 2.6 Four-strategy rate-limiting engine — dead code

Production rate limiting is the flat global limiter in §1.5 (`src/api/middleware/rate_limiter.py`, 100/min). The multi-strategy engine (token bucket / sliding window / fixed window / leaky bucket) in `src/security/rate_limiter.py` is **dead code** — not wired into the app. There are no per-endpoint or burst rate-limit configurations.

## 2.7 Input-validation layer — dead code

`src/security/validators.py` (SQL-injection / XSS / path-traversal / command-injection / file-upload validators) is **dead code, not in the request path.** (Cerebro does rely on SQLAlchemy parameterized queries and Pydantic request-model validation, but the dedicated `validators.py` layer is not invoked.)

## 2.8 Audit logging — models only, subsystem not wired

An `audit_log` DB model exists, but the "40+ event types" audit subsystem is **not wired**. `src/security/audit_logger.py` is test-only dead code. Likewise the Security Alerts subsystem ("26 alert types with automatic remediation") has a `security_alert` DB model but **no live detection or remediation engine**.

## 2.9 Compliance reporting — not implemented

The **only** compliance feature that exists is the single GDPR delete endpoint (§1.8). GDPR access/consent reporting, HIPAA, SOC 2, and PCI DSS controls and reports are **not implemented**.

## 2.10 Database security claims — unsubstantiated

There is **no evidence** in the codebase of field-level, row-level, or column-level encryption, query auditing, or "Redis session encryption at rest." These claims are **unsubstantiated** and should not be represented as implemented. Database connections use TLS where the deployment provides it; application-layer encryption of PII fields is not present.

---

## Summary matrix

| Area | Status |
|---|---|
| JWT RS256, 15-min / 7-day, Redis blacklist revocation | Implemented |
| bcrypt 12 rounds | Implemented |
| Password min-length 12 + complexity (login and register) | Implemented |
| Password history (limit 5), breach checks | Implemented |
| Session revoke-all | Implemented |
| Session limit 5 / 24h expiry, per-`device_id` termination | **Not implemented** (dead config; per-device delete is a stub) |
| Per-endpoint `Depends` auth | Implemented |
| CORS + Idempotency + flat Redis rate limit (100/min) | Implemented |
| GDPR delete endpoint | Implemented |
| API-gateway auth (query/agents/masr protected) | **Not implemented** (no-op middleware) |
| OAuth2 login | **Not implemented** (model scaffolding) |
| MFA | **Not implemented** (model scaffolding, default off) |
| RBAC / role catalog | **Not implemented** (`is_superuser` only) |
| Security headers (CSP/HSTS/etc.) | **Not implemented** (dead code) |
| Multi-strategy rate limiter | **Not implemented** (dead code) |
| Input-validation layer | **Not implemented** (dead code) |
| Audit logging / security alerts | **Not implemented** (models only) |
| HIPAA / SOC 2 / PCI reporting | **Not implemented** |
| Field/row/column encryption | **Unsubstantiated** |
