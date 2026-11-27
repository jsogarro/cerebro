# Development setup

How to install the dependencies, configure the environment, run the stack, authenticate against it and check code quality locally. For the test suites see the [testing guide](testing-guide.md); for settings see [configuration](reference/02-configuration.md); for failures see [troubleshooting](troubleshooting.md).

## Prerequisites

| Tool | Version | Source |
|---|---|---|
| Python | 3.11 or newer | `pyproject.toml` (`requires-python = ">=3.11"`); `.python-version` pins `3.11`; the CI test job runs 3.11, 3.12 and 3.13; the `Dockerfile` and `docker/Dockerfile.masr` use Python 3.13 |
| uv | any recent release | `make install` runs `uv pip install`; CI installs uv 0.13.7 (`UV_VERSION` in `ci.yml`) and runs `uv pip install --system` |
| Docker with Compose v2 | `docker compose` | `scripts/compose.sh` and the Make targets call `docker compose` |
| Node.js | 20 | `NODE_VERSION: "20"` in `.github/workflows/ci.yml` and `FROM node:20-alpine` in `cerebro/web/Dockerfile`; `cerebro/web/package.json` declares no `engines` field |
| make | any | `Makefile` |
| tmux | any | `scripts/smoke_test.sh` boots the API in a tmux session |

## Install

```bash
uv venv && . .venv/bin/activate   # uv pip install refuses to run without a virtual environment
make install            # uv pip install -e ".[dev]"
pre-commit install
```

`make install-pip` is the alternative that does not need uv: it runs `scripts/setup-python-env.sh`, which creates `.venv` with `python3.11` (or `python3.12`, or the interpreter named in `PYTHON`), then runs `pip install -e ".[dev]"`. It resolves from the ranges in `pyproject.toml`, not from `uv.lock`, so its versions can differ from CI. Activate it with `. .venv/bin/activate`.

The `dev` extra in `pyproject.toml` contains pytest and its plugins, `ruff`, `black`, `mypy`, `pre-commit`, `testcontainers`, `locust`, `playwright`, `factory-boy`, `faker`, `aiosqlite` and the `mcp` and `stats` extras (`fastmcp`, `scipy`) that some tests import.

## Configure

```bash
cp .env.example .env
```

How the environment is read:

- `Settings` in `src/core/config.py` sets no `env_file`. Dotenv files are loaded into the process environment by `load_environment` in `src/core/environment.py`, which runs when `src/core/config.py` is imported.
- Precedence is the existing process environment first, then `~/.env`, then the project `.env`. Setting `CEREBRO_DISABLE_DOTENV` to `1`, `true`, `yes` or `on` skips both files.
- `scripts/compose.sh` passes the project `.env` and `~/.env` to Compose as `--env-file`, so they feed Compose variable substitution (`${GEMINI_API_KEY:-}`, `${API_HOST_PORT:-8000}`, and so on).

Values that matter for local work:

