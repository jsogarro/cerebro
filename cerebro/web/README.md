# Cerebro Web (Work in Progress)

> **Status: WORK IN PROGRESS — not the current development focus.**
>
> This web frontend is an early scaffold. Active development is currently
> concentrated on making the **agent framework robust** (multi-provider
> routing, multi-domain execution, verification loops, adaptive routing,
> and their evaluation harnesses) before building a full web experience on
> top of it. Expect incomplete features, unpolished UI, and breaking
> changes without notice.

## What this is

A React + TypeScript + Vite single-page app intended to become the UI for
the Cerebro platform. It talks to the FastAPI backend at `/api/v1`
(configured via `VITE_API_URL`; see `docker-compose.yml`, which builds and
serves it on port 3000).

## What to rely on instead

The stable, tested surface of Cerebro today is the API and CLI:

- **Primary API**: `POST /api/v1/query/{research,analyze,synthesize}` —
  MASR-routed multi-agent execution.
- **Bypass API**: `POST /api/v1/agents/{type}/execute` — direct agent
  access for testing.
- **CLI**: `cerebro-cli` / `research-cli` (1:1 with the API).

See the repository root `README.md` and `docs/` for the backend
architecture, configuration reference, and agent-domain documentation.

## Development (at your own risk)

```bash
npm install
npm run dev        # Vite dev server
npm run build      # production build (served by nginx in Docker)
npm run lint
```

Playwright config exists (`playwright.config.ts`) but the E2E suite is
skipped in CI while the frontend is in this state.

## Roadmap gate

This app graduates from WIP when the agent framework's remaining gated
items land (adaptive-routing promotion after live A/B, provider
observability, external tool integrations) and the API surface it depends
on is declared stable.
