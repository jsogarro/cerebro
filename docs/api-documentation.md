# API Documentation

## Overview

The Multi-Agent Research Platform provides a comprehensive REST API for managing research projects, generating reports, and monitoring system health. The API follows RESTful principles and includes real-time WebSocket support for progress updates.

## Base URL

```
http://localhost:8000/api/v1
```

Production:
```
https://api.researchplatform.com/v1
```

## Authentication

The API uses JWT (JSON Web Token) authentication with bearer tokens:

```bash
Authorization: Bearer <jwt_token>
```

### Getting a Token

```bash
# Login to get access token
curl -X POST "/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "your_password"
  }'

# Response
{
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9...",
  "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9...",
  "token_type": "bearer",
  "expires_in": 900
}
```

## Content Types

All API endpoints accept and return JSON unless otherwise specified:

```
Content-Type: application/json
Accept: application/json
```

## Error Handling

The API uses standard HTTP status codes and returns error details in the response body:

```json
{
  "detail": "Error description",
  "type": "error_type",
  "code": "ERROR_CODE"
}
```

### Common Status Codes

- `200` - Success
- `201` - Created
- `202` - Accepted (Async operation started)
- `204` - No Content
- `400` - Bad Request
- `401` - Unauthorized
- `403` - Forbidden
- `404` - Not Found
- `422` - Validation Error
- `500` - Internal Server Error
- `503` - Service Unavailable

## Rate Limiting

API endpoints are rate-limited per user:

- **Standard endpoints**: 100 requests/minute
- **Compute-intensive endpoints**: 10 requests/hour
- **Authentication endpoints**: 5 requests/5 minutes

Rate limit headers are included in responses:

```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1640995200
```

## API Endpoints

### Health Endpoints

#### GET /health

Basic health check endpoint.

**Response:**
```json
{
  "status": "healthy",
  "service": "research-platform-api"
}
```

#### GET /ready

Readiness check for Kubernetes deployments.

**Response:**
```json
{
  "status": "ready",
  "service": "research-platform-api",
  "checks": {
    "database": "ok",
    "redis": "ok",
    "temporal": "ok"
  }
}
```

#### GET /live

Liveness check for Kubernetes deployments.

**Response:**
```json
{
  "status": "alive"
}
```

### Research Project Endpoints

#### POST /research/projects

Create a new research project.

**Request Body:**
```json
{
  "title": "AI Impact on Healthcare",
  "query": {
    "text": "How does artificial intelligence impact healthcare outcomes?",
    "domains": ["AI", "Healthcare", "Medicine"],
    "timeframe": "last_5_years",
    "language": "en"
  },
  "user_id": "user-123",
  "scope": {
    "research_depth": "comprehensive",
    "paper_limit": 100,
    "include_preprints": true,
    "geographic_scope": "global"
  }
}
```

**Response (201):**
```json
{
  "id": "proj-550e8400-e29b-41d4-a716-446655440000",
  "title": "AI Impact on Healthcare",
  "query": {
    "text": "How does artificial intelligence impact healthcare outcomes?",
    "domains": ["AI", "Healthcare", "Medicine"],
    "timeframe": "last_5_years",
    "language": "en"
  },
  "user_id": "user-123",
  "status": "pending",
  "created_at": "2024-01-01T12:00:00Z",
  "updated_at": "2024-01-01T12:00:00Z",
  "scope": {
    "research_depth": "comprehensive",
    "paper_limit": 100,
    "include_preprints": true,
    "geographic_scope": "global"
  }
}
```

#### GET /research/projects/{project_id}

Get a research project by ID.

**Response (200):**
```json
{
  "id": "proj-550e8400-e29b-41d4-a716-446655440000",
  "title": "AI Impact on Healthcare",
  "query": {
    "text": "How does artificial intelligence impact healthcare outcomes?",
    "domains": ["AI", "Healthcare", "Medicine"]
  },
  "user_id": "user-123",
  "status": "in_progress",
  "created_at": "2024-01-01T12:00:00Z",
  "updated_at": "2024-01-01T12:05:00Z",
  "completion_estimate": "2024-01-01T12:30:00Z",
  "workflow_id": "wf-abc123"
}
```