| Setting | Behavior |
|---|---|
| `SECRET_KEY` | `Settings.validate_secret_key` rejects any value shorter than 32 characters. In production it also rejects the placeholder `MUST_SET_IN_ENV`. The value in `.env.example` is 36 characters. |
| `DEBUG` | Defaults to `false`. `.env.example` sets `true`. Controls the `/docs` and `/redoc` pages ([API documentation pages](#api-documentation-pages)). |
| `ENABLE_RATE_LIMITING` | Defaults to `true` in `Settings`. `.env.example` sets `false`. |
| `GEMINI_API_KEY` | Optional. When it is unset, the lifespan in `src/api/main.py` logs `GEMINI_API_KEY not set` as a warning and creates no `GeminiService`. See [LLM providers](reference/10-llm-providers.md). |
| `JWT_PRIVATE_KEY_PATH`, `JWT_PUBLIC_KEY_PATH` | Default to `/secrets/jwt_private.pem` and `/secrets/jwt_public.pem`. See below. |

JWT keys (`src/auth/jwt_service.py`, `JWTService`):

- When the configured private key file exists, it is loaded.
- When it does not exist and `ENVIRONMENT` is not `production`, the service generates an RSA key pair and tries to write it to the configured paths. If the directory is not writable, it logs `jwt_private_key_path_not_writable_using_ephemeral` and keeps an in-memory key that does not survive a restart.
- When `ENVIRONMENT` is `production`, a missing or unreadable key file raises `RuntimeError`.

The compose file mounts no volume at `/secrets`. The settings are described in [configuration](reference/02-configuration.md).

## Run the stack

```bash
make dev          # ./scripts/compose.sh up -d
make dev-build    # ./scripts/compose.sh up -d --build
make dev-logs     # ./scripts/compose.sh logs -f
make dev-stop     # ./scripts/compose.sh down
```

`scripts/compose.sh` runs `docker compose` with `--project-directory` set to the repository root and the two `--env-file` arguments described above.

Services in `docker-compose.yml` and their host ports:

| Service | Host port | Notes |
|---|---|---|
| `api` | `${API_HOST_PORT:-8000}` | Built from the `development` stage of `Dockerfile`; runs `uvicorn src.api.main:app` on container port 8000; healthcheck `curl http://localhost:8000/health` |
| `mcp-server` | none published | Runs `MCPServer` from `src/mcp/server.py` over HTTP on container port 9000; `entrypoint: []`, so it does not run migrations |
| `postgres` | `${POSTGRES_HOST_PORT:-5432}` | `postgres:16-alpine`, database `research_db`, user `research` |
| `redis` | `${REDIS_HOST_PORT:-6379}` | `redis:7-alpine` with append-only persistence |
| `web` | `${WEB_HOST_PORT:-3000}` | Static build of `cerebro/web`, served by an unprivileged nginx on container port 8080. It has no `/api` proxy. |
| `nginx` | `${NGINX_HTTP_HOST_PORT:-80}` and `${NGINX_HTTPS_HOST_PORT:-443}` | Reverse proxy from `docker/nginx/nginx.conf`: `/api/` and `/ws` go to `api:8000`; `/health` is answered by nginx itself and never reaches the API; `/docs` is served from nginx's own static root and returns 404 (FastAPI's `/docs` is not reachable through the proxy); everything else goes to `web:8080` |
| `pgadmin` | 5050 | Profile `dev-tools` |
| `redis-commander` | 8081 | Profile `dev-tools` |
| `masr-router` | 9100 | Profile `legacy-masr-service` |

Migrations: the `api` service uses `/entrypoint.sh` (`docker/entrypoint.sh`), which runs `alembic upgrade head`, exits with the migration's status if it fails and otherwise `exec`s the command. `make db-init` runs the same upgrade inside a running `api` container.

Web workbench: the web client calls the relative base URL `/api/v1` (`cerebro/web/src/api/client.ts`), so it needs a same-origin proxy. Through Compose, open `http://localhost` (the `nginx` service, `NGINX_HTTP_HOST_PORT`). For a local dev server, run `npm install` and `npm run dev` in `cerebro/web`; `cerebro/web/vite.config.ts` proxies `/api` and `/ws` to `VITE_API_URL`, or to `http://localhost:8000` when it is unset. The Playwright config starts that dev server on port 5173 (`PLAYWRIGHT_PORT`).

Optional profiles:

```bash
./scripts/compose.sh --profile dev-tools up -d     # pgAdmin and Redis Commander
make legacy-masr                                   # the standalone MASR (Multi-Agent System Router) diagnostics service on port 9100
```

The Makefile describes `legacy-masr` as the "non-authoritative standalone MASR diagnostics service". The API routes through the in-process router (see [MASR routing](reference/06-masr-routing.md)).

## Get an access token

Every route except the ones in `PUBLIC_ROUTE_ALLOWLIST` (`src/middleware/auth_middleware.py`) needs a bearer token. The ten entries are listed in [HTTP API](reference/03-http-api.md#authentication-requirement).

Register (`RegisterRequest` in `src/auth/models.py`):

```bash
curl -s -X POST http://localhost:8000/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{
    "email": "dev@example.com",
    "username": "dev-user",
    "password": "Str0ng!Passphrase#1",
    "confirm_password": "Str0ng!Passphrase#1",
    "accept_terms": true
  }'
```

Field rules: `username` is 3 to 50 characters from letters, digits, `_` and `-`, and is lowercased; `password` is at least 12 characters with an uppercase letter, a lowercase letter, a digit and one of `!@#$%^&*()_+-=[]{}|;:,.<>?`; `confirm_password` must equal `password`; `accept_terms` must be `true`. `full_name` and `organization` are optional. `register` also rejects, with 400, a password that is one of `123456`, `password`, `qwerty`, `abc123`, `111111`, `123123`, `admin` or `letmein`, contains the username or the email local part, has three ascending or descending sequential characters or three repeated characters, or has an entropy below 40. It rejects, with 400, a password found in the HaveIBeenPwned breach list. A duplicate email returns 409.

The 201 response is an `AuthResponse`: a `user` object and a `tokens` object with `access_token`, `refresh_token`, `token_type` and `expires_in`. Log in later with `POST /api/v1/auth/login` and `{"email": ..., "password": ...}`, which applies the same password rules and returns the same shape.

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email": "dev@example.com", "password": "Str0ng!Passphrase#1"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["tokens"]["access_token"])')

curl -s http://localhost:8000/api/v1/auth/me -H "Authorization: Bearer $TOKEN"
curl -s http://localhost:8000/api/v1/research/projects -H "Authorization: Bearer $TOKEN"
```

Organization claim: tenant-scoped routes resolve their caller through `get_tenant_context` or `get_tenant_identity` (`src/middleware/tenant_context.py`). Both call `require_organization_id` and answer 403 `Tenant organization claim is required` when the token has no `organization_id`.

A local user always gets one: `register` creates the user with a fresh `uuid4()` organization, and `login` assigns one through `provisioned_organization_id` when the user has none, then puts it in the token (`src/api/auth/auth_router.py`). See [authentication and tenancy](reference/04-authentication-and-tenancy.md).

## Execution authority

A fresh install has no route, CLI command or script that creates an execution authority. Until a deployment supplies one, query routes return `422` with `EXECUTION_AUTHORITY_REQUIRED` for a request without an `authority_reference` and `EXECUTION_AUTHORITY_UNAVAILABLE` for a reference that no stored authority matches, and `POST /api/v1/research/projects` creates a project that stays `pending`. The model-backed requests in this guide, including the query in the [README](../README.md#submit-a-routed-query), therefore cannot run end to end on a fresh install. [Research execution](reference/05-research-execution.md#execution-authority) describes the mechanism: stored snapshot rows are loaded at startup, or a resolver is placed on `app.state`. A plan-backed route must also name the `deepseek`, `llama` or `openrouter` provider, because the `gemini` provider rejects every request ([LLM providers](reference/10-llm-providers.md)).

## Use the CLI

The `research-cli` entry point is declared in `pyproject.toml` (`src.cli.main:cli`).

```bash
export RESEARCH_API_URL=http://localhost:8000     # default when unset
export RESEARCH_AUTH_TOKEN="$TOKEN"               # sent as "Authorization: Bearer ..."
research-cli health                               # GET /health and GET /ready, no token needed
research-cli projects list                        # authenticated
```

`CLIConfig.from_env` (`src/cli/config.py`) reads `~/.research-cli.env` when it exists and `.env.cli` in the current directory otherwise; `.env.cli.example` lists the variables. Without `RESEARCH_AUTH_TOKEN`, authenticated commands receive 401. See [CLI and web clients](reference/13-cli-and-web-clients.md).

## API documentation pages

With `DEBUG` false the API serves no interactive documentation pages, and the nginx `/docs` location is a separate static root that returns 404, so it is not FastAPI's `/docs`.

`FastAPI(...)` in `src/api/main.py` sets `docs_url` and `redoc_url` to `/docs` and `/redoc` only when `DEBUG` is true, and leaves `openapi_url` at FastAPI's default `/openapi.json`. `AuthMiddleware` applies to all three paths, so a browser request without an `Authorization` header receives 401 (`tests/security/test_route_authentication_boundary.py` asserts this for `/docs`, `/redoc` and `/openapi.json`). Fetch the schema with a token:

```bash
curl -s http://localhost:8000/openapi.json -H "Authorization: Bearer $TOKEN"
```

## Smoke tests

Both smoke scripts currently fail their last check. They send the final `POST /api/v1/query/research` without a token and expect 200, so the route's `401` is reported as `FAIL` and the script exits 1 even when every earlier check passes.

`./scripts/smoke_test.sh` boots the API in a tmux session named `cerebro-smoke` on port 8099 with `DATABASE_URL=sqlite+aiosqlite:///./cerebro_smoke_test.db`. It generates an RSA key pair in `/tmp/cerebro_smoke_keys` with `.venv/bin/python3` and mints an access token itself (subject `00000000-0000-0000-0000-00000000beef`, organization `00000000-0000-0000-0000-00000000cafe`), so it does not call `register` or `login`. It then runs these checks:

- `GET /health`.
- Under `/api/v1/research/projects`: `POST`, `GET`, `GET .../progress`, list, `GET .../results` (expects 404), a missing project (expects 404) and `POST .../cancel` (expects 204).
- `POST /api/v1/query/research`.

Each check prints `PASS` or `FAIL` with the status code. The script prints `SMOKE TEST PASSED` and exits 0, or `SMOKE TEST FAILED` and exits 1, and on exit it removes the tmux session, the database file and the keys. It requires `.venv`, `tmux` and an `httpx` importable from `python3`.

`./scripts/smoke_test_postgres.sh` targets an already running Compose stack on `http://localhost:8000`. It registers a unique user through `/api/v1/auth/register`, logs in through `/api/v1/auth/login` and runs the same project checks with the login token, then the same final query check, which sends no token. The same pass and fail output and exit codes apply. Its start-up check is `docker compose ps | grep -q research-api`; the dev compose file sets no `container_name`, so the check matches only when the Compose project name makes the container name contain `research-api` (for example `COMPOSE_PROJECT_NAME=research`).

That route is not in `PUBLIC_ROUTE_ALLOWLIST`, and `query_api.router` declares `Depends(get_current_token)`, which is why the unauthenticated request receives 401.

## Code quality

```bash
ruff format --check src tests     # what CI runs; use "ruff format src tests" to rewrite
ruff check src tests              # what CI runs
mypy src                          # strict mode, from [tool.mypy]
make quality                      # make format lint type-check
```

- `make format` runs `black src tests` (rewrites files); `make lint` runs `ruff check src tests`; `make type-check` runs `mypy src`; `make quality-fix` runs `format` and `lint-fix`. CI and pre-commit format with `ruff format`, not `black`.
- `[tool.ruff]` sets `fix = true`, so `ruff check` rewrites fixable findings in place. Add `--no-fix` to only report.
- Pre-commit hooks (`.pre-commit-config.yaml`): `ruff` with `--fix --exit-non-zero-on-fix`, `ruff-format`, `trailing-whitespace`, `end-of-file-fixer`, `check-yaml`, `check-added-large-files` (`--maxkb=500`), `check-merge-conflict` and `detect-private-key`.
- Pre-commit pins ruff `v0.17.6`; the `dev` extra declares `ruff>=0.8.0` and CI installs it unpinned, so CI uses the newest release and the hook can lag behind it. Bump the pin in `.pre-commit-config.yaml` when it does. To reproduce CI findings run `uvx ruff@latest check src tests`.
- Web: `cd cerebro/web && npm run lint && npm run build` (`build` is `tsc -b && vite build`).

## Make targets

Targets whose recipes reference only files and services that exist:

| Target | Runs |
|---|---|
| `install` | `uv pip install -e ".[dev]"` |
| `install-pip` | `./scripts/setup-python-env.sh` |
| `dev`, `dev-build`, `dev-logs`, `dev-stop` | `./scripts/compose.sh up -d`, `up -d --build`, `logs -f`, `down` |
| `dev-clean` | `./scripts/compose.sh down -v --remove-orphans`, then `docker system prune -f` |
| `legacy-masr` | `./scripts/compose.sh --profile legacy-masr-service up -d masr-router` |
| `prod-build`, `prod-up`, `prod-down`, `prod-logs` | `./scripts/compose.sh -f docker-compose.production.yml` with `build`, `up -d`, `down`, `logs -f` |
| `db-init` | `./scripts/compose.sh exec api alembic upgrade head` |
| `db-migrate` | prompts for a message, then `alembic revision --autogenerate -m` inside `api` |
| `db-reset` | stops `postgres`, runs `docker volume rm cerebro_postgres_data` (the volume is named `<compose project>_postgres_data`, so the command works only when the checkout directory is named `cerebro`), starts `postgres`, sleeps 10 seconds, runs `make db-init` |
| `db-backup` | `pg_dump -U research research_db` inside `postgres` into `backups/backup_<timestamp>.sql` |
| `db-shell` | `psql -U research -d research_db` inside `postgres` |
| `test`, `test-unit`, `test-integration`, `test-e2e`, `test-coverage` | `pytest`, `pytest tests/unit -v`, `pytest tests/integration -v`, `pytest tests/e2e -v`, `pytest --cov=src --cov-report=html --cov-report=term` |
| `test-docker` | `docker compose -f tests/integration/docker-compose.test.yml up --build --abort-on-container-exit`; the file defines only `postgres` and `redis`, so no tests run |
| `lint`, `lint-fix`, `format`, `format-check`, `type-check`, `quality`, `quality-fix` | see [Code quality](#code-quality); `format-check` runs `black --check src tests` |
| `cli-health`, `cli-projects`, `cli-test` | `research-cli health`, `research-cli projects list`, `research-cli projects create --title "Test Project" --query "Test query" --domains "test"` |
| `masr-health` | `python -c` that constructs `MASRouter()` from `src/ai_brain/router/masr.py` and prints `health_check()` |
| `load-test` | `locust -f tests/load/locustfile.py --host=http://localhost:8000` (see the [testing guide](testing-guide.md#load-tests)) |
| `k8s-namespace`, `k8s-config`, `k8s-deploy`, `k8s-status`, `k8s-logs`, `k8s-clean` | `kubectl` against `k8s/namespace.yaml`, `k8s/configmap.yaml`, `k8s/` (kustomize) and namespace `research-platform`; `k8s-logs` follows `deployment/research-api` |
| `logs-api`, `logs-masr`, `logs-all` | `./scripts/compose.sh logs -f` for `api`, `masr-router` (with the `legacy-masr-service` profile) and all services |
| `clean`, `clean-docker` | delete `__pycache__`, `*.pyc` and tool caches; `docker system prune -f`, `docker volume prune -f`, `docker network prune -f` |
| `dev-workflow`, `ci-workflow`, `quick-test`, `quick-format` | `make format lint type-check test`; `make quality test test-docker`; `pytest tests/unit -x -v`; `black src tests && ruff check --fix src tests` |
| `docker-login`, `docker-push-dev`, `docker-push-prod` | `docker login` with `DOCKER_REGISTRY_TOKEN` and `DOCKER_REGISTRY`; `docker compose build` and `push` for the dev and production files |
| `version` | prints Python, Docker, `docker compose` and `kubectl` versions |
| `backup-all` | `make db-backup`, then a Redis RDB dump (`redis-cli --rdb` inside `redis`, copied out with `compose.sh cp`) into `backups/<date>/` |

### Targets that reference missing files

| Target | What is missing |
|---|---|
| `setup`, `env-help` | a Temporal service. They print `Temporal UI: http://localhost:8080` and `TEMPORAL_HOST=localhost:7233`; `docker-compose.yml` defines no Temporal service |
| `status` | a published MCP port. It curls `http://localhost:9000/health`; the `mcp-server` service publishes no host port |

## Repository layout

```
alembic/            Alembic environment and the revision scripts used by "alembic upgrade head"
cerebro/web/        React and TypeScript web workbench (Vite, Playwright specs in tests/e2e)
config/             Per-environment Python config modules and the example promotion criteria JSON
configs/models/     YAML model configuration (base, development, production)
docker/             API entrypoint, MASR service Dockerfile, init.sql, nginx reverse-proxy config
docs/               This documentation
evals/live/         Live-provider evaluation suite
examples/           Sample research papers and report (Markdown)
k8s/                Kubernetes manifests and kustomization
landing_page/       Static landing page
migrations/         Three revision scripts outside alembic's configured script location
scripts/            compose wrapper, smoke tests, test runners, evaluation entry points
src/
  agents/           Worker agents, supervisors, agent factory, communication and tool helpers
  ai_brain/         MASR router, LLM providers, memory tiers, experimentation, compaction, config
  api/              FastAPI app (main.py), routes/, auth/, middleware/, services/, websocket/
  auth/             JWT service, password service, auth request and response models
  cli/              research-cli (click commands, API client, formatters)
  core/             Settings, environment loading, kernel, contracts, capabilities, claims, tools
  evals/            Agent cost pilot: runner, trial setup, cost arithmetic (used by evals/live)
  mcp/              MCP server, client, registry, and tools
  middleware/       Authentication middleware and tenant-context dependencies
  models/           API models; db/ holds the SQLAlchemy models
  prompts/          Prompt manager, versioning, YAML prompt templates
  qa/               MAST labels and legacy annotation helpers
  reliability/      Retry strategies and service registry
  repositories/     Database repositories
  research_platform/ Entry point named by the research-platform script
  security/         Audit logger and middleware, security headers, content sanitizer, login throttle
  services/         Gemini service, report generation, exporters, hallucination detector
  templates/        Jinja report templates
  utils/            Async, serialization, and type-coercion helpers
tests/              Test suite (see the testing guide)
```
