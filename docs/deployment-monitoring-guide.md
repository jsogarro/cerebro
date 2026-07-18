# Deployment and Monitoring Guide

## Overview

This guide covers deployment strategies, infrastructure setup, monitoring, and operational procedures for **Cerebro** — a multi-agent LLM research platform whose current focus is financial research (US equities). Cerebro is a FastAPI application that routes natural-language queries through the in-process Multi-Agent System Router (MASR) to hierarchical domain supervisors.

Two identity notes matter for operators:

- **Product vs. infra naming.** The product is **Cerebro**, but the deployment artifacts still carry the pre-rebrand **`research-platform`** identity: the FastAPI title is `Research Platform API`, the Kubernetes namespace is `research-platform`, images are published under `gcr.io/PROJECT_ID/research-platform-api`, the database is `research_db`, and the CLI is `research-cli`. These infra names are accurate and are preserved verbatim in the manifests below. Do not rename them.
- **No worker tier.** Query execution runs **in-process** via `DirectExecutionService` (`src/api/services/direct_execution_service.py`), an asyncio engine that spawns a background task per request. It replaced Temporal, which has been removed from the codebase. There is no separate worker deployment, no workflow engine, and no `temporalio` dependency. Any leftover `TEMPORAL_HOST` / `TEMPORAL_NAMESPACE` settings are dead vestiges and are not required to run the service.

## Architecture Overview

### Production Architecture

```mermaid
graph TB
    subgraph "Edge"
        LB[Load Balancer]
        CDN[CDN]
    end

    subgraph "Application Layer"
        API1[API Instance 1]
        API2[API Instance 2]
        API3[API Instance 3]
    end

    subgraph "Data Layer"
        PG[(PostgreSQL)]
        REDIS[(Redis)]
    end

    subgraph "External Services"
        GEMINI[Google Gemini API]
        MCP[MCP Tool Servers]
        STORAGE[Object Storage]
    end

    subgraph "Monitoring"
        PROMETHEUS[Prometheus]
        GRAFANA[Grafana]
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

    API1 --> GEMINI
    API2 --> GEMINI
    API3 --> GEMINI

    API1 --> MCP
    API2 --> MCP
    API3 --> MCP

    API1 --> PROMETHEUS
    API2 --> PROMETHEUS
    API3 --> PROMETHEUS

    PROMETHEUS --> GRAFANA
    PROMETHEUS --> ALERTS
```

Each API instance runs `DirectExecutionService` internally, so scaling the API tier scales execution capacity — there is no separate pool of workers to scale independently.

## Deployment Strategies

### 1. Docker Deployment

#### Production Docker Compose

There is a single service tier: the API. The repo's `docker-compose.production.yml` still defines a build-only `worker` service (a Temporal-era vestige) with no `image:` key, but it cannot be built — its `docker/Dockerfile.worker` does not exist in the repo — and there is no Temporal server. The Compose file below therefore omits it.

```yaml
# docker-compose.prod.yml
version: '3.8'

services:
  # API Services (run DirectExecutionService in-process)
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
      - GEMINI_API_KEY=${GEMINI_API_KEY}
    networks:
      - app-network
    depends_on:
      - postgres
      - redis
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

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
    image: postgres:16-alpine
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

        # Health Checks
        location /health {
            access_log off;
            proxy_pass http://api_backend;
        }
    }
}
```

### 2. Kubernetes Deployment

