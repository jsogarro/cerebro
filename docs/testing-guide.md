# Testing

The Python test suite under `tests/`, the live-provider evaluations under `evals/live/`, the web Playwright specs under `cerebro/web/tests/e2e/`, and the CI workflows that run them. For installing the tools see the [development setup guide](development-setup-guide.md).

## Layout

| Path | Contents |
|---|---|
| `tests/test_*.py` | Flat module-level tests for the API, agents, MASR routing, supervisors, reports, authentication, WebSocket, CLI, and the durable-run layers |
| `tests/unit/` | Tests grouped by package: `agents` (with `integrations`), `ai_brain` (with `experimentation`, `integration`, `router`), `api` (with `services`), `core` (with `acquisition`, `claims`, `contracts`, `kernel`, `tools`), `models/db`, `qa`, `repositories`, `services` |
| `tests/agents/` | Supervisor revision and TalkHier provenance tests |
| `tests/architecture/` | Static ratchets: `test_module_reachability.py` and `test_settings_read_sites.py` |
| `tests/characterization/` | Baselines that pin the public query, agent, routing, MCP, and CLI surfaces, including `test_public_route_manifest.py` |
| `tests/docker/` | Contract tests for `docker-compose.yml`, the Dockerfiles, the entrypoint script, `scripts/compose.sh`, and `k8s/` |
| `tests/e2e/` | Python API tests against a running stack on `http://localhost:8000` |
| `tests/integration/` | Tests that need PostgreSQL and Redis containers |
| `tests/load/` | Locust file |
| `tests/regression/` | Golden-dataset and hallucination-detector regression tests |
| `tests/security/` | Authentication boundary, tenant isolation, rate limit, audit, and the adversarial corpus in `security/adversarial/` |
| `tests/factories/`, `tests/fixtures/`, `tests/utils/` | Shared test data and helpers ([Factories and fixtures](#factories-and-fixtures)) |
| `evals/live/` | Live-provider evaluation suite ([Live evaluations](#live-evaluations)) |
| `cerebro/web/tests/e2e/` | Playwright specs for the web workbench |

## Pytest configuration

From `[tool.pytest.ini_options]` in `pyproject.toml`:

- `addopts = "-ra -q --strict-markers --cov=src --cov-report=term-missing -m 'not live_eval'"`. Every run measures coverage of `src`, and `live_eval` tests are deselected. Passing `-m` on the command line replaces the `-m` in `addopts`.
- `testpaths = ["tests"]`, `python_files = "test_*.py"`, `asyncio_mode = "auto"`, `asyncio_default_fixture_loop_scope = "function"`.
- `timeout = 120` with `timeout_method = "thread"`: a test that runs longer than 120 seconds fails with a stack dump.
- `--strict-markers` rejects any marker that is not registered. Registered markers: `acceptance`, `integration`, `unit`, `e2e`, `slow`, `asyncio`, `live_eval`. Plugins register others, such as `benchmark` from pytest-benchmark. In the repository, `integration` is applied by modules in `tests/integration/` and a few others, `slow` by the three migration modules in `tests/integration/`, `acceptance` by `tests/test_devops_hardening_characterization.py`, and `live_eval` by the `evals/live` modules; no test applies `unit` or `e2e`, so `-m unit` and `-m e2e` select nothing.
- `[tool.coverage.report]` sets `precision`, `show_missing`, and `skip_covered` but no `fail_under`; a plain `pytest` run does not fail on coverage.

`tests/conftest.py` applies to every test:

- At import it sets `CEREBRO_DISABLE_DOTENV=true`, `ENVIRONMENT=test`, `DATABASE_URL=sqlite+aiosqlite:///:memory:`, `REDIS_URL=redis://localhost:6379/15`, a test `SECRET_KEY`, and `ENABLE_RATE_LIMITING=false`.
- The autouse fixture `install_test_audit_logger` puts an in-memory audit logger on `app.state.audit_logger` for each test and restores the previous value afterwards.
- The autouse fixture `block_external_sockets` fails any test that opens a non-loopback socket, including when the code under test swallows the error. Tests under `tests/integration/` or `tests/e2e/` and tests marked `live_eval`, `integration`, or `e2e` are exempt. `tests/test_no_network_in_unit_tests.py` covers it.
- Fixtures: `async_client` (an `httpx.AsyncClient` over `ASGITransport` with a SQLite file database at `/tmp/cerebro_async_client_test.db` and the SQLite-compatible tables `ResearchProject`, `ResearchResult`, `WorkflowCheckpoint`), `db_session` (in-memory SQLite), `mock_gemini_client`, `mock_temporal_client`, `sample_research_query`, `sample_research_project`.

## Running tests

Unit tests (no Docker):

```bash
pytest tests/unit -v
pytest tests/test_api.py
pytest tests/test_cli.py tests/test_cli_response_contract.py
```

A bare `pytest` also collects `tests/integration/` and `tests/e2e/`. The first needs a Docker daemon and the second needs a running API, so exclude them for a local unit run: `pytest --ignore=tests/integration --ignore=tests/e2e`. CI's `test` job uses `--ignore=tests/e2e` and its own `integration-tests` job runs `tests/integration/`.

**Integration** (`tests/integration/`). `tests/integration/conftest.py` starts `postgres:16-alpine` and `redis:7-alpine` with testcontainers (`postgres_container`, `redis_container`, session scope), builds `test_engine` with `Base.metadata.create_all` and drops the tables afterwards, and exposes `db_session`, `redis_client` (database 15), `jwt_service` (in-memory keys), `authenticated_client`, `admin_client`, `seed_test_data`, `test_data_factory`, `mock_gemini_service`, and `mock_mcp_client`. The autouse fixtures `cleanup_database` and `cleanup_redis` depend on `db_session` and `redis_client`, so every test in the directory starts the containers and needs a Docker daemon.

```bash
pytest tests/integration -v
pytest tests/integration/test_run_lifecycle_repository.py -v
```

`tests/integration/docker-compose.test.yml` defines `postgres` (host port 5433, user `test_user`, database `test_research_db`) and `redis` (host port 6380) and no other service. The `docker_compose` fixture in `conftest.py` starts it, but no test requests that fixture. `make test-docker` starts the same file without running tests.

**End-to-end Python tests** (`tests/e2e/`: `test_auth_e2e.py`, `test_research_project_e2e.py`, `test_tenant_isolation_e2e.py`, `test_websocket_e2e.py`). They use `httpx` (and `httpx_ws` for WebSockets) against the hard-coded `BASE_URL = "http://localhost:8000"`, with no browser. Start the stack, then run:

```bash
make dev
pytest tests/e2e/ --no-cov -v
```

**Route authentication boundary.** `tests/security/test_route_authentication_boundary.py` checks `PUBLIC_ROUTE_ALLOWLIST`, that every mounted HTTP route, the `/metrics` mount, and `/docs`, `/redoc`, `/openapi.json` return 401 without a token, and the mounted WebSocket routes. `tests/security/test_every_route_declares_auth_dependency.py` checks that each non-public route resolves `get_current_token` in its own dependency tree. `tests/security/test_routes_reject_anonymous_without_middleware.py` and `tests/characterization/test_public_route_manifest.py` cover the same boundary from the route side.

```bash
pytest tests/security -v
```

**Adversarial corpus.** `tests/security/adversarial/` holds the scenarios (`scenarios/`), the registry, and the harness that runs them against the real tool boundary. Verdicts are `HELD`, `HELD_VIA_FLOOR`, `VIOLATED`, and `NOT_EXERCISABLE` (`harness.py`). `test_corpus_execution.py` records a violated guarantee as `xfail(strict=True)`, so fixing the defect turns the test red until the marker is removed.

**Regression.** `tests/regression/test_golden_dataset.py` validates `tests/fixtures/golden_dataset.json` (at least 50 cases) and scores agent outputs through `src/services/evaluation_regression.py`; `test_hallucination_detector.py` checks `HallucinationDetector`. CI runs them first with `pytest -o addopts='' tests/regression -q`.

**Architecture.** `tests/architecture/test_module_reachability.py` builds a static import graph from `src.api.main`, `src.cli.main`, and `src.mcp.server` and fails on a module under `src/` that none of them reach, apart from the entries in its `KNOWN_UNREACHABLE` set. `tests/architecture/test_settings_read_sites.py` fails on a setting in `src/core/config.py` that nothing reads, apart from `KNOWN_UNREAD`. Both sets can only shrink: an entry that has become reachable or read also fails.

**Collection guards.** `tests/test_collection_is_not_silently_empty.py` fails when a test module collects no tests, and `tests/test_dev_extra_covers_test_deps.py` fails when a module that a test passes to `pytest.importorskip` is not installed by the `dev` extra.

**Containers and manifests.** `tests/docker/` checks `docker-compose.yml`, the Dockerfiles, `k8s/`, `docker/entrypoint.sh` (run against a stub `alembic`), and `scripts/compose.sh` (run against a stub `docker`). No Docker daemon is needed.

## Factories and fixtures

`tests/factories/`:

| Module | Classes |
|---|---|
| `user_factory.py` | `UserFactory` (`factory_boy`; classmethods `create_admin`, `create_researcher`, `create_viewer`, `create_unverified`, `create_inactive`, `create_with_custom_password`, `create_batch_with_roles`) and `TestUserGenerator` (`create_organization_users`, `create_test_authentication_scenarios`) |
| `project_factory.py` | `ResearchProjectFactory`, `ResearchResultFactory` |
| `agent_factory.py` | `MockAgentResponseFactory`, `MockGeminiResponseFactory`, `MockMCPToolFactory` |

There is no API-key factory.

`tests/fixtures/`: `golden_dataset.json`; `authenticated_caller.py` (`allow_authenticated(app)`, which overrides the token dependency with a fixed `TokenPayload`); `agent_system/baseline.py` (route and failure-taxonomy baselines used by the characterization tests).

`tests/utils/`: `auth_utils.py` (`TestAuthManager`, `TestAuthScenarios`, `MockOAuthProvider`, `TestPermissionChecker`), `db_utils.py` (`TestDatabaseManager`, `TestDataSeeder`, `DatabaseAssertion`, `DatabaseSnapshot`), `docker_utils.py` (`DockerTestManager`, `DockerComposeManager`, and the `docker_test_environment` and `docker_compose_environment` context managers).

## Coverage

- `.github/workflows/ci.yml` runs `pytest --cov=src --cov-report=xml --cov-report=html --cov-report=term --ignore=tests/e2e`, then `coverage report --fail-under=25`.
- `.github/workflows/integration-tests.yml` runs `pytest tests/integration/ --cov=src ... --cov-fail-under=25`.
- `scripts/run_integration_tests.sh` uses `COVERAGE_THRESHOLD=80`. It starts `tests/integration/docker-compose.test.yml` with the legacy `docker-compose` binary and then waits up to 60 times, two seconds apart, for a `temporal` service that the compose file does not define. It then runs the migrations against port 5433 and runs `pytest tests/integration` with that threshold. Run the pytest commands in [Running tests](#running-tests) instead.

## Load tests

```bash
make load-test     # locust -f tests/load/locustfile.py --host=http://localhost:8000
```

`tests/load/locustfile.py` defines `ResearchPlatformUser`, `AdminUser`, `WebSocketUser`, and the `QuickLoadTest`, `SustainedLoadTest`, and `SpikeLoadTest` subclasses. Its requests do not match the mounted API:

- `ResearchPlatformUser` posts to `/api/v1/auth/register` without `confirm_password` and `accept_terms`, which `RegisterRequest` requires, and reads `access_token` from the top level of the response, where `AuthResponse` nests it under `tokens`.
- Its project tasks call `/api/v1/projects...`; the projects routes are mounted at `/api/v1/research/projects` (`src/api/routes/research.py`). Its profile task sends `PATCH /api/v1/auth/me`, and the auth router defines only `GET /me`.
- `AdminUser` calls `/api/v1/admin/users`, `/api/v1/admin/stats`, and `/api/v1/admin/audit-logs`; the API mounts no `/admin` routes.
- `WebSocketUser` sends `GET /ws/ping`; `src/api/routes/websocket.py` defines no `/ws/ping`.

## Web end-to-end tests

The specs are TypeScript files in `cerebro/web/tests/e2e/` (`a11y-axe`, `a11y-keyboard`, `api-contract`, `dark-mode-axe`, `golden-run`, `landing`, `responsive-walkthrough`, `run-detail`, `runs`, `theme-tokens`, `user-journey`, `wave6-accessibility`, `workbench-contracts`, `workbench-ui`, `workflows`), with API stubs in `helpers/` and `fixtures/`. Most specs stub `/api/v1/**` through `page.route`. `golden-run.spec.ts` runs against a real backend only when `PLAYWRIGHT_REAL_BACKEND=1` and is skipped otherwise.

```bash
cd cerebro/web
npm ci
npx playwright install chromium
npm run test:e2e        # playwright test
```

`playwright.config.ts` defines one project (`chromium`) and starts `npm run dev -- --host 127.0.0.1 --port 5173` itself, with `VITE_API_URL` set from `PLAYWRIGHT_BACKEND_URL`, then `VITE_API_URL`, then `http://127.0.0.1:8000`. It reuses a running dev server unless `CI` or `PLAYWRIGHT_ISOLATED` is set.

In CI, `.github/workflows/ci.yml` (`frontend-ci`) installs dependencies, lints, and builds; its Playwright install and run steps carry `if: false`. In `.github/workflows/e2e-tests.yml` the `e2e-tests` job also carries `if: false`; its comment says it runs `pytest tests/e2e/ --browser=...` for pytest-playwright tests that do not exist.

## Live evaluations

`evals/live/` (`test_provider_health.py`, `test_text_routing.py`, `test_structured_routing.py`, `test_multi_domain.py`) calls real providers and costs money. Every module is marked `live_eval`, so a normal `pytest` run deselects it, and `testpaths` does not include `evals/`.

```bash
export OPENROUTER_API_KEY=...
export ENABLE_LIVE_EVAL=1
export LIVE_EVAL_BUDGET_USD=0.25      # default when unset
bash scripts/run_live_evals.sh
```

- `scripts/run_live_evals.sh` exits 1 when `OPENROUTER_API_KEY` is unset or `ENABLE_LIVE_EVAL` is not `1`, creates `evals/out/`, and runs `pytest evals/live -m live_eval -v --no-cov --tb=short`.
- The suite is serial only (`evals/live/conftest.py`); parallel runners such as pytest-xdist are unsupported.
- Without the two variables, `live_eval_settings_guard` skips every test. With `LIVE_EVAL_REQUIRED=1` it fails instead, so a misconfigured runner cannot report green. The shell script exits first when the variables are missing, so `LIVE_EVAL_REQUIRED` matters when running pytest directly.
- `LIVE_EVAL_BUDGET_USD` (default `0.25`) is enforced three ways: a check before each test, a check inside each recorded LLM call that raises once the total exceeds the budget, and a check at session end.
- The session writes `evals/out/live_eval_report.json` and a Markdown report next to it (`evals/live/report.py`), with per-call model, token counts, and cost.
- The fixtures `openrouter_spy` and `gemini_fallback_guard` record request payloads and count calls into the Gemini fallback path.

The template [nightly workflow](ci/nightly-live-eval.yml.example) runs the script at 06:00 UTC and on manual dispatch, uploads the report, and opens an issue on failure. Copy it to `.github/workflows/` to use it; it is not active from `docs/ci/`. It needs the repository secrets `OPENROUTER_API_KEY` and `GEMINI_API_KEY`.

## Adaptive routing evaluation

Two scripts evaluate the Thompson-sampling allocator (see [MASR routing](reference/06-masr-routing.md)). Neither turns it on.

- `python scripts/run_adaptive_routing_eval.py` runs `AdaptiveRoutingEvaluator` (`src/ai_brain/experimentation/eval/adaptive_routing_eval.py`) with seed 42 over 200 synthetic queries, prints a Markdown report, and writes `/tmp/adaptive_routing_eval_results.json`. The module states that a synthetic corpus cannot validate real quality improvements.
- `python scripts/evaluate_adaptive_routing_promotion.py --output PATH [--criteria FILE] [--corpus FILE] [--seed N]` runs the manual promotion gate in `src/ai_brain/experimentation/eval/adaptive_routing_promotion.py`. `--output` is required and must name a new file outside the repository; the report is written with mode `0600` and an existing path is refused. The exit code is 0 for `PASS`, 2 for `FAIL`, and 3 for any other status, including a criteria or corpus file that fails validation. Missing inputs and synthetic corpora cannot produce `PASS`. `config/adaptive_routing_promotion_criteria.example.json` is an example criteria file with `approved_for_promotion` set to `false`.

## Continuous integration

| Workflow | Triggers | Jobs |
|---|---|---|
| `ci.yml` (CI Pipeline) | push and pull request on `main` and `develop`; manual | `lint` (`ruff check`, `ruff format --check`, `mypy src`); `test` (Python 3.11 and 3.12 matrix with `postgres:16` and `redis:7-alpine` services: golden-dataset regression, then full pytest with `--ignore=tests/e2e` and `--fail-under=25`); `test-cli` (`research-cli --version`, `--help`, `pytest tests/test_cli.py`); `security` (`bandit` with `--exit-zero`, `pip-audit`); `validate-docker` (hadolint on `Dockerfile`, test builds of the API `production` target and the web image with `push: false`); `validate-k8s` (`yamllint` on `k8s/*.yaml`); `frontend-ci` (`npm ci`, `npm run lint`, `npm run build`); `all-checks` (fails when any needed job failed) |
| `integration-tests.yml` | push and pull request on `main`; manual | `integration-tests` (30-minute timeout; PostgreSQL 16 and Redis 7 services, `alembic upgrade head`, `pytest tests/integration/` with `--cov-fail-under=25`, artifact and Codecov upload, and on pull requests a comment whose text is fixed: it reports "Coverage: 85%" regardless of the result) |
| `e2e-tests.yml` | push and pull request on `main`; manual | `e2e-tests` (disabled with `if: false`); `e2e-python` (30-minute timeout; `docker compose up -d`, waits for `/health`, `pytest tests/e2e/`, uploads logs on failure, `docker compose down -v`); `e2e-report` (writes a fixed summary table to the step summary) |
| `release.yml` | tags `v*`; manual with a `version` input | `validate-version` (`vMAJOR.MINOR.PATCH` with optional suffix), `generate-changelog`, `build-release-artifacts` (wheel, sdist, and a PyInstaller `research-cli` binary on `ubuntu-latest` and `macos-latest`), `create-release`, `publish-pypi` (skipped for pre-release versions; needs `PYPI_API_TOKEN`), `notify-release` (Slack webhook) |
| `cd.yml` (CD Pipeline) | tags `v*`; push to `develop`; manual with `environment` (`staging` or `production`) and `skip_tests` | `determine-environment`, `pre-deployment-checks` (`pytest tests/test_api.py::TestHealthEndpoints`, `kustomize build k8s/`), `deploy-staging` and `deploy-production` (GKE through `google-github-actions`; production only for `v*` tags, with a blue-green switch), `rollback` |

`cd.yml` sets image names with `kustomize edit set image` for `research-platform-api` and `research-platform-web` under `gcr.io`. No workflow in this repository builds or pushes those images (`ci.yml` builds them with `push: false`). See the [deployment guide](deployment-monitoring-guide.md).
