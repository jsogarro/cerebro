# Deployment and monitoring

This guide covers the deployment artifacts that exist in this repository: one Dockerfile, two Docker Compose files, a Kustomize set under `k8s/`, a CD workflow, and the health and metrics endpoints the application exposes. For settings, see [configuration](reference/02-configuration.md).

## Deployment artifacts in this repository

| Artifact | Path | Purpose |
|---|---|---|
| Image definition | `Dockerfile` | Four stages: `base`, `development`, `builder`, `production` |
| Container entrypoint | `docker/entrypoint.sh` | Runs `alembic upgrade head`, then starts the command |
| Database init script | `docker/init.sql` | Extensions, schemas and types for a fresh Postgres container |
| Reverse proxy config | `docker/nginx/nginx.conf` | Routes `/api/` and `/ws` to the API and `/` to the web container; answers `/health` itself |
| Standalone MASR image | `docker/Dockerfile.masr` | Image for the legacy MASR service |
| Development stack | `docker-compose.yml` | API, MCP server, Postgres, Redis, nginx, web, optional tools |
| Production stack | `docker-compose.production.yml` | The same services with limits, secrets, Prometheus and Grafana |
| Kubernetes manifests | `k8s/` | Namespace, service account, config map, external secrets, deployment, services, ingress, autoscaler, network policy, Kustomize file |
| CD workflow | `.github/workflows/cd.yml` | Deploys to GKE with Kustomize; the Kustomize file does not build (see [Kubernetes](#kubernetes)) |
| Local runner | `start.sh` | Starts Uvicorn and the Vite dev server without containers |

`Makefile` wraps these: `make prod-build`, `make prod-up`, `make prod-down` and `make prod-logs` run `docker-compose.production.yml` through `scripts/compose.sh`, which calls `docker compose` with `.env` and `~/.env` as env files when they exist. `make k8s-deploy` runs `kubectl apply -k k8s/`, which fails because the kustomization does not build (see [Kubernetes](#kubernetes)).

## Container image

`Dockerfile` pins the Python 3.11 slim base image by digest and defines these stages:

| Stage | Contents | Command |
|---|---|---|
| `base` | `gcc`, `g++`, `curl`, `git`, `uv` | none |
| `development` | `pip install -e ".[dev,mcp]"`, `src/`, `tests/`, `alembic/`, entrypoint | `uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload` |
| `builder` | `pip install -e .` and `src/` | none |
| `production` | Site-packages, `alembic`, `uvicorn`, `src/`, `alembic/` copied from `builder`; runs as the non-root user `app` | `uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --workers 4` |

The `production` stage declares a healthcheck that runs `curl -f http://localhost:8000/health` every 30 seconds (timeout 3 seconds, start period 40 seconds, 3 retries).

Both runnable stages use `docker/entrypoint.sh` as the entrypoint. It runs `alembic upgrade head` and, if that fails, prints the exit status and exits with it, so the container never starts the application against an unmigrated schema. It exits with status 64 when no command is supplied. Otherwise it `exec`s the command.

Build locally with `docker compose build` (development stage, `docker-compose.yml`) or `make prod-build` (production stage). No workflow in this repository pushes images to a registry.

## Docker Compose (production file)

`docker-compose.production.yml` defines these services:

| Service | Image or build | Host port | Notes |
|---|---|---|---|
| `api` | `Dockerfile`, target `production` | 8000 | Memory limit 2G, `read_only: true`, `tmpfs` for `/tmp` and `/var/cache/nginx` |
| `masr-router` | `docker/Dockerfile.masr` | 9100 | Profile `legacy-masr-service`; see [legacy MASR service](#legacy-standalone-masr-service) |
| `mcp-server` | `Dockerfile`, target `production`, command `python -m src.mcp.server` | 9000 | Uses Redis database 2. `src/mcp/server.py` has no `__main__` block and `src/mcp` has no `__main__.py`, so that command imports the module and exits, nothing listens on 9000, and `restart: always` restarts it in a loop. The production target also installs the package without the `[mcp]` extra, and the service passes no `SECRET_KEY` or `DATABASE_URL` to the image entrypoint's `alembic upgrade head`. |
| `postgres` | `postgres:16-alpine` | 5432 | Volume `postgres_data_prod`, user `research`, database `research_db` |
| `redis` | `redis:7-alpine` | 6379 | Started with `--requirepass ${REDIS_PASSWORD}`, volume `redis_data_prod` |
| `web` | `cerebro/web/Dockerfile` | none | Built with `VITE_API_URL=/api/v1` |
| `nginx` | `nginx:alpine` | 80, 443 | Mounts `docker/nginx/nginx.conf` |
| `prometheus` | `prom/prometheus:latest` | 9090 | 90-day retention |
| `grafana` | `grafana/grafana:latest` | 3000 | Admin password from `GRAFANA_PASSWORD` |

Variables the file reads from the environment or an env file: `DB_PASSWORD`, `REDIS_PASSWORD`, `SECRET_KEY`, `CORS_ORIGINS` (a JSON array, for example `["https://app.example"]`), `GRAFANA_PASSWORD`, and (optional, empty by default) `GEMINI_API_KEY`. Compose interpolates an unset variable among these to an empty string with a warning, so they are required in effect: an empty `SECRET_KEY` fails the application's 32-character check, and an empty `DB_PASSWORD` makes the Postgres container refuse to start. `JWT_PRIVATE_KEY_FILE` and `JWT_PUBLIC_KEY_FILE` are the only two enforced by Compose: it refuses to start without them. They point at the RSA PEM files, which Compose mounts into the `api` container as `/run/secrets/jwt_private.pem` and `/run/secrets/jwt_public.pem`; the service sets `JWT_PRIVATE_KEY_PATH` and `JWT_PUBLIC_KEY_PATH` to those paths. The file also declares `db_password`, `redis_password` and `gemini_api_key` secrets that read from `./secrets/*.txt`; no service references them.

The `api` service sets `GUNICORN_WORKERS`, `GUNICORN_WORKER_CLASS`, `GUNICORN_MAX_REQUESTS` and `GUNICORN_MAX_REQUESTS_JITTER`. Nothing in the image or the application reads them: the image runs Uvicorn with `--workers 4`, and the service does not override the command.

Several files the production compose file mounts are not in the repository: `docker/postgresql.conf`, `docker/redis.conf`, `docker/nginx/conf.d/`, `docker/ssl/`, `docker/prometheus/prometheus.yml` and `docker/grafana/provisioning/`. Create them, or remove the corresponding `volumes` entries and `command` flags, before `docker compose -f docker-compose.production.yml up`.

`docker/nginx/nginx.conf` rate-limits `/api/` (10 requests per second per client address, burst 20) and `/ws` (5 per second, burst 10), proxies `/api/` and `/ws` to `api:8000`, answers `/health` with a fixed `200 healthy` that never reaches the API, serves `/docs` from its own static root (404 there, so FastAPI's `/docs` is not reachable through the proxy even with `DEBUG=true`), and sends everything else to `web:8080`. The `/ws` location keeps connections open for up to 86400 seconds.

The `api` service has no `volumes` entry. `SOURCE_SNAPSHOT_DIR` (default `data/source_snapshots`, resolved against the working directory) holds source files that evidence rows cite by digest, and the application expects it on a persistent volume. With `read_only: true`, mount a writable volume at that path and set `SOURCE_SNAPSHOT_DIR` to it.

## Kubernetes

`k8s/kustomization.yaml` lists these manifests, all in the namespace `research-platform`. The kustomization does not build: `kubectl kustomize k8s/` and `kustomize build k8s/` fail with `merging from generator ... ConfigMap research-platform-config does not exist; cannot merge or replace`, because the `configMapGenerator` uses `behavior: merge` against a namespaced base ConfigMap. `ci.yml` omits the build step for the same reason. `kubectl apply -k k8s/` therefore fails, and so does every `cd.yml` step that runs `kustomize build`. The manifests are:

| Manifest | Defines |
|---|---|
| `namespace.yaml` | The `research-platform` namespace |
| `serviceaccount.yaml` | Service account `research-platform-sa` (with a GKE workload-identity annotation) and a read-only `Role` and `RoleBinding` |
| `configmap.yaml` | `research-platform-config`: `ENVIRONMENT`, `LOG_LEVEL`, `MAX_REQUESTS_PER_MINUTE`, `ENABLE_RATE_LIMITING` and related values the API container loads with `envFrom` |
| `external-secrets.yaml` | Two `ExternalSecret` resources that build `research-platform-secrets` (`GEMINI_API_KEY`, `SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`, `TEMPORAL_HOST`) and `research-platform-jwt-keys` (`jwt_private.pem`, `jwt_public.pem`) from a `ClusterSecretStore` named `research-platform-secret-store` |
| `deployment-api.yaml` | Deployment `research-api`: 3 replicas, non-root user 1000, read-only root filesystem, `/tmp` as an `emptyDir`, JWT keys mounted read-only at `/secrets`, resource requests 512Mi/250m and limits 2Gi/1000m |
| `service.yaml` | `ClusterIP` services `research-api-service` (port 80 to 8000) and `mcp-service` (port 9000) |
| `ingress.yaml` | A GCE ingress for `api.research-platform.example.com` and a managed certificate |
| `hpa.yaml` | `research-api-hpa`: 3 to 20 replicas on 70% CPU and 80% memory |
| `networkpolicy.yaml` | Ingress to the API pods on 8000; egress to Postgres (5432), Redis (6379), Temporal (7233), DNS, and HTTPS outside the metadata address |

No `secrets.yaml` exists in `k8s/`; secrets come from `external-secrets.yaml`, so the External Secrets Operator and the `ClusterSecretStore` must exist in the cluster first. `k8s/` has no Deployment for Postgres, Redis or an MCP server; `DATABASE_URL` and `REDIS_URL` come from the secret store, and `mcp-service` selects pods labelled `app: mcp-server`, which no manifest creates. The deployment mounts only `/tmp` and the JWT keys, so `SOURCE_SNAPSHOT_DIR` needs a volume added to it.

The image reference in `deployment-api.yaml` is `gcr.io/PROJECT_ID/research-platform-api:latest`. The Kustomize `images` entry changes only the tag, so `PROJECT_ID` stays literal until something replaces the whole name; `cd.yml` does this with `kustomize edit set image`. The deployment's probes call `/health` (liveness) and `/ready` (readiness) on port 8000.

Make targets `k8s-namespace` and `k8s-config` apply files that exist. `k8s-deploy` and `k8s-clean` fail because the kustomization does not build. `k8s-secrets` applies `k8s/secrets.yaml`, which does not exist. `k8s-status` and `k8s-logs` use the namespace `cerebro` and the deployment name `cerebro-api`, which match nothing in the manifests. Use `kubectl -n research-platform get pods,services,ingress` and `kubectl -n research-platform logs -f deployment/research-api` instead.

## Continuous deployment

`.github/workflows/cd.yml` runs on tags matching `v*`, pushes to `develop`, and manual dispatch (inputs `environment`, `skip_tests`). The `determine-environment` job maps a manual dispatch to its input, `develop` to `staging`, and a `v*` tag to `production`.

1. `pre-deployment-checks` (skipped when `skip_tests` is `true`) installs the project, runs `pytest tests/test_api.py::TestHealthEndpoints`, and runs `kustomize build k8s/`, which fails (see [Kubernetes](#kubernetes)).
2. `deploy-staging` authenticates to Google Cloud with `GCP_SA_KEY`, fetches GKE credentials, runs `kustomize edit set image` for `research-platform-api` and `research-platform-web` under `gcr.io/<GCP_PROJECT_ID>/`, applies the result, waits for `deployment/research-api` to roll out, then requests `/health` and `/ready` through the ingress address.
3. `deploy-production` runs only for a `refs/tags/v*` ref. It reads the selector of a Service named `research-platform-active`, deploys with a `color` label of the opposite colour, runs `curl` against `/health` and `/ready` inside the new pod, patches `research-platform-active` to the new colour, and creates a GitHub release.
4. `rollback` runs when `deploy-production` fails and patches `research-platform-active` back to the colour recorded in the `last-stable-version` annotation.

The workflow tags images with the Git tag (or commit SHA for staging) but contains no step that builds or pushes them, and no manifest in `k8s/` defines `research-platform-active`. The production verification step requests `/metrics` without an `Authorization` header, which the authentication middleware rejects (see [monitoring](#monitoring)).

## Health and readiness

The router in `src/api/routes/health.py` is mounted without a prefix:

| Path | Behaviour | Authentication |
|---|---|---|
| `GET /health` | Returns `{"status": "healthy", "service": "research-platform-api"}` without checking dependencies | none |
| `GET /live` | Returns `{"status": "alive"}` | none |
| `GET /ready` | Runs `SELECT 1` against Postgres and a Redis `PING`, each with a 2-second timeout. Returns 200 with `"status": "ready"` when both succeed, otherwise 503 with `"status": "not_ready"` and a `checks` object of `ok` or `unavailable` per dependency | none |
| `GET /ws/health` | WebSocket service probe | none |

These four paths are the only unauthenticated routes besides the six authentication bootstrap routes in `PUBLIC_ROUTE_ALLOWLIST` in `src/middleware/auth_middleware.py`. The Dockerfile healthcheck and both compose files call `/health`; the Kubernetes deployment uses `/health` for liveness and `/ready` for readiness. The rate limiter skips `/health`, `/ready`, `/live`, `/metrics`, `/docs` and `/openapi.json`. For the authentication model, see [authentication and tenancy](reference/04-authentication-and-tenancy.md); for the metrics and audit surfaces, see [security, audit and observability](reference/12-security-audit-observability.md).

## Monitoring

`src/api/main.py` mounts the Prometheus ASGI app from `prometheus_client` at `/metrics`. The path is not in `PUBLIC_ROUTE_ALLOWLIST`, so a scraper must send `Authorization: Bearer <access token>`. A Prometheus scrape job therefore needs a bearer token (`authorization` credentials in the scrape config) from a user whose token has not expired; access tokens last 15 minutes by default (`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`).

The series exported are the `llm_*` metrics defined in `src/core/observability.py`; the table of series and labels is in [security, audit and observability](reference/12-security-audit-observability.md). Interactive API documentation at `/docs` and `/redoc` is served only when `DEBUG=true`, and those routes also require a token.

The production compose file runs Prometheus and Grafana containers. Their configuration files are not in the repository (see [Docker Compose](#docker-compose-production-file)), so scrape targets, dashboards and data sources are yours to provide.

Logs go to standard output through structlog; `LOG_LEVEL` sets verbosity. Read them with `docker compose logs -f api` (or `make logs-api` for the development stack) and `kubectl -n research-platform logs -f deployment/research-api`.

## Legacy standalone MASR service

`masr-router` is an optional container behind the `legacy-masr-service` profile (`make legacy-masr` starts it). The API does not call it: routing runs in-process in the `api` container. See [MASR routing](reference/06-masr-routing.md).
