# Security and Authentication Implementation Guide

## Overview

This document describes the comprehensive security and authentication implementation for the Multi-Agent Research Platform. The system implements defense-in-depth with multiple security layers including authentication, authorization, rate limiting, input validation, audit logging, and security monitoring.

## Architecture

### Security Layers

```
┌─────────────────────────────────────────────────────────────┐
│                        Client Request                        │
└─────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────┐
│                    Security Headers                          │
│              (CSP, HSTS, X-Frame-Options)                   │
└─────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────┐
│                      Rate Limiting                           │
│            (Sliding Window, Token Bucket)                    │
└─────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────┐
│                   Input Validation                           │
│         (SQL Injection, XSS, Command Injection)             │
└─────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────┐
│                    Authentication                            │
│                  (JWT RS256, MFA, OAuth)                    │
└─────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────┐
│                    Authorization                             │
│                  (RBAC, Permissions)                        │
└─────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────┐
│                    Business Logic                            │
│                  (Research Platform)                         │
└─────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────┐
│                     Audit Logging                            │
│              (All Actions & Security Events)                 │
└─────────────────────────────────────────────────────────────┘
```

## Components

### 1. Database Models

#### Authentication Models
- **User**: Core user model with authentication fields
- **PasswordHistory**: Track password changes and prevent reuse
- **UserSession**: Active session management with device tracking
- **MFASettings**: Multi-factor authentication configuration
- **OAuthAccount**: OAuth provider connections (Google, GitHub, etc.)

#### Security Models
- **AuditLog**: Comprehensive audit trail for all actions
- **SecurityAlert**: Security incidents and alerts
- **APIKey**: API key management with rate limits

### 2. Rate Limiting

#### Strategies
- **Sliding Window**: Most accurate, memory-intensive
- **Token Bucket**: Allows burst traffic
- **Fixed Window**: Simple and efficient
- **Leaky Bucket**: Smooths out bursts

#### Configuration
```python
# Rate limit configurations by endpoint
"/api/v1/auth/login": {
    "limit": 5,
    "window": 300,  # 5 attempts per 5 minutes
    "strategy": RateLimitStrategy.FIXED_WINDOW,
    "scope": RateLimitScope.IP
}

"/api/v1/research/execute": {
    "limit": 10,
    "window": 3600,  # 10 executions per hour
    "strategy": RateLimitStrategy.TOKEN_BUCKET,
    "scope": RateLimitScope.USER
}
```

### 3. Security Headers

#### Content Security Policy (CSP)
```
default-src 'self';
script-src 'self' 'nonce-{random}' 'strict-dynamic';
style-src 'self' 'nonce-{random}';
img-src 'self' data: https:;
connect-src 'self' https:;
frame-ancestors 'none';
```

#### Other Headers
- **Strict-Transport-Security**: HSTS with preload
- **X-Frame-Options**: DENY (clickjacking protection)
- **X-Content-Type-Options**: nosniff (MIME sniffing protection)
- **Permissions-Policy**: Restrictive feature policy

### 4. Input Validation

#### Detection Patterns
- SQL Injection patterns
- XSS (Cross-Site Scripting) patterns
- Command Injection patterns
- Path Traversal patterns
- LDAP Injection patterns
- XML Injection patterns

#### Sanitization
- HTML sanitization with allowed tags
- Filename sanitization
- URL validation
- Email validation
- IP address validation

### 5. JWT Authentication

#### Features
- **RS256 Algorithm**: Asymmetric signing for enhanced security
- **Token Types**: Access, Refresh, API Key, Password Reset, Email Verification, MFA Temp
- **Token Rotation**: Automatic refresh token rotation
- **Token Revocation**: Blacklist support with Redis
- **Device Fingerprinting**: Bind tokens to devices

#### Token Structure
```json
{
  "sub": "user_id",
  "email": "user@example.com",
  "roles": ["researcher"],
  "permissions": ["read", "write"],
  "jti": "unique_token_id",
  "iat": 1234567890,
  "exp": 1234567890,
  "type": "access",
  "device_id": "device_fingerprint"
}
```