The k8s manifests deploy a single application workload: the API. (A `research-platform-worker` Deployment still exists in the repo's `k8s/` directory, but it is a Temporal-era vestige — it has no worker entrypoint module, no command override, and its liveness probe is just `python -c 'import sys; sys.exit(0)'`. Do not treat it as an execution tier; it is not documented here.)

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

> The repo's checked-in ConfigMap still sets `TEMPORAL_NAMESPACE`. That value is inert — nothing in `src/` reads it on the query path — so it is omitted here and can be dropped from your ConfigMap.

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
        image: gcr.io/PROJECT_ID/research-platform-api:latest
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

Because execution is in-process, the API HPA is the only autoscaler you need — scaling API replicas scales concurrent query execution.

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

Only three infrastructure dependencies are required: Postgres, Redis, and a Gemini API key. There is no Temporal endpoint to configure.

#### Development

```bash
# .env.development
ENVIRONMENT=development
DEBUG=true
LOG_LEVEL=DEBUG
DATABASE_URL=postgresql+asyncpg://research:research123@localhost:5432/research_db
REDIS_URL=redis://localhost:6379/0
```

#### Staging

```bash
# .env.staging
ENVIRONMENT=staging
DEBUG=false
LOG_LEVEL=INFO
DATABASE_URL=postgresql+asyncpg://research:password@staging-db.example.com:5432/research_staging_db
REDIS_URL=redis://staging-redis.example.com:6379/0
```

#### Production

```bash
# .env.production
ENVIRONMENT=production
DEBUG=false
LOG_LEVEL=WARNING
DATABASE_URL=postgresql+asyncpg://research:secure_password@prod-db.example.com:5432/research_prod_db
REDIS_URL=redis://prod-redis.example.com:6379/0
```

> `DEBUG` defaults to `false`; interactive `/docs` and `/redoc` are served only when `DEBUG=true`, so they are off in production.

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

  engine_version     = "16.3"
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
description: Cerebro multi-agent research platform
type: application
version: 1.0.0
appVersion: "1.0.0"

# helm/research-platform/values.yaml
replicaCount:
  api: 3

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
```

## Monitoring and Observability

### What the application actually exposes

The application ships a Prometheus scrape endpoint at **`/metrics`** (a Prometheus ASGI app mounted in `src/api/main.py`). The metrics themselves are defined in `src/core/observability.py` and are LLM-centric:

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `llm_call_duration_seconds` | Histogram | `model`, `provider` | LLM provider call latency in seconds |
| `llm_tokens_total` | Counter | `model`, `provider`, `type` | Total LLM tokens used (prompt/completion) |
| `llm_cost_usd_total` | Counter | `model`, `provider` | Total estimated LLM call cost in USD |
| `llm_request_cost_drift_ratio` | Histogram | `method`, `route` | Absolute ratio between MASR-estimated and actual per-request cost |
| `llm_cost_drift_events_total` | Counter | `method`, `route`, `direction` | Count of cost-drift events beyond the alert threshold |

`record_llm_call()` in `observability.py` both increments these Prometheus series and emits a structured log line via structlog. `LLMCostDriftMiddleware` (part of the request middleware stack) compares the MASR cost estimate against the actual provider cost and feeds the two drift metrics, warning when drift exceeds `0.2`.

Optional distributed tracing is available via **Langfuse**, opt-in behind `LANGFUSE_ENABLED` (default `false`). There is no built-in OpenTelemetry backbone, Grafana Loki, Jaeger, CloudWatch, or Sentry integration — provision those separately if you need them.

### Metrics Collection

#### Prometheus Configuration

The API is the only application target — there is no worker to scrape.

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
  # Cerebro API metrics (LLM observability from src/core/observability.py)
  - job_name: 'research-platform-api'
    static_configs:
      - targets: ['api:8000']
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

### Grafana Dashboards

Build panels from the metrics the app actually exports.

```json
{
  "dashboard": {
    "title": "Cerebro LLM Overview",
    "panels": [
      {
        "title": "LLM Call Rate by Provider",
        "type": "graph",
        "targets": [
          {
            "expr": "sum by (provider, model) (rate(llm_call_duration_seconds_count[5m]))",
            "legendFormat": "{{provider}} {{model}}"
          }
        ]
      },
      {
        "title": "LLM Call Latency (p95)",
        "type": "graph",
        "targets": [
          {
            "expr": "histogram_quantile(0.95, sum by (le, provider) (rate(llm_call_duration_seconds_bucket[5m])))",
            "legendFormat": "{{provider}} p95"
          }
        ]
      },
      {
        "title": "Cumulative LLM Cost (USD)",
        "type": "stat",
        "targets": [
          {
            "expr": "sum(llm_cost_usd_total)",
            "legendFormat": "Total cost"
          }
        ]
      },
      {
        "title": "Cost Drift Events",
        "type": "graph",
        "targets": [
          {
            "expr": "sum by (direction) (rate(llm_cost_drift_events_total[10m]))",
            "legendFormat": "{{direction}}"
          }
        ]
      }
    ]
  }
}
```

### Alerting

Alert on the metrics the app exports plus your infrastructure exporters. (There is no workflow-failure alert because there is no workflow engine.)

```yaml
# monitoring/alerts.yml
groups:
  - name: cerebro_alerts
    rules:
      # Slow LLM Calls
      - alert: HighLLMLatency
        expr: histogram_quantile(0.95, sum by (le, provider) (rate(llm_call_duration_seconds_bucket[5m]))) > 30
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High LLM provider latency"
          description: "95th percentile LLM call latency for {{ $labels.provider }} is {{ $value }} seconds"

      # Sustained Cost Drift
      - alert: HighCostDrift
        expr: sum(rate(llm_cost_drift_events_total[10m])) > 0.05
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "MASR cost estimates drifting from actuals"
          description: "Cost drift event rate is {{ $value }} events/second"

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

Cerebro uses `structlog` throughout — modules obtain a logger via `structlog.get_logger()` (for example in `src/core/observability.py`, `src/api/middleware/`, `src/auth/`, and `src/security/`). Note one important gap, however: the codebase **never calls `structlog.configure()`** — there is no central logging-setup module. As a result structlog falls back to its **default console renderer in every environment, including production**, so production logs are human-readable console lines, not JSON.

If you want JSON output for log aggregation, you must add a startup configuration step that selects `structlog.processors.JSONRenderer` in production. The block below is a **template you would need to add** — it does **not** exist in the repo today (there is no `src/core/logging.py`).

```python
# Example logging-setup module you could add (NOT present in the repo)
import sys
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
    "Processing research query",
    project_id="proj-123",
    user_id="user-456",
    agent_type="literature_review",
    execution_time=1.23
)
```

#### Log Aggregation with ELK Stack

If you want centralized log search, ship the logs to an ELK (or equivalent) stack. This is an optional add-on, not a built-in dependency. Note the caveat above: until you add JSON log configuration, structlog emits console-formatted lines rather than JSON, so wire up `JSONRenderer` first if your pipeline expects structured JSON.

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

The application exposes three health endpoints from `src/api/routes/health.py`:

- **`GET /health`** — basic liveness for load balancers; returns `{"status": "healthy", "service": "research-platform-api"}`.
- **`GET /ready`** — Kubernetes readiness.
- **`GET /live`** — Kubernetes liveness; returns `{"status": "alive"}`.

**Important operational caveat:** `/ready` currently returns a **static, always-`ok` payload** — its dependency checks are not yet implemented (they are `TODO`s in the source). It does not actually probe Postgres or Redis, and the `"temporal": "ok"` entry is a leftover string, not evidence that any Temporal service exists. Treat `/ready` as "the process is up and serving", not as a real dependency check. If you need genuine readiness gating, implement the checks before relying on them.

```python
# src/api/routes/health.py (as shipped)
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

router = APIRouter()

@router.get("/health", status_code=status.HTTP_200_OK)
async def health_check() -> JSONResponse:
    """Basic health check endpoint."""
    return JSONResponse(
        content={"status": "healthy", "service": "research-platform-api"}
    )

@router.get("/ready", status_code=status.HTTP_200_OK)
async def readiness_check() -> JSONResponse:
    """Readiness check endpoint for Kubernetes."""
    # TODO: Check database connection
    # TODO: Check Redis connection
    # TODO: Check Temporal connection

    return JSONResponse(
        content={
            "status": "ready",
            "service": "research-platform-api",
            "checks": {
                "database": "ok",
                "redis": "ok",
                "temporal": "ok",
            },
        }
    )

@router.get("/live", status_code=status.HTTP_200_OK)
async def liveness_check() -> JSONResponse:
    """Liveness check endpoint for Kubernetes."""
    return JSONResponse(content={"status": "alive"})
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

### Application Auth & Rate Limiting

- **JWT:** RS256, 15-minute access tokens / 7-day refresh tokens, keys mounted at `/secrets/jwt_private.pem` and `/secrets/jwt_public.pem`, bcrypt with 12 rounds, `PASSWORD_MIN_LENGTH=12`. Authentication is enforced **per-endpoint** via FastAPI `Depends`, not by a global middleware — the `AuthMiddleware` in the stack is a no-op. As a result `/api/v1/query`, `/api/v1/agents`, and `/api/v1/masr` are effectively unauthenticated; only the auth, GDPR, and parts of the research/reports routers require a token. Put an authenticating gateway in front if you need blanket protection.
- **Rate limiting:** a single global limiter, `MAX_REQUESTS_PER_MINUTE=100` with `ENABLE_RATE_LIMITING=True`. There are no tiers, no burst config, and no per-endpoint overrides at the application layer — use the Nginx/Ingress rate limits above for finer control.
- **Middleware order (outermost first at request time):** Auth (no-op) → LLMCostDrift → RateLimit → Idempotency → CORS → application.

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

All durable state lives in Postgres and Redis. There is no separate workflow-engine state to back up — execution state is held in-memory by `DirectExecutionService` (with optional checkpoints persisted to the `workflow_checkpoints` table in Postgres), so a Postgres backup captures it.

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
import datetime
import boto3

def check_todays_backup() -> bool:
    """Return True if a database backup exists for today."""
    s3 = boto3.client('s3')
    bucket = 'research-platform-backups'
    today = datetime.datetime.now().strftime('%Y%m%d')

    try:
        response = s3.list_objects_v2(
            Bucket=bucket,
            Prefix=f'database/research_db_backup_{today}'
        )
        return 'Contents' in response
    except Exception as e:
        print(f"Backup monitoring failed: {e}")
        return False


if __name__ == "__main__":
    ok = check_todays_backup()
    print("backup_present" if ok else "backup_missing")
```

Wire the exit status of this check into whatever your platform uses for scheduled-job alerting (for example, a CloudWatch alarm on the job's exit code, or a Prometheus Pushgateway push if you run it as a cron job).

## Performance Optimization

### Caching Strategy

#### Redis Caching

The actual cache layer lives in the **`src/services/cache/`** package: `CacheManager` (`cache_manager.py`) plus the pluggable strategies in `cache_strategies.py` (`TTLStrategy`, `LRUStrategy`, `DependencyStrategy`, `HybridStrategy`, `VersionedCacheStrategy`). The snippet below is an illustrative sketch of the get/set/delete pattern, not a verbatim copy of that module.

```python
# Illustrative Redis cache wrapper (see src/services/cache/ for the real CacheManager)
import json
import hashlib
from typing import Any, Optional
from redis.asyncio import Redis

class CacheService:
    """Redis-based caching service (illustrative)."""

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

The async SQLAlchemy engine and session factory live in **`src/models/db/session.py`** (`create_async_engine` + `async_sessionmaker`), reading the URL from `settings.DATABASE_URL`. Data access is organized under the repository pattern in **`src/repositories/`** (`BaseRepository[ModelType]` plus `ResearchRepository`, `ResultRepository`, `TaskRepository`, `CheckpointRepository`, `ReportRepository`, `UserRepository`, `APIKeyRepository`).

#### Connection Pooling

Tune pool settings on the engine created in `src/models/db/session.py`:

```python
# src/models/db/session.py (pool tuning)
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from src.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=20,        # connections to maintain
    max_overflow=30,     # additional connections above pool_size
    pool_pre_ping=True,  # validate connections before use
    pool_recycle=3600,   # recycle connections after 1 hour
    echo=settings.DEBUG, # log SQL in debug mode
)

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
```

#### Query Optimization

Keep read-heavy queries inside the repository layer and use eager loading to avoid N+1 access. The following is an illustrative extension of `ResearchRepository` in `src/repositories/`:

```python
# Illustrative eager-loading query within src/repositories/
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload, joinedload

async def get_projects_with_results(self, user_id: str) -> list[ResearchProject]:
    """Get projects with eager loading of related data."""
    query = (
        select(ResearchProject)
        .options(
            selectinload(ResearchProject.agent_tasks),
            joinedload(ResearchProject.generated_reports),
        )
        .where(
            and_(
                ResearchProject.user_id == user_id,
                ResearchProject.status == 'completed',
            )
        )
        .order_by(ResearchProject.created_at.desc())
    )
    result = await self.session.execute(query)
    return result.scalars().all()
```

---

This guide reflects Cerebro's actual deployment shape: a single stateless API tier running in-process asyncio execution (`DirectExecutionService`), backed by Postgres and Redis, observed through the LLM-centric Prometheus metrics in `src/core/observability.py`. There is no worker tier and no workflow engine to operate.
