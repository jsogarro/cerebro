# Backup and Recovery Guide

This guide provides comprehensive procedures for backing up and recovering data in the Multi-Agent Research Platform.

## Table of Contents
- [Backup Strategy Overview](#backup-strategy-overview)
- [Database Backup](#database-backup)
- [File Storage Backup](#file-storage-backup)
- [Configuration Backup](#configuration-backup)
- [Application State Backup](#application-state-backup)
- [Recovery Procedures](#recovery-procedures)
- [Disaster Recovery](#disaster-recovery)
- [Monitoring and Testing](#monitoring-and-testing)
- [Automation Scripts](#automation-scripts)

## Backup Strategy Overview

### Backup Types

| Type | Frequency | Retention | Recovery Time | Purpose |
|------|-----------|-----------|---------------|---------|
| **Full Backup** | Weekly | 12 weeks | 2-4 hours | Complete system restore |
| **Incremental** | Daily | 30 days | 30 minutes | Recent changes |
| **Transaction Log** | Every 15 minutes | 7 days | 5 minutes | Point-in-time recovery |
| **Configuration** | On change | Indefinite | 5 minutes | Settings restore |
| **Critical Data** | Real-time | 90 days | Immediate | Business continuity |

### RTO/RPO Targets

| Component | RTO (Recovery Time) | RPO (Recovery Point) | Business Impact |
|-----------|-------------------|-------------------|-----------------|
| **Database** | < 30 minutes | < 15 minutes | Critical |
| **API Service** | < 10 minutes | < 5 minutes | High |
| **File Storage** | < 1 hour | < 1 hour | Medium |
| **Cache** | < 5 minutes | Acceptable loss | Low |
| **Workflows** | < 15 minutes | < 15 minutes | High |

### Backup Architecture

```mermaid
graph TB
    subgraph "Production Environment"
        PG[(PostgreSQL)]
        REDIS[(Redis)]
        FILES[File Storage]
        CONFIG[Configuration]
    end
    
    subgraph "Backup Storage"
        S3[AWS S3]
        LOCAL[Local Storage]
        OFFSITE[Offsite Storage]
    end
    
    subgraph "Backup Tools"
        PGDUMP[pg_dump]
        RDBTOOLS[RDB Tools]
        RSYNC[rsync]
        TAR[tar/gzip]
    end
    
    PG --> PGDUMP
    REDIS --> RDBTOOLS
    FILES --> RSYNC
    CONFIG --> TAR
    
    PGDUMP --> S3
    RDBTOOLS --> S3
    RSYNC --> S3
    TAR --> S3
    
    S3 --> OFFSITE
    LOCAL --> OFFSITE
```

## Database Backup

### PostgreSQL Backup

#### Automated Daily Backup

```bash
#!/bin/bash
# scripts/backup_database.sh

set -euo pipefail

# Configuration
BACKUP_DIR="/var/backups/postgresql"
DB_NAME="research_db"
DB_USER="research"
DB_HOST="localhost"
DB_PORT="5432"
RETENTION_DAYS=30
S3_BUCKET="research-platform-backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Function to log with timestamp
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# Function to send notification
notify() {
    local level=$1
    local message=$2
    # Send to monitoring system (Slack, email, etc.)
    curl -X POST -H 'Content-type: application/json' \
        --data "{\"text\":\"[$level] Database Backup: $message\"}" \
        "$SLACK_WEBHOOK_URL" || true
}

log "Starting database backup..."

# Create backup filename
BACKUP_FILE="$BACKUP_DIR/${DB_NAME}_${TIMESTAMP}.sql"
COMPRESSED_FILE="${BACKUP_FILE}.gz"

# Perform backup
log "Creating database dump..."
if docker-compose exec -T postgres pg_dump \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    --verbose \
    --no-owner \
    --no-privileges \
    --clean \
    --if-exists > "$BACKUP_FILE"; then
    
    log "Database dump created successfully"
else
    log "ERROR: Database dump failed"
    notify "ERROR" "Database backup failed"
    exit 1
fi

# Compress backup
log "Compressing backup..."
gzip "$BACKUP_FILE"

# Verify backup integrity
log "Verifying backup integrity..."
if gzip -t "$COMPRESSED_FILE"; then
    log "Backup integrity verified"
else
    log "ERROR: Backup integrity check failed"
    notify "ERROR" "Backup integrity check failed"
    exit 1
fi

# Upload to S3
log "Uploading to S3..."
if aws s3 cp "$COMPRESSED_FILE" "s3://$S3_BUCKET/database/" \
    --storage-class STANDARD_IA; then
    log "Backup uploaded to S3 successfully"
else
    log "ERROR: S3 upload failed"
    notify "ERROR" "S3 upload failed"
fi

# Calculate backup size
BACKUP_SIZE=$(du -h "$COMPRESSED_FILE" | cut -f1)
log "Backup size: $BACKUP_SIZE"

# Clean up old local backups
log "Cleaning up old backups..."
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +$RETENTION_DAYS -delete

# Clean up old S3 backups
aws s3 ls "s3://$S3_BUCKET/database/" --recursive | \
    awk '$1 < "'$(date -d "-$RETENTION_DAYS days" +%Y-%m-%d)'" {print $4}' | \
    xargs -I {} aws s3 rm "s3://$S3_BUCKET/{}"

log "Database backup completed successfully"
notify "INFO" "Database backup completed successfully (Size: $BACKUP_SIZE)"
```

#### Point-in-Time Recovery Setup

```bash
# Enable WAL archiving for PITR
# In postgresql.conf:
wal_level = replica
archive_mode = on
archive_command = 'aws s3 cp %p s3://research-platform-backups/wal/%f'
archive_timeout = 900  # 15 minutes

# Restart PostgreSQL to apply changes
docker-compose restart postgres
```

#### Custom Backup Script with Table-Level Backups

```python
#!/usr/bin/env python3
# scripts/advanced_backup.py

import asyncio
import asyncpg
import boto3
import gzip
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DatabaseBackupManager:
    def __init__(self):
        self.db_url = os.getenv('DATABASE_URL')
        self.backup_dir = Path('/var/backups/postgresql')
        self.s3_bucket = os.getenv('S3_BACKUP_BUCKET', 'research-platform-backups')
        self.s3_client = boto3.client('s3')
        
    async def create_full_backup(self):
        """Create a full database backup"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # Connect to database
        conn = await asyncpg.connect(self.db_url)
        
        try:
            # Get database schema
            schema = await self.get_database_schema(conn)
            
            # Backup critical tables first
            critical_tables = [
                'research_projects',
                'users',
                'agent_tasks',
                'research_results'
            ]
            
            backup_manifest = {
                'timestamp': timestamp,
                'type': 'full',
                'tables': {},
                'schema': schema
            }
            
            for table in critical_tables:
                logger.info(f"Backing up table: {table}")
                backup_file = await self.backup_table(conn, table, timestamp)
                backup_manifest['tables'][table] = backup_file
            
            # Save backup manifest
            manifest_file = self.backup_dir / f'manifest_{timestamp}.json'
            with open(manifest_file, 'w') as f:
                json.dump(backup_manifest, f, indent=2, default=str)
            
            # Upload manifest to S3
            await self.upload_to_s3(str(manifest_file), f'manifests/manifest_{timestamp}.json')
            
            logger.info("Full backup completed successfully")
            return timestamp
            
        finally:
            await conn.close()
    
    async def backup_table(self, conn, table_name, timestamp):
        """Backup individual table to compressed file"""
        backup_file = self.backup_dir / f'{table_name}_{timestamp}.sql.gz'
        
        # Get table data
        query = f"SELECT * FROM {table_name}"
        rows = await conn.fetch(query)
        
        # Write compressed backup
        with gzip.open(backup_file, 'wt') as f:
            # Write table structure
            f.write(f"-- Backup of {table_name} at {timestamp}\\n")
            f.write(f"TRUNCATE TABLE {table_name} CASCADE;\\n")
            
            # Write data
            for row in rows:
                values = ', '.join([
                    f"'{str(val).replace(\"'\", \"''\")}'" if val is not None else 'NULL'
                    for val in row
                ])
                columns = ', '.join(row.keys())
                f.write(f"INSERT INTO {table_name} ({columns}) VALUES ({values});\\n")
        
        # Upload to S3
        s3_key = f'tables/{table_name}_{timestamp}.sql.gz'
        await self.upload_to_s3(str(backup_file), s3_key)
        
        return str(backup_file)
    
    async def get_database_schema(self, conn):
        """Get database schema information"""
        schema_query = """
        SELECT 
            table_name,
            column_name,
            data_type,
            is_nullable,
            column_default
        FROM information_schema.columns 
        WHERE table_schema = 'public'
        ORDER BY table_name, ordinal_position
        """
        
        rows = await conn.fetch(schema_query)
        schema = {}
        
        for row in rows:
            table = row['table_name']
            if table not in schema:
                schema[table] = []
            
            schema[table].append({
                'column': row['column_name'],
                'type': row['data_type'],
                'nullable': row['is_nullable'],
                'default': row['column_default']
            })
        
        return schema
    
    async def upload_to_s3(self, local_path, s3_key):
        """Upload file to S3"""
        try:
            self.s3_client.upload_file(
                local_path,
                self.s3_bucket,
                s3_key,
                ExtraArgs={'StorageClass': 'STANDARD_IA'}
            )
            logger.info(f"Uploaded {local_path} to s3://{self.s3_bucket}/{s3_key}")
        except Exception as e:
            logger.error(f"Failed to upload {local_path}: {e}")
            raise

async def main():
    backup_manager = DatabaseBackupManager()
    await backup_manager.create_full_backup()

if __name__ == "__main__":
    asyncio.run(main())
```

### Redis Backup

```bash
#!/bin/bash
# scripts/backup_redis.sh

set -euo pipefail

BACKUP_DIR="/var/backups/redis"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
S3_BUCKET="research-platform-backups"

mkdir -p "$BACKUP_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

log "Starting Redis backup..."

# Create Redis backup
REDIS_BACKUP="$BACKUP_DIR/redis_${TIMESTAMP}.rdb"

# Use BGSAVE for non-blocking backup
docker-compose exec redis redis-cli BGSAVE

# Wait for backup to complete
while docker-compose exec redis redis-cli LASTSAVE | grep -q "$(docker-compose exec redis redis-cli LASTSAVE)"; do
    sleep 1
done

# Copy the RDB file
docker-compose exec redis cp /data/dump.rdb "/backups/redis_${TIMESTAMP}.rdb"

# Compress backup
gzip "$REDIS_BACKUP"

# Upload to S3
aws s3 cp "${REDIS_BACKUP}.gz" "s3://$S3_BUCKET/redis/"

# Clean up old backups
find "$BACKUP_DIR" -name "*.rdb.gz" -mtime +7 -delete

log "Redis backup completed"
```

## File Storage Backup

### Local File Storage Backup

```bash
#!/bin/bash
# scripts/backup_files.sh

set -euo pipefail

SOURCE_DIR="/app/storage"
BACKUP_DIR="/var/backups/files"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
S3_BUCKET="research-platform-backups"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

log "Starting file storage backup..."

# Create incremental backup using rsync
BACKUP_PATH="$BACKUP_DIR/$TIMESTAMP"
LATEST_LINK="$BACKUP_DIR/latest"

# Create hardlink backup for space efficiency
if [ -d "$LATEST_LINK" ]; then
    rsync -av --delete --link-dest="$LATEST_LINK" "$SOURCE_DIR/" "$BACKUP_PATH/"
else
    rsync -av "$SOURCE_DIR/" "$BACKUP_PATH/"
fi

# Update latest link
rm -f "$LATEST_LINK"
ln -s "$BACKUP_PATH" "$LATEST_LINK"

# Create tar archive for S3 upload
TAR_FILE="$BACKUP_DIR/files_${TIMESTAMP}.tar.gz"
tar -czf "$TAR_FILE" -C "$BACKUP_PATH" .

# Upload to S3
aws s3 cp "$TAR_FILE" "s3://$S3_BUCKET/files/"

# Clean up old backups (keep last 30 days)
find "$BACKUP_DIR" -maxdepth 1 -type d -name "20*" -mtime +30 -exec rm -rf {} \\;
find "$BACKUP_DIR" -name "files_*.tar.gz" -mtime +30 -delete

# Calculate backup statistics
BACKUP_SIZE=$(du -sh "$BACKUP_PATH" | cut -f1)
FILE_COUNT=$(find "$BACKUP_PATH" -type f | wc -l)

log "File backup completed - Size: $BACKUP_SIZE, Files: $FILE_COUNT"
```

### S3 to S3 Cross-Region Backup

```python
#!/usr/bin/env python3
# scripts/s3_cross_region_backup.py

import boto3
import logging
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class S3CrossRegionBackup:
    def __init__(self):
        self.source_bucket = 'research-platform-storage'
        self.backup_bucket = 'research-platform-backup-eu'
        self.source_region = 'us-east-1'
        self.backup_region = 'eu-west-1'
        
        self.source_s3 = boto3.client('s3', region_name=self.source_region)
        self.backup_s3 = boto3.client('s3', region_name=self.backup_region)
    
    def sync_to_backup_region(self):
        """Sync files to backup region"""
        logger.info("Starting cross-region backup sync...")
        
        # List objects in source bucket
        paginator = self.source_s3.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=self.source_bucket)
        
        synced_count = 0
        error_count = 0
        
        for page in pages:
            if 'Contents' not in page:
                continue
                
            for obj in page['Contents']:
                key = obj['Key']
                
                try:
                    # Check if object exists in backup bucket
                    try:
                        self.backup_s3.head_object(Bucket=self.backup_bucket, Key=key)
                        continue  # Object already exists
                    except self.backup_s3.exceptions.NoSuchKey:
                        pass  # Object doesn't exist, proceed with copy
                    
                    # Copy object to backup region
                    copy_source = {
                        'Bucket': self.source_bucket,
                        'Key': key
                    }
                    
                    self.backup_s3.copy_object(
                        CopySource=copy_source,
                        Bucket=self.backup_bucket,
                        Key=key,
                        StorageClass='STANDARD_IA'
                    )
                    
                    synced_count += 1
                    logger.info(f"Synced: {key}")
                    
                except Exception as e:
                    logger.error(f"Failed to sync {key}: {e}")
                    error_count += 1
        
        logger.info(f"Cross-region sync completed - Synced: {synced_count}, Errors: {error_count}")
    
    def cleanup_old_backups(self, retention_days=90):
        """Clean up old backup files"""
        cutoff_date = datetime.now() - timedelta(days=retention_days)
        
        logger.info(f"Cleaning up backups older than {cutoff_date}")
        
        paginator = self.backup_s3.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=self.backup_bucket)
        
        deleted_count = 0
        
        for page in pages:
            if 'Contents' not in page:
                continue
                
            for obj in page['Contents']:
                if obj['LastModified'].replace(tzinfo=None) < cutoff_date:
                    try:
                        self.backup_s3.delete_object(
                            Bucket=self.backup_bucket,
                            Key=obj['Key']
                        )
                        deleted_count += 1
                        logger.info(f"Deleted old backup: {obj['Key']}")
                    except Exception as e:
                        logger.error(f"Failed to delete {obj['Key']}: {e}")
        
        logger.info(f"Cleanup completed - Deleted: {deleted_count} objects")

if __name__ == "__main__":
    backup = S3CrossRegionBackup()
    backup.sync_to_backup_region()
    backup.cleanup_old_backups()
```

## Configuration Backup

### Environment and Configuration Backup

```bash
#!/bin/bash
# scripts/backup_config.sh

set -euo pipefail

CONFIG_BACKUP_DIR="/var/backups/config"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
S3_BUCKET="research-platform-backups"

mkdir -p "$CONFIG_BACKUP_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

log "Starting configuration backup..."

# Create configuration archive
CONFIG_ARCHIVE="$CONFIG_BACKUP_DIR/config_${TIMESTAMP}.tar.gz"

# Backup configuration files (excluding sensitive data)
tar -czf "$CONFIG_ARCHIVE" \\
    --exclude="*.log" \\
    --exclude="*.tmp" \\
    --exclude="*secret*" \\
    --exclude="*password*" \\
    --exclude="*key*" \\
    docker-compose.yml \\
    docker-compose.override.yml \\
    nginx.conf \\
    alembic.ini \\
    pyproject.toml \\
    k8s/ \\
    helm/ \\
    config/ \\
    scripts/

# Create environment template (sanitized)
ENV_TEMPLATE="$CONFIG_BACKUP_DIR/env_template_${TIMESTAMP}.txt"
cat > "$ENV_TEMPLATE" << 'EOF'
# Environment Template (sensitive values removed)
ENVIRONMENT=production
DEBUG=false
LOG_LEVEL=INFO

# Database (update with actual values)
DATABASE_URL=postgresql+asyncpg://user:password@host:port/db
DATABASE_POOL_SIZE=20

# Redis (update with actual values)  
REDIS_URL=redis://host:port/0

# External APIs (add actual keys)
GEMINI_API_KEY=your-gemini-api-key

# Authentication (generate new secrets)
JWT_SECRET_KEY=generate-new-secret-key

# Storage
STORAGE_BACKEND=s3
STORAGE_S3_BUCKET=your-bucket-name

# Monitoring
METRICS_ENABLED=true
EOF

# Create deployment checklist
CHECKLIST="$CONFIG_BACKUP_DIR/deployment_checklist_${TIMESTAMP}.md"
cat > "$CHECKLIST" << 'EOF'
# Deployment Checklist

## Pre-deployment
- [ ] Backup current database
- [ ] Backup current configuration
- [ ] Test new configuration in staging
- [ ] Verify all environment variables
- [ ] Check SSL certificates
- [ ] Verify monitoring setup

## Deployment
- [ ] Deploy application
- [ ] Run database migrations
- [ ] Start services
- [ ] Verify health checks
- [ ] Test critical functionality
- [ ] Monitor logs for errors

## Post-deployment
- [ ] Verify all services running
- [ ] Check monitoring dashboards
- [ ] Test user workflows
- [ ] Verify backup processes
- [ ] Update documentation
- [ ] Notify stakeholders
EOF

# Upload to S3
aws s3 cp "$CONFIG_ARCHIVE" "s3://$S3_BUCKET/config/"
aws s3 cp "$ENV_TEMPLATE" "s3://$S3_BUCKET/config/"
aws s3 cp "$CHECKLIST" "s3://$S3_BUCKET/config/"

# Clean up old config backups
find "$CONFIG_BACKUP_DIR" -name "config_*.tar.gz" -mtime +90 -delete

log "Configuration backup completed"
```

## Application State Backup

### Temporal Workflow State Backup

```python
#!/usr/bin/env python3
# scripts/backup_temporal_state.py

import asyncio
import json
import logging
from datetime import datetime
from temporalio.client import Client
from temporalio.service import WorkflowService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TemporalStateBackup:
    def __init__(self):
        self.temporal_host = "localhost:7233"
        self.namespace = "default"
        self.backup_file = f"/var/backups/temporal/workflows_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
    async def backup_workflow_state(self):
        """Backup all active workflow states"""
        client = await Client.connect(self.temporal_host)
        
        try:
            # Get all running workflows
            async for workflow in client.list_workflows():
                workflow_id = workflow.id
                workflow_type = workflow.workflow_type
                
                logger.info(f"Backing up workflow: {workflow_id}")
                
                # Get workflow history
                async for event in client.get_workflow_history(workflow_id):
                    # Process and store workflow state
                    pass
                    
            logger.info("Temporal state backup completed")
            
        finally:
            await client.close()

# Additional backup for LangGraph checkpoints
async def backup_langgraph_state():
    """Backup LangGraph workflow checkpoints"""
    # Implementation depends on your checkpoint storage
    pass
```

## Recovery Procedures

### Database Recovery

#### Full Database Restore

```bash
#!/bin/bash
# scripts/restore_database.sh

set -euo pipefail

BACKUP_FILE="$1"
DB_NAME="research_db"
DB_USER="research"

if [ -z "$BACKUP_FILE" ]; then
    echo "Usage: $0 <backup_file>"
    exit 1
fi

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

log "Starting database restore from: $BACKUP_FILE"

# Stop application services
log "Stopping application services..."
docker-compose stop api worker

# Create backup of current database
log "Creating backup of current database..."
CURRENT_BACKUP="/tmp/pre_restore_backup_$(date +%Y%m%d_%H%M%S).sql"
docker-compose exec -T postgres pg_dump -U "$DB_USER" -d "$DB_NAME" > "$CURRENT_BACKUP"

# Drop and recreate database
log "Recreating database..."
docker-compose exec -T postgres psql -U "$DB_USER" -c "DROP DATABASE IF EXISTS ${DB_NAME};"
docker-compose exec -T postgres psql -U "$DB_USER" -c "CREATE DATABASE ${DB_NAME};"

# Restore from backup
log "Restoring from backup..."
if [ "${BACKUP_FILE##*.}" = "gz" ]; then
    zcat "$BACKUP_FILE" | docker-compose exec -T postgres psql -U "$DB_USER" -d "$DB_NAME"
else
    cat "$BACKUP_FILE" | docker-compose exec -T postgres psql -U "$DB_USER" -d "$DB_NAME"
fi

# Run migrations to ensure schema is current
log "Running database migrations..."
docker-compose exec api alembic upgrade head

# Verify restore
log "Verifying restore..."
RECORD_COUNT=$(docker-compose exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -t -c "SELECT COUNT(*) FROM research_projects;")
log "Restored database contains $RECORD_COUNT research projects"

# Start services
log "Starting application services..."
docker-compose start api worker

# Verify application health
sleep 10
if curl -f http://localhost:8000/health > /dev/null 2>&1; then
    log "Database restore completed successfully"
else
    log "WARNING: Application health check failed after restore"
fi
```

#### Point-in-Time Recovery

```bash
#!/bin/bash
# scripts/pitr_restore.sh

TARGET_TIME="$1"  # Format: 2024-01-15 14:30:00

if [ -z "$TARGET_TIME" ]; then
    echo "Usage: $0 'YYYY-MM-DD HH:MM:SS'"
    exit 1
fi

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

log "Starting point-in-time recovery to: $TARGET_TIME"

# Stop PostgreSQL
docker-compose stop postgres

# Restore base backup
BASE_BACKUP="/var/backups/postgresql/base_backup_latest.tar.gz"
tar -xzf "$BASE_BACKUP" -C /var/lib/docker/volumes/postgres_data/_data/

# Create recovery configuration
cat > /var/lib/docker/volumes/postgres_data/_data/recovery.conf << EOF
restore_command = 'aws s3 cp s3://research-platform-backups/wal/%f %p'
recovery_target_time = '$TARGET_TIME'
recovery_target_timeline = 'latest'
EOF

# Start PostgreSQL in recovery mode
docker-compose start postgres

# Monitor recovery progress
log "Monitoring recovery progress..."
while docker-compose exec postgres pg_controldata | grep -q "in archive recovery"; do
    sleep 5
done

log "Point-in-time recovery completed"
```

### Application Recovery

#### Complete System Recovery

```bash
#!/bin/bash
# scripts/disaster_recovery.sh

set -euo pipefail

RECOVERY_DATE="$1"
S3_BUCKET="research-platform-backups"

if [ -z "$RECOVERY_DATE" ]; then
    echo "Usage: $0 YYYYMMDD"
    exit 1
fi

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

log "Starting disaster recovery for date: $RECOVERY_DATE"

# Create recovery directory
RECOVERY_DIR="/tmp/recovery_${RECOVERY_DATE}"
mkdir -p "$RECOVERY_DIR"

# Download backups from S3
log "Downloading backups from S3..."
aws s3 sync "s3://$S3_BUCKET" "$RECOVERY_DIR" --exclude "*" --include "*${RECOVERY_DATE}*"

# Restore configuration
log "Restoring configuration..."
CONFIG_ARCHIVE=$(find "$RECOVERY_DIR" -name "config_${RECOVERY_DATE}*.tar.gz" | head -1)
if [ -n "$CONFIG_ARCHIVE" ]; then
    tar -xzf "$CONFIG_ARCHIVE" -C /
fi

# Restore database
log "Restoring database..."
DB_BACKUP=$(find "$RECOVERY_DIR" -name "*${RECOVERY_DATE}*.sql.gz" | head -1)
if [ -n "$DB_BACKUP" ]; then
    ./scripts/restore_database.sh "$DB_BACKUP"
fi

# Restore Redis
log "Restoring Redis data..."
REDIS_BACKUP=$(find "$RECOVERY_DIR" -name "redis_${RECOVERY_DATE}*.rdb.gz" | head -1)
if [ -n "$REDIS_BACKUP" ]; then
    docker-compose stop redis
    zcat "$REDIS_BACKUP" > /var/lib/docker/volumes/redis_data/_data/dump.rdb
    docker-compose start redis
fi

# Restore files
log "Restoring file storage..."
FILES_BACKUP=$(find "$RECOVERY_DIR" -name "files_${RECOVERY_DATE}*.tar.gz" | head -1)
if [ -n "$FILES_BACKUP" ]; then
    rm -rf /app/storage/*
    tar -xzf "$FILES_BACKUP" -C /app/storage/
fi

# Start all services
log "Starting all services..."
docker-compose up -d

# Wait for services to start
sleep 30

# Verify recovery
log "Verifying recovery..."
if curl -f http://localhost:8000/health > /dev/null 2>&1; then
    log "Disaster recovery completed successfully"
else
    log "ERROR: System health check failed after recovery"
    exit 1
fi

# Clean up recovery directory
rm -rf "$RECOVERY_DIR"

log "Recovery process completed"
```

## Monitoring and Testing

### Backup Monitoring

```python
#!/usr/bin/env python3
# scripts/monitor_backups.py

import boto3
import json
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Dict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class BackupStatus:
    component: str
    last_backup: datetime
    size: int
    status: str
    age_hours: float

class BackupMonitor:
    def __init__(self):
        self.s3_client = boto3.client('s3')
        self.bucket = 'research-platform-backups'
        self.slack_webhook = os.getenv('SLACK_WEBHOOK_URL')
        
        # Define backup requirements
        self.backup_requirements = {
            'database': {'max_age_hours': 24, 'min_size_mb': 10},
            'redis': {'max_age_hours': 24, 'min_size_mb': 1},
            'files': {'max_age_hours': 24, 'min_size_mb': 50},
            'config': {'max_age_hours': 168, 'min_size_mb': 1}  # Weekly
        }
    
    def check_backup_status(self) -> List[BackupStatus]:
        """Check status of all backup components"""
        statuses = []
        
        for component in self.backup_requirements.keys():
            try:
                latest_backup = self.get_latest_backup(component)
                if latest_backup:
                    age_hours = (datetime.now() - latest_backup['LastModified'].replace(tzinfo=None)).total_seconds() / 3600
                    size_mb = latest_backup['Size'] / (1024 * 1024)
                    
                    # Determine status
                    requirements = self.backup_requirements[component]
                    if age_hours > requirements['max_age_hours']:
                        status = 'STALE'
                    elif size_mb < requirements['min_size_mb']:
                        status = 'TOO_SMALL'
                    else:
                        status = 'OK'
                    
                    statuses.append(BackupStatus(
                        component=component,
                        last_backup=latest_backup['LastModified'].replace(tzinfo=None),
                        size=latest_backup['Size'],
                        status=status,
                        age_hours=age_hours
                    ))
                else:
                    statuses.append(BackupStatus(
                        component=component,
                        last_backup=None,
                        size=0,
                        status='MISSING',
                        age_hours=0
                    ))
            except Exception as e:
                logger.error(f"Error checking {component} backup: {e}")
                statuses.append(BackupStatus(
                    component=component,
                    last_backup=None,
                    size=0,
                    status='ERROR',
                    age_hours=0
                ))
        
        return statuses
    
    def get_latest_backup(self, component: str) -> Dict:
        """Get latest backup for component"""
        try:
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=f'{component}/',
                MaxKeys=1000
            )
            
            if 'Contents' not in response:
                return None
            
            # Sort by last modified and return latest
            objects = sorted(response['Contents'], key=lambda x: x['LastModified'], reverse=True)
            return objects[0] if objects else None
            
        except Exception as e:
            logger.error(f"Error listing {component} backups: {e}")
            return None
    
    def send_alert(self, statuses: List[BackupStatus]):
        """Send alert if backups are failing"""
        failed_backups = [s for s in statuses if s.status != 'OK']
        
        if not failed_backups:
            return
        
        message = "🚨 Backup Alert\\n\\n"
        for backup in failed_backups:
            message += f"• {backup.component}: {backup.status}"
            if backup.last_backup:
                message += f" (last: {backup.last_backup}, age: {backup.age_hours:.1f}h)"
            message += "\\n"
        
        # Send to Slack
        if self.slack_webhook:
            requests.post(self.slack_webhook, json={'text': message})
        
        # Log alert
        logger.warning(f"Backup alert: {len(failed_backups)} components failing")
    
    def generate_report(self) -> Dict:
        """Generate backup status report"""
        statuses = self.check_backup_status()
        
        report = {
            'timestamp': datetime.now().isoformat(),
            'overall_status': 'OK' if all(s.status == 'OK' for s in statuses) else 'ALERT',
            'components': {}
        }
        
        for status in statuses:
            report['components'][status.component] = {
                'status': status.status,
                'last_backup': status.last_backup.isoformat() if status.last_backup else None,
                'size_mb': round(status.size / (1024 * 1024), 2),
                'age_hours': round(status.age_hours, 1)
            }
        
        return report
    
    def run_monitoring_check(self):
        """Run complete monitoring check"""
        logger.info("Starting backup monitoring check...")
        
        statuses = self.check_backup_status()
        self.send_alert(statuses)
        
        report = self.generate_report()
        
        # Save report
        report_file = f"/var/logs/backup_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2)
        
        logger.info(f"Monitoring check completed. Report saved to {report_file}")
        
        return report

if __name__ == "__main__":
    monitor = BackupMonitor()
    monitor.run_monitoring_check()
```

### Backup Testing

```bash
#!/bin/bash
# scripts/test_backup_restore.sh

set -euo pipefail

TEST_ENV="backup_test"
ORIGINAL_DB="research_db"
TEST_DB="test_restore_db"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

log "Starting backup restore test..."

# Find latest backup
LATEST_BACKUP=$(ls -t /var/backups/postgresql/*.sql.gz | head -1)
log "Testing restore of: $LATEST_BACKUP"

# Create test database
docker-compose exec -T postgres psql -U research -c "DROP DATABASE IF EXISTS $TEST_DB;"
docker-compose exec -T postgres psql -U research -c "CREATE DATABASE $TEST_DB;"

# Restore backup to test database
log "Restoring backup to test database..."
zcat "$LATEST_BACKUP" | docker-compose exec -T postgres psql -U research -d "$TEST_DB"

# Verify restore
log "Verifying restore..."

# Check table counts
ORIGINAL_COUNT=$(docker-compose exec -T postgres psql -U research -d "$ORIGINAL_DB" -t -c "SELECT COUNT(*) FROM research_projects;")
RESTORED_COUNT=$(docker-compose exec -T postgres psql -U research -d "$TEST_DB" -t -c "SELECT COUNT(*) FROM research_projects;")

log "Original projects: $ORIGINAL_COUNT"
log "Restored projects: $RESTORED_COUNT"

# Verify data integrity
ORIGINAL_CHECKSUM=$(docker-compose exec -T postgres psql -U research -d "$ORIGINAL_DB" -t -c "SELECT md5(string_agg(id::text, '' ORDER BY id)) FROM research_projects;")
RESTORED_CHECKSUM=$(docker-compose exec -T postgres psql -U research -d "$TEST_DB" -t -c "SELECT md5(string_agg(id::text, '' ORDER BY id)) FROM research_projects;")

if [ "$ORIGINAL_CHECKSUM" = "$RESTORED_CHECKSUM" ]; then
    log "✅ Backup restore test PASSED"
    TEST_RESULT="PASS"
else
    log "❌ Backup restore test FAILED - data integrity check failed"
    TEST_RESULT="FAIL"
fi

# Clean up test database
docker-compose exec -T postgres psql -U research -c "DROP DATABASE $TEST_DB;"

# Log test result
echo "$(date): Backup restore test $TEST_RESULT" >> /var/logs/backup_tests.log

log "Backup restore test completed: $TEST_RESULT"
```

## Automation Scripts

### Cron Configuration

```bash
# /etc/crontab - Backup automation

# Database backup (daily at 2 AM)
0 2 * * * root /opt/research-platform/scripts/backup_database.sh

# Redis backup (daily at 2:30 AM)
30 2 * * * root /opt/research-platform/scripts/backup_redis.sh

# File backup (daily at 3 AM)
0 3 * * * root /opt/research-platform/scripts/backup_files.sh

# Configuration backup (weekly on Sunday at 1 AM)
0 1 * * 0 root /opt/research-platform/scripts/backup_config.sh

# Cross-region sync (daily at 4 AM)
0 4 * * * root /usr/bin/python3 /opt/research-platform/scripts/s3_cross_region_backup.py

# Backup monitoring (every 6 hours)
0 */6 * * * root /usr/bin/python3 /opt/research-platform/scripts/monitor_backups.py

# Backup testing (weekly on Saturday at 6 AM)
0 6 * * 6 root /opt/research-platform/scripts/test_backup_restore.sh

# Cleanup old local backups (daily at 5 AM)
0 5 * * * root find /var/backups -type f -mtime +30 -delete
```

### Comprehensive Backup Orchestrator

```python
#!/usr/bin/env python3
# scripts/backup_orchestrator.py

import asyncio
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class BackupOrchestrator:
    def __init__(self):
        self.script_dir = Path('/opt/research-platform/scripts')
        self.backup_sequence = [
            ('database', 'backup_database.sh'),
            ('redis', 'backup_redis.sh'),
            ('files', 'backup_files.sh'),
            ('config', 'backup_config.sh'),
        ]
    
    async def run_backup_sequence(self) -> Dict[str, str]:
        """Run all backups in sequence"""
        results = {}
        
        logger.info("Starting backup sequence...")
        
        for component, script in self.backup_sequence:
            logger.info(f"Starting {component} backup...")
            
            try:
                result = await self.run_backup_script(script)
                results[component] = 'SUCCESS' if result == 0 else 'FAILED'
                
                if result != 0:
                    logger.error(f"{component} backup failed with exit code {result}")
                else:
                    logger.info(f"{component} backup completed successfully")
                    
            except Exception as e:
                logger.error(f"Error running {component} backup: {e}")
                results[component] = 'ERROR'
        
        return results
    
    async def run_backup_script(self, script_name: str) -> int:
        """Run individual backup script"""
        script_path = self.script_dir / script_name
        
        if not script_path.exists():
            raise FileNotFoundError(f"Backup script not found: {script_path}")
        
        process = await asyncio.create_subprocess_exec(
            str(script_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if stdout:
            logger.info(f"Script output: {stdout.decode()}")
        if stderr:
            logger.error(f"Script errors: {stderr.decode()}")
        
        return process.returncode
    
    def send_summary_notification(self, results: Dict[str, str]):
        """Send backup summary notification"""
        failed_backups = [k for k, v in results.items() if v != 'SUCCESS']
        
        if failed_backups:
            status = "🚨 FAILED"
            message = f"Backup failures: {', '.join(failed_backups)}"
        else:
            status = "✅ SUCCESS"
            message = "All backups completed successfully"
        
        summary = f"""
Backup Summary - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Status: {status}
Message: {message}

Details:
{chr(10).join([f"  {k}: {v}" for k, v in results.items()])}
        """
        
        logger.info(summary)
        
        # Send notification (implement your notification method)
        # e.g., Slack, email, etc.

async def main():
    orchestrator = BackupOrchestrator()
    results = await orchestrator.run_backup_sequence()
    orchestrator.send_summary_notification(results)
    
    # Exit with error code if any backup failed
    if any(result != 'SUCCESS' for result in results.values()):
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
```

This comprehensive backup and recovery guide ensures that your Multi-Agent Research Platform data is protected and can be quickly restored in case of any failures or disasters. Regular testing of backup and recovery procedures is essential to ensure they work when needed.