### 6. Multi-Factor Authentication (MFA)

#### Supported Methods
- **TOTP**: Time-based One-Time Password (Google Authenticator)
- **SMS**: Text message verification
- **Email**: Email verification codes
- **Backup Codes**: Pre-generated recovery codes
- **WebAuthn**: Hardware security keys
- **Push**: Push notifications to mobile app

#### MFA Flow
1. User enters credentials
2. System validates credentials
3. If MFA enabled, prompt for second factor
4. Validate second factor
5. Issue session tokens

### 7. OAuth Integration

#### Supported Providers
- Google
- GitHub
- Microsoft
- Facebook
- LinkedIn
- Apple

#### OAuth Flow
1. Redirect to provider
2. User authorizes
3. Receive authorization code
4. Exchange for tokens
5. Fetch user profile
6. Create/update local account
7. Issue session tokens

### 8. Audit Logging

#### Event Types
- Authentication events (login, logout, password changes)
- Account events (creation, deletion, lockout)
- MFA events (enabled, disabled, verified)
- Session events (created, revoked, expired)
- API key events (created, used, revoked)
- Permission events (granted, revoked)
- Security events (suspicious activity, attacks)
- Data access events (read, write, delete, export)

#### Audit Log Entry
```json
{
  "event_type": "login_success",
  "severity": "info",
  "user_id": "user123",
  "action": "user_login",
  "resource_type": "session",
  "resource_id": "session123",
  "ip_address": "192.168.1.1",
  "user_agent": "Mozilla/5.0...",
  "metadata": {
    "mfa_used": true,
    "device_id": "device123"
  }
}
```

### 9. Security Alerts

#### Alert Types
- Suspicious login attempts
- Failed authentication attempts
- New device/location login
- Account security changes
- Rate limit violations
- Injection attempts
- Data exfiltration

#### Alert Severity Levels
- **Low**: Informational
- **Medium**: Requires attention
- **High**: Immediate action needed
- **Critical**: System breach or attack

## API Endpoints

### Authentication Endpoints

```python
# User Registration
POST /api/v1/auth/register
{
  "email": "user@example.com",
  "username": "username",
  "password": "SecurePassword123!",
  "full_name": "John Doe",
  "organization": "Research Lab"
}

# User Login
POST /api/v1/auth/login
{
  "email": "user@example.com",
  "password": "SecurePassword123!",
  "mfa_code": "123456",  # Optional
  "remember_me": false
}

# Token Refresh
POST /api/v1/auth/refresh
{
  "refresh_token": "eyJ..."
}

# Logout
POST /api/v1/auth/logout
{
  "refresh_token": "eyJ..."
}

# Password Reset Request
POST /api/v1/auth/password-reset
{
  "email": "user@example.com"
}

# Password Reset Confirm
POST /api/v1/auth/password-reset-confirm
{
  "token": "reset_token",
  "new_password": "NewSecurePassword123!"
}
```

### MFA Endpoints

```python
# Enable MFA
POST /api/v1/auth/mfa/enable
{
  "method": "totp"
}
Response: {
  "secret": "JBSWY3DPEHPK3PXP",
  "qr_code": "data:image/png;base64,..."
}

# Verify MFA Setup
POST /api/v1/auth/mfa/verify
{
  "code": "123456"
}

# Disable MFA
POST /api/v1/auth/mfa/disable
{
  "password": "current_password",
  "code": "123456"
}

# Generate Backup Codes
POST /api/v1/auth/mfa/backup-codes
Response: {
  "codes": ["ABC123", "DEF456", ...]
}
```

### OAuth Endpoints

```python
# OAuth Login
GET /api/v1/auth/oauth/{provider}

# OAuth Callback
GET /api/v1/auth/oauth/{provider}/callback

# Disconnect OAuth
DELETE /api/v1/auth/oauth/{provider}
```

### Session Management

```python
# Get Active Sessions
GET /api/v1/auth/sessions

# Revoke Session
DELETE /api/v1/auth/sessions/{session_id}

# Revoke All Sessions
POST /api/v1/auth/sessions/revoke-all
```

## Security Best Practices

