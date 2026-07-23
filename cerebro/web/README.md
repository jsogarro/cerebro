# Cerebro Web

> **Status:** early scaffold; not yet a complete research workbench.

This React, TypeScript, and Vite application is intended to become the visual
surface for running and inspecting Cerebro research workflows. The existing
pages include mock-backed and incomplete states and should not be presented as a
finished product.

## Target Experience

The workbench should make the full research run legible:

- select a versioned workflow;
- submit an objective and source material;
- follow a stable task timeline or graph;
- inspect evidence linked to report claims;
- review artifacts and verification results;
- compare measured cost, latency, and quality across runs.

Agent class names, routing internals, and raw prompts should be available through
progressive disclosure, not used as the primary navigation model.

## Current Backend Surfaces

- Routed queries: `POST /api/v1/query/{research,analyze,synthesize}`
- Direct agent execution: `POST /api/v1/agents/{type}/execute`
- Routing inspection: `/api/v1/masr/*`
- Legacy research projects: `/api/v1/research/projects/*`
- Progress updates: WebSocket endpoints documented under `docs/`

These surfaces are not yet the target neutral `Workflow` and `Run` contract.
Frontend work should preserve current compatibility while new view models are
introduced.

## Development

```bash
npm install
npm run dev
npm run build
npm run lint
```

The backend URL is configured with `VITE_API_URL`. Docker Compose builds and
serves the application on port 3000.

Playwright configuration exists, but the current end-to-end suite does not yet
cover a production-ready golden workflow.

## Graduation Criteria

The web application is ready to serve as the primary demo when:

1. a fixture-backed run can be started without paid credentials;
2. the run lifecycle uses measured backend state rather than mock values;
3. tasks, evidence, artifacts, and evaluations have stable API view models;
4. running, degraded, failed, cancelled, and completed states are implemented;
5. the golden path passes Playwright checks on desktop and mobile viewports;
6. screenshots and the root README match the shipped experience.
