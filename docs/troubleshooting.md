# Troubleshooting Guide

This comprehensive guide helps you diagnose and resolve common issues with the Multi-Agent Research Platform.

## Table of Contents
- [Quick Diagnostics](#quick-diagnostics)
- [Common Issues](#common-issues)
  - [Connection Issues](#connection-issues)
  - [Authentication Errors](#authentication-errors)
  - [Database Problems](#database-problems)
  - [Agent Failures](#agent-failures)
  - [Workflow Issues](#workflow-issues)
  - [Performance Problems](#performance-problems)
- [Service-Specific Issues](#service-specific-issues)
- [Debug Logging](#debug-logging)
- [Health Checks](#health-checks)
- [Recovery Procedures](#recovery-procedures)
- [Getting Help](#getting-help)

## Quick Diagnostics

Run these commands to quickly diagnose system health:

```bash
# Check overall system health
research-cli health --verbose

# Check API connectivity
curl -v http://localhost:8000/health

# Check service status
docker-compose ps

# View recent logs
docker-compose logs --tail=50 api worker

# Check database connectivity
docker-compose exec postgres pg_isready

# Check Redis connectivity
docker-compose exec redis redis-cli ping

# Check Temporal status
curl http://localhost:8080/health
```

## Common Issues

### Connection Issues

#### Problem: "Connection refused" when using CLI
**Symptoms:**
```
Error: Connection refused to http://localhost:8000
```

**Solutions:**
1. Verify API is running:
   ```bash
   docker-compose ps api
   # Should show "Up" status
   ```

2. Check API logs for startup errors:
   ```bash
   docker-compose logs api | grep ERROR
   ```

3. Verify port binding:
   ```bash
   netstat -an | grep 8000
   # or
   lsof -i :8000
   ```

4. Check firewall settings:
   ```bash
   # macOS
   sudo pfctl -s rules | grep 8000
   
   # Linux
   sudo iptables -L -n | grep 8000
   ```

5. Try alternative connection:
   ```bash
   # Use Docker network IP
   docker inspect research-platform_api_1 | grep IPAddress
   research-cli --api-url http://<container-ip>:8000 health
   ```

#### Problem: WebSocket connection fails
**Symptoms:**
```
WebSocket connection to 'ws://localhost:8000/ws' failed
```

**Solutions:**
1. Check WebSocket endpoint:
   ```bash
   curl -i -N -H "Connection: Upgrade" \
        -H "Upgrade: websocket" \
        -H "Sec-WebSocket-Version: 13" \
        -H "Sec-WebSocket-Key: test" \
        http://localhost:8000/ws
   ```

2. Verify CORS settings in API:
   ```bash
   grep -r "allow_origins" src/api/main.py
   ```

3. Check proxy/reverse proxy configuration if applicable

### Authentication Errors

#### Problem: "401 Unauthorized" errors
**Symptoms:**
```
Error: Authentication failed - 401 Unauthorized
```

**Solutions:**
1. Verify API key is set:
   ```bash
   echo $RESEARCH_API_KEY
   ```

2. Check API key in database:
   ```bash
   docker-compose exec postgres psql -U research -d research_db \
     -c "SELECT * FROM api_keys WHERE key_hash='<your-key-hash>';"
   ```

3. Regenerate API key:
   ```bash
   research-cli auth generate-key --user-id <user-id>
   ```

4. Clear authentication cache:
   ```bash
   docker-compose exec redis redis-cli FLUSHDB
   ```

#### Problem: JWT token expired
**Symptoms:**
```
Error: Token has expired
```

**Solutions:**
1. Refresh token:
   ```bash
   research-cli auth refresh
   ```

2. Re-authenticate:
   ```bash
   research-cli auth login --username <username> --password <password>
   ```

### Database Problems

#### Problem: Database connection pool exhausted
**Symptoms:**
```
sqlalchemy.exc.TimeoutError: QueuePool limit of size 10 overflow 10 reached
```

**Solutions:**
1. Increase pool size in configuration:
   ```bash
   # In .env file
   DATABASE_POOL_SIZE=20
   DATABASE_MAX_OVERFLOW=20
   ```

2. Check for connection leaks:
   ```bash
   docker-compose exec postgres psql -U research -c \
     "SELECT pid, usename, application_name, state \
      FROM pg_stat_activity WHERE datname='research_db';"
   ```

3. Kill idle connections:
   ```bash
   docker-compose exec postgres psql -U research -c \
     "SELECT pg_terminate_backend(pid) \
      FROM pg_stat_activity \
      WHERE state = 'idle' AND state_change < now() - interval '10 minutes';"
   ```

#### Problem: Migration failures
**Symptoms:**
```
alembic.util.exc.CommandError: Can't locate revision identified by 'xxx'
```

**Solutions:**
1. Check current migration status:
   ```bash
   docker-compose exec api alembic current
   ```

2. Reset to specific revision:
   ```bash
   docker-compose exec api alembic downgrade <revision>
   docker-compose exec api alembic upgrade head
   ```

3. Force migration table rebuild:
   ```bash
   docker-compose exec postgres psql -U research -d research_db \
     -c "DROP TABLE IF EXISTS alembic_version;"
   docker-compose exec api alembic stamp head
   ```

### Agent Failures

#### Problem: Agent timeout errors
**Symptoms:**
```
Agent 'LiteratureReviewAgent' timed out after 300 seconds
```

**Solutions:**
1. Increase agent timeout:
   ```bash
   # In .env
   AGENT_TIMEOUT_SECONDS=600
   ```

2. Check Gemini API rate limits:
   ```bash
   research-cli metrics gemini-usage
   ```

3. Verify agent health:
   ```bash
   research-cli agents health --agent-type literature_review
   ```

4. Restart failed agent task:
   ```bash
   research-cli projects retry-task <project-id> <task-id>
   ```

#### Problem: Gemini API errors
**Symptoms:**
```
google.api_core.exceptions.ResourceExhausted: 429 Quota exceeded
```

**Solutions:**
1. Check API quota:
   ```bash
   research-cli metrics gemini-quota
   ```

2. Implement rate limiting:
   ```bash
   # In .env
   GEMINI_RATE_LIMIT=10  # requests per minute
   GEMINI_RETRY_MAX=5
   GEMINI_RETRY_BACKOFF=2.0
   ```

3. Use caching for repeated queries:
   ```bash
   # Enable Redis caching
   ENABLE_GEMINI_CACHE=true
   GEMINI_CACHE_TTL=3600
   ```

### Workflow Issues

#### Problem: Temporal workflow stuck
**Symptoms:**
```
Workflow 'ResearchWorkflow' status: Running for > 1 hour
```

**Solutions:**
1. Check workflow status:
   ```bash
   docker-compose exec temporal tctl workflow describe \
     --workflow-id <workflow-id>
   ```

2. View workflow history:
   ```bash
   docker-compose exec temporal tctl workflow show \
     --workflow-id <workflow-id>
   ```

3. Terminate stuck workflow:
   ```bash
   docker-compose exec temporal tctl workflow terminate \
     --workflow-id <workflow-id> \
     --reason "Stuck workflow"
   ```

4. Restart workflow:
   ```bash
   research-cli projects restart <project-id>
   ```

#### Problem: Activity retries exhausted
**Symptoms:**
```
Activity 'LiteratureReviewActivity' failed after 3 retries
```

**Solutions:**
1. Check activity logs:
   ```bash
   docker-compose logs worker | grep <activity-id>
   ```

2. Increase retry attempts:
   ```python
   # In workflow configuration
   ACTIVITY_RETRY_POLICY = {
       "maximum_attempts": 5,
       "initial_interval": timedelta(seconds=1),
       "maximum_interval": timedelta(seconds=30),
       "backoff_coefficient": 2.0,
   }
   ```

3. Manual retry:
   ```bash
   research-cli workflows retry-activity \
     --workflow-id <workflow-id> \
     --activity-id <activity-id>
   ```

### Performance Problems

#### Problem: Slow API response times
**Symptoms:**
```
API requests taking > 5 seconds
```

**Solutions:**
1. Check database query performance:
   ```bash
   docker-compose exec postgres psql -U research -d research_db -c \
     "SELECT query, mean_exec_time, calls \
      FROM pg_stat_statements \
      ORDER BY mean_exec_time DESC LIMIT 10;"
   ```

2. Enable query optimization:
   ```bash
   # Add indexes
   docker-compose exec api python -m src.scripts.optimize_db
   ```

3. Increase worker processes:
   ```bash
   # In docker-compose.yml
   command: uvicorn src.api.main:app --workers 4
   ```

4. Enable response caching:
   ```bash
   # In .env
   ENABLE_API_CACHE=true
   API_CACHE_TTL=300
   ```

#### Problem: High memory usage
**Symptoms:**
```
Container using > 2GB RAM
```

**Solutions:**
1. Check memory usage:
   ```bash
   docker stats --no-stream
   ```

2. Limit container memory:
   ```yaml
   # In docker-compose.yml
   services:
     api:
       mem_limit: 2g
       memswap_limit: 2g
   ```

3. Profile memory usage:
   ```bash
   docker-compose exec api python -m memory_profiler src.api.main
   ```

4. Clear caches:
   ```bash
   docker-compose exec redis redis-cli FLUSHALL
   ```

## Service-Specific Issues

### PostgreSQL Issues

#### Problem: "FATAL: too many connections"
**Solutions:**
```bash
# Increase max connections
docker-compose exec postgres psql -U postgres -c \
  "ALTER SYSTEM SET max_connections = 200;"
docker-compose restart postgres
```

#### Problem: Slow queries
**Solutions:**
```bash
# Enable query logging
docker-compose exec postgres psql -U research -d research_db -c \
  "ALTER SYSTEM SET log_min_duration_statement = 1000;"  # Log queries > 1s

# Analyze tables
docker-compose exec postgres psql -U research -d research_db -c \
  "ANALYZE;"
```

### Redis Issues

#### Problem: "OOM command not allowed"
**Solutions:**
```bash
# Check memory usage
docker-compose exec redis redis-cli INFO memory

# Set memory limit
docker-compose exec redis redis-cli CONFIG SET maxmemory 1gb
docker-compose exec redis redis-cli CONFIG SET maxmemory-policy allkeys-lru
```

### Temporal Issues

#### Problem: Worker not picking up tasks
**Solutions:**
```bash
# Check worker registration
docker-compose exec temporal tctl taskqueue describe \
  --taskqueue research-queue

# Restart worker
docker-compose restart worker

# Check worker logs
docker-compose logs --tail=100 worker
```

## Debug Logging

### Enable Debug Mode

1. **API Debug Logging:**
   ```bash
   # In .env
   LOG_LEVEL=DEBUG
   FASTAPI_DEBUG=true
   ```

2. **Database Query Logging:**
   ```bash
   # In .env
   SQLALCHEMY_ECHO=true
   ```

3. **Temporal Debug Logging:**
   ```bash
   # In .env
   TEMPORAL_LOG_LEVEL=DEBUG
   ```

4. **Agent Debug Logging:**
   ```bash
   # In .env
   AGENT_DEBUG=true
   GEMINI_DEBUG=true
   ```

### View Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f api

# Filter by level
docker-compose logs api | grep ERROR

# Save logs to file
docker-compose logs > debug.log 2>&1

# Structured log parsing
docker-compose logs api | jq '.level == "ERROR"'
```

## Health Checks

### Comprehensive Health Check Script

```bash
#!/bin/bash
# save as check_health.sh

echo "=== System Health Check ==="

# API Health
echo -n "API: "
curl -s http://localhost:8000/health | jq -r '.status' || echo "DOWN"

# Database
echo -n "PostgreSQL: "
docker-compose exec -T postgres pg_isready > /dev/null 2>&1 && echo "UP" || echo "DOWN"

# Redis
echo -n "Redis: "
docker-compose exec -T redis redis-cli ping > /dev/null 2>&1 && echo "UP" || echo "DOWN"

# Temporal
echo -n "Temporal: "
curl -s http://localhost:8080/health | grep -q "OK" && echo "UP" || echo "DOWN"

# Worker
echo -n "Worker: "
docker-compose ps worker | grep -q "Up" && echo "UP" || echo "DOWN"

# Disk Space
echo -n "Disk Space: "
df -h / | awk 'NR==2 {print $5 " used"}'

# Memory
echo -n "Memory: "
free -h | awk 'NR==2 {print $3 "/" $2 " used"}'

# Docker Resources
echo "=== Docker Resources ==="
docker system df
```

### Monitoring Endpoints

```bash
# Prometheus metrics
curl http://localhost:8000/metrics

# Detailed health with dependencies
curl http://localhost:8000/ready

# Liveness probe
curl http://localhost:8000/live

# Custom health checks
curl http://localhost:8000/api/v1/health/agents
curl http://localhost:8000/api/v1/health/workflows
```

## Recovery Procedures

### Full System Restart

```bash
#!/bin/bash
# Complete system restart procedure

echo "Stopping all services..."
docker-compose down

echo "Cleaning up volumes..."
docker volume prune -f

echo "Rebuilding images..."
docker-compose build --no-cache

echo "Starting services..."
docker-compose up -d

echo "Waiting for services to be ready..."
sleep 30

echo "Running migrations..."
docker-compose exec api alembic upgrade head

echo "Verifying health..."
research-cli health --verbose

echo "System restart complete!"
```

### Database Recovery

```bash
# Backup current state
docker-compose exec postgres pg_dump -U research research_db > backup_$(date +%Y%m%d).sql

# Restore from backup
docker-compose exec -T postgres psql -U research research_db < backup_20240115.sql

# Verify integrity
docker-compose exec postgres psql -U research -d research_db -c \
  "SELECT COUNT(*) FROM research_projects;"
```

### Clear All Caches

```bash
# Redis cache
docker-compose exec redis redis-cli FLUSHALL

# Application cache
rm -rf /tmp/research_platform_cache/*

# Python cache
find . -type d -name __pycache__ -exec rm -rf {} +
```

## Getting Help

### Collect Diagnostic Information

```bash
#!/bin/bash
# save as collect_diagnostics.sh

DIAG_DIR="diagnostics_$(date +%Y%m%d_%H%M%S)"
mkdir -p $DIAG_DIR

# System info
uname -a > $DIAG_DIR/system_info.txt
docker version >> $DIAG_DIR/system_info.txt

# Service status
docker-compose ps > $DIAG_DIR/service_status.txt

# Recent logs
docker-compose logs --tail=1000 > $DIAG_DIR/logs.txt 2>&1

# Configuration (sanitized)
grep -v PASSWORD .env > $DIAG_DIR/config.txt

# Database status
docker-compose exec postgres psql -U research -d research_db \
  -c "SELECT version();" > $DIAG_DIR/db_info.txt 2>&1

# Health checks
research-cli health --verbose > $DIAG_DIR/health.txt 2>&1

# Create archive
tar -czf $DIAG_DIR.tar.gz $DIAG_DIR
echo "Diagnostics collected in $DIAG_DIR.tar.gz"
```

### Support Channels

1. **GitHub Issues**: [Report bugs](https://github.com/your-org/research-platform/issues)
2. **Documentation**: Check [full documentation](./README.md)
3. **Community Discord**: Join discussions
4. **Email Support**: support@research-platform.ai

### Before Reporting Issues

1. Check this troubleshooting guide
2. Search existing GitHub issues
3. Collect diagnostic information
4. Try recovery procedures
5. Include:
   - Error messages
   - Steps to reproduce
   - System configuration
   - Diagnostic archive

## Common Error Codes

| Code | Description | Solution |
|------|-------------|----------|
| E001 | Database connection failed | Check PostgreSQL status and credentials |
| E002 | Redis connection failed | Check Redis status and configuration |
| E003 | Temporal connection failed | Check Temporal server status |
| E004 | Gemini API quota exceeded | Wait for quota reset or upgrade plan |
| E005 | Authentication failed | Verify API key or credentials |
| E006 | Workflow timeout | Increase timeout or check workflow status |
| E007 | Agent execution failed | Check agent logs and retry |
| E008 | Validation error | Check input parameters |
| E009 | Resource not found | Verify resource ID exists |
| E010 | Permission denied | Check user permissions |

## Performance Optimization Tips

1. **Enable connection pooling** for database
2. **Use Redis caching** for frequently accessed data
3. **Implement rate limiting** for external APIs
4. **Use async operations** where possible
5. **Monitor resource usage** with Prometheus
6. **Optimize database queries** with indexes
7. **Scale workers horizontally** for parallel processing
8. **Use CDN** for static assets
9. **Enable compression** for API responses
10. **Implement circuit breakers** for external services