### Password Requirements
- Minimum 8 characters
- At least one uppercase letter
- At least one lowercase letter
- At least one digit
- At least one special character
- Not in common password lists
- No personal information

### Session Security
- Secure, HttpOnly, SameSite cookies
- Session timeout after inactivity
- Concurrent session limits
- Device fingerprinting
- IP address validation

### API Security
- Rate limiting per endpoint
- Request signing for sensitive operations
- API key rotation
- IP whitelisting for admin endpoints

### Data Protection
- Encryption at rest (AES-256)
- Encryption in transit (TLS 1.3)
- Field-level encryption for PII
- Data minimization
- Right to deletion (GDPR)

## Monitoring and Alerting

### Metrics to Monitor
- Failed login attempts per user
- Rate limit violations
- Unusual access patterns
- Geographic anomalies
- Permission escalations
- Data export volumes

### Alert Thresholds
```python
alert_thresholds = {
    "failed_login_attempts": 5,      # per 15 minutes
    "rate_limit_exceeded": 10,       # per 5 minutes
    "suspicious_activity": 3,        # per hour
    "unauthorized_access": 1,        # immediate
    "data_exfiltration": 1,          # immediate
    "sql_injection_attempt": 1,      # immediate
}
```

### Incident Response
1. **Detection**: Automated alert generation
2. **Triage**: Severity assessment
3. **Containment**: Account lockout, session revocation
4. **Investigation**: Audit log analysis
5. **Remediation**: Password reset, security patches
6. **Recovery**: Service restoration
7. **Post-Incident**: Report and improvements

## Compliance

### GDPR Compliance
- Consent management
- Data portability
- Right to deletion
- Breach notification (72 hours)
- Privacy by design
- Data minimization

### HIPAA Compliance
- Access controls
- Audit logs
- Encryption
- Business associate agreements
- Risk assessments

### SOC 2 Controls
- Access management
- Change management
- Risk assessment
- Incident response
- Business continuity

## Testing

### Security Testing
- Unit tests for validators
- Integration tests for auth flow
- Penetration testing
- Vulnerability scanning
- Security code review

### Test Coverage Areas
- Input validation
- Authentication flows
- Authorization checks
- Rate limiting
- Session management
- Audit logging
- Alert generation

## Deployment

### Environment Variables
```bash
# JWT Configuration
JWT_PRIVATE_KEY_PATH=/keys/jwt_private.pem
JWT_PUBLIC_KEY_PATH=/keys/jwt_public.pem
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7

# OAuth Providers
GOOGLE_CLIENT_ID=your_client_id
GOOGLE_CLIENT_SECRET=your_client_secret
GITHUB_CLIENT_ID=your_client_id
GITHUB_CLIENT_SECRET=your_client_secret

# Security Settings
BCRYPT_ROUNDS=12
ARGON2_TIME_COST=2
ARGON2_MEMORY_COST=65536
RATE_LIMIT_ENABLED=true
MFA_ENFORCED=false

# Email Service
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your_email
SMTP_PASSWORD=your_password
```

### Production Checklist
- [ ] Generate RSA keys for JWT
- [ ] Configure OAuth providers
- [ ] Set up Redis for sessions
- [ ] Configure email service
- [ ] Enable HTTPS only
- [ ] Set secure cookie flags
- [ ] Configure CORS origins
- [ ] Set up monitoring
- [ ] Configure alerting
- [ ] Review security headers
- [ ] Enable rate limiting
- [ ] Set up backup codes
- [ ] Configure audit retention
- [ ] Test incident response

## Maintenance

### Regular Tasks
- Review audit logs (daily)
- Check security alerts (hourly)
- Rotate API keys (quarterly)
- Update dependencies (monthly)
- Security patches (immediate)
- Penetration testing (annually)

### Key Rotation
- JWT signing keys (annually)
- OAuth client secrets (annually)
- Database encryption keys (annually)
- API keys (quarterly)

## Support

For security issues or questions:
- Security Team: security@researchplatform.com
- Bug Bounty: https://researchplatform.com/security/bug-bounty
- Security Updates: https://researchplatform.com/security/updates