#### GET /research/projects

List research projects with filtering.

**Query Parameters:**
- `user_id` (string, optional) - Filter by user ID
- `status` (string, optional) - Filter by status (`pending`, `in_progress`, `completed`, `failed`, `cancelled`)
- `limit` (integer, default: 10, max: 100) - Number of results to return
- `offset` (integer, default: 0) - Number of results to skip

**Example:**
```bash
GET /research/projects?user_id=user-123&status=in_progress&limit=20
```

**Response (200):**
```json
[
  {
    "id": "proj-550e8400-e29b-41d4-a716-446655440000",
    "title": "AI Impact on Healthcare",
    "status": "in_progress",
    "created_at": "2024-01-01T12:00:00Z",
    "progress_percentage": 65.0
  },
  {
    "id": "proj-550e8400-e29b-41d4-a716-446655440001",
    "title": "Machine Learning in Finance",
    "status": "completed",
    "created_at": "2024-01-01T10:00:00Z",
    "progress_percentage": 100.0
  }
]
```

#### GET /research/projects/{project_id}/progress

Get real-time progress of a research project.

**Response (200):**
```json
{
  "project_id": "proj-550e8400-e29b-41d4-a716-446655440000",
  "total_tasks": 5,
  "completed_tasks": 3,
  "in_progress_tasks": 1,
  "pending_tasks": 1,
  "progress_percentage": 60.0,
  "current_agent": "synthesis_agent",
  "current_phase": "analysis",
  "estimated_completion": "2024-01-01T12:30:00Z",
  "agent_progress": {
    "literature_review": {"status": "completed", "confidence": 0.92},
    "comparative_analysis": {"status": "completed", "confidence": 0.88},
    "methodology": {"status": "completed", "confidence": 0.85},
    "synthesis": {"status": "in_progress", "progress": 0.4},
    "citation": {"status": "pending"}
  }
}
```

#### POST /research/projects/{project_id}/cancel

Cancel a research project.

**Response (204):** No content

#### POST /research/projects/{project_id}/refine

Refine the scope of an existing research project.

**Request Body:**
```json
{
  "research_depth": "focused",
  "paper_limit": 50,
  "additional_domains": ["Ethics"],
  "exclude_preprints": true
}
```

**Response (200):**
```json
{
  "id": "proj-550e8400-e29b-41d4-a716-446655440000",
  "scope": {
    "research_depth": "focused",
    "paper_limit": 50,
    "include_preprints": false,
    "additional_domains": ["Ethics"]
  },
  "status": "scope_updated"
}
```

#### GET /research/projects/{project_id}/results

Get the results of a completed research project.

**Response (200):**
```json
{
  "project_id": "proj-550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "completion_time": "2024-01-01T12:28:00Z",
  "results": {
    "literature_review": {
      "papers_found": 147,
      "papers_analyzed": 89,
      "key_findings": [
        "AI diagnostic tools show 15% improvement in accuracy",
        "Cost reduction of 23% in diagnostic workflows"
      ],
      "trends": ["Increased adoption in radiology", "Growing use in pathology"]
    },
    "comparative_analysis": {
      "approaches_compared": 8,
      "effectiveness_ranking": [
        {"approach": "Deep Learning", "score": 0.89},
        {"approach": "Traditional ML", "score": 0.72}
      ]
    },
    "synthesis": {
      "main_conclusions": "AI demonstrates significant potential...",
      "confidence_score": 0.87,
      "research_gaps": ["Long-term outcome studies", "Cost-effectiveness analysis"]
    },
    "citations": {
      "total_sources": 89,
      "primary_sources": 67,
      "peer_reviewed": 82
    }
  },
  "quality_metrics": {
    "overall_confidence": 0.87,
    "source_reliability": 0.92,
    "methodology_rigor": 0.84
  }
}
```

### Report Generation Endpoints

#### POST /reports/generate

Generate a new report asynchronously.

