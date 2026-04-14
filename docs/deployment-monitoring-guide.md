# Deployment and Monitoring Guide

## Overview

This guide covers deployment strategies, infrastructure setup, monitoring, and operational procedures for the Multi-Agent Research Platform. The platform is designed for cloud-native deployment with support for multiple environments and scaling strategies.

## Architecture Overview

### Production Architecture

```mermaid
graph TB
    subgraph "Load Balancer"
        LB[Load Balancer]
        CDN[CDN]
    end
    
    subgraph "Application Layer"
        API1[API Instance 1]
        API2[API Instance 2]
        API3[API Instance 3]
        WORKER1[Temporal Worker 1]
        WORKER2[Temporal Worker 2]
        WORKER3[Temporal Worker 3]
    end
    
    subgraph "Data Layer"
        PG[(PostgreSQL Cluster)]
        REDIS[(Redis Cluster)]
        TEMPORAL[(Temporal Cluster)]
    end
    
    subgraph "External Services"
        GEMINI[Google Gemini API]
        MCP[MCP Tool Servers]
        STORAGE[Object Storage]
    end
    
    subgraph "Monitoring"
        PROMETHEUS[Prometheus]
        GRAFANA[Grafana]
        LOGS[Log Aggregation]
        ALERTS[Alerting]
    end
    
    CDN --> LB
    LB --> API1
    LB --> API2
    LB --> API3
    
    API1 --> PG
    API2 --> PG
    API3 --> PG
    
    API1 --> REDIS
    API2 --> REDIS
    API3 --> REDIS
    
    WORKER1 --> TEMPORAL
    WORKER2 --> TEMPORAL
    WORKER3 --> TEMPORAL
    
    API1 --> GEMINI
    API2 --> GEMINI
    API3 --> GEMINI
    
    WORKER1 --> MCP
    WORKER2 --> MCP
    WORKER3 --> MCP
    
    TEMPORAL --> PG
    
    API1 --> PROMETHEUS
    API2 --> PROMETHEUS
    API3 --> PROMETHEUS
    WORKER1 --> PROMETHEUS
    WORKER2 --> PROMETHEUS
    WORKER3 --> PROMETHEUS
    
    PROMETHEUS --> GRAFANA
    PROMETHEUS --> ALERTS
```

## Deployment Strategies

### 1. Docker Deployment

#### Production Docker Compose

```yaml
# docker-compose.prod.yml
version: '3.8'

services:
  # API Services
  api:
    image: research-platform/api:${VERSION}
    deploy:
      replicas: 3
      resources:
        limits:
          memory: 2G
          cpus: '1.0'
        reservations:
          memory: 1G
          cpus: '0.5'
    environment:
      - ENVIRONMENT=production
      - DATABASE_URL=${DATABASE_URL}
      - REDIS_URL=${REDIS_URL}
      - TEMPORAL_HOST=${TEMPORAL_HOST}
      - GEMINI_API_KEY=${GEMINI_API_KEY}
    networks:
      - app-network
    depends_on:
      - postgres
      - redis
      - temporal
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

  # Temporal Workers
  temporal-worker:
    image: research-platform/worker:${VERSION}
    deploy:
      replicas: 3
      resources:
        limits:
          memory: 4G
          cpus: '2.0'
        reservations:
          memory: 2G
          cpus: '1.0'
    environment:
      - ENVIRONMENT=production
      - DATABASE_URL=${DATABASE_URL}
      - REDIS_URL=${REDIS_URL}
      - TEMPORAL_HOST=${TEMPORAL_HOST}
      - GEMINI_API_KEY=${GEMINI_API_KEY}
    networks:
      - app-network
    depends_on:
      - temporal
      - postgres

  # Load Balancer
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf
      - ./ssl:/etc/nginx/ssl
    networks:
      - app-network
    depends_on:
      - api

  # Database
  postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: research_db
      POSTGRES_USER: research
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./postgres/init.sql:/docker-entrypoint-initdb.d/init.sql
    networks:
      - app-network
    deploy:
      resources:
        limits:
          memory: 4G
          cpus: '2.0'

  # Redis
  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes --requirepass ${REDIS_PASSWORD}
    volumes:
      - redis_data:/data
    networks:
      - app-network
    deploy:
      resources:
        limits:
          memory: 1G
          cpus: '0.5'

  # Temporal
  temporal:
    image: temporalio/auto-setup:latest
    environment:
      - DB=postgresql
      - DB_PORT=5432
      - POSTGRES_USER=temporal
      - POSTGRES_PWD=${TEMPORAL_DB_PASSWORD}
      - POSTGRES_SEEDS=postgres
    networks:
      - app-network
    depends_on:
      - postgres

  # Monitoring
  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus_data:/prometheus
    networks:
      - app-network

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PASSWORD}
    volumes:
      - grafana_data:/var/lib/grafana
      - ./monitoring/grafana/dashboards:/etc/grafana/provisioning/dashboards
      - ./monitoring/grafana/datasources:/etc/grafana/provisioning/datasources
    networks:
      - app-network

volumes:
  postgres_data:
  redis_data:
  prometheus_data:
  grafana_data:

networks:
  app-network:
    driver: overlay
```

