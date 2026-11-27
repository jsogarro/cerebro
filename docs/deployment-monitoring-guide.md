# Deployment and monitoring

This guide covers the deployment artifacts that exist in this repository: one Dockerfile, two Docker Compose files, a Kustomize set under `k8s/`, a CD workflow and the health and metrics endpoints the application exposes. For settings, see [configuration](reference/02-configuration.md).

## Deployment artifacts in this repository

| Artifact | Path | Purpose |
|---|---|---|
| Image definition | `Dockerfile` | Four stages: `base`, `development`, `builder`, `production` |
| Container entrypoint | `docker/entrypoint.sh` | Runs `alembic upgrade head`, then starts the command |
| Database init script | `docker/init.sql` | Extensions, schemas and types for a fresh Postgres container |
| Reverse proxy config | `docker/nginx/nginx.conf` | Routes `/api/` and `/ws` to the API and `/` to the web container; answers `/health` itself |
| Development stack | `docker-compose.yml` | API, Postgres, Redis, nginx, web, optional tools |
| Production stack | `docker-compose.production.yml` | The same services with limits, secrets, Prometheus and Grafana |
| Kubernetes manifests | `k8s/` | Namespace, service account, config map, external secrets, deployment, services, ingress, autoscaler, network policy, Kustomize file |
| CD workflow | `.github/workflows/cd.yml` | Deploys to GKE with Kustomize; the Kustomize file does not build (see [Kubernetes](#kubernetes)) |
| Local runner | `start.sh` | Starts Uvicorn and the Vite dev server without containers |

`Makefile` wraps these: `make prod-build`, `make prod-up`, `make prod-down` and `make prod-logs` run `docker-compose.production.yml` through `scripts/compose.sh`, which calls `docker compose` with `.env` and `~/.env` as env files when they exist. `make k8s-deploy` runs `kubectl apply -k k8s/`, which fails because the kustomization does not build (see [Kubernetes](#kubernetes)).

## Container image

`Dockerfile` pins the Python 3.13 slim base image by digest and defines these stages:

| Stage | Contents | Command |
|---|---|---|
| `base` | `gcc`, `g++`, `curl`, `git`, `uv` | none |
| `development` | `pip install -e ".[dev,mcp]"`, `src/`, `tests/`, `alembic/`, entrypoint | `uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload` |
| `builder` | `scripts/install-locked.sh --extra stats` and `src/` | none |
| `production` | Site-packages, `alembic`, `uvicorn`, `src/`, `alembic/` copied from `builder`; runs as the non-root user `app` | `uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --workers 1` |

The `production` stage declares a healthcheck that runs `curl -f http://localhost:8000/health` every 30 seconds (timeout 3 seconds, start period 40 seconds, 3 retries).

Both runnable stages use `docker/entrypoint.sh` as the entrypoint. It runs `alembic upgrade head` and, if that fails, prints the exit status and exits with it, so the container never starts the application against an unmigrated schema. It exits with status 64 when no command is supplied. Otherwise it `exec`s the command.

Build locally with `docker compose build` (development stage, `docker-compose.yml`) or `make prod-build` (production stage). The `build-image` job of `cd.yml` pushes the API image to `gcr.io`.

## Docker Compose (production file)

`docker-compose.production.yml` defines these services:

| Service | Image or build | Host port | Notes |
|---|---|---|---|
| `api` | `Dockerfile`, target `production` | 8000 | Memory limit 2G, `read_only: true`, `tmpfs` for `/tmp` and `/var/cache/nginx` |
| `postgres` | `postgres:16-alpine` | 5432 | Volume `postgres_data_prod`, user `research`, database `research_db` |
| `redis` | `redis:7-alpine` | 6379 | Started with `--requirepass ${REDIS_PASSWORD}`, volume `redis_data_prod` |
| `web` | `cerebro/web/Dockerfile` | none | Built with `VITE_API_URL=/api/v1` |
| `nginx` | `nginx:alpine` | 80, 443 | Mounts `docker/nginx/nginx.conf` |
| `prometheus` | `prom/prometheus:latest` | 9090 | 90-day retention |
| `grafana` | `grafana/grafana:latest` | 3000 | Admin password from `GRAFANA_PASSWORD` |

The file reads these variables from the environment or an env file: `DB_PASSWORD`, `REDIS_PASSWORD`, `SECRET_KEY`, `CORS_ORIGINS` (a JSON array, for example `["https://app.example"]`), `GRAFANA_PASSWORD` and (optional, empty by default) `GEMINI_API_KEY`. Compose interpolates an unset variable among these to an empty string with a warning, so they are required in effect: an empty `SECRET_KEY` fails the application's 32-character check, and an empty `DB_PASSWORD` makes the Postgres container refuse to start.

`JWT_PRIVATE_KEY_FILE` and `JWT_PUBLIC_KEY_FILE` are the only two variables Compose enforces: it refuses to start without them. They point at the RSA PEM files, which Compose mounts into the `api` container as `/run/secrets/jwt_private.pem` and `/run/secrets/jwt_public.pem`; the service sets `JWT_PRIVATE_KEY_PATH` and `JWT_PUBLIC_KEY_PATH` to those paths.

The file also declares `db_password`, `redis_password` and `gemini_api_key` secrets that read from `./secrets/*.txt`; no service references them.

The `api` service sets `GUNICORN_WORKERS`, `GUNICORN_WORKER_CLASS`, `GUNICORN_MAX_REQUESTS` and `GUNICORN_MAX_REQUESTS_JITTER`. Nothing in the image or the application reads them: the image runs Uvicorn with `--workers 1`, and the service does not override the command.

Several files the production compose file mounts are not in the repository: `docker/postgresql.conf`, `docker/redis.conf`, `docker/nginx/conf.d/`, `docker/ssl/`, `docker/prometheus/prometheus.yml` and `docker/grafana/provisioning/`. Create them, or remove the corresponding `volumes` entries and `command` flags, before `docker compose -f docker-compose.production.yml up`.

`docker/nginx/nginx.conf` rate-limits `/api/` (10 requests per second per client address, burst 20) and `/ws` (5 per second, burst 10), proxies `/api/` and `/ws` to `api:8000`, answers `/health` with a fixed `200 healthy` that never reaches the API, serves `/docs` from its own static root (404 there, so FastAPI's `/docs` is not reachable through the proxy even with `DEBUG=true`) and sends everything else to `web:8080`. The `/ws` location keeps connections open for up to 86400 seconds.

The `api` service mounts the named volume `source_snapshots_prod` at `/var/lib/cerebro/source_snapshots`. The production image sets that path as the default `SOURCE_SNAPSHOT_DIR` and creates it owned by the `app` user, so the volume is writable under `read_only: true` and the snapshots survive container replacement.

## Kubernetes

`k8s/kustomization.yaml` lists these manifests, all in the namespace `research-platform`. The kustomization does not build: `kubectl kustomize k8s/` and `kustomize build k8s/` fail with `merging from generator ... ConfigMap research-platform-config does not exist; cannot merge or replace`, because the `configMapGenerator` uses `behavior: merge` against a namespaced base ConfigMap. `ci.yml` omits the build step for the same reason. `kubectl apply -k k8s/` therefore fails, and so does every `cd.yml` step that runs `kustomize build`. The manifests are:

| Manifest | Defines |
|---|---|
| `namespace.yaml` | The `research-platform` namespace |
| `serviceaccount.yaml` | Service account `research-platform-sa` (with a GKE workload-identity annotation) and a read-only `Role` and `RoleBinding` |
| `configmap.yaml` | `research-platform-config`: `ENVIRONMENT`, `LOG_LEVEL`, `MAX_REQUESTS_PER_MINUTE`, `ENABLE_RATE_LIMITING` and related values the API container loads with `envFrom` |
| `external-secrets.yaml` | Three `ExternalSecret` resources (including `research-platform-metrics-token`, a bearer token for the `/metrics` scraper) that build `research-platform-secrets` (`GEMINI_API_KEY`, `SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`, `TEMPORAL_HOST`) and `research-platform-jwt-keys` (`jwt_private.pem`, `jwt_public.pem`) from a `ClusterSecretStore` named `research-platform-secret-store` |
| `deployment-api.yaml` | Deployment `research-api`: 3 replicas, non-root user 1000, read-only root filesystem, `/tmp` as an `emptyDir`, JWT keys mounted read-only at `/secrets`, resource requests 512Mi/250m and limits 2Gi/1000m |
| `service.yaml` | `ClusterIP` services `research-api-service` (port 80 to 8000) |
| `ingress.yaml` | A GCE ingress for `api.research-platform.example.com` and a managed certificate |
| `hpa.yaml` | `research-api-hpa`: 3 to 20 replicas on 70% CPU and 80% memory |
| `networkpolicy.yaml` | Ingress to the API pods on 8000; egress to Postgres (5432), Redis (6379), Temporal (7233), DNS and HTTPS outside the metadata address |

No `secrets.yaml` exists in `k8s/`; secrets come from `external-secrets.yaml`, so the External Secrets Operator and the `ClusterSecretStore` must exist in the cluster first. `k8s/` has no Deployment for Postgres or Redis; `DATABASE_URL` and `REDIS_URL` come from the secret store. The deployment mounts `/tmp`, the JWT keys and the ReadWriteMany claim `research-platform-source-snapshots` (`pvc-source-snapshots.yaml`) at `/var/lib/cerebro/source_snapshots`; set `storageClassName` in that file to a class that supports ReadWriteMany.

The image reference in `deployment-api.yaml` is `gcr.io/PROJECT_ID/research-platform-api`, and the Kustomize `images` entry pins it to a placeholder all-zero digest. `PROJECT_ID` and the digest stay placeholders until `cd.yml` replaces both with `kustomize edit set image`; no tag is ever deployed. The deployment's probes call `/health` (liveness) and `/ready` (readiness) on port 8000.

Make targets `k8s-namespace`, `k8s-config`, `k8s-deploy` and `k8s-clean` apply or delete files that exist, and `k8s-status` and `k8s-logs` use the namespace `research-platform` and `deployment/research-api`. `kubectl kustomize k8s` builds; CI runs it.

## Continuous deployment

`.github/workflows/cd.yml` runs on tags matching `v*`, pushes to `develop` and manual dispatch (inputs `environment`, `skip_tests`). The `determine-environment` job maps a manual dispatch to its input, `develop` to `staging` and a `v*` tag to `production`.

1. `pre-deployment-checks` (skipped when `skip_tests` is `true`) installs the project, runs `pytest tests/test_api.py::TestHealthEndpoints` and runs `python scripts/verify_deployment_assets.py k8s`, which renders `k8s/` and checks that Services, the Ingress, the HPA, volumes and Secrets reference rendered objects and that every image is pinned by digest.
2. `build-image` builds the `production` target of the `Dockerfile` and pushes it to `gcr.io/<GCP_PROJECT_ID>/research-platform-api`, tagged with the Git tag (or the commit SHA outside a tag), and exports the pushed image digest. The deploy jobs consume that digest, not the tag.
3. `deploy-staging` authenticates to Google Cloud with `GCP_SA_KEY`, fetches GKE credentials, runs `kustomize edit set image` for `research-platform-api`, applies the result, waits for `deployment/research-api` to roll out, then requests `/health` and `/ready` through the ingress address.
4. `deploy-production` runs only for a `refs/tags/v*` ref. It applies the same way, waits for the rollout, requests `/health` and `/ready` through the ingress address and creates a GitHub release.
5. `rollback` runs when `deploy-production` fails and runs `kubectl rollout undo deployment/research-api`.

No manifest in `k8s/` deploys a web image, so the workflow builds none. The verification steps do not request `/metrics`, which needs the metrics credential (see [monitoring](#monitoring)).

## Health and readiness

The probes `GET /health`, `GET /live`, `GET /ready` and `GET /ws/health` are unauthenticated; their bodies and the public-route allowlist are in [HTTP API](reference/03-http-api.md#health-and-metrics), and what `/ready` checks is in [security, audit and observability](reference/12-security-audit-observability.md#health-probes). The Dockerfile healthcheck calls `/health`; the `api` service healthcheck in both compose files calls `/ready`, and the `nginx` service healthchecks call nginx's proxied `/health`; the Kubernetes deployment uses `/health` for liveness and `/ready` for readiness. `/health` checks no dependency, so use `/ready` to confirm Postgres, Redis, the audit logger and the JWT keys. The rate limiter skips these paths.

## Monitoring

`src/api/main.py` mounts the Prometheus ASGI app from `prometheus_client` at `/metrics`. `ENABLE_METRICS=false` leaves it unmounted. `AuthMiddleware` admits only an access token whose `aud` claim is `cerebro-metrics` and whose permissions include `metrics:read`: anonymous requests and tenant tokens (which carry no audience) get `401`, a token with another audience gets `401`, and the right audience without the permission gets `403`. The metrics token is rejected on every other route. Mint it with `JWTService.generate_token_pair(..., permissions=["metrics:read"], additional_claims={"aud": "cerebro-metrics"})` and store it under the remote key `research-platform/metrics-token`; the `research-platform-metrics-token` Secret then holds it in `token`. Access tokens last 15 minutes by default (`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`), so the stored token must be refreshed on that cadence or minted with a longer lifetime.

The series exported are the `llm_*` metrics defined in `src/core/observability.py`; the table of series and labels is in [security, audit and observability](reference/12-security-audit-observability.md). Interactive API documentation at `/docs` and `/redoc` is served only when `DEBUG=true`, and those routes also require a token.

The production compose file runs Prometheus and Grafana containers. Their configuration files are not in the repository (see [Docker Compose](#docker-compose-production-file)), so you must supply the scrape targets, dashboards and data sources.

Logs go to standard output through structlog; `LOG_LEVEL` sets the minimum level ([configuration](reference/02-configuration.md)). Read them with `docker compose logs -f api` (or `make logs-api` for the development stack) and `kubectl -n research-platform logs -f deployment/research-api`.