**Request Body:**
```json
{
  "title": "AI in Healthcare: Comprehensive Analysis",
  "query": "Impact of AI on healthcare outcomes",
  "domains": ["AI", "Healthcare"],
  "project_id": "proj-550e8400-e29b-41d4-a716-446655440000",
  "user_id": "user-123",
  "report_type": "comprehensive",
  "citation_style": "APA",
  "formats": ["html", "pdf", "markdown"],
  "include_toc": true,
  "include_executive_summary": true,
  "include_visualizations": true,
  "include_citations": true,
  "include_methodology": true,
  "workflow_data": {
    "aggregated_results": {
      "literature_findings": "...",
      "analysis_results": "..."
    }
  },
  "save_to_storage": true,
  "notify_completion": false
}
```

**Response (202):**
```json
{
  "id": "rpt-550e8400-e29b-41d4-a716-446655440000",
  "title": "AI in Healthcare: Comprehensive Analysis",
  "query": "Impact of AI on healthcare outcomes",
  "report_type": "comprehensive",
  "generation_status": "generating",
  "formats_generated": [],
  "word_count": 0,
  "page_count": 0,
  "quality_score": 0.0,
  "confidence_score": 0.0,
  "created_at": "2024-01-01T12:30:00Z",
  "generation_time_seconds": null,
  "download_urls": {}
}
```

#### GET /reports/{report_id}

Get report details by ID.

**Response (200):**
```json
{
  "id": "rpt-550e8400-e29b-41d4-a716-446655440000",
  "title": "AI in Healthcare: Comprehensive Analysis",
  "query": "Impact of AI on healthcare outcomes",
  "report_type": "comprehensive",
  "generation_status": "completed",
  "formats_generated": ["html", "pdf", "markdown"],
  "word_count": 8547,
  "page_count": 23,
  "quality_score": 0.91,
  "confidence_score": 0.87,
  "created_at": "2024-01-01T12:30:00Z",
  "generation_time_seconds": 127.5,
  "download_urls": {
    "html": "/reports/rpt-550e8400-e29b-41d4-a716-446655440000/download/html",
    "pdf": "/reports/rpt-550e8400-e29b-41d4-a716-446655440000/download/pdf",
    "markdown": "/reports/rpt-550e8400-e29b-41d4-a716-446655440000/download/markdown"
  }
}
```

#### GET /reports/{report_id}/download/{format_type}

Download report in specific format.

**Parameters:**
- `format_type` - One of: `html`, `pdf`, `latex`, `docx`, `markdown`, `json`

**Response:** File download with appropriate MIME type

**Example:**
```bash
curl -o report.pdf \
  -H "Authorization: Bearer <token>" \
  "http://localhost:8000/api/v1/reports/rpt-123/download/pdf"
```

#### GET /reports

List reports with filtering and pagination.

**Query Parameters:**
- `user_id` (UUID, optional) - Filter by user ID
- `status_filter` (string, optional) - Filter by status
- `report_type` (string, optional) - Filter by report type
- `page` (integer, default: 1) - Page number
- `page_size` (integer, default: 20, max: 100) - Results per page

**Response (200):**
```json
{
  "reports": [
    {
      "id": "rpt-550e8400-e29b-41d4-a716-446655440000",
      "title": "AI in Healthcare: Comprehensive Analysis",
      "generation_status": "completed",
      "quality_score": 0.91,
      "created_at": "2024-01-01T12:30:00Z"
    }
  ],
  "total_count": 1,
  "page": 1,
  "page_size": 20,
  "has_more": false
}
```

#### POST /reports/search

Search reports by text and filters.

**Request Body:**
```json
{
  "search_term": "artificial intelligence healthcare",
  "user_id": "user-123",
  "report_type": "comprehensive",
  "min_quality_score": 0.8,
  "limit": 20,
  "offset": 0
}
```

**Response (200):**
```json
{
  "reports": [
    {
      "id": "rpt-550e8400-e29b-41d4-a716-446655440000",
      "title": "AI in Healthcare: Comprehensive Analysis",
      "generation_status": "completed",
      "quality_score": 0.91
    }
  ],
  "total_count": 1,
  "page": 1,
  "page_size": 20,
  "has_more": false
}
```