#### Nginx Configuration

```nginx
# nginx/nginx.conf
events {
    worker_connections 1024;
}

http {
    upstream api_backend {
        least_conn;
        server api:8000 max_fails=3 fail_timeout=30s;
    }
    
    # Rate limiting
    limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;
    limit_req_zone $binary_remote_addr zone=uploads:10m rate=1r/s;
    
    server {
        listen 80;
        server_name api.researchplatform.com;
        
        # Redirect HTTP to HTTPS
        return 301 https://$server_name$request_uri;
    }
    
    server {
        listen 443 ssl http2;
        server_name api.researchplatform.com;
        
        # SSL Configuration
        ssl_certificate /etc/nginx/ssl/cert.pem;
        ssl_certificate_key /etc/nginx/ssl/key.pem;
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers HIGH:!aNULL:!MD5;
        
        # Security Headers
        add_header X-Frame-Options DENY;
        add_header X-Content-Type-Options nosniff;
        add_header X-XSS-Protection "1; mode=block";
        add_header Strict-Transport-Security "max-age=31536000; includeSubDomains";
        
        # API Routes
        location /api/ {
            limit_req zone=api burst=20 nodelay;
            
            proxy_pass http://api_backend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            
            # Timeouts
            proxy_connect_timeout 30s;
            proxy_send_timeout 30s;
            proxy_read_timeout 60s;
        }
        
        # WebSocket Support
        location /ws/ {
            proxy_pass http://api_backend;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            
            # WebSocket timeouts
            proxy_read_timeout 3600s;
            proxy_send_timeout 3600s;
        }
        
        # File Uploads
        location /api/v1/reports/upload {
            limit_req zone=uploads burst=5 nodelay;
            client_max_body_size 50M;
            
            proxy_pass http://api_backend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            
            proxy_request_buffering off;
        }
        
        # Health Checks
        location /health {
            access_log off;
            proxy_pass http://api_backend;
        }
    }
}
```

### 2. Kubernetes Deployment

#### Namespace and Resources

```yaml
# k8s/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: research-platform
  labels:
    name: research-platform
```

#### ConfigMap and Secrets

```yaml
# k8s/configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: research-platform-config
  namespace: research-platform
data:
  ENVIRONMENT: "production"
  LOG_LEVEL: "INFO"
  TEMPORAL_HOST: "temporal.research-platform.svc.cluster.local:7233"
  REDIS_URL: "redis://redis.research-platform.svc.cluster.local:6379"

---
apiVersion: v1
kind: Secret
metadata:
  name: research-platform-secrets
  namespace: research-platform
type: Opaque
data:
  DATABASE_URL: <base64-encoded-database-url>
  GEMINI_API_KEY: <base64-encoded-gemini-key>
  JWT_SECRET_KEY: <base64-encoded-jwt-secret>
  REDIS_PASSWORD: <base64-encoded-redis-password>
```

#### API Deployment

```yaml
# k8s/api-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: research-platform-api
  namespace: research-platform
  labels:
    app: research-platform-api
spec:
  replicas: 3
  selector:
    matchLabels:
      app: research-platform-api
  template:
    metadata:
      labels:
        app: research-platform-api
    spec:
      containers:
      - name: api
        image: research-platform/api:latest
        ports:
        - containerPort: 8000
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: research-platform-secrets
              key: DATABASE_URL
        - name: GEMINI_API_KEY
          valueFrom:
            secretKeyRef:
              name: research-platform-secrets
              key: GEMINI_API_KEY
        envFrom:
        - configMapRef:
            name: research-platform-config
        resources:
          requests:
            memory: "1Gi"
            cpu: "500m"
          limits:
            memory: "2Gi"
            cpu: "1"
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /ready
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 5
        lifecycle:
          preStop:
            exec:
              command: ["/bin/sh", "-c", "sleep 15"]

---
apiVersion: v1
kind: Service
metadata:
  name: research-platform-api
  namespace: research-platform
spec:
  selector:
    app: research-platform-api
  ports:
  - port: 8000
    targetPort: 8000
  type: ClusterIP
```

