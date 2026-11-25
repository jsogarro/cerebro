# Cerebro documentation

These documents describe the implemented system: the FastAPI application, the execution path that turns a research query into a durable run, the data model, the clients and the operating guides. Each reference page marks every feature as Active, Off by default or Not wired.

## Reference

| Page | Covers |
|---|---|
| [Architecture overview](reference/01-architecture-overview.md) | Processes, application assembly, middleware order, mounted routers, execution paths, the component registry and the contracts package |
| [Configuration](reference/02-configuration.md) | The settings production code reads, the 100 declared settings that have no effect, and components that read their own variables |
| [HTTP API](reference/03-http-api.md) | Conventions, the public-route allowlist, every mounted route group, error envelope and fixed responses |
| [Authentication and tenancy](reference/04-authentication-and-tenancy.md) | RS256 JWTs, `AuthMiddleware`, WebSocket tokens, organization claims and tenant isolation |
| [Research execution](reference/05-research-execution.md) | Execution authority, routing proposal, plan compilation, admission, topology execution, status, cancellation and checkpoints |
| [MASR routing](reference/06-masr-routing.md) | Complexity analysis, collaboration modes, worker allocation, cost and quality estimates and the `/api/v1/masr` endpoints |
| [Agents and supervisors](reference/07-agents-and-supervisors.md) | The 17 worker agents, four domain supervisors, the direct agent API, the supervisor API and the verification gate |
| [Durable runs and events](reference/08-durable-runs-and-events.md) | Run, task and attempt records, the append-only event log, the outbox relay, SSE and WebSocket run streams |
| [Tool boundary, capabilities and evidence](reference/09-tool-boundary-and-evidence.md) | `ToolBoundary`, capability grants, the budget ledger, source snapshots, evidence rows and claim support |
| [LLM providers](reference/10-llm-providers.md) | `GeminiService`, the provider package, the model router and the optional multi-provider route |
| [Reports](reference/11-reports.md) | Report generation, output formats, storage and the `/api/v1/reports` routes |
| [Security, audit and observability](reference/12-security-audit-observability.md) | Audit trail, security headers, log redaction, Prometheus metrics and Langfuse tracing |
| [CLI and web clients](reference/13-cli-and-web-clients.md) | The `research-cli` commands and the React workbench, including the pages that call routes the API does not mount |
| [Data model and persistence](reference/14-data-model-and-persistence.md) | Tables, models, repositories, migrations and the tenant filter |
| [Code that is not wired](reference/15-not-wired.md) | Code and settings with no production caller |

## Guides

| Guide | Use it to |
|---|---|
| [Development setup](development-setup-guide.md) | Install dependencies, configure the environment, run the stack, get an access token and run the smoke tests |
| [Testing](testing-guide.md) | Run the Python suites, the live-provider evaluations and the web Playwright specs, and see which CI workflow runs each |
| [Deployment and monitoring](deployment-monitoring-guide.md) | Deploy with Docker Compose or Kustomize and read the health and metrics endpoints |
| [Backup and recovery](backup-recovery.md) | Back up and restore each store that holds state |
| [Performance tuning](performance-tuning.md) | Find the rate, concurrency, connection and model-call limits and where each is set |
| [Troubleshooting](troubleshooting.md) | Diagnose common failures |
| [Nightly live-eval workflow template](ci/nightly-live-eval.yml.example) | Start from a scheduled workflow that runs the live evaluations |

## Status labels

- **Active**: a production caller reaches the feature on the default configuration.
- **Off by default**: a production caller reaches the feature only when a setting whose default disables it is changed.
- **Not wired**: the code is present and no production caller reaches it. [Code that is not wired](reference/15-not-wired.md) collects these.

## Documentation rules

- Documents describe the implemented system in the present tense.
- Each claim is checked against the code it cites.
- Code is cited by path and symbol.