#### GET /reports/statistics

Get report generation statistics.

**Query Parameters:**
- `user_id` (UUID, optional) - Filter by user ID
- `days` (integer, default: 30, max: 365) - Days to look back

**Response (200):**
```json
{
  "total_reports": 157,
  "status_counts": {
    "completed": 142,
    "generating": 8,
    "failed": 7
  },
  "type_counts": {
    "comprehensive": 89,
    "executive_summary": 45,
    "academic_paper": 23
  },
  "average_quality_score": 0.87,
  "average_confidence_score": 0.84,
  "average_generation_time": 142.7,
  "average_word_count": 7834,
  "total_access_count": 2341,
  "storage_statistics": {
    "total_storage_mb": 1247,
    "total_files": 471
  }
}
```

#### DELETE /reports/{report_id}

Delete a report and optionally its files.

**Query Parameters:**
- `delete_files` (boolean, default: true) - Also delete associated files

**Response (204):** No content

#### GET /reports/{report_id}/integrity

Verify the integrity of a report and its files.

**Response (200):**
```json
{
  "report_id": "rpt-550e8400-e29b-41d4-a716-446655440000",
  "integrity_status": "valid",
  "checksum_verification": {
    "html": {"expected": "abc123", "actual": "abc123", "valid": true},
    "pdf": {"expected": "def456", "actual": "def456", "valid": true}
  },
  "file_verification": {
    "html": {"exists": true, "size_bytes": 157834},
    "pdf": {"exists": true, "size_bytes": 2847291}
  },
  "last_verified": "2024-01-01T13:00:00Z"
}
```

### WebSocket Endpoints

#### WS /ws

General WebSocket connection for system-wide events.

**Connection:**
```bash
ws://localhost:8000/ws?token=<jwt_token>
```

**Messages Received:**
```json
{
  "type": "project_started",
  "project_id": "proj-123",
  "timestamp": "2024-01-01T12:00:00Z",
  "data": {
    "title": "AI Research Project",
    "estimated_duration": 1800
  }
}
```

#### WS /ws/projects/{project_id}

Project-specific WebSocket connection for filtered updates.

**Connection:**
```bash
ws://localhost:8000/ws/projects/proj-123?token=<jwt_token>
```

**Messages Received:**
```json
{
  "type": "progress",
  "project_id": "proj-123",
  "timestamp": "2024-01-01T12:05:00Z",
  "data": {
    "progress_percentage": 25.0,
    "completed_tasks": 1,
    "total_tasks": 4,
    "current_agent": "literature_review_agent",
    "current_phase": "search"
  }
}
```

#### WS /ws/cli/{project_id}

CLI-optimized WebSocket connection with Rich terminal formatting.

**Connection:**
```bash
ws://localhost:8000/ws/cli/proj-123?token=<jwt_token>
```

**Messages Received:**
```json
{
  "type": "progress",
  "project_id": "proj-123",
  "timestamp": "2024-01-01T12:05:00Z",
  "data": {
    "progress_percentage": 25.0,
    "formatted_message": "[green]✓[/green] Literature review completed",
    "progress_bar": "████████░░░░░░░░░░░░ 25%"
  }
}
```

#### GET /ws/health

WebSocket system health check.

**Response (200):**
```json
{
  "status": "healthy",
  "websocket_stats": {
    "active_connections": 15,
    "total_messages_sent": 1247,
    "uptime_seconds": 3600
  }
}
```

## Authentication Endpoints

### POST /auth/register

Register a new user account.

**Request Body:**
```json
{
  "email": "user@example.com",
  "username": "username",
  "password": "SecurePassword123!",
  "full_name": "John Doe",
  "organization": "Research Lab"
}
```

**Response (201):**
```json
{
  "user_id": "user-123",
  "email": "user@example.com",
  "username": "username",
  "verification_required": true,
  "verification_sent": true
}
```

### POST /auth/login

Authenticate user and receive tokens.

**Request Body:**
```json
{
  "email": "user@example.com",
  "password": "SecurePassword123!",
  "mfa_code": "123456",
  "remember_me": false
}
```