#### Temporal Worker Deployment

```yaml
# k8s/worker-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: research-platform-worker
  namespace: research-platform
  labels:
    app: research-platform-worker
spec:
  replicas: 3
  selector:
    matchLabels:
      app: research-platform-worker
  template:
    metadata:
      labels:
        app: research-platform-worker
    spec:
      containers:
      - name: worker
        image: research-platform/worker:latest
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: research-platform-secrets
              key: DATABASE_URL
        - name: GEMINI_API_KEY
          valueFrom:
            secretKeyRef:
              name: research-platform-secrets
              key: GEMINI_API_KEY
        envFrom:
        - configMapRef:
            name: research-platform-config
        resources:
          requests:
            memory: "2Gi"
            cpu: "1"
          limits:
            memory: "4Gi"
            cpu: "2"
        livenessProbe:
          exec:
            command:
            - /bin/sh
            - -c
            - "pgrep -f 'temporal.*worker' || exit 1"
          initialDelaySeconds: 60
          periodSeconds: 30
```

#### Ingress Configuration

```yaml
# k8s/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: research-platform-ingress
  namespace: research-platform
  annotations:
    kubernetes.io/ingress.class: nginx
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/rate-limit: "100"
    nginx.ingress.kubernetes.io/rate-limit-window: "1m"
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    nginx.ingress.kubernetes.io/proxy-body-size: "50m"
spec:
  tls:
  - hosts:
    - api.researchplatform.com
    secretName: research-platform-tls
  rules:
  - host: api.researchplatform.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: research-platform-api
            port:
              number: 8000
```

#### Horizontal Pod Autoscaler

```yaml
# k8s/hpa.yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: research-platform-api-hpa
  namespace: research-platform
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: research-platform-api
  minReplicas: 3
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: 80
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 60
      policies:
      - type: Percent
        value: 100
        periodSeconds: 15
    scaleDown:
      stabilizationWindowSeconds: 300
      policies:
      - type: Percent
        value: 10
        periodSeconds: 60
```

### 3. AWS ECS Deployment

#### Task Definition

```json
{
  "family": "research-platform-api",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "1024",
  "memory": "2048",
  "executionRoleArn": "arn:aws:iam::ACCOUNT:role/ecsTaskExecutionRole",
  "taskRoleArn": "arn:aws:iam::ACCOUNT:role/researchPlatformTaskRole",
  "containerDefinitions": [
    {
      "name": "api",
      "image": "research-platform/api:latest",
      "portMappings": [
        {
          "containerPort": 8000,
          "protocol": "tcp"
        }
      ],
      "environment": [
        {
          "name": "ENVIRONMENT",
          "value": "production"
        }
      ],
      "secrets": [
        {
          "name": "DATABASE_URL",
          "valueFrom": "arn:aws:secretsmanager:region:account:secret:research-platform/database-url"
        },
        {
          "name": "GEMINI_API_KEY",
          "valueFrom": "arn:aws:secretsmanager:region:account:secret:research-platform/gemini-key"
        }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/research-platform-api",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "ecs"
        }
      },
      "healthCheck": {
        "command": ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"],
        "interval": 30,
        "timeout": 5,
        "retries": 3,
        "startPeriod": 60
      }
    }
  ]
}
```

#### Service Definition

```json
{
  "serviceName": "research-platform-api",
  "cluster": "research-platform-cluster",
  "taskDefinition": "research-platform-api:1",
  "desiredCount": 3,
  "launchType": "FARGATE",
  "networkConfiguration": {
    "awsvpcConfiguration": {
      "subnets": [
        "subnet-12345678",
        "subnet-87654321"
      ],
      "securityGroups": [
        "sg-12345678"
      ],
      "assignPublicIp": "DISABLED"
    }
  },
  "loadBalancers": [
    {
      "targetGroupArn": "arn:aws:elasticloadbalancing:region:account:targetgroup/research-platform-api",
      "containerName": "api",
      "containerPort": 8000
    }
  ],
  "serviceRegistries": [
    {
      "registryArn": "arn:aws:servicediscovery:region:account:service/srv-research-api"
    }
  ]
}
```

