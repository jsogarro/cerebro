# Error Code Reference

This document provides a comprehensive reference for all error codes used in the Multi-Agent Research Platform, including their meanings, causes, and resolution steps.

## Table of Contents
- [Error Code Format](#error-code-format)
- [HTTP Status Codes](#http-status-codes)
- [Application Error Codes](#application-error-codes)
- [Agent Error Codes](#agent-error-codes)
- [Workflow Error Codes](#workflow-error-codes)
- [Database Error Codes](#database-error-codes)
- [External Service Error Codes](#external-service-error-codes)
- [Troubleshooting Guide](#troubleshooting-guide)

## Error Code Format

Error codes follow this format: `{SERVICE}-{CATEGORY}-{NUMBER}`

- **SERVICE**: Component that generated the error (API, AGT, WFL, DB, EXT)
- **CATEGORY**: Error category (AUT, VAL, CON, TIM, etc.)
- **NUMBER**: Unique identifier within category (001-999)

**Examples:**
- `API-AUT-001`: API Authentication error #1
- `AGT-EXE-003`: Agent Execution error #3
- `WFL-TIM-001`: Workflow Timeout error #1

## HTTP Status Codes

### 2xx Success Codes

| Code | Message | Description |
|------|---------|-------------|
| 200 | OK | Request successful |
| 201 | Created | Resource created successfully |
| 202 | Accepted | Request accepted for processing |
| 204 | No Content | Request successful, no response body |

### 4xx Client Error Codes

| Code | Message | Common Causes | Resolution |
|------|---------|---------------|------------|
| 400 | Bad Request | Invalid request syntax, malformed JSON | Validate request format and parameters |
| 401 | Unauthorized | Missing or invalid authentication | Check API key or login credentials |
| 403 | Forbidden | Insufficient permissions | Verify user permissions for resource |
| 404 | Not Found | Resource doesn't exist | Check resource ID and endpoint |
| 409 | Conflict | Resource already exists or conflict | Check for duplicate resources |
| 422 | Unprocessable Entity | Validation errors | Fix validation errors in request |
| 429 | Too Many Requests | Rate limit exceeded | Wait before retrying or upgrade limits |

### 5xx Server Error Codes

| Code | Message | Common Causes | Resolution |
|------|---------|---------------|------------|
| 500 | Internal Server Error | Unhandled server error | Check server logs and report bug |
| 502 | Bad Gateway | Upstream service unavailable | Check dependent service status |
| 503 | Service Unavailable | Service temporarily down | Wait and retry, check service status |
| 504 | Gateway Timeout | Upstream service timeout | Check network and service health |

## Application Error Codes

### Authentication Errors (API-AUT-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| API-AUT-001 | Invalid API key | API key not found or inactive | Generate new API key or activate existing one |
| API-AUT-002 | API key expired | API key has expired | Renew or regenerate API key |
| API-AUT-003 | JWT token invalid | JWT signature verification failed | Re-authenticate to get new token |
| API-AUT-004 | JWT token expired | JWT token has expired | Refresh token or re-authenticate |
| API-AUT-005 | Invalid credentials | Username/password incorrect | Check credentials and try again |
| API-AUT-006 | Account locked | Too many failed login attempts | Wait for lockout period or contact admin |
| API-AUT-007 | MFA required | Multi-factor authentication needed | Complete MFA challenge |
| API-AUT-008 | Session expired | User session has expired | Log in again |

**Example Error Response:**
```json
{
  "error": {
    "code": "API-AUT-001",
    "message": "Invalid API key",
    "details": "The provided API key is not valid or has been deactivated",
    "timestamp": "2024-01-15T10:30:00Z",
    "request_id": "req_123456789"
  }
}
```

### Validation Errors (API-VAL-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| API-VAL-001 | Missing required field | Required field not provided | Include all required fields in request |
| API-VAL-002 | Invalid field format | Field format doesn't match expected | Check field format requirements |
| API-VAL-003 | Field too long | Field exceeds maximum length | Reduce field length to within limits |
| API-VAL-004 | Field too short | Field below minimum length | Increase field length to minimum |
| API-VAL-005 | Invalid enum value | Value not in allowed enum list | Use one of the allowed enum values |
| API-VAL-006 | Invalid UUID format | UUID format is incorrect | Provide valid UUID format |
| API-VAL-007 | Invalid email format | Email format is incorrect | Provide valid email address |
| API-VAL-008 | Invalid date format | Date format is incorrect | Use ISO 8601 date format |
| API-VAL-009 | Value out of range | Numeric value outside allowed range | Use value within specified range |
| API-VAL-010 | Invalid JSON schema | Request doesn't match schema | Review API documentation for correct schema |

### Connection Errors (API-CON-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| API-CON-001 | Database connection failed | Cannot connect to database | Check database server status and credentials |
| API-CON-002 | Redis connection failed | Cannot connect to Redis | Check Redis server status and configuration |
| API-CON-003 | Temporal connection failed | Cannot connect to Temporal | Check Temporal server status |
| API-CON-004 | External API unavailable | External service not responding | Check external service status |
| API-CON-005 | Connection pool exhausted | No available database connections | Increase pool size or check for connection leaks |
| API-CON-006 | Network timeout | Network operation timed out | Check network connectivity and increase timeout |

### Rate Limiting Errors (API-RTE-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| API-RTE-001 | Rate limit exceeded | Too many requests in time window | Wait before making more requests |
| API-RTE-002 | Quota exceeded | Monthly/daily quota exceeded | Upgrade plan or wait for quota reset |
| API-RTE-003 | Concurrent limit exceeded | Too many concurrent requests | Reduce concurrent request count |

## Agent Error Codes

### Execution Errors (AGT-EXE-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| AGT-EXE-001 | Agent initialization failed | Agent failed to initialize | Check agent configuration and dependencies |
| AGT-EXE-002 | Agent execution timeout | Agent exceeded execution time limit | Increase timeout or optimize agent logic |
| AGT-EXE-003 | Agent crashed unexpectedly | Agent process terminated abnormally | Check agent logs and restart agent |
| AGT-EXE-004 | Invalid agent input | Input data doesn't match agent requirements | Validate input data format and content |
| AGT-EXE-005 | Agent resource exhausted | Agent ran out of memory or CPU | Increase resource limits or optimize agent |
| AGT-EXE-006 | Agent validation failed | Agent output failed validation | Check agent output format and content |

### Literature Review Agent Errors (AGT-LIT-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| AGT-LIT-001 | Search query invalid | Literature search query is malformed | Refine search query syntax |
| AGT-LIT-002 | No results found | No literature found for query | Broaden search terms or try different keywords |
| AGT-LIT-003 | Source access denied | Cannot access academic source | Check credentials or subscription status |
| AGT-LIT-004 | Parse error | Failed to parse academic paper | Check paper format or use alternative source |
| AGT-LIT-005 | Rate limit hit | Academic API rate limit exceeded | Wait for rate limit reset |

### Comparative Analysis Agent Errors (AGT-CMP-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| AGT-CMP-001 | Insufficient data | Not enough data for comparison | Provide more input data or sources |
| AGT-CMP-002 | Incompatible formats | Data formats cannot be compared | Normalize data formats before comparison |
| AGT-CMP-003 | Analysis failed | Comparative analysis process failed | Check input data quality and format |

### Methodology Agent Errors (AGT-MTH-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| AGT-MTH-001 | Invalid research type | Research type not supported | Use supported research methodology type |
| AGT-MTH-002 | Methodology conflict | Conflicting methodology requirements | Resolve methodology conflicts in input |
| AGT-MTH-003 | Sample size error | Sample size calculation failed | Provide valid parameters for sample size |

### Synthesis Agent Errors (AGT-SYN-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| AGT-SYN-001 | Conflicting findings | Cannot reconcile conflicting results | Review input data for inconsistencies |
| AGT-SYN-002 | Synthesis failed | Synthesis process failed | Check input data completeness and quality |
| AGT-SYN-003 | Output too large | Synthesis output exceeds size limits | Reduce input scope or increase limits |

### Citation Agent Errors (AGT-CIT-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| AGT-CIT-001 | Citation format invalid | Citation doesn't match required format | Use correct citation format (APA, MLA, etc.) |
| AGT-CIT-002 | Source not found | Referenced source cannot be verified | Verify source exists and is accessible |
| AGT-CIT-003 | DOI resolution failed | Cannot resolve DOI to source | Check DOI validity or use alternative identifier |

## Workflow Error Codes

### Temporal Workflow Errors (WFL-TMP-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| WFL-TMP-001 | Workflow timeout | Workflow exceeded maximum execution time | Increase workflow timeout or optimize activities |
| WFL-TMP-002 | Activity failed | Workflow activity failed to execute | Check activity logs and retry |
| WFL-TMP-003 | Workflow cancelled | Workflow was cancelled by user | Restart workflow if needed |
| WFL-TMP-004 | Signal timeout | Workflow signal timed out | Check signal handling and retry |
| WFL-TMP-005 | Workflow not found | Workflow instance not found | Verify workflow ID is correct |
| WFL-TMP-006 | Invalid workflow state | Workflow in unexpected state | Check workflow history and state |

### LangGraph Workflow Errors (WFL-LNG-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| WFL-LNG-001 | Graph validation failed | Workflow graph has invalid structure | Fix graph definition and node connections |
| WFL-LNG-002 | Node execution failed | Graph node failed to execute | Check node implementation and inputs |
| WFL-LNG-003 | State corruption | Workflow state is corrupted | Reset workflow state or restart |
| WFL-LNG-004 | Checkpoint failed | Failed to create workflow checkpoint | Check storage and retry |
| WFL-LNG-005 | Recovery failed | Failed to recover from checkpoint | Verify checkpoint integrity |

### Orchestration Errors (WFL-ORC-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| WFL-ORC-001 | Agent coordination failed | Agents failed to coordinate properly | Check agent communication and retry |
| WFL-ORC-002 | Resource conflict | Multiple agents accessing same resource | Implement proper resource locking |
| WFL-ORC-003 | Dependency cycle | Circular dependency detected | Remove circular dependencies from workflow |

## Database Error Codes

### Connection Errors (DB-CON-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| DB-CON-001 | Connection failed | Cannot establish database connection | Check database server and credentials |
| DB-CON-002 | Connection timeout | Database connection timed out | Increase connection timeout or check network |
| DB-CON-003 | Pool exhausted | Connection pool has no available connections | Increase pool size or check for leaks |
| DB-CON-004 | Authentication failed | Database authentication failed | Verify database credentials |

### Query Errors (DB-QRY-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| DB-QRY-001 | Syntax error | SQL query has syntax error | Fix SQL syntax |
| DB-QRY-002 | Constraint violation | Database constraint violated | Fix data to meet constraints |
| DB-QRY-003 | Deadlock detected | Database deadlock occurred | Retry transaction |
| DB-QRY-004 | Transaction rolled back | Transaction was rolled back | Check transaction logic and retry |
| DB-QRY-005 | Table not found | Referenced table doesn't exist | Run migrations or check table name |
| DB-QRY-006 | Column not found | Referenced column doesn't exist | Check column name or run migrations |

### Migration Errors (DB-MIG-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| DB-MIG-001 | Migration failed | Database migration failed | Check migration script and database state |
| DB-MIG-002 | Version conflict | Migration version conflict | Resolve version conflicts manually |
| DB-MIG-003 | Rollback failed | Migration rollback failed | Manually restore database state |

## External Service Error Codes

### Gemini API Errors (EXT-GEM-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| EXT-GEM-001 | API key invalid | Gemini API key is invalid | Verify and update API key |
| EXT-GEM-002 | Quota exceeded | Gemini API quota exceeded | Wait for quota reset or upgrade plan |
| EXT-GEM-003 | Request too large | Request exceeds size limits | Reduce request size |
| EXT-GEM-004 | Model not found | Requested model not available | Use available model |
| EXT-GEM-005 | Rate limit exceeded | Too many requests to Gemini API | Implement rate limiting and retry |
| EXT-GEM-006 | Content filtered | Content was filtered by safety filters | Modify content to pass safety filters |
| EXT-GEM-007 | Service unavailable | Gemini service temporarily unavailable | Retry with exponential backoff |

### Academic Database Errors (EXT-ACD-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| EXT-ACD-001 | Access denied | No access to academic database | Check subscription or credentials |
| EXT-ACD-002 | Search failed | Academic search query failed | Modify search parameters |
| EXT-ACD-003 | Parse error | Failed to parse search results | Check result format |
| EXT-ACD-004 | Service timeout | Academic service timed out | Retry with longer timeout |

### Storage Errors (EXT-STG-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| EXT-STG-001 | Upload failed | File upload failed | Check file size and format |
| EXT-STG-002 | Access denied | No permission to access storage | Check storage credentials |
| EXT-STG-003 | Storage full | Storage quota exceeded | Free up space or upgrade storage |
| EXT-STG-004 | File not found | Requested file not found in storage | Verify file path and existence |

## Troubleshooting Guide

### Error Investigation Steps

1. **Check Error Code**: Look up the specific error code in this document
2. **Review Error Message**: Read the detailed error message for context
3. **Check Logs**: Review application logs for additional information
4. **Verify Configuration**: Ensure all configuration is correct
5. **Check Dependencies**: Verify all required services are running
6. **Test Connectivity**: Check network connectivity to external services

### Common Resolution Patterns

#### Authentication Issues
```bash
# Check API key
research-cli config show | grep api_key

# Verify token validity
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/health

# Regenerate API key
research-cli auth generate-key
```

#### Connection Issues
```bash
# Check service status
docker-compose ps

# Test database connection
docker-compose exec postgres pg_isready

# Test Redis connection
docker-compose exec redis redis-cli ping

# Check network connectivity
telnet localhost 8000
```

#### Resource Issues
```bash
# Check disk space
df -h

# Check memory usage
free -h

# Check CPU usage
top

# Check container resources
docker stats
```

### Error Logging Format

All errors are logged in structured JSON format:

```json
{
  "timestamp": "2024-01-15T10:30:00Z",
  "level": "ERROR",
  "logger": "research_platform.api",
  "error": {
    "code": "API-AUT-001",
    "message": "Invalid API key",
    "details": "The provided API key is not valid or has been deactivated",
    "request_id": "req_123456789",
    "user_id": "user_456",
    "endpoint": "/api/v1/projects",
    "method": "POST"
  },
  "context": {
    "trace_id": "trace_789",
    "span_id": "span_012",
    "user_agent": "research-cli/1.0.0"
  }
}
```

### Error Monitoring

#### Set Up Alerts

```yaml
# prometheus/alerts.yml
groups:
  - name: error_rates
    rules:
      - alert: HighErrorRate
        expr: rate(http_requests_total{status=~"5.."}[5m]) > 0.1
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High error rate detected"
          description: "Error rate is {{ $value }} errors per second"

      - alert: AuthenticationErrors
        expr: increase(auth_errors_total[5m]) > 10
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "High authentication error rate"
```

#### Error Tracking Dashboard

```python
# monitoring/error_dashboard.py
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta

def create_error_dashboard():
    st.title("Error Monitoring Dashboard")
    
    # Error rate over time
    st.subheader("Error Rate (Last 24 Hours)")
    error_data = get_error_metrics(hours=24)
    st.line_chart(error_data)
    
    # Top error codes
    st.subheader("Top Error Codes")
    top_errors = get_top_error_codes(limit=10)
    st.bar_chart(top_errors)
    
    # Recent errors
    st.subheader("Recent Errors")
    recent_errors = get_recent_errors(limit=20)
    st.dataframe(recent_errors)
```

### Recovery Procedures

#### Automatic Recovery

```python
# utils/error_recovery.py
from typing import Dict, Any
import asyncio

async def handle_error_recovery(error_code: str, context: Dict[str, Any]):
    """Automatic error recovery based on error code"""
    
    recovery_map = {
        "DB-CON-001": recover_database_connection,
        "EXT-GEM-002": handle_quota_exceeded,
        "WFL-TMP-001": restart_workflow,
        "AGT-EXE-002": increase_agent_timeout,
    }
    
    if error_code in recovery_map:
        recovery_func = recovery_map[error_code]
        return await recovery_func(context)
    
    return False  # No automatic recovery available

async def recover_database_connection(context: Dict[str, Any]):
    """Attempt to recover database connection"""
    try:
        # Reset connection pool
        await reset_connection_pool()
        # Verify connection
        await test_database_connection()
        return True
    except Exception:
        return False
```

### Custom Error Handling

#### API Error Responses

```python
# api/error_handlers.py
from fastapi import HTTPException
from fastapi.responses import JSONResponse

class ResearchPlatformError(Exception):
    def __init__(self, code: str, message: str, details: str = None):
        self.code = code
        self.message = message
        self.details = details
        super().__init__(self.message)

def create_error_response(error: ResearchPlatformError, status_code: int):
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": error.code,
                "message": error.message,
                "details": error.details,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
        }
    )
```

#### CLI Error Handling

```python
# cli/error_handlers.py
import click
from rich.console import Console

console = Console()

def handle_cli_error(error_code: str, message: str, details: str = None):
    """Handle CLI errors with formatted output"""
    
    console.print(f"[red]Error[/red] {error_code}: {message}")
    
    if details:
        console.print(f"[yellow]Details:[/yellow] {details}")
    
    # Provide resolution hints
    resolution = ERROR_RESOLUTIONS.get(error_code)
    if resolution:
        console.print(f"[green]Resolution:[/green] {resolution}")
    
    # Exit with appropriate code
    click.get_current_context().exit(1)
```