**Response (200):**
```json
{
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9...",
  "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9...",
  "token_type": "bearer",
  "expires_in": 900,
  "user": {
    "id": "user-123",
    "email": "user@example.com",
    "username": "username",
    "full_name": "John Doe"
  }
}
```

### POST /auth/refresh

Refresh access token using refresh token.

**Request Body:**
```json
{
  "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9..."
}
```

**Response (200):**
```json
{
  "access_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9...",
  "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9...",
  "token_type": "bearer",
  "expires_in": 900
}
```

### POST /auth/logout

Logout and revoke tokens.

**Request Body:**
```json
{
  "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9..."
}
```

**Response (200):**
```json
{
  "message": "Successfully logged out"
}
```

## Data Models

### ResearchProject

```json
{
  "id": "string (UUID)",
  "title": "string",
  "query": {
    "text": "string",
    "domains": ["string"],
    "timeframe": "string",
    "language": "string"
  },
  "user_id": "string",
  "status": "pending | in_progress | completed | failed | cancelled",
  "created_at": "string (ISO 8601)",
  "updated_at": "string (ISO 8601)",
  "completion_estimate": "string (ISO 8601)",
  "workflow_id": "string",
  "scope": {
    "research_depth": "comprehensive | focused | quick",
    "paper_limit": "number",
    "include_preprints": "boolean",
    "geographic_scope": "string"
  }
}
```

### ResearchProgress

```json
{
  "project_id": "string (UUID)",
  "total_tasks": "number",
  "completed_tasks": "number",
  "in_progress_tasks": "number",
  "pending_tasks": "number",
  "progress_percentage": "number (0-100)",
  "current_agent": "string",
  "current_phase": "string",
  "estimated_completion": "string (ISO 8601)",
  "agent_progress": {
    "agent_name": {
      "status": "pending | in_progress | completed | failed",
      "progress": "number (0-1)",
      "confidence": "number (0-1)"
    }
  }
}
```

### Report

```json
{
  "id": "string (UUID)",
  "title": "string",
  "query": "string",
  "report_type": "comprehensive | executive_summary | academic_paper | technical_analysis",
  "generation_status": "generating | completed | failed",
  "formats_generated": ["string"],
  "word_count": "number",
  "page_count": "number",
  "quality_score": "number (0-1)",
  "confidence_score": "number (0-1)",
  "created_at": "string (ISO 8601)",
  "generation_time_seconds": "number",
  "download_urls": {
    "format": "string (URL)"
  }
}
```

## SDK and Client Libraries

### Python SDK

```python
from research_platform_sdk import ResearchClient

# Initialize client
client = ResearchClient(
    api_url="http://localhost:8000/api/v1",
    api_key="your-api-key"
)

# Create research project
project = await client.research.create_project(
    title="AI Impact Study",
    query="How does AI affect employment?",
    domains=["AI", "Economics"]
)

# Monitor progress
async for progress in client.research.stream_progress(project.id):
    print(f"Progress: {progress.progress_percentage}%")

# Generate report
report = await client.reports.generate(
    project_id=project.id,
    report_type="comprehensive",
    formats=["html", "pdf"]
)
```

### JavaScript SDK

```javascript
import { ResearchClient } from '@research-platform/sdk';

// Initialize client
const client = new ResearchClient({
  apiUrl: 'http://localhost:8000/api/v1',
  apiKey: 'your-api-key'
});

// Create research project
const project = await client.research.createProject({
  title: 'AI Impact Study',
  query: 'How does AI affect employment?',
  domains: ['AI', 'Economics']
});

// Monitor progress with WebSocket
client.research.onProgress(project.id, (progress) => {
  console.log(`Progress: ${progress.progressPercentage}%`);
});
```

## Rate Limiting and Quotas

### Rate Limits by Endpoint Type

| Endpoint Type | Rate Limit | Burst |
|---------------|------------|-------|
| Authentication | 5/5min | 10 |
| Project CRUD | 100/min | 200 |
| Progress Monitoring | 200/min | 500 |
| Report Generation | 10/hour | 20 |
| Report Download | 50/min | 100 |
| WebSocket Connections | 10/min | 20 |