## Environment Management

### Environment Configuration

#### Development

```bash
# .env.development
ENVIRONMENT=development
DEBUG=true
LOG_LEVEL=DEBUG
DATABASE_URL=postgresql+asyncpg://research:research123@localhost:5432/research_db
REDIS_URL=redis://localhost:6379/0
TEMPORAL_HOST=localhost:7233
```

#### Staging

```bash
# .env.staging
ENVIRONMENT=staging
DEBUG=false
LOG_LEVEL=INFO
DATABASE_URL=postgresql+asyncpg://research:password@staging-db.example.com:5432/research_staging_db
REDIS_URL=redis://staging-redis.example.com:6379/0
TEMPORAL_HOST=staging-temporal.example.com:7233
```

#### Production

```bash
# .env.production
ENVIRONMENT=production
DEBUG=false
LOG_LEVEL=WARNING
DATABASE_URL=postgresql+asyncpg://research:secure_password@prod-db.example.com:5432/research_prod_db
REDIS_URL=redis://prod-redis.example.com:6379/0
TEMPORAL_HOST=prod-temporal.example.com:7233
```

### Infrastructure as Code

#### Terraform Configuration

```hcl
# infrastructure/main.tf
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# VPC and Networking
module "vpc" {
  source = "./modules/vpc"
  
  cidr_block = "10.0.0.0/16"
  availability_zones = var.availability_zones
  
  tags = {
    Project     = "research-platform"
    Environment = var.environment
  }
}

# RDS Database
module "database" {
  source = "./modules/rds"
  
  vpc_id             = module.vpc.vpc_id
  subnet_ids         = module.vpc.private_subnet_ids
  security_group_ids = [module.security_groups.database_sg_id]
  
  engine_version     = "15.3"
  instance_class     = var.db_instance_class
  allocated_storage  = var.db_allocated_storage
  
  database_name = "research_db"
  username      = "research"
  
  backup_retention_period = 7
  backup_window          = "03:00-04:00"
  maintenance_window     = "sun:04:00-sun:05:00"
  
  tags = {
    Project     = "research-platform"
    Environment = var.environment
  }
}

# ElastiCache Redis
module "redis" {
  source = "./modules/elasticache"
  
  vpc_id             = module.vpc.vpc_id
  subnet_ids         = module.vpc.private_subnet_ids
  security_group_ids = [module.security_groups.redis_sg_id]
  
  node_type          = var.redis_node_type
  num_cache_nodes    = var.redis_num_nodes
  parameter_group    = "default.redis7"
  
  tags = {
    Project     = "research-platform"
    Environment = var.environment
  }
}

# ECS Cluster
module "ecs" {
  source = "./modules/ecs"
  
  cluster_name = "research-platform-${var.environment}"
  
  tags = {
    Project     = "research-platform"
    Environment = var.environment
  }
}

# Application Load Balancer
module "alb" {
  source = "./modules/alb"
  
  vpc_id             = module.vpc.vpc_id
  subnet_ids         = module.vpc.public_subnet_ids
  security_group_ids = [module.security_groups.alb_sg_id]
  
  certificate_arn = var.ssl_certificate_arn
  
  tags = {
    Project     = "research-platform"
    Environment = var.environment
  }
}
```

#### Kubernetes Helm Chart

```yaml
# helm/research-platform/Chart.yaml
apiVersion: v2
name: research-platform
description: Multi-Agent Research Platform
type: application
version: 1.0.0
appVersion: "1.0.0"

# helm/research-platform/values.yaml
replicaCount:
  api: 3
  worker: 3

image:
  repository: research-platform
  tag: latest
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 8000

ingress:
  enabled: true
  className: "nginx"
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/rate-limit: "100"
  hosts:
    - host: api.researchplatform.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: research-platform-tls
      hosts:
        - api.researchplatform.com

resources:
  api:
    limits:
      cpu: 1000m
      memory: 2Gi
    requests:
      cpu: 500m
      memory: 1Gi
  worker:
    limits:
      cpu: 2000m
      memory: 4Gi
    requests:
      cpu: 1000m
      memory: 2Gi

autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70
  targetMemoryUtilizationPercentage: 80

database:
  host: postgres.research-platform.svc.cluster.local
  port: 5432
  name: research_db

redis:
  host: redis.research-platform.svc.cluster.local
  port: 6379

temporal:
  host: temporal.research-platform.svc.cluster.local
  port: 7233
```

