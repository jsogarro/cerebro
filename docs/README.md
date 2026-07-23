# Cerebro Documentation

This index separates current product guidance from detailed implementation
references and historical design material.

## Start Here

| Document | Purpose |
| --- | --- |
| [Project README](../README.md) | Canonical product positioning, current status, and quick start |
| [Agent domains](agent-domains.md) | Current supervisor and worker catalog, including implementation coupling |
| [Runtime architecture](multi-agent-architecture.md) | Detailed current multi-agent implementation |
| [Configuration reference](configuration-reference.md) | Environment variables and runtime configuration |
| [CLI guide](CLI.md) | Current `research-cli` commands |
| [API documentation](api-documentation.md) | Existing HTTP and WebSocket surfaces |

## Product Overview

Cerebro is an open-source workbench for source-grounded AI research workflows.
Its public product vocabulary describes the research process in terms of:

- workflow definition and version;
- run and task lifecycle;
- evidence and claim support;
- artifacts and verification;
- measured cost, latency, and quality;
- reproducible evaluation.

Finance is an included domain example. It is not the product boundary, and the
current Finance domain does not provide institutional data retrieval.

`Workflow`, `Run`, `Evidence`, `Artifact`, and `Evaluator` are product-level
concepts rather than a fully implemented public contract. The current API and
persistence layers retain `ResearchProject`, `ResearchQuery`, and other legacy
names.

## Implementation References

These documents describe specific subsystems. Verify examples against the
current code when making changes because some documents contain older snippets.

### Runtime and Agents

- [Agent flowcharts](agent-flowcharts.md)
- [Agent framework overview](api/agent-framework-overview.md)
- [Agent API reference](api/agent-api-reference.md)
- [Agent execution patterns](api/agent-execution-patterns.md)
- [MASR API guide](api/masr-api-guide.md)
- [MASR complete guide](api/masr-api-complete-guide.md)
- [Intelligent routing strategy](api/intelligent-routing-strategy.md)
- [LangGraph integration](langgraph-integration.md)
- [Outstanding provider and learning work](outstanding-provider-and-learning.md)

### Data and Operations

- [Database architecture](database-architecture.md)
- [Repository pattern](repository-pattern-guide.md)
- [Report generation](report-generation-system.md)
- [WebSocket updates](websocket-realtime-updates.md)
- [Security authentication](security-authentication.md)
- [Security implementation](security-implementation.md)
- [Backup and recovery](backup-recovery.md)
- [Deployment monitoring](deployment-monitoring-guide.md)
- [Performance tuning](performance-tuning.md)
- [Troubleshooting](troubleshooting.md)

### Development and Verification

- [Development setup](development-setup-guide.md)
- [Integration testing](integration-testing-guide.md)
- [End-to-end verification](E2E_VERIFICATION.md)

## Historical and Research Material

Documents under [`experimentation/`](experimentation/) preserve subsystem
designs and research history. They do not describe current product behavior.
Additional local planning and research artifacts may exist in git-ignored files;
do not add them to the public repository without an explicit documentation
decision.

## Documentation Rules

When updating Cerebro:

1. Describe shipped behavior in the present tense.
2. Label target behavior as planned or proposed.
3. Keep private product specs, PRDs, reviews, and implementation plans outside
   the repository.
4. Update the project README and this index when the product boundary changes.
5. Preserve legacy identifiers where they refer to concrete APIs, models,
   commands, or deployment resources.
6. Do not treat agent count, orchestration complexity, or theoretical novelty
   as evidence of research quality.
