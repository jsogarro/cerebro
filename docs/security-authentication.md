# Security and Authentication Documentation

## Overview

The Multi-Agent Research Platform implements enterprise-grade security and authentication with multiple layers of protection, comprehensive audit logging, and compliance support for GDPR, HIPAA, and SOC 2.

## Architecture

### Security Layers

```
┌─────────────────────────────────────────────────────┐
│                   Edge Layer                        │
│  Rate Limiting | DDoS Protection | IP Filtering     │
├─────────────────────────────────────────────────────┤
│                  API Gateway                        │
│  Authentication | Authorization | Request Routing   │
├─────────────────────────────────────────────────────┤
│                Application Layer                    │
│  Business Logic | Input Validation | RBAC          │
├─────────────────────────────────────────────────────┤
│                   Data Layer                        │
│  Encryption | Access Control | Data Masking        │
├─────────────────────────────────────────────────────┤
│                  Audit Layer                        │
│  Logging | Monitoring | Compliance Reporting       │
└─────────────────────────────────────────────────────┘
```

## Authentication System

### JWT Authentication

The platform uses JWT tokens with RS256 (RSA signature with SHA-256) for secure authentication:

- **Access Token**: Short-lived (15 minutes), contains user claims
- **Refresh Token**: Long-lived (7 days), used to obtain new access tokens
- **Token Rotation**: Automatic rotation on refresh for enhanced security
- **Blacklisting**: Redis-based token blacklist for immediate revocation

#### Token Structure

```json
{
  "sub": "user_uuid",
  "email": "user@example.com",
  "roles": ["researcher", "admin"],
  "permissions": ["read:projects", "write:projects"],
  "device_id": "device_fingerprint",
  "session_id": "session_uuid",
  "iat": 1634567890,
  "exp": 1634568790,
  "jti": "unique_token_id"
}
```

### OAuth2 Integration

Support for 11 OAuth providers:

- **Google**: Google OAuth 2.0 with OpenID Connect
- **GitHub**: GitHub OAuth with organization verification
- **Microsoft**: Azure AD and personal accounts
- **Facebook**: Facebook Login
- **Twitter**: Twitter OAuth 2.0
- **LinkedIn**: LinkedIn OAuth 2.0
- **Apple**: Sign in with Apple
- **Discord**: Discord OAuth2
- **Slack**: Slack OAuth with workspace integration
- **GitLab**: GitLab OAuth2
- **Bitbucket**: Bitbucket OAuth2

### Multi-Factor Authentication (MFA)

Multiple MFA methods supported:

1. **TOTP**: Time-based One-Time Passwords (Google Authenticator, Authy)
2. **SMS**: Text message verification
3. **Email**: Email verification codes
4. **Backup Codes**: One-time use recovery codes
5. **WebAuthn**: Hardware security keys (YubiKey, etc.)
6. **Push Notifications**: Mobile app push notifications

## Authorization System

### Role-Based Access Control (RBAC)

Predefined roles with specific permissions:

- **Superuser**: Full system access
- **Admin**: Administrative functions
- **Researcher**: Create and manage research projects
- **Viewer**: Read-only access
- **Guest**: Limited public access

### Permission System

Fine-grained permissions:

```python
# Resource:Action format
permissions = [
    "projects:read",
    "projects:write",
    "projects:delete",
    "users:manage",
    "api_keys:create",
    "settings:modify"
]
```

## Security Features

### Rate Limiting

Multiple rate limiting strategies:

1. **Token Bucket**: Burst capacity with sustained rate
2. **Sliding Window**: Smooth rate limiting
3. **Fixed Window**: Simple time-based limits
4. **Leaky Bucket**: Queue-based rate limiting

Configuration per endpoint:

```python
@rate_limit(
    strategy="token_bucket",
    requests_per_minute=100,
    burst_size=20,
    scope="user"  # user, ip, api_key
)
async def sensitive_endpoint():
    pass
```

### Security Headers

Comprehensive security headers:

- **Content-Security-Policy**: Prevents XSS attacks
- **X-Frame-Options**: Prevents clickjacking
- **X-Content-Type-Options**: Prevents MIME sniffing
- **Strict-Transport-Security**: Enforces HTTPS
- **X-XSS-Protection**: Additional XSS protection
- **Referrer-Policy**: Controls referrer information
- **Permissions-Policy**: Controls browser features

### Input Validation

Multiple layers of input validation:

1. **SQL Injection Prevention**: Parameterized queries, input sanitization
2. **XSS Prevention**: HTML escaping, content validation
3. **Path Traversal Prevention**: Path normalization, whitelist validation
4. **Command Injection Prevention**: Command sanitization, subprocess safety
5. **File Upload Validation**: Type checking, size limits, content scanning

### Password Security

Strong password requirements:

- **Minimum Length**: 12 characters
- **Complexity**: Uppercase, lowercase, numbers, special characters
- **History**: Prevents reuse of last 5 passwords
- **Expiration**: Optional password expiration policy
- **Breach Detection**: Checks against known breached passwords