## Monitoring and Observability

### Metrics Collection

#### Prometheus Configuration

```yaml
# monitoring/prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - "alerts.yml"

alerting:
  alertmanagers:
    - static_configs:
        - targets:
          - alertmanager:9093

scrape_configs:
  # API Metrics
  - job_name: 'research-platform-api'
    static_configs:
      - targets: ['api:8000']
    metrics_path: '/metrics'
    scrape_interval: 15s

  # Worker Metrics
  - job_name: 'research-platform-worker'
    static_configs:
      - targets: ['worker:8000']
    metrics_path: '/metrics'
    scrape_interval: 15s

  # Database Metrics
  - job_name: 'postgres'
    static_configs:
      - targets: ['postgres-exporter:9187']

  # Redis Metrics
  - job_name: 'redis'
    static_configs:
      - targets: ['redis-exporter:9121']

  # System Metrics
  - job_name: 'node'
    static_configs:
      - targets: ['node-exporter:9100']
```

#### Application Metrics

```python
# src/monitoring/metrics.py
from prometheus_client import Counter, Histogram, Gauge, Info

# API Metrics
api_requests_total = Counter(
    'api_requests_total',
    'Total API requests',
    ['method', 'endpoint', 'status_code']
)

api_request_duration = Histogram(
    'api_request_duration_seconds',
    'API request duration',
    ['method', 'endpoint']
)

api_active_connections = Gauge(
    'api_active_connections',
    'Active API connections'
)

# Research Project Metrics
research_projects_total = Counter(
    'research_projects_total',
    'Total research projects created',
    ['user_id', 'status']
)

research_project_duration = Histogram(
    'research_project_duration_seconds',
    'Research project completion time',
    ['depth_level']
)

research_project_quality = Histogram(
    'research_project_quality_score',
    'Research project quality scores'
)

# Agent Metrics
agent_executions_total = Counter(
    'agent_executions_total',
    'Total agent executions',
    ['agent_type', 'status']
)

agent_execution_duration = Histogram(
    'agent_execution_duration_seconds',
    'Agent execution duration',
    ['agent_type']
)

agent_confidence_scores = Histogram(
    'agent_confidence_scores',
    'Agent confidence scores',
    ['agent_type']
)

# Temporal Metrics
temporal_workflow_executions = Counter(
    'temporal_workflow_executions_total',
    'Total Temporal workflow executions',
    ['workflow_type', 'status']
)

temporal_activity_executions = Counter(
    'temporal_activity_executions_total',
    'Total Temporal activity executions',
    ['activity_name', 'status']
)

# System Info
app_info = Info(
    'research_platform_app',
    'Research Platform application info'
)

# Initialize app info
app_info.info({
    'version': '1.0.0',
    'environment': 'production',
    'build_date': '2024-01-01'
})
```

#### Metrics Middleware

```python
# src/middleware/metrics_middleware.py
import time
from fastapi import Request, Response
from src.monitoring.metrics import api_requests_total, api_request_duration

async def metrics_middleware(request: Request, call_next):
    """Middleware to collect API metrics."""
    
    start_time = time.time()
    
    # Extract route information
    method = request.method
    path = request.url.path
    
    # Process request
    response = await call_next(request)
    
    # Calculate duration
    duration = time.time() - start_time
    
    # Record metrics
    api_requests_total.labels(
        method=method,
        endpoint=path,
        status_code=response.status_code
    ).inc()
    
    api_request_duration.labels(
        method=method,
        endpoint=path
    ).observe(duration)
    
    # Add metrics to response headers
    response.headers["X-Response-Time"] = str(duration)
    
    return response
```

### Grafana Dashboards

#### Research Platform Overview Dashboard

```json
{
  "dashboard": {
    "title": "Research Platform Overview",
    "panels": [
      {
        "title": "API Request Rate",
        "type": "graph",
        "targets": [
          {
            "expr": "rate(api_requests_total[5m])",
            "legendFormat": "{{method}} {{endpoint}}"
          }
        ]
      },
      {
        "title": "API Response Times",
        "type": "graph",
        "targets": [
          {
            "expr": "histogram_quantile(0.95, rate(api_request_duration_seconds_bucket[5m]))",
            "legendFormat": "95th percentile"
          },
          {
            "expr": "histogram_quantile(0.50, rate(api_request_duration_seconds_bucket[5m]))",
            "legendFormat": "50th percentile"
          }
        ]
      },
      {
        "title": "Active Research Projects",
        "type": "stat",
        "targets": [
          {
            "expr": "sum(research_projects_active)",
            "legendFormat": "Active Projects"
          }
        ]
      },
      {
        "title": "Agent Success Rate",
        "type": "graph",
        "targets": [
          {
            "expr": "rate(agent_executions_total{status=\"success\"}[5m]) / rate(agent_executions_total[5m])",
            "legendFormat": "{{agent_type}}"
          }
        ]
      }
    ]
  }
}
```

