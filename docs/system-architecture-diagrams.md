# System Architecture Diagrams

This document contains comprehensive architectural diagrams for the Multi-Agent Research Platform using Mermaid syntax.

## Table of Contents
- [High-Level System Architecture](#high-level-system-architecture)
- [Multi-Agent Architecture](#multi-agent-architecture)
- [Data Flow Architecture](#data-flow-architecture)
- [API Layer Architecture](#api-layer-architecture)
- [Database Schema](#database-schema)
- [Temporal Workflow Architecture](#temporal-workflow-architecture)
- [Deployment Architecture](#deployment-architecture)

## High-Level System Architecture

```mermaid
graph TB
    subgraph "Client Layer"
        CLI[Research CLI]
        WEB[Web Dashboard]
        API_CLIENT[API Clients]
    end
    
    subgraph "API Gateway"
        FASTAPI[FastAPI Server]
        WS[WebSocket Server]
        AUTH[Authentication]
        RATE[Rate Limiter]
    end
    
    subgraph "Orchestration Layer"
        TEMPORAL[Temporal Server]
        LANGGRAPH[LangGraph Engine]
        QUEUE[Task Queue]
    end
    
    subgraph "Agent Layer"
        LIT[Literature Review Agent]
        COMP[Comparative Analysis Agent]
        METH[Methodology Agent]
        SYNTH[Synthesis Agent]
        CIT[Citation Agent]
    end
    
    subgraph "AI Services"
        GEMINI[Google Gemini API]
        EMBED[Embedding Service]
        NLP[NLP Processor]
    end
    
    subgraph "Data Layer"
        PG[(PostgreSQL)]
        REDIS[(Redis Cache)]
        VECTOR[(Vector DB)]
        S3[S3 Storage]
    end
    
    subgraph "External Services"
        SCHOLAR[Google Scholar]
        PUBMED[PubMed]
        ARXIV[arXiv]
        CROSSREF[CrossRef]
    end
    
    subgraph "Monitoring"
        PROM[Prometheus]
        GRAF[Grafana]
        OTEL[OpenTelemetry]
        LOGS[Log Aggregator]
    end
    
    CLI --> FASTAPI
    WEB --> FASTAPI
    API_CLIENT --> FASTAPI
    
    FASTAPI --> AUTH
    AUTH --> RATE
    RATE --> TEMPORAL
    
    TEMPORAL --> LANGGRAPH
    LANGGRAPH --> LIT
    LANGGRAPH --> COMP
    LANGGRAPH --> METH
    LANGGRAPH --> SYNTH
    LANGGRAPH --> CIT
    
    LIT --> GEMINI
    COMP --> GEMINI
    METH --> GEMINI
    SYNTH --> GEMINI
    CIT --> GEMINI
    
    GEMINI --> EMBED
    EMBED --> VECTOR
    
    TEMPORAL --> PG
    LANGGRAPH --> PG
    FASTAPI --> PG
    FASTAPI --> REDIS
    
    LIT --> SCHOLAR
    LIT --> PUBMED
    LIT --> ARXIV
    CIT --> CROSSREF
    
    FASTAPI --> OTEL
    TEMPORAL --> OTEL
    OTEL --> PROM
    PROM --> GRAF
    OTEL --> LOGS
    
    WS -.-> FASTAPI
    FASTAPI --> S3
```

## Multi-Agent Architecture

```mermaid
graph TD
    subgraph "Agent Factory"
        FACTORY[AgentFactory]
        BASE[BaseAgent]
    end
    
    subgraph "Specialized Agents"
        LIT_AGENT[Literature Review Agent]
        COMP_AGENT[Comparative Analysis Agent]
        METH_AGENT[Methodology Agent]
        SYNTH_AGENT[Synthesis Agent]
        CIT_AGENT[Citation Agent]
    end
    
    subgraph "Agent Components"
        PROMPT[Prompt Manager]
        VALID[Result Validator]
        RETRY[Retry Handler]
        CACHE[Response Cache]
    end
    
    subgraph "Communication"
        MSG_BUS[Message Bus]
        STATE[Shared State]
        CONFLICT[Conflict Resolver]
    end
    
    FACTORY --> BASE
    BASE --> LIT_AGENT
    BASE --> COMP_AGENT
    BASE --> METH_AGENT
    BASE --> SYNTH_AGENT
    BASE --> CIT_AGENT
    
    LIT_AGENT --> PROMPT
    COMP_AGENT --> PROMPT
    METH_AGENT --> PROMPT
    SYNTH_AGENT --> PROMPT
    CIT_AGENT --> PROMPT
    
    LIT_AGENT --> VALID
    COMP_AGENT --> VALID
    METH_AGENT --> VALID
    SYNTH_AGENT --> VALID
    CIT_AGENT --> VALID
    
    PROMPT --> CACHE
    VALID --> RETRY
    
    LIT_AGENT --> MSG_BUS
    COMP_AGENT --> MSG_BUS
    METH_AGENT --> MSG_BUS
    SYNTH_AGENT --> MSG_BUS
    CIT_AGENT --> MSG_BUS
    
    MSG_BUS --> STATE
    STATE --> CONFLICT
```

## Data Flow Architecture

```mermaid
graph LR
    subgraph "Input"
        USER[User Request]
        PARAMS[Research Parameters]
    end
    
    subgraph "Processing Pipeline"
        VALIDATE[Validation]
        ENRICH[Enrichment]
        ROUTE[Routing]
        EXEC[Execution]
        AGGREGATE[Aggregation]
    end
    
    subgraph "Storage"
        TEMP[Temporary Storage]
        PERSIST[Persistent Storage]
        CACHE_STORE[Cache Storage]
    end
    
    subgraph "Output"
        REPORT[Report Generation]
        STREAM[Stream Updates]
        NOTIFY[Notifications]
    end
    
    USER --> VALIDATE
    PARAMS --> VALIDATE
    VALIDATE --> ENRICH
    ENRICH --> ROUTE
    ROUTE --> EXEC
    EXEC --> TEMP
    TEMP --> AGGREGATE
    AGGREGATE --> PERSIST
    EXEC --> CACHE_STORE
    PERSIST --> REPORT
    EXEC --> STREAM
    AGGREGATE --> NOTIFY
```

## API Layer Architecture

```mermaid
graph TB
    subgraph "API Endpoints"
        HEALTH[/health]
        PROJECTS[/api/v1/projects]
        TASKS[/api/v1/tasks]
        RESULTS[/api/v1/results]
        REPORTS[/api/v1/reports]
        USERS[/api/v1/users]
        WS_EP[/ws]
    end
    
    subgraph "Middleware Stack"
        CORS[CORS Middleware]
        AUTH_MW[Auth Middleware]
        RATE_MW[Rate Limit MW]
        LOG_MW[Logging MW]
        ERROR_MW[Error Handler MW]
    end
    
    subgraph "Business Logic"
        PROJ_SVC[Project Service]
        TASK_SVC[Task Service]
        RESULT_SVC[Result Service]
        REPORT_SVC[Report Service]
        USER_SVC[User Service]
    end
    
    subgraph "Repositories"
        PROJ_REPO[Project Repository]
        TASK_REPO[Task Repository]
        RESULT_REPO[Result Repository]
        REPORT_REPO[Report Repository]
        USER_REPO[User Repository]
    end
    
    PROJECTS --> CORS
    TASKS --> CORS
    RESULTS --> CORS
    REPORTS --> CORS
    USERS --> CORS
    WS_EP --> CORS
    
    CORS --> AUTH_MW
    AUTH_MW --> RATE_MW
    RATE_MW --> LOG_MW
    LOG_MW --> ERROR_MW
    
    ERROR_MW --> PROJ_SVC
    ERROR_MW --> TASK_SVC
    ERROR_MW --> RESULT_SVC
    ERROR_MW --> REPORT_SVC
    ERROR_MW --> USER_SVC
    
    PROJ_SVC --> PROJ_REPO
    TASK_SVC --> TASK_REPO
    RESULT_SVC --> RESULT_REPO
    REPORT_SVC --> REPORT_REPO
    USER_SVC --> USER_REPO
```

## Database Schema

```mermaid
erDiagram
    USER ||--o{ RESEARCH_PROJECT : creates
    USER ||--o{ API_KEY : has
    RESEARCH_PROJECT ||--o{ AGENT_TASK : contains
    RESEARCH_PROJECT ||--o{ RESEARCH_RESULT : produces
    RESEARCH_PROJECT ||--o{ GENERATED_REPORT : generates
    RESEARCH_PROJECT ||--o{ WORKFLOW_CHECKPOINT : checkpoints
    AGENT_TASK ||--o{ TASK_DEPENDENCY : depends_on
    RESEARCH_RESULT ||--o{ RESULT_METADATA : has
    
    USER {
        uuid id PK
        string email UK
        string username UK
        string password_hash
        boolean is_active
        timestamp created_at
        timestamp last_login
        json preferences
        int request_count
        int monthly_limit
    }
    
    API_KEY {
        uuid id PK
        uuid user_id FK
        string key_hash UK
        string name
        timestamp expires_at
        timestamp last_used
        boolean is_active
    }
    
    RESEARCH_PROJECT {
        uuid id PK
        uuid user_id FK
        string title
        text research_query
        string status
        json domains
        json config
        timestamp created_at
        timestamp updated_at
        timestamp completed_at
    }
    
    AGENT_TASK {
        uuid id PK
        uuid project_id FK
        string agent_type
        string status
        json input_data
        json output_data
        text error_message
        int retry_count
        timestamp created_at
        timestamp execution_start
        timestamp execution_end
        json task_metadata
    }
    
    RESEARCH_RESULT {
        uuid id PK
        uuid project_id FK
        string result_type
        json content
        float confidence_score
        json metadata
        timestamp created_at
    }
    
    GENERATED_REPORT {
        uuid id PK
        uuid project_id FK
        string format
        text content
        string storage_path
        json metadata
        timestamp created_at
    }
    
    WORKFLOW_CHECKPOINT {
        uuid id PK
        uuid project_id FK
        string workflow_id
        json state
        string checkpoint_type
        timestamp created_at
    }
    
    TASK_DEPENDENCY {
        uuid id PK
        uuid task_id FK
        uuid depends_on_id FK
        string dependency_type
    }
    
    RESULT_METADATA {
        uuid id PK
        uuid result_id FK
        string key
        text value
        string data_type
    }
```

## Temporal Workflow Architecture

```mermaid
graph TD
    subgraph "Workflow Types"
        MAIN_WF[ResearchWorkflow]
        PARALLEL_WF[ParallelResearchWorkflow]
        RETRY_WF[RetryableWorkflow]
    end
    
    subgraph "Activities"
        LIT_ACT[Literature Review Activity]
        COMP_ACT[Comparative Analysis Activity]
        METH_ACT[Methodology Activity]
        SYNTH_ACT[Synthesis Activity]
        CIT_ACT[Citation Activity]
        REPORT_ACT[Report Generation Activity]
    end
    
    subgraph "Workflow Components"
        SIGNAL[Signal Handler]
        QUERY[Query Handler]
        TIMER[Timer/Cron]
        CHECKPOINT[Checkpointing]
        VERSION[Versioning]
    end
    
    subgraph "Worker Pool"
        WORKER1[Worker 1]
        WORKER2[Worker 2]
        WORKER3[Worker 3]
        WORKER_N[Worker N]
    end
    
    subgraph "Temporal Server"
        HISTORY[History Service]
        MATCHING[Matching Service]
        FRONTEND[Frontend Service]
        WORKER_SVC[Worker Service]
    end
    
    MAIN_WF --> LIT_ACT
    MAIN_WF --> COMP_ACT
    MAIN_WF --> METH_ACT
    MAIN_WF --> SYNTH_ACT
    MAIN_WF --> CIT_ACT
    MAIN_WF --> REPORT_ACT
    
    PARALLEL_WF --> LIT_ACT
    PARALLEL_WF --> COMP_ACT
    PARALLEL_WF --> METH_ACT
    
    MAIN_WF --> SIGNAL
    MAIN_WF --> QUERY
    MAIN_WF --> TIMER
    MAIN_WF --> CHECKPOINT
    MAIN_WF --> VERSION
    
    WORKER1 --> HISTORY
    WORKER2 --> HISTORY
    WORKER3 --> HISTORY
    WORKER_N --> HISTORY
    
    HISTORY --> MATCHING
    MATCHING --> FRONTEND
    FRONTEND --> WORKER_SVC
    WORKER_SVC --> WORKER1
    WORKER_SVC --> WORKER2
    WORKER_SVC --> WORKER3
    WORKER_SVC --> WORKER_N
```

## Deployment Architecture

### Development Environment
```mermaid
graph TB
    subgraph "Developer Machine"
        IDE[IDE/Editor]
        CLI_DEV[CLI Tool]
        DOCKER_DEV[Docker Desktop]
    end
    
    subgraph "Local Containers"
        API_LOCAL[API Container]
        PG_LOCAL[PostgreSQL Container]
        REDIS_LOCAL[Redis Container]
        TEMPORAL_LOCAL[Temporal Container]
    end
    
    subgraph "Development Tools"
        PGADMIN[pgAdmin]
        REDIS_CMD[Redis Commander]
        TEMPORAL_UI[Temporal UI]
        SWAGGER[Swagger UI]
    end
    
    IDE --> API_LOCAL
    CLI_DEV --> API_LOCAL
    API_LOCAL --> PG_LOCAL
    API_LOCAL --> REDIS_LOCAL
    API_LOCAL --> TEMPORAL_LOCAL
    
    PGADMIN --> PG_LOCAL
    REDIS_CMD --> REDIS_LOCAL
    TEMPORAL_UI --> TEMPORAL_LOCAL
    SWAGGER --> API_LOCAL
```

### Production Environment (Kubernetes)
```mermaid
graph TB
    subgraph "Internet"
        USERS[Users]
        CDN[CloudFlare CDN]
    end
    
    subgraph "Kubernetes Cluster"
        subgraph "Ingress"
            NGINX[NGINX Ingress]
            CERT[Cert Manager]
        end
        
        subgraph "Application Namespace"
            subgraph "API Deployment"
                API_POD1[API Pod 1]
                API_POD2[API Pod 2]
                API_POD3[API Pod 3]
            end
            
            subgraph "Worker Deployment"
                WORKER_POD1[Worker Pod 1]
                WORKER_POD2[Worker Pod 2]
            end
            
            API_SVC[API Service]
            WORKER_SVC[Worker Service]
        end
        
        subgraph "Temporal Namespace"
            TEMPORAL_SERVER[Temporal Server]
            TEMPORAL_WEB[Temporal Web]
        end
        
        subgraph "Data Namespace"
            PG_PRIMARY[PostgreSQL Primary]
            PG_REPLICA[PostgreSQL Replica]
            REDIS_MASTER[Redis Master]
            REDIS_SLAVE[Redis Slave]
        end
        
        subgraph "Monitoring Namespace"
            PROMETHEUS[Prometheus]
            GRAFANA[Grafana]
            LOKI[Loki]
            JAEGER[Jaeger]
        end
    end
    
    subgraph "Cloud Services"
        S3_STORAGE[AWS S3]
        SECRETS[AWS Secrets Manager]
        RDS[AWS RDS]
    end
    
    USERS --> CDN
    CDN --> NGINX
    NGINX --> CERT
    CERT --> API_SVC
    
    API_SVC --> API_POD1
    API_SVC --> API_POD2
    API_SVC --> API_POD3
    
    API_POD1 --> TEMPORAL_SERVER
    API_POD2 --> TEMPORAL_SERVER
    API_POD3 --> TEMPORAL_SERVER
    
    TEMPORAL_SERVER --> WORKER_SVC
    WORKER_SVC --> WORKER_POD1
    WORKER_SVC --> WORKER_POD2
    
    API_POD1 --> PG_PRIMARY
    PG_PRIMARY --> PG_REPLICA
    
    API_POD1 --> REDIS_MASTER
    REDIS_MASTER --> REDIS_SLAVE
    
    API_POD1 --> S3_STORAGE
    API_POD1 --> SECRETS
    
    API_POD1 --> PROMETHEUS
    WORKER_POD1 --> PROMETHEUS
    PROMETHEUS --> GRAFANA
    API_POD1 --> LOKI
    API_POD1 --> JAEGER
```

### CI/CD Pipeline
```mermaid
graph LR
    subgraph "Source Control"
        GIT[GitHub Repository]
        PR[Pull Request]
    end
    
    subgraph "CI Pipeline"
        LINT[Linting]
        TEST[Unit Tests]
        INTEGRATION[Integration Tests]
        BUILD[Build Docker Images]
        SCAN[Security Scan]
    end
    
    subgraph "CD Pipeline"
        STAGING[Deploy to Staging]
        SMOKE[Smoke Tests]
        PROD[Deploy to Production]
        ROLLBACK[Rollback if Failed]
    end
    
    subgraph "Artifacts"
        REGISTRY[Container Registry]
        HELM[Helm Charts]
        DOCS[Documentation]
    end
    
    GIT --> PR
    PR --> LINT
    LINT --> TEST
    TEST --> INTEGRATION
    INTEGRATION --> BUILD
    BUILD --> SCAN
    SCAN --> REGISTRY
    
    REGISTRY --> STAGING
    STAGING --> SMOKE
    SMOKE --> PROD
    PROD --> ROLLBACK
    
    BUILD --> HELM
    BUILD --> DOCS
```