### Quota Limits

| Resource | Free Tier | Pro Tier | Enterprise |
|----------|-----------|----------|------------|
| Projects/month | 10 | 100 | Unlimited |
| Reports/month | 20 | 200 | Unlimited |
| Storage (GB) | 1 | 10 | Unlimited |
| API Calls/day | 1,000 | 10,000 | Unlimited |

## Error Codes

### Research Project Errors

- `PROJECT_NOT_FOUND` - Project does not exist
- `PROJECT_ACCESS_DENIED` - User lacks access to project
- `PROJECT_ALREADY_CANCELLED` - Cannot perform operation on cancelled project
- `WORKFLOW_START_FAILED` - Unable to start research workflow
- `INVALID_RESEARCH_QUERY` - Research query validation failed

### Report Generation Errors

- `REPORT_NOT_FOUND` - Report does not exist
- `REPORT_GENERATION_FAILED` - Report generation encountered errors
- `UNSUPPORTED_FORMAT` - Requested format not supported
- `STORAGE_FULL` - User storage quota exceeded
- `TEMPLATE_ERROR` - Report template processing failed

### Authentication Errors

- `INVALID_CREDENTIALS` - Username/password incorrect
- `TOKEN_EXPIRED` - JWT token has expired
- `TOKEN_INVALID` - JWT token is malformed or invalid
- `MFA_REQUIRED` - Multi-factor authentication required
- `ACCOUNT_LOCKED` - Account temporarily locked due to failed attempts

## Webhooks

### Configuring Webhooks

```bash
POST /webhooks/configure
```

```json
{
  "url": "https://your-app.com/webhooks/research-platform",
  "events": ["project.completed", "report.generated"],
  "secret": "your-webhook-secret"
}
```

### Webhook Events

#### project.completed

Sent when a research project finishes.

```json
{
  "event": "project.completed",
  "project_id": "proj-123",
  "timestamp": "2024-01-01T12:30:00Z",
  "data": {
    "status": "completed",
    "completion_time": "2024-01-01T12:28:00Z",
    "quality_score": 0.87
  }
}
```

#### report.generated

Sent when a report is successfully generated.

```json
{
  "event": "report.generated",
  "report_id": "rpt-123",
  "project_id": "proj-123",
  "timestamp": "2024-01-01T12:35:00Z",
  "data": {
    "formats": ["html", "pdf"],
    "download_urls": {
      "html": "https://api.example.com/reports/rpt-123/download/html",
      "pdf": "https://api.example.com/reports/rpt-123/download/pdf"
    }
  }
}
```

## Testing

### Test Environment

Base URL: `http://localhost:8000/api/v1`

Test user credentials:
- Email: `test@example.com`
- Password: `TestPassword123!`

### Example cURL Commands

```bash
# Get access token
export TOKEN=$(curl -s -X POST "http://localhost:8000/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"TestPassword123!"}' \
  | jq -r '.access_token')

# Create research project
curl -X POST "http://localhost:8000/api/v1/research/projects" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Test Research Project",
    "query": {
      "text": "Impact of AI on education",
      "domains": ["AI", "Education"]
    },
    "user_id": "test-user"
  }'

# List projects
curl -X GET "http://localhost:8000/api/v1/research/projects" \
  -H "Authorization: Bearer $TOKEN"

# Generate report
curl -X POST "http://localhost:8000/api/v1/reports/generate" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Test Report",
    "query": "AI impact analysis",
    "formats": ["html", "pdf"]
  }'
```

## Changelog

### Version 1.0.0 (2024-01-01)
- Initial API release
- Research project management
- Report generation system
- WebSocket real-time updates
- JWT authentication

### Version 1.1.0 (TBD)
- Enhanced search capabilities
- Bulk operations support
- Advanced filtering options
- Performance improvements

This comprehensive API documentation provides all the information needed to integrate with the Multi-Agent Research Platform, including examples, error handling, and best practices for optimal usage.