### Alerting

#### Alert Rules

```yaml
# monitoring/alerts.yml
groups:
  - name: research_platform_alerts
    rules:
      # High Error Rate
      - alert: HighErrorRate
        expr: rate(api_requests_total{status_code=~"5.."}[5m]) > 0.1
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "High error rate detected"
          description: "API error rate is {{ $value }} requests/second"

      # High Response Time
      - alert: HighResponseTime
        expr: histogram_quantile(0.95, rate(api_request_duration_seconds_bucket[5m])) > 2
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High API response time"
          description: "95th percentile response time is {{ $value }} seconds"

      # Database Connection Issues
      - alert: DatabaseConnectionFailure
        expr: up{job="postgres"} == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Database connection failure"
          description: "PostgreSQL database is down"

      # Redis Connection Issues
      - alert: RedisConnectionFailure
        expr: up{job="redis"} == 0
        for: 1m
        labels:
          severity: warning
        annotations:
          summary: "Redis connection failure"
          description: "Redis cache is down"

      # Temporal Workflow Failures
      - alert: HighWorkflowFailureRate
        expr: rate(temporal_workflow_executions{status="failed"}[10m]) > 0.05
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High workflow failure rate"
          description: "Temporal workflow failure rate is {{ $value }} failures/second"

      # Memory Usage
      - alert: HighMemoryUsage
        expr: (node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes) / node_memory_MemTotal_bytes > 0.9
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "High memory usage"
          description: "Memory usage is {{ $value | humanizePercentage }}"

      # Disk Space
      - alert: LowDiskSpace
        expr: (node_filesystem_size_bytes - node_filesystem_free_bytes) / node_filesystem_size_bytes > 0.85
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Low disk space"
          description: "Disk usage is {{ $value | humanizePercentage }} on {{ $labels.mountpoint }}"
```

#### AlertManager Configuration

```yaml
# monitoring/alertmanager.yml
global:
  smtp_smarthost: 'smtp.gmail.com:587'
  smtp_from: 'alerts@researchplatform.com'

route:
  group_by: ['alertname']
  group_wait: 10s
  group_interval: 10s
  repeat_interval: 1h
  receiver: 'web.hook'
  routes:
  - match:
      severity: critical
    receiver: 'critical-alerts'
  - match:
      severity: warning
    receiver: 'warning-alerts'

receivers:
- name: 'web.hook'
  webhook_configs:
  - url: 'http://localhost:5001/'

- name: 'critical-alerts'
  email_configs:
  - to: 'oncall@researchplatform.com'
    subject: 'CRITICAL: {{ .GroupLabels.alertname }}'
    body: |
      {{ range .Alerts }}
      Alert: {{ .Annotations.summary }}
      Description: {{ .Annotations.description }}
      {{ end }}
  slack_configs:
  - api_url: 'YOUR_SLACK_WEBHOOK_URL'
    channel: '#alerts-critical'
    title: 'Critical Alert'
    text: '{{ range .Alerts }}{{ .Annotations.summary }}{{ end }}'

- name: 'warning-alerts'
  email_configs:
  - to: 'team@researchplatform.com'
    subject: 'WARNING: {{ .GroupLabels.alertname }}'
    body: |
      {{ range .Alerts }}
      Alert: {{ .Annotations.summary }}
      Description: {{ .Annotations.description }}
      {{ end }}
```

### Logging

#### Structured Logging Configuration

```python
# src/core/logging.py
import structlog
import logging
from src.core.config import settings

def configure_logging():
    """Configure structured logging."""
    
    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.LOG_LEVEL.upper())
    )
    
    # Configure structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer() if settings.ENVIRONMENT == "production" 
            else structlog.dev.ConsoleRenderer(colors=True)
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

# Usage throughout the application
logger = structlog.get_logger(__name__)

# Contextual logging
logger.info(
    "Processing research project",
    project_id="proj-123",
    user_id="user-456",
    agent_type="literature_review",
    execution_time=1.23
)
```

