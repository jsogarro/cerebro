# Security and Authentication Implementation Plan

## Overview
Implement a production-ready security and authentication system for the Multi-Agent Research Platform with JWT authentication, OAuth2 integration, rate limiting, and comprehensive security features.

## Phase 1: JWT Authentication Core

### 1.1 JWT Token Service (`src/auth/jwt_service.py`)
- Generate RS256 tokens with public/private key pairs
- Create access tokens (15 min expiry) and refresh tokens (7 days expiry)
- Implement token validation and decoding
- Add token blacklisting for logout using Redis
- Manage token claims (user_id, email, roles, permissions)

### 1.2 Authentication Models (`src/auth/models.py`)
- `TokenPayload`: JWT token structure with claims
- `TokenPair`: Access and refresh token pair
- `LoginRequest`: Email/password validation model
- `RegisterRequest`: User registration with validation
- `RefreshRequest`: Token refresh payload
- `PasswordResetRequest`: Password reset models

### 1.3 Password Service (`src/auth/password_service.py`)
- Bcrypt password hashing with configurable rounds
- Password strength validation (length, complexity)
- Password history tracking to prevent reuse
- Secure password reset token generation
- Optional breach detection integration

### 1.4 Authentication Endpoints (`src/api/auth/auth_router.py`)
- `POST /auth/register`: User registration with email verification
- `POST /auth/login`: User login returning JWT tokens
- `POST /auth/refresh`: Refresh access token
- `POST /auth/logout`: Revoke tokens
- `POST /auth/forgot-password`: Request password reset
- `POST /auth/reset-password`: Complete password reset
- `GET /auth/verify-email`: Email verification
- `GET /auth/me`: Get current user info

### 1.5 Update Authentication Middleware
- Enhance existing `auth_middleware.py` with proper JWT validation
- Add request context with user information
- Implement permission and role-based access control
- Add rate limiting per user/IP

## Phase 2: OAuth2 Integration

### 2.1 OAuth2 Base (`src/auth/oauth2/base.py`)
- Abstract OAuth2 provider interface
- Common OAuth2 flow handling
- State parameter for CSRF protection
- PKCE support for enhanced security

### 2.2 Provider Implementations
- **Google OAuth** (`src/auth/oauth2/google_provider.py`)
  - Google OAuth2 flow with ID token validation
  - Optional Google Workspace domain restrictions
- **GitHub OAuth** (`src/auth/oauth2/github_provider.py`)
  - GitHub OAuth2 implementation
  - Organization membership verification

### 2.3 OAuth2 Endpoints (`src/api/auth/oauth2_router.py`)
- `GET /auth/oauth2/{provider}/login`: Initiate OAuth flow
- `GET /auth/oauth2/{provider}/callback`: Handle callback
- `POST /auth/oauth2/{provider}/link`: Link OAuth account
- `DELETE /auth/oauth2/{provider}/unlink`: Unlink account

## Phase 3: Advanced Security Features

### 3.1 Rate Limiting (`src/security/rate_limiter.py`)
- Token bucket algorithm implementation
- Redis-based distributed rate limiting
- Configurable limits per endpoint
- User-specific and IP-based limits

### 3.2 Request Validation (`src/security/validators.py`)
- Input sanitization for XSS prevention
- SQL injection prevention (already handled by SQLAlchemy)
- CSRF token validation for state-changing operations
- Request size limits

### 3.3 Security Headers (`src/middleware/security_headers.py`)
- Content Security Policy (CSP)
- X-Frame-Options, X-Content-Type-Options
- Strict-Transport-Security (HSTS)
- Referrer-Policy

### 3.4 Session Management (`src/auth/session_service.py`)
- Redis-based session storage
- Session fingerprinting for security
- Concurrent session limits
- Automatic session expiry

### 3.5 Audit Logging (`src/security/audit_logger.py`)
- Structured audit events
- Authentication attempts logging
- Permission changes tracking
- Failed access attempts

## Phase 4: Database Updates

### 4.1 New Database Models
- `PasswordHistory`: Track password history
- `UserSession`: Active sessions tracking
- `AuditLog`: Security audit trail
- `OAuthAccount`: OAuth provider accounts
- `MFASettings`: Multi-factor auth settings