Password hashing using bcrypt with 12 rounds.

## Session Management

### Session Security

- **Device Fingerprinting**: Tracks device characteristics
- **Geolocation Tracking**: Monitors login locations
- **Concurrent Session Limits**: Maximum 5 sessions per user
- **Automatic Expiration**: 24-hour timeout
- **Session Revocation**: Immediate termination capability

### Session Storage

Redis-based session storage with:

- High-performance access
- Distributed session sharing
- Automatic cleanup
- Encryption at rest

## Audit Logging

### Event Types

40+ security event types tracked:

- Authentication events (login, logout, failed attempts)
- Authorization events (permission changes, role assignments)
- Data access events (read, write, delete operations)
- Configuration changes
- Security alerts
- Compliance events

### Audit Log Structure

```json
{
  "id": "uuid",
  "timestamp": "2024-01-01T00:00:00Z",
  "event_type": "user_login",
  "severity": "info",
  "user_id": "user_uuid",
  "resource_type": "auth",
  "resource_id": "session_uuid",
  "details": {
    "ip_address": "192.168.1.1",
    "user_agent": "Mozilla/5.0...",
    "geolocation": "San Francisco, CA"
  },
  "risk_score": 0.1
}
```

### Compliance Reporting

Built-in compliance reports for:

- **GDPR**: Data access logs, consent tracking, deletion records
- **HIPAA**: Access controls, audit trails, encryption status
- **SOC 2**: Security controls, incident reports, access reviews
- **PCI DSS**: Payment data access, security scans

## Security Alerts

### Alert Types

26 security alert types:

- Suspicious login attempts
- Brute force attacks
- Account takeover attempts
- Privilege escalation
- Data exfiltration
- Configuration tampering
- API abuse
- Session hijacking

### Alert Response

Automatic remediation actions:

1. **Account Lockout**: Temporary or permanent suspension
2. **Password Reset**: Force password change
3. **Session Termination**: Kill all active sessions
4. **IP Blocking**: Add to blacklist
5. **Rate Limit Reduction**: Decrease allowed requests
6. **MFA Enforcement**: Require additional verification

## API Security

### API Key Management

- **Secure Generation**: Cryptographically random keys
- **Scoped Permissions**: Limited access per key
- **Usage Tracking**: Monitor API key usage
- **Rotation Policy**: Regular key rotation
- **Revocation**: Immediate key termination

### Request Security

- **HTTPS Only**: TLS 1.3 enforcement
- **Certificate Pinning**: Prevent MITM attacks
- **Request Signing**: HMAC signature validation
- **Replay Prevention**: Nonce and timestamp validation

## Database Security

### Encryption

- **At Rest**: AES-256 encryption for sensitive data
- **In Transit**: TLS for all database connections
- **Field-Level**: Encryption for PII and sensitive fields

### Access Control

- **Row-Level Security**: User-specific data access
- **Column-Level Security**: Field access restrictions
- **Query Auditing**: Log all database queries
- **Connection Limits**: Prevent connection exhaustion

## Email Security

### Email Verification

- **Double Opt-In**: Confirmation required
- **Token Expiration**: 24-hour validity
- **Rate Limiting**: Prevent email bombing
- **SPF/DKIM/DMARC**: Email authentication

### Password Reset

- **Secure Tokens**: Cryptographically random
- **Single Use**: Tokens invalidated after use
- **Time Limited**: 1-hour expiration
- **Account Verification**: Additional security questions

## Implementation Guide

### Environment Variables

```env
# JWT Configuration
JWT_ALGORITHM=RS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=15
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7
JWT_PRIVATE_KEY_PATH=/secrets/jwt_private.pem
JWT_PUBLIC_KEY_PATH=/secrets/jwt_public.pem

# Security Settings
BCRYPT_ROUNDS=12
PASSWORD_MIN_LENGTH=12
PASSWORD_HISTORY_LIMIT=5
CHECK_PASSWORD_BREACHES=true

# Rate Limiting
RATE_LIMIT_REQUESTS=100
RATE_LIMIT_PERIOD=60
RATE_LIMIT_STRATEGY=token_bucket

# Session Configuration
SESSION_SECRET_KEY=your-secret-key
SESSION_EXPIRE_HOURS=24
MAX_SESSIONS_PER_USER=5

# OAuth Providers
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret
GITHUB_CLIENT_ID=your-github-client-id
GITHUB_CLIENT_SECRET=your-github-client-secret

# MFA Settings
MFA_ISSUER=ResearchPlatform
ENABLE_MFA=true
```

### RSA Key Generation

Generate RSA keys for JWT signing:

```bash
# Create keys directory
mkdir -p keys

# Generate private key
openssl genrsa -out keys/jwt_private.pem 2048

# Extract public key
openssl rsa -in keys/jwt_private.pem -pubout -out keys/jwt_public.pem

# Set proper permissions
chmod 600 keys/jwt_private.pem
chmod 644 keys/jwt_public.pem
```

### Database Migration

Run the security tables migration:

```bash
# Apply migration
alembic upgrade head

# Verify tables created
psql -U research -d research_db -c "\dt"
```

## API Endpoints

### Authentication Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /auth/register | User registration |
| POST | /auth/login | User login |
| POST | /auth/refresh | Refresh access token |
| POST | /auth/logout | Logout and revoke tokens |
| GET | /auth/me | Get current user |
| POST | /auth/change-password | Change password |
| POST | /auth/forgot-password | Request password reset |
| POST | /auth/reset-password | Complete password reset |
| GET | /auth/verify-email | Verify email address |
| GET | /auth/sessions | List active sessions |
| DELETE | /auth/sessions/{id} | Terminate session |

### OAuth2 Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /auth/oauth2/{provider}/login | Initiate OAuth flow |
| GET | /auth/oauth2/{provider}/callback | OAuth callback |
| POST | /auth/oauth2/{provider}/link | Link OAuth account |
| DELETE | /auth/oauth2/{provider}/unlink | Unlink OAuth account |

### MFA Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /auth/mfa/enable | Enable MFA |
| POST | /auth/mfa/disable | Disable MFA |
| POST | /auth/mfa/verify | Verify MFA code |
| GET | /auth/mfa/backup-codes | Get backup codes |
| POST | /auth/mfa/backup-codes/regenerate | Regenerate backup codes |

## Security Best Practices

### For Developers

1. **Never commit secrets**: Use environment variables
2. **Validate all inputs**: Use the provided validators
3. **Use parameterized queries**: Prevent SQL injection
4. **Implement proper error handling**: Don't leak sensitive info
5. **Follow least privilege**: Grant minimum required permissions
6. **Keep dependencies updated**: Regular security updates
7. **Use secure defaults**: Fail closed, not open

### For Administrators

1. **Regular security audits**: Review logs and alerts
2. **Update security patches**: Keep system current
3. **Monitor suspicious activity**: Set up alerting
4. **Backup security keys**: Store securely offline
5. **Review access regularly**: Remove unused accounts
6. **Test incident response**: Regular drills
7. **Document security procedures**: Keep runbooks updated

### For Users

1. **Use strong passwords**: Follow complexity requirements
2. **Enable MFA**: Use authenticator apps
3. **Review active sessions**: Terminate unknown sessions
4. **Report suspicious activity**: Contact administrators
5. **Keep contact info updated**: For security alerts
6. **Don't share credentials**: Each user needs own account
7. **Use API keys properly**: Don't embed in code

## Incident Response

### Security Incident Procedure

1. **Detection**: Automated alerts or user reports
2. **Assessment**: Determine severity and scope
3. **Containment**: Isolate affected systems
4. **Investigation**: Analyze logs and evidence
5. **Remediation**: Fix vulnerabilities
6. **Recovery**: Restore normal operations
7. **Post-Mortem**: Document lessons learned

### Contact Information

- **Security Team**: security@researchplatform.com
- **Emergency Hotline**: +1-xxx-xxx-xxxx
- **Bug Bounty Program**: security.researchplatform.com/bugbounty

## Compliance

### GDPR Compliance

- Right to access data
- Right to rectification
- Right to erasure
- Right to data portability
- Consent management
- Privacy by design

### HIPAA Compliance

- Access controls
- Audit controls
- Integrity controls
- Transmission security
- Business associate agreements

### SOC 2 Controls

- CC1: Control environment
- CC2: Communication and information
- CC3: Risk assessment
- CC4: Monitoring activities
- CC5: Control activities
- CC6: Logical and physical access
- CC7: System operations
- CC8: Change management
- CC9: Risk mitigation

## Testing

### Security Testing

Run the security test suite:

```bash
# Run all security tests
pytest tests/test_security.py -v

# Run specific test categories
pytest tests/test_security.py::TestRateLimiter -v
pytest tests/test_security.py::TestSecurityHeaders -v
pytest tests/test_security.py::TestInputValidators -v
pytest tests/test_security.py::TestAuditLogger -v

# Run with coverage
pytest tests/test_security.py --cov=src/security --cov-report=html
```

### Penetration Testing

Regular penetration testing schedule:

- **Quarterly**: Automated vulnerability scanning
- **Bi-Annually**: Manual penetration testing
- **Annually**: Full security audit

## Maintenance

### Regular Tasks

- **Daily**: Review security alerts and audit logs
- **Weekly**: Check failed login attempts
- **Monthly**: Review user permissions and roles
- **Quarterly**: Update security documentation
- **Annually**: Complete security audit

### Security Updates

Subscribe to security advisories:

- Python Security: python.org/security
- OWASP: owasp.org
- CVE Database: cve.mitre.org

## Conclusion

The Multi-Agent Research Platform's security implementation provides comprehensive protection against modern threats while maintaining usability and performance. The layered security approach ensures defense in depth, while extensive audit logging and compliance features meet regulatory requirements.

For additional security questions or concerns, please contact the security team.