#### Log Aggregation with ELK Stack

```yaml
# monitoring/elasticsearch.yml
version: '3.8'
services:
  elasticsearch:
    image: docker.elastic.co/elasticsearch/elasticsearch:8.8.0
    environment:
      - discovery.type=single-node
      - xpack.security.enabled=false
    volumes:
      - elasticsearch_data:/usr/share/elasticsearch/data
    ports:
      - "9200:9200"

  logstash:
    image: docker.elastic.co/logstash/logstash:8.8.0
    volumes:
      - ./logstash/pipeline:/usr/share/logstash/pipeline
    ports:
      - "5000:5000"
    depends_on:
      - elasticsearch

  kibana:
    image: docker.elastic.co/kibana/kibana:8.8.0
    environment:
      - ELASTICSEARCH_HOSTS=http://elasticsearch:9200
    ports:
      - "5601:5601"
    depends_on:
      - elasticsearch

volumes:
  elasticsearch_data:
```

### Health Checks

#### Application Health Checks

```python
# src/api/routes/health.py
from fastapi import APIRouter, Depends, HTTPException
from src.dependencies import get_database_session, get_redis_client

router = APIRouter()

@router.get("/health")
async def health_check():
    """Basic health check."""
    return {"status": "healthy", "service": "research-platform-api"}

@router.get("/ready")
async def readiness_check(
    db_session = Depends(get_database_session),
    redis_client = Depends(get_redis_client)
):
    """Comprehensive readiness check."""
    
    checks = {
        "database": "unknown",
        "redis": "unknown",
        "temporal": "unknown"
    }
    
    # Database check
    try:
        await db_session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {str(e)}"
    
    # Redis check
    try:
        await redis_client.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {str(e)}"
    
    # Temporal check
    try:
        from src.temporal.client import temporal_client
        await temporal_client.service_client.health()
        checks["temporal"] = "ok"
    except Exception as e:
        checks["temporal"] = f"error: {str(e)}"
    
    # Determine overall status
    all_healthy = all(status == "ok" for status in checks.values())
    status_code = 200 if all_healthy else 503
    
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if all_healthy else "not ready",
            "checks": checks
        }
    )

@router.get("/live")
async def liveness_check():
    """Liveness check for container orchestration."""
    return {"status": "alive"}
```

## Security

### SSL/TLS Configuration

#### Certificate Management with Cert-Manager

```yaml
# k8s/cert-manager.yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: admin@researchplatform.com
    privateKeySecretRef:
      name: letsencrypt-prod
    solvers:
    - http01:
        ingress:
          class: nginx
```

### Network Security

#### Security Groups (AWS)

```hcl
# infrastructure/modules/security_groups/main.tf
resource "aws_security_group" "alb" {
  name        = "research-platform-alb-${var.environment}"
  description = "ALB security group"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "api" {
  name        = "research-platform-api-${var.environment}"
  description = "API security group"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "database" {
  name        = "research-platform-db-${var.environment}"
  description = "Database security group"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.api.id]
  }
}
```

## Backup and Disaster Recovery

### Database Backups

#### Automated Backups

```bash
#!/bin/bash
# scripts/backup-database.sh

set -e

BACKUP_DIR="/backups/postgres"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="research_db_backup_${TIMESTAMP}.sql"

# Create backup
pg_dump ${DATABASE_URL} > "${BACKUP_DIR}/${BACKUP_FILE}"

# Compress backup
gzip "${BACKUP_DIR}/${BACKUP_FILE}"

# Upload to S3
aws s3 cp "${BACKUP_DIR}/${BACKUP_FILE}.gz" \
    "s3://research-platform-backups/database/${BACKUP_FILE}.gz"

# Clean up old local backups (keep last 7 days)
find ${BACKUP_DIR} -name "*.gz" -mtime +7 -delete

# Clean up old S3 backups (keep last 30 days)
aws s3 ls s3://research-platform-backups/database/ \
    --query 'Contents[?LastModified<=`'$(date -d '30 days ago' --iso-8601)'`].[Key]' \
    --output text | xargs -I {} aws s3 rm s3://research-platform-backups/database/{}

echo "Database backup completed: ${BACKUP_FILE}.gz"
```

#### Backup Monitoring

