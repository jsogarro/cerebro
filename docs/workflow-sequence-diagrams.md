# Workflow Sequence Diagrams

This document contains detailed sequence diagrams for key workflows in the Multi-Agent Research Platform.

## Table of Contents
- [Research Project Creation Workflow](#research-project-creation-workflow)
- [Agent Orchestration Workflow](#agent-orchestration-workflow)
- [Literature Review Process](#literature-review-process)
- [Report Generation Workflow](#report-generation-workflow)
- [Real-time Progress Updates](#real-time-progress-updates)
- [Error Handling and Retry Workflow](#error-handling-and-retry-workflow)
- [Authentication Flow](#authentication-flow)
- [Caching Strategy](#caching-strategy)

## Research Project Creation Workflow

```mermaid
sequenceDiagram
    participant User
    participant CLI
    participant API
    participant Auth
    participant Validator
    participant DB
    participant Temporal
    participant Queue
    participant Worker
    
    User->>CLI: research-cli projects create
    CLI->>CLI: Parse arguments
    CLI->>API: POST /api/v1/projects
    API->>Auth: Verify JWT token
    Auth-->>API: Token valid
    API->>Validator: Validate request
    Validator-->>API: Validation passed
    
    API->>DB: Create project record
    DB-->>API: Project ID
    
    API->>Temporal: Start ResearchWorkflow
    Temporal->>Queue: Queue workflow
    Queue-->>Temporal: Workflow queued
    
    Temporal->>Worker: Assign to worker
    Worker-->>Temporal: Accepted
    
    API-->>CLI: Project created (ID, status)
    CLI-->>User: Display project details
    
    Note over Worker: Workflow begins execution
    Worker->>DB: Update project status
    Worker->>Temporal: Report progress
```

## Agent Orchestration Workflow

```mermaid
sequenceDiagram
    participant Temporal
    participant LangGraph
    participant Factory
    participant LitAgent as Literature Agent
    participant CompAgent as Comparative Agent
    participant MethAgent as Methodology Agent
    participant SynthAgent as Synthesis Agent
    participant CitAgent as Citation Agent
    participant Gemini
    participant Cache
    participant DB
    
    Temporal->>LangGraph: Execute workflow
    LangGraph->>Factory: Create agent: Literature
    Factory-->>LangGraph: LitAgent instance
    
    LangGraph->>LitAgent: Execute with context
    LitAgent->>Cache: Check cache
    Cache-->>LitAgent: Cache miss
    LitAgent->>Gemini: Generate search queries
    Gemini-->>LitAgent: Search queries
    LitAgent->>DB: Store intermediate results
    
    LangGraph->>Factory: Create agent: Comparative
    Factory-->>LangGraph: CompAgent instance
    LangGraph->>CompAgent: Execute with lit results
    CompAgent->>Gemini: Analyze comparisons
    Gemini-->>CompAgent: Comparison matrix
    CompAgent->>Cache: Store in cache
    
    LangGraph->>Factory: Create agent: Methodology
    Factory-->>LangGraph: MethAgent instance
    LangGraph->>MethAgent: Execute with context
    MethAgent->>Gemini: Recommend methods
    Gemini-->>MethAgent: Methodology recommendations
    
    LangGraph->>Factory: Create agent: Synthesis
    Factory-->>LangGraph: SynthAgent instance
    LangGraph->>SynthAgent: Execute with all results
    SynthAgent->>Gemini: Synthesize findings
    Gemini-->>SynthAgent: Unified narrative
    
    LangGraph->>Factory: Create agent: Citation
    Factory-->>LangGraph: CitAgent instance
    LangGraph->>CitAgent: Execute with references
    CitAgent->>Gemini: Format citations
    Gemini-->>CitAgent: Formatted citations
    
    LangGraph->>DB: Store final results
    LangGraph-->>Temporal: Workflow complete
```

## Literature Review Process

```mermaid
sequenceDiagram
    participant Agent as Literature Agent
    participant Prompt as Prompt Manager
    participant Gemini
    participant Scholar as Google Scholar
    participant PubMed
    participant arXiv
    participant CrossRef
    participant Embedder
    participant VectorDB
    participant Validator
    
    Agent->>Prompt: Get literature review prompt
    Prompt-->>Agent: Formatted prompt
    
    Agent->>Gemini: Generate search strategies
    Gemini-->>Agent: Search queries & keywords
    
    par Search Academic Sources
        Agent->>Scholar: Search papers
        Scholar-->>Agent: Paper list
    and
        Agent->>PubMed: Search medical literature
        PubMed-->>Agent: Medical papers
    and
        Agent->>arXiv: Search preprints
        arXiv-->>Agent: Preprint papers
    end
    
    Agent->>CrossRef: Verify citations
    CrossRef-->>Agent: Citation metadata
    
    Agent->>Gemini: Extract key findings
    Gemini-->>Agent: Extracted findings
    
    Agent->>Embedder: Generate embeddings
    Embedder-->>Agent: Document embeddings
    
    Agent->>VectorDB: Store embeddings
    VectorDB-->>Agent: Storage confirmed
    
    Agent->>Validator: Validate results
    Validator-->>Agent: Validation passed
    
    Agent-->>Agent: Compile literature review
```

## Report Generation Workflow

```mermaid
sequenceDiagram
    participant Worker
    participant ReportGen as Report Generator
    participant Template as Template Engine
    participant DataAgg as Data Aggregator
    participant Formatter
    participant Storage as S3 Storage
    participant DB
    participant Notifier
    participant User
    
    Worker->>ReportGen: Generate report request
    ReportGen->>DB: Fetch project data
    DB-->>ReportGen: Project details
    
    ReportGen->>DB: Fetch all results
    DB-->>ReportGen: Research results
    
    ReportGen->>DataAgg: Aggregate data
    DataAgg->>DataAgg: Combine agent outputs
    DataAgg->>DataAgg: Resolve conflicts
    DataAgg-->>ReportGen: Aggregated data
    
    ReportGen->>Template: Select template
    Template-->>ReportGen: Report template
    
    ReportGen->>Formatter: Format report
    
    alt PDF Format
        Formatter->>Formatter: Generate PDF
    else Markdown Format
        Formatter->>Formatter: Generate Markdown
    else HTML Format
        Formatter->>Formatter: Generate HTML
    end
    
    Formatter-->>ReportGen: Formatted report
    
    ReportGen->>Storage: Upload report
    Storage-->>ReportGen: Storage URL
    
    ReportGen->>DB: Save report metadata
    DB-->>ReportGen: Saved
    
    ReportGen->>Notifier: Send notification
    Notifier->>User: Report ready email/webhook
```

## Real-time Progress Updates

```mermaid
sequenceDiagram
    participant Client
    participant WebSocket
    participant API
    participant Redis as Redis PubSub
    participant Worker
    participant Temporal
    
    Client->>API: Connect WebSocket
    API->>WebSocket: Establish connection
    WebSocket-->>Client: Connected
    
    Client->>WebSocket: Subscribe to project
    WebSocket->>Redis: Subscribe to channel
    Redis-->>WebSocket: Subscribed
    
    loop Workflow Execution
        Worker->>Temporal: Update progress
        Temporal->>Worker: Progress saved
        Worker->>Redis: Publish update
        Redis->>WebSocket: Broadcast update
        WebSocket->>Client: Progress message
        Client->>Client: Update UI
    end
    
    Worker->>Redis: Publish completion
    Redis->>WebSocket: Broadcast completion
    WebSocket->>Client: Completion message
    
    Client->>WebSocket: Unsubscribe
    WebSocket->>Redis: Unsubscribe
    WebSocket->>Client: Close connection
```

## Error Handling and Retry Workflow

```mermaid
sequenceDiagram
    participant Worker
    participant Activity
    participant ErrorHandler
    participant RetryPolicy
    participant DeadLetter
    participant Monitor
    participant Alert
    participant Admin
    
    Worker->>Activity: Execute activity
    Activity->>Activity: Process fails
    Activity-->>Worker: Error thrown
    
    Worker->>ErrorHandler: Handle error
    ErrorHandler->>ErrorHandler: Classify error
    
    alt Retryable Error
        ErrorHandler->>RetryPolicy: Check retry policy
        RetryPolicy->>RetryPolicy: Calculate backoff
        RetryPolicy-->>ErrorHandler: Wait duration
        
        loop Retry Attempts
            ErrorHandler->>Activity: Retry execution
            alt Success
                Activity-->>Worker: Success result
                Worker->>Monitor: Log recovery
            else Failure
                Activity-->>ErrorHandler: Error
                ErrorHandler->>RetryPolicy: Increment attempts
            end
        end
        
        alt Max Retries Exceeded
            ErrorHandler->>DeadLetter: Move to DLQ
            DeadLetter->>Monitor: Log failure
            Monitor->>Alert: Trigger alert
            Alert->>Admin: Send notification
        end
    else Non-Retryable Error
        ErrorHandler->>Worker: Fail workflow
        Worker->>Monitor: Log critical error
        Monitor->>Alert: Immediate alert
        Alert->>Admin: Urgent notification
    end
```

## Authentication Flow

```mermaid
sequenceDiagram
    participant User
    participant Client
    participant API
    participant AuthService
    participant TokenValidator
    participant Cache
    participant DB
    participant RefreshService
    
    User->>Client: Enter credentials
    Client->>API: POST /auth/login
    API->>AuthService: Authenticate user
    
    AuthService->>DB: Verify credentials
    DB-->>AuthService: User record
    
    AuthService->>AuthService: Hash comparison
    
    alt Valid Credentials
        AuthService->>AuthService: Generate JWT
        AuthService->>AuthService: Generate refresh token
        AuthService->>Cache: Store refresh token
        AuthService-->>API: Tokens
        API-->>Client: Access & refresh tokens
        Client->>Client: Store tokens
    else Invalid Credentials
        AuthService-->>API: Authentication failed
        API-->>Client: 401 Unauthorized
    end
    
    Note over Client: Subsequent requests
    
    Client->>API: Request with JWT
    API->>TokenValidator: Validate token
    
    alt Token Valid
        TokenValidator->>Cache: Check blacklist
        Cache-->>TokenValidator: Not blacklisted
        TokenValidator-->>API: Valid
        API->>API: Process request
    else Token Expired
        TokenValidator-->>API: Token expired
        API-->>Client: 401 Token Expired
        
        Client->>API: POST /auth/refresh
        API->>RefreshService: Refresh tokens
        RefreshService->>Cache: Validate refresh token
        Cache-->>RefreshService: Valid
        RefreshService->>RefreshService: Generate new JWT
        RefreshService-->>API: New tokens
        API-->>Client: New access token
    end
```

## Caching Strategy

```mermaid
sequenceDiagram
    participant Client
    participant API
    participant CacheManager
    participant Redis
    participant DB
    participant Invalidator
    
    Client->>API: GET /api/v1/projects/{id}
    API->>CacheManager: Check cache
    CacheManager->>Redis: GET project:{id}
    
    alt Cache Hit
        Redis-->>CacheManager: Cached data
        CacheManager->>CacheManager: Check TTL
        alt TTL Valid
            CacheManager-->>API: Return cached data
            API-->>Client: Project data (from cache)
        else TTL Expired
            CacheManager->>DB: Fetch fresh data
            DB-->>CacheManager: Project data
            CacheManager->>Redis: SET with new TTL
            CacheManager-->>API: Return fresh data
            API-->>Client: Project data (refreshed)
        end
    else Cache Miss
        CacheManager->>DB: Fetch from database
        DB-->>CacheManager: Project data
        CacheManager->>Redis: SET with TTL
        CacheManager-->>API: Return data
        API-->>Client: Project data (from DB)
    end
    
    Note over Client: Update operation
    
    Client->>API: PUT /api/v1/projects/{id}
    API->>DB: Update project
    DB-->>API: Updated
    API->>Invalidator: Invalidate cache
    Invalidator->>Redis: DEL project:{id}
    Invalidator->>Redis: DEL related keys
    Redis-->>Invalidator: Deleted
    API-->>Client: Update successful
```

## Parallel Agent Execution

```mermaid
sequenceDiagram
    participant Workflow
    participant Scheduler
    participant Pool as Worker Pool
    participant Agent1 as Literature Agent
    participant Agent2 as Comparative Agent
    participant Agent3 as Methodology Agent
    participant Aggregator
    participant Conflict as Conflict Resolver
    
    Workflow->>Scheduler: Schedule parallel tasks
    
    Scheduler->>Pool: Request workers
    Pool-->>Scheduler: Workers available
    
    par Parallel Execution
        Scheduler->>Agent1: Execute literature review
        Agent1->>Agent1: Process
        Agent1-->>Scheduler: Literature results
    and
        Scheduler->>Agent2: Execute comparison
        Agent2->>Agent2: Process
        Agent2-->>Scheduler: Comparison results
    and
        Scheduler->>Agent3: Execute methodology
        Agent3->>Agent3: Process
        Agent3-->>Scheduler: Methodology results
    end
    
    Scheduler->>Aggregator: Combine results
    Aggregator->>Conflict: Check conflicts
    
    alt Conflicts Found
        Conflict->>Conflict: Apply resolution rules
        Conflict->>Conflict: Prioritize by confidence
        Conflict-->>Aggregator: Resolved data
    else No Conflicts
        Conflict-->>Aggregator: Pass through
    end
    
    Aggregator-->>Workflow: Combined results
```

## Monitoring and Alerting Flow

```mermaid
sequenceDiagram
    participant Service
    participant OTel as OpenTelemetry
    participant Prometheus
    participant Loki
    participant Grafana
    participant AlertManager
    participant PagerDuty
    participant Slack
    participant Ops as Ops Team
    
    Service->>OTel: Send metrics
    Service->>OTel: Send traces
    Service->>OTel: Send logs
    
    OTel->>Prometheus: Export metrics
    OTel->>Loki: Export logs
    
    Prometheus->>Prometheus: Evaluate rules
    
    alt Alert Triggered
        Prometheus->>AlertManager: Send alert
        AlertManager->>AlertManager: Check severity
        
        alt Critical Alert
            AlertManager->>PagerDuty: Page on-call
            PagerDuty->>Ops: Wake up engineer
            AlertManager->>Slack: Post to #incidents
        else Warning Alert
            AlertManager->>Slack: Post to #alerts
        else Info Alert
            AlertManager->>Slack: Post to #monitoring
        end
    end
    
    Grafana->>Prometheus: Query metrics
    Grafana->>Loki: Query logs
    Grafana->>Grafana: Render dashboards
    
    Ops->>Grafana: View dashboards
    Ops->>Loki: Search logs
    Ops->>Service: Apply fix
```