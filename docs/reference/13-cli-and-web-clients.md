# CLI and web clients

Two clients talk to the HTTP API: the `research-cli` command-line tool in `src/cli/` and the React workbench in `cerebro/web/`. Both authenticate with a bearer token and neither has a sign-in flow of its own. Part of the workbench calls routes the API does not mount.

## Summary

| Feature | Status | Entry point | Source |
|---|---|---|---|
| `research-cli` commands (`agents`, `projects`, `config`, `health`, `completion`) | Active | `research-cli` | `src/cli/main.py` (`cli`) |
| Progress streaming over WebSocket | Active | `research-cli projects progress ID --stream` | `src/cli/websocket_client.py` (`CLIWebSocketClient`) |
| `research-platform` script | Not wired | `research-platform` | `src/research_platform/__init__.py` (`main`) |
| Web workbench: research projects, agents list | Active | `/app/research`, `/app/agents` | `cerebro/web/src/api/research.ts`, `cerebro/web/src/api/agents.ts` |
| Web workbench: workflows, runs, run detail | Not wired | `/app/workflows`, `/app/runs`, `/app/runs/:id` | `cerebro/web/src/api/workflows.ts`, `cerebro/web/src/api/runs.ts` |
| Web workbench: memory | Not wired | `/app/memory` | `cerebro/web/src/api/memory.ts` |
| Web workbench: dashboard, QA, costs, benchmarks, settings | Not wired | `/app/dashboard`, `/app/qa`, `/app/costs`, `/app/benchmarks`, `/app/settings` | `cerebro/web/src/pages/` |
| Playwright end-to-end suite | Active | `npm run test:e2e` | `cerebro/web/tests/e2e/` |

## research-cli

### Install and run

`pyproject.toml` declares the script `research-cli = "src.cli.main:cli"`, so installing the package from a repository checkout (`pip install -e .` or `uv pip install -e .`) puts `research-cli` on the path. The `development` image target used by the compose `api` service installs the package with `uv pip install -e ".[dev,mcp]"`. The `api` service has no fixed container name, so run the CLI inside it with:

```bash
docker compose exec api research-cli health
```

The `api` service publishes host port `${API_HOST_PORT:-8000}`, so a CLI installed on the host reaches it at the default `http://localhost:8000`. The Makefile wraps three calls: `make cli-health` (`research-cli health`), `make cli-projects` (`research-cli projects list`), and `make cli-test` (creates a project titled "Test Project").

### Configuration

`CLIConfig.from_env` loads `~/.research-cli.env` when it exists and the `.env.cli` file in the current directory otherwise, then reads these variables. `.env.cli.example` in the repository root lists them.

| Variable | Default | Meaning |
|---|---|---|
| `RESEARCH_API_URL` | `http://localhost:8000` | API base URL. Must start with `http://` or `https://`; a trailing slash is removed. |
| `RESEARCH_API_TIMEOUT` | `30` | Request timeout in seconds. |
| `RESEARCH_OUTPUT_FORMAT` | `table` | One of `table`, `json`, `yaml`, `csv`. |
| `RESEARCH_VERBOSE` | `false` | `true`, `1`, or `yes` enables verbose output. |
| `RESEARCH_COLOR` | `true` | `true`, `1`, or `yes` keeps colored output. |
| `RESEARCH_MAX_RETRIES` | `3` | Attempts for a request that fails at the transport level. |
| `RESEARCH_API_KEY` | none | Sent as the `X-API-Key` header. |
| `RESEARCH_AUTH_TOKEN` | none | Sent as `Authorization: Bearer <token>`. |

The `--format` option always has a value (its default is `table`) and is applied after the configuration loads, so it overrides `RESEARCH_OUTPUT_FORMAT` and a saved `output_format` on every run. Pass `--format` to choose another format.

`research-cli config` takes an action:

- `show [KEY]` prints the whole configuration or one key.
- `set KEY VALUE` changes a value for the current process only. `verbose` and `color` accept `true`, `1`, `yes`; `api_timeout` and `max_retries` are integers.
- `save` writes the configuration to `~/.research-cli.env`.

Because `set` does not persist, use `--api-url`, an exported `RESEARCH_*` variable, or `config save` after exporting the variable to keep a setting.

The client retries only `httpx.TransportError` and `httpx.TimeoutException`, with exponential backoff between 4 and 10 seconds. HTTP error responses are raised at once and not retried.

### Authentication

`RESEARCH_AUTH_TOKEN` is sent as `Authorization: Bearer`. When no token is set and `RESEARCH_API_KEY` is, the client sends `X-API-Key` instead. The API's authentication middleware reads only the `Authorization` bearer header, and no API code reads `X-API-Key`, so a request that carries only an API key receives `401` on every protected route.

Every command except `health` needs a token; `/health` and `/ready` are public. Obtain a token from the login route (see [authentication and tenancy](04-authentication-and-tenancy.md)) and export it as `RESEARCH_AUTH_TOKEN`.

`projects create` sends a `user_id`, and the API returns `403` when it differs from the authenticated user. `--user-id` is optional: the client takes the `sub` claim of `RESEARCH_AUTH_TOKEN` (decoded without verification) when the option is absent, and fails with `user id unknown: pass --user-id or authenticate with a JWT token` when there is neither.

### Global options

| Option | Default | Meaning |
|---|---|---|
| `--api-url TEXT` | `RESEARCH_API_URL` | API base URL |
| `--format`, `-f` `[table\|json\|yaml\|csv]` | `table` | Output format |
| `--verbose`, `-v` | off | Enable verbose output |
| `--no-color` | off | Disable colored output |
| `--version` | | Print `research-cli, version 0.1.0` |

### Commands

A failed API call in an `agents` or `projects` command prints an error and exits with status `1`.

**`research-cli health`** calls `GET /health` and `GET /ready`. With `--verbose` it also lists each readiness check.

**`research-cli completion [bash|zsh|fish]`** prints the shell line that enables completion.

**`research-cli agents`**

| Command | Arguments and options | Calls |
|---|---|---|
| `query` | `QUERY_TEXT`; `--domains`/`-d` (repeatable); `--type`/`-t` `research\|analyze\|synthesize` (default `research`); `--authority-id`; `--authority-version` | `POST /api/v1/query/{type}` |
| `route` | `QUERY_TEXT`; `--strategy`/`-s` `cost_efficient\|quality_focused\|balanced` (default `balanced`) | `POST /api/v1/masr/route` |
| `estimate` | `QUERY_TEXT`; `--domains`/`-d` (repeatable) | `POST /api/v1/masr/estimate-cost` |
| `execute` | `AGENT_TYPE QUERY_TEXT`; `--max-sources` (integer, for literature review); `--authority-id`; `--authority-version` | `POST /api/v1/agents/{AGENT_TYPE}/execute` |
| `chain` | `QUERY_TEXT`; `--agents`/`-a` (repeatable, required, in order); `--authority-id`; `--authority-version` | `POST /api/v1/agents/chain` |
| `status` | none | `GET /api/v1/masr/status` |

`--authority-id` and `--authority-version` must be given together; either one alone is a usage error. Together they are sent as `authority_reference` with `authority_id` and `authority_version`. `query`, `execute`, and `chain` print the `results` field of the response, or the whole response when it has none.

**`research-cli projects`**

| Command | Arguments and options | Calls |
|---|---|---|
| `create` | `--title`/`-t`; `--query`/`-q`; `--domains`/`-d` (comma-separated); `--user-id`/`-u`; `--depth` `survey\|comprehensive\|exhaustive` (default `comprehensive`); `--scope`/`-s` `key=value` (repeatable); `--interactive`/`-i`; `--file`/`-f` PATH | `POST /api/v1/research/projects` |
| `get` | `PROJECT_ID` | `GET /api/v1/research/projects/{id}` |
| `list` | `--user-id`/`-u`; `--status`/`-s`; `--limit`/`-l` (default `10`); `--offset`/`-o` (default `0`) | `GET /api/v1/research/projects` |
| `progress` | `PROJECT_ID`; `--watch`/`-w`; `--stream`/`-s`; `--interval`/`-i` seconds (default `5`) | `GET /api/v1/research/projects/{id}/progress`; WebSocket `/ws/cli/{id}` |
| `cancel` | `PROJECT_ID`; `--force`/`-f` skips the confirmation prompt | `POST /api/v1/research/projects/{id}/cancel` |
| `results` | `PROJECT_ID`; `--output`/`-o` PATH | `GET /api/v1/research/projects/{id}/results` |
| `refine` | `PROJECT_ID`; `--scope`/`-s` `key=value` (repeatable); `--max-sources` (integer); `--languages` (comma-separated) | `POST /api/v1/research/projects/{id}/refine` |

Notes on individual commands:

- **`create`** works in three modes. With `--file`, a `.yaml`, `.yml`, or `.json` file holds one project or a list of projects, each with `title`, `query_text`, and `domains` (`depth_level` defaults to `comprehensive`), and the command prints a created/failed summary. With `--interactive`, it prompts for each value. Otherwise `--title`, `--query`, and `--domains` are all required. `--scope` values are parsed to booleans, integers, floats, or bracketed lists when they look like them.
- **`progress`** prints the current progress once. `--watch` polls every `--interval` seconds until `progress_percentage` reaches 100. `--stream` opens a WebSocket to `/ws/cli/{project_id}` with the token in a `token` query parameter and prints updates as they arrive; when the connection fails it falls back to polling. `--watch` and `--stream` cannot be combined.
- **`results`** prints the results as JSON. With `--output` it writes a file: JSON for `--format json`, YAML for `--format yaml`, and the Python `str()` of the results for `--format table`.
- **`refine`** exits with an error when no scope parameter is given.

### Output formats

`--format` selects `table`, `json`, `yaml`, or `csv` for the `projects` commands that print projects or progress. Project lists and single projects support all four formats, with `csv` rendered by `tabulate`. Progress is rendered as `table` text for both `table` and `csv`. The `agents` commands print with Rich and ignore `--format`.

### Errors

A failed request prints `<operation> failed: <detail>` (for example `Failed to fetch project: ...`) and exits with status `1`. `<detail>` is the `detail` field of the JSON error body when the body has one, and the raw response text otherwise. The API's error envelope is `{"error": {"code", "message", "details"}}` with no `detail` key, so the CLI prints that JSON text. A `404` from `projects get`, `progress`, or `cancel` prints `Project not found: <id>`, and from `projects results` prints `Results not found for project: <id>`. No command treats `401`, `403`, or `422` differently from other statuses. `health` prints `Failed to connect to API: ...` when the connection fails.

## research-platform script

> **Not wired.** `pyproject.toml` declares `research-platform = "research_platform:main"`. `main()` in `src/research_platform/__init__.py` only logs `Research platform initialized`; it starts no server and runs no work. Use `research-cli` for command-line access and `uvicorn src.api.main:app` for the API.

## Web workbench

### Stack and commands

`cerebro/web` is a Vite single-page app: React 19, TypeScript, React Router 7, TanStack Query 5, axios, zustand, Tailwind CSS 3, and Radix UI components. `package.json` declares no Node engine.

| Command | What it runs |
|---|---|
| `npm run dev` | `vite` dev server; proxies `/api` and `/ws` to `VITE_API_URL` (default `http://localhost:8000`) |
| `npm run build` | `tsc -b && vite build` |
| `npm run lint` | `eslint .` |
| `npm run preview` | `vite preview` |
| `npm run test:e2e` | `playwright test` |

### Serving

The compose `web` service builds `cerebro/web/Dockerfile` (a Node 20 build stage, then an nginx image that runs as the `nginx` user and listens on 8080) and publishes it on host port `${WEB_HOST_PORT:-3000}`. Its nginx serves the built files with a fallback to `index.html` for client-side routes and does not proxy the API. The compose `nginx` service publishes `${NGINX_HTTP_HOST_PORT:-80}` and `${NGINX_HTTPS_HOST_PORT:-443}` and uses `docker/nginx/nginx.conf`: `/api/` and `/ws` go to `api:8000`, `/health` is answered by nginx itself, and every other path goes to `web:8080`.

### Authentication

`cerebro/web/src/api/auth.ts` reads the access token from `localStorage` under the key `cerebro.access_token`. The axios client in `src/api/client.ts` uses base URL `/api/v1` and adds `Authorization: Bearer <token>` to every request when a token exists. The WebSocket manager in `src/api/websocket.ts` sends the token as a `token` query parameter. No code in `src/` calls `setAccessToken` and the workbench has no sign-in page, so the token has to be written to `localStorage` by hand. `tokenSubject` decodes the token's `sub` claim, and creating a research project refuses to proceed without a token. The `isAuthenticated` flag in `src/stores/authStore.ts` starts `true`, and no route reads it.

### Pages

> **Not wired.** The workflows, runs, run detail, and memory pages call routes the API does not mount. The API mounts no `/api/v1/workflows`, no `/api/v1/runs` list, create, or detail route, no `/api/v1/runs/{id}/cancel`, no `/api/v1/memory/nodes`, and no `/api/v1/agents/{id}/logs`. The only run route it mounts is `GET /api/v1/runs/{run_id}/events`, which streams server-sent events rather than returning a JSON collection. The dashboard, QA, costs, and benchmarks pages never call the API: the dashboard holds its figures in the component, and the QA, costs, and benchmarks clients return fixed values with the real call commented out. The settings page has inputs and a switch with no handlers.

| Route | Page | Backend calls | Backed |
|---|---|---|---|
| `/` | `Landing` | none | static |
| `/app` | redirect to `/app/workflows` | none | static |
| `/app/workflows` | `Workflows` | `GET /api/v1/workflows`; `POST /api/v1/runs` from the run form | No. The page shows an unavailable notice and substitutes no data. |
| `/app/runs` | `Runs` | `GET /api/v1/runs`; `POST /api/v1/runs/{id}/cancel` | No. The page shows an unavailable notice. |
| `/app/runs/:id` | `RunDetail` | `GET /api/v1/runs/{id}`, `/tasks`, `/events`, `/evidence`, `/artifacts`, `/evaluations` | No. Only `/events` is mounted, and it streams. |
| `/app/dashboard` | `Dashboard` | none | static; fixed values in the component |
| `/app/research`, `/app/research/:id` | `Research` | `GET` and `POST /api/v1/research/projects`; `GET /api/v1/research/projects/{id}`; WebSocket `/ws` | Yes |
| `/app/agents` | `Agents` | `GET /api/v1/agents`; `GET /api/v1/agents/{id}/logs` | Partly: the list is mounted, the logs route is not |
| `/app/memory` | `Memory` | `GET /api/v1/memory/nodes` | No |
| `/app/qa` | `QA` | none | static; fixed values |
| `/app/costs` | `Costs` | none | static; fixed values |
| `/app/benchmarks` | `Benchmarks` | none | static; fixed values |
| `/app/settings` | `Settings` | none | static |

### End-to-end tests

`cerebro/web/tests/e2e/` holds Playwright specs written in TypeScript: accessibility (`a11y-axe`, `a11y-keyboard`, `dark-mode-axe`, `wave6-accessibility`), layout and theme (`responsive-walkthrough`, `theme-tokens`, `landing`), and workbench behaviour (`runs`, `run-detail`, `workflows`, `workbench-ui`, `workbench-contracts`, `user-journey`, `api-contract`, `golden-run`). Run them from `cerebro/web` with `npm run test:e2e`. `playwright.config.ts` runs one Chromium project, starts `npm run dev` on `127.0.0.1:5173` (override with `PLAYWRIGHT_HOST` and `PLAYWRIGHT_PORT`), points the dev server at `PLAYWRIGHT_BACKEND_URL` (then `VITE_API_URL`, then `http://127.0.0.1:8000`), and retries twice when `CI` is set. The `user-journey`, `api-contract`, `wave6-accessibility`, and `workbench-ui` specs intercept API calls with `page.route`. `golden-run.spec.ts` skips itself unless `PLAYWRIGHT_REAL_BACKEND=1`.

## Source map

- `pyproject.toml` — `[project.scripts]` entries `research-cli` and `research-platform`
- `src/cli/main.py` — root group, `config`, `health`, `completion`
- `src/cli/commands/agents.py`, `src/cli/commands/projects.py` — command groups
- `src/cli/client.py` — `ResearchAPIClient`, `APIError`, retry policy, token subject decoding
- `src/cli/config.py` — `CLIConfig` and the `RESEARCH_*` variables
- `src/cli/formatters.py` — `OutputFormatter` for table, JSON, YAML, and CSV
- `src/cli/websocket_client.py` — `CLIWebSocketClient` used by `progress --stream`
- `src/cli/utils.py` — file loading, `key=value` parsing, interactive prompts
- `.env.cli.example` — example CLI environment file
- `src/research_platform/__init__.py` — the `research-platform` entry point stub
- `cerebro/web/src/router/index.tsx` — route table
- `cerebro/web/src/api/` — API clients (`client.ts`, `auth.ts`, `websocket.ts`, `research.ts`, `agents.ts`, `runs.ts`, `workflows.ts`, `memory.ts`, `qa.ts`, `costs.ts`, `benchmarks.ts`)
- `cerebro/web/src/pages/` — page components
- `cerebro/web/tests/e2e/`, `cerebro/web/playwright.config.ts` — end-to-end suite
- `cerebro/web/Dockerfile`, `cerebro/web/nginx.conf` — web image
- `docker-compose.yml`, `docker/nginx/nginx.conf` — `web` and `nginx` services and the reverse proxy