```python
# scripts/backup-monitor.py
import boto3
import datetime
from src.monitoring.metrics import backup_success, backup_duration

def monitor_backup_status():
    """Monitor backup status and update metrics."""
    
    s3 = boto3.client('s3')
    bucket = 'research-platform-backups'
    
    # Check for today's backup
    today = datetime.datetime.now().strftime('%Y%m%d')
    
    try:
        response = s3.list_objects_v2(
            Bucket=bucket,
            Prefix=f'database/research_db_backup_{today}'
        )
        
        if 'Contents' in response:
            backup_success.labels(type='database').set(1)
            latest_backup = max(response['Contents'], key=lambda x: x['LastModified'])
            
            # Calculate backup age
            backup_age = datetime.datetime.now(tz=datetime.timezone.utc) - latest_backup['LastModified']
            backup_duration.labels(type='database').set(backup_age.total_seconds())
        else:
            backup_success.labels(type='database').set(0)
            
    except Exception as e:
        backup_success.labels(type='database').set(0)
        print(f"Backup monitoring failed: {e}")
```

### Application State Backups

#### Temporal Workflow State

```bash
#!/bin/bash
# scripts/backup-temporal.sh

# Export workflow definitions
tctl --namespace research-platform workflow list --query 'WorkflowType="ResearchWorkflow"' \
    --output json > temporal_workflows_backup.json

# Upload to S3
aws s3 cp temporal_workflows_backup.json \
    s3://research-platform-backups/temporal/workflows_$(date +%Y%m%d).json
```

## Performance Optimization

### Caching Strategy

#### Redis Caching

```python
# src/services/cache_service.py
import json
import hashlib
from typing import Any, Optional
from redis.asyncio import Redis

class CacheService:
    """Redis-based caching service."""
    
    def __init__(self, redis_client: Redis):
        self.redis = redis_client
        self.default_ttl = 3600  # 1 hour
    
    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        try:
            value = await self.redis.get(key)
            if value:
                return json.loads(value)
        except Exception as e:
            logger.warning(f"Cache get failed: {e}")
        return None
    
    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Set value in cache."""
        try:
            serialized = json.dumps(value, default=str)
            await self.redis.setex(
                key, 
                ttl or self.default_ttl, 
                serialized
            )
            return True
        except Exception as e:
            logger.warning(f"Cache set failed: {e}")
            return False
    
    async def delete(self, key: str) -> bool:
        """Delete value from cache."""
        try:
            await self.redis.delete(key)
            return True
        except Exception as e:
            logger.warning(f"Cache delete failed: {e}")
            return False
    
    def generate_key(self, prefix: str, *args) -> str:
        """Generate cache key."""
        key_data = f"{prefix}:{'|'.join(str(arg) for arg in args)}"
        return hashlib.md5(key_data.encode()).hexdigest()
```

### Database Optimization

#### Connection Pooling

```python
# src/database/connection.py
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from src.core.config import settings

# Create engine with optimized pool settings
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=20,  # Number of connections to maintain
    max_overflow=30,  # Additional connections above pool_size
    pool_pre_ping=True,  # Validate connections before use
    pool_recycle=3600,  # Recycle connections after 1 hour
    echo=settings.DEBUG,  # Log SQL in debug mode
)

AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)
```

#### Query Optimization

```python
# src/repositories/optimized_queries.py
from sqlalchemy import select, func, and_
from sqlalchemy.orm import selectinload, joinedload

class OptimizedResearchRepository:
    """Repository with optimized queries."""
    
    async def get_projects_with_results(self, user_id: str) -> list[ResearchProject]:
        """Get projects with eager loading of related data."""
        
        query = (
            select(ResearchProject)
            .options(
                selectinload(ResearchProject.agent_tasks),
                joinedload(ResearchProject.generated_reports)
            )
            .where(
                and_(
                    ResearchProject.user_id == user_id,
                    ResearchProject.status == 'completed'
                )
            )
            .order_by(ResearchProject.created_at.desc())
        )
        
        result = await self.session.execute(query)
        return result.scalars().all()
    
    async def get_project_statistics(self) -> dict:
        """Get aggregated project statistics."""
        
        query = (
            select(
                func.count(ResearchProject.id).label('total_projects'),
                func.avg(ResearchProject.quality_score).label('avg_quality'),
                func.count(
                    case(
                        (ResearchProject.status == 'completed', 1),
                        else_=None
                    )
                ).label('completed_projects')
            )
        )
        
        result = await self.session.execute(query)
        return result.first()._asdict()
```

This comprehensive deployment and monitoring guide provides the foundation for successfully operating the Multi-Agent Research Platform in production environments with proper observability, security, and reliability practices.