### 4.2 Alembic Migration
- Create migration for new auth tables
- Add indexes for performance
- Set up foreign key relationships

## Phase 5: Testing & Documentation

### 5.1 Comprehensive Tests
- Unit tests for JWT service
- Integration tests for auth endpoints
- OAuth2 flow tests
- Rate limiting tests
- Security header tests

### 5.2 Documentation
- API documentation with examples
- Security best practices guide
- Authentication flow diagrams
- Configuration guide

## Implementation Order

1. **Day 1: Core JWT Authentication**
   - JWT service implementation
   - Authentication models
   - Password service
   - Basic auth endpoints
   - Update existing middleware

2. **Day 2: OAuth2 & Security Features**
   - OAuth2 base and providers
   - Rate limiting
   - Security headers
   - Session management
   - Audit logging

3. **Day 3: Database & Testing**
   - Database models and migrations
   - Comprehensive test suite
   - Documentation
   - Integration testing

## Key Security Considerations

1. **Token Security**
   - Use RS256 algorithm with key pairs
   - Short-lived access tokens
   - Secure token storage (never in localStorage)
   - Token rotation on refresh

2. **Password Security**
   - Bcrypt with 12+ rounds
   - Minimum 12 character passwords
   - Password complexity requirements
   - History to prevent reuse

3. **Session Security**
   - Session fingerprinting
   - IP validation
   - Concurrent session limits
   - Automatic expiry

4. **API Security**
   - Rate limiting
   - Input validation
   - CORS configuration
   - Security headers

## Dependencies to Add
- `PyJWT[crypto]` for RS256 support
- `python-multipart` for OAuth2 forms
- `httpx` for OAuth2 requests
- `pyotp` for TOTP/MFA (future)

## Success Criteria
- ✅ Secure user registration and login
- ✅ JWT token generation and validation
- ✅ OAuth2 integration working
- ✅ Rate limiting preventing abuse
- ✅ All endpoints properly secured
- ✅ Comprehensive audit logging
- ✅ 90%+ test coverage
- ✅ No OWASP Top 10 vulnerabilities

## Configuration

### Environment Variables
```env
# JWT Configuration
JWT_ALGORITHM=RS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=15
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7
JWT_PRIVATE_KEY_PATH=/secrets/jwt_private.pem
JWT_PUBLIC_KEY_PATH=/secrets/jwt_public.pem

# OAuth2 Providers
GOOGLE_CLIENT_ID=xxx
GOOGLE_CLIENT_SECRET=xxx
GITHUB_CLIENT_ID=xxx
GITHUB_CLIENT_SECRET=xxx

# Security Settings
BCRYPT_ROUNDS=12
PASSWORD_MIN_LENGTH=12
MFA_ISSUER=ResearchPlatform
RATE_LIMIT_REQUESTS=100
RATE_LIMIT_PERIOD=60

# Session Configuration
SESSION_SECRET_KEY=xxx
SESSION_EXPIRE_HOURS=24
MAX_SESSIONS_PER_USER=5
```

### Database Schema Updates
```sql
-- Password history table
CREATE TABLE password_history (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    password_hash VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Session table
CREATE TABLE user_sessions (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    token_hash VARCHAR(255) UNIQUE,
    device_info JSONB,
    ip_address INET,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Audit log table
CREATE TABLE audit_logs (
    id UUID PRIMARY KEY,
    user_id UUID,
    action VARCHAR(100),
    resource_type VARCHAR(50),
    resource_id UUID,
    details JSONB,
    ip_address INET,
    user_agent TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- OAuth accounts table
CREATE TABLE oauth_accounts (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    provider VARCHAR(50),
    provider_user_id VARCHAR(255),
    access_token TEXT,
    refresh_token TEXT,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(provider, provider_user_id)
);

-- MFA settings table
CREATE TABLE mfa_settings (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id) UNIQUE,
    totp_secret VARCHAR(255),
    backup_codes TEXT[],
    sms_number VARCHAR(20),
    is_enabled BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);
```