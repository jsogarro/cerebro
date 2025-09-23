# Cerebro AI Brain Platform - Developer Makefile
# Provides convenient commands for development, testing, and deployment

.PHONY: help install dev test lint type-check format clean docker-dev docker-prod k8s-deploy

# Default target
help: ## Show this help message
	@echo "Cerebro AI Brain Platform - Development Commands"
	@echo "================================================"
	@echo ""
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

## Development Environment
install: ## Install dependencies with uv
	uv pip install -e ".[dev]"

dev: ## Start development environment
	docker-compose up -d

dev-build: ## Build and start development environment
	docker-compose up -d --build

dev-logs: ## Show development logs
	docker-compose logs -f

dev-stop: ## Stop development environment
	docker-compose down

dev-clean: ## Clean development environment (removes volumes)
	docker-compose down -v --remove-orphans
	docker system prune -f

## Production Environment
prod-build: ## Build production images
	docker-compose -f docker-compose.production.yml build

prod-up: ## Start production environment
	docker-compose -f docker-compose.production.yml up -d

prod-down: ## Stop production environment
	docker-compose -f docker-compose.production.yml down

prod-logs: ## Show production logs
	docker-compose -f docker-compose.production.yml logs -f

## Database Management
db-init: ## Initialize database with migrations
	docker-compose exec api alembic upgrade head

db-migrate: ## Create new database migration
	@read -p "Migration message: " msg; \
	docker-compose exec api alembic revision --autogenerate -m "$$msg"

db-reset: ## Reset database (WARNING: destroys all data)
	docker-compose down postgres
	docker volume rm cerebro_postgres_data
	docker-compose up -d postgres
	sleep 10
	make db-init

db-backup: ## Backup database
	@mkdir -p backups
	docker-compose exec postgres pg_dump -U research research_db > backups/backup_$$(date +%Y%m%d_%H%M%S).sql
	@echo "Database backup created in backups/ directory"

db-shell: ## Connect to database shell
	docker-compose exec postgres psql -U research -d research_db

## Testing
test: ## Run all tests
	pytest

test-unit: ## Run unit tests only
	pytest tests/unit -v

test-integration: ## Run integration tests
	pytest tests/integration -v

test-e2e: ## Run end-to-end tests
	pytest tests/e2e -v

test-coverage: ## Run tests with coverage report
	pytest --cov=src --cov-report=html --cov-report=term

test-docker: ## Run tests in Docker environment
	docker-compose -f tests/integration/docker-compose.test.yml up --build --abort-on-container-exit

## Code Quality
lint: ## Run linting (ruff)
	ruff check src tests

lint-fix: ## Fix linting issues
	ruff check --fix src tests

format: ## Format code (black)
	black src tests

format-check: ## Check code formatting
	black --check src tests

type-check: ## Run type checking (mypy)
	mypy src

quality: ## Run all quality checks
	make format lint type-check

quality-fix: ## Fix all auto-fixable quality issues
	make format lint-fix

## CLI Tools
cli-health: ## Check API health via CLI
	research-cli health

cli-projects: ## List research projects
	research-cli projects list

cli-test: ## Test CLI functionality
	research-cli projects create --title "Test Project" --query "Test query" --domains "test"

## MASR Testing
masr-test: ## Test MASR integration
	python examples/masr_supervisor_integration_test.py

masr-health: ## Check MASR components health
	python -c "import asyncio; from src.ai_brain.router.masr import MASRouter; print(asyncio.run(MASRouter().health_check()))"

hierarchical-test: ## Test hierarchical agent communication
	python examples/hierarchical_agent_example.py

## Docker Registry
docker-login: ## Login to Docker registry
	echo ${DOCKER_REGISTRY_TOKEN} | docker login -u _json_key --password-stdin ${DOCKER_REGISTRY}

docker-push-dev: ## Push development images
	docker-compose build
	docker-compose push

docker-push-prod: ## Push production images
	docker-compose -f docker-compose.production.yml build
	docker-compose -f docker-compose.production.yml push

## Kubernetes Deployment
k8s-namespace: ## Create Kubernetes namespace
	kubectl apply -f k8s/namespace.yaml

k8s-secrets: ## Apply Kubernetes secrets (ensure secrets are configured first)
	kubectl apply -f k8s/secrets.yaml

k8s-config: ## Apply Kubernetes configuration
	kubectl apply -f k8s/configmap.yaml

k8s-deploy: ## Deploy to Kubernetes
	kubectl apply -k k8s/

k8s-status: ## Check Kubernetes deployment status
	kubectl get pods,services,ingress -n cerebro

k8s-logs: ## Get Kubernetes logs
	kubectl logs -f deployment/cerebro-api -n cerebro

k8s-clean: ## Clean Kubernetes deployment
	kubectl delete -k k8s/

## Monitoring
logs-api: ## Follow API logs
	docker-compose logs -f api

logs-worker: ## Follow worker logs
	docker-compose logs -f worker

logs-masr: ## Follow MASR logs
	docker-compose logs -f masr-router

logs-all: ## Follow all service logs
	docker-compose logs -f

metrics: ## Show system metrics
	docker-compose exec prometheus curl -s localhost:9090/api/v1/query?query=up

## Maintenance
clean: ## Clean up development artifacts
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true

clean-docker: ## Clean Docker system
	docker system prune -f
	docker volume prune -f
	docker network prune -f

backup-all: ## Create full system backup
	@mkdir -p backups/$$(date +%Y%m%d)
	make db-backup
	docker-compose exec redis redis-cli --rdb backups/$$(date +%Y%m%d)/redis_backup.rdb
	cp -r src backups/$$(date +%Y%m%d)/
	@echo "Full system backup created in backups/$$(date +%Y%m%d)/"

## Performance Testing
perf-test: ## Run performance tests
	python -m pytest tests/performance/ -v

load-test: ## Run load testing with locust
	locust -f tests/load/locustfile.py --host=http://localhost:8000

benchmark: ## Run performance benchmarks
	python tests/benchmarks/run_benchmarks.py

## Development Workflow
dev-workflow: ## Complete development workflow (format, lint, test)
	make format lint type-check test

ci-workflow: ## Simulate CI workflow locally
	make quality test test-docker

setup: ## Initial project setup
	@echo "Setting up Cerebro AI Brain Platform..."
	@echo "1. Installing dependencies..."
	make install
	@echo "2. Starting development environment..."
	make dev-build
	@echo "3. Waiting for services to be ready..."
	sleep 30
	@echo "4. Running initial database migration..."
	make db-init
	@echo "5. Running health check..."
	make cli-health
	@echo ""
	@echo "✅ Setup completed! Available endpoints:"
	@echo "   - API: http://localhost:8000"
	@echo "   - API Docs: http://localhost:8000/docs"
	@echo "   - Temporal UI: http://localhost:8080"
	@echo "   - pgAdmin: http://localhost:5050 (dev-tools profile)"
	@echo "   - Redis Commander: http://localhost:8081 (dev-tools profile)"

setup-prod: ## Setup production environment
	@echo "Setting up production environment..."
	@echo "⚠️  Ensure production secrets are configured first!"
	@read -p "Continue with production setup? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	make prod-build
	make prod-up
	@echo "✅ Production environment started"

## Quick Commands
quick-test: ## Quick test run (unit tests only)
	pytest tests/unit -x -v

quick-format: ## Quick format and lint fix
	black src tests && ruff check --fix src tests

status: ## Show development environment status
	@echo "Cerebro AI Brain Platform Status"
	@echo "==============================="
	@echo ""
	@docker-compose ps
	@echo ""
	@echo "Health Checks:"
	@curl -s http://localhost:8000/health || echo "❌ API not responding"
	@curl -s http://localhost:9000/health || echo "❌ MCP not responding"
	@echo ""

# Environment Variables Help
env-help: ## Show required environment variables
	@echo "Required Environment Variables:"
	@echo "=============================="
	@echo ""
	@echo "Development (.env):"
	@echo "  GEMINI_API_KEY=your-gemini-api-key"
	@echo "  DATABASE_URL=postgresql+asyncpg://research:research123@localhost:5432/research_db"
	@echo "  REDIS_URL=redis://localhost:6379/0"
	@echo "  TEMPORAL_HOST=localhost:7233"
	@echo ""
	@echo "Production (.env.production):"
	@echo "  DB_PASSWORD=secure-db-password"
	@echo "  REDIS_PASSWORD=secure-redis-password"
	@echo "  SECRET_KEY=secure-app-secret"
	@echo "  JWT_SECRET_KEY=secure-jwt-secret"
	@echo "  GEMINI_API_KEY=production-gemini-key"
	@echo "  GRAFANA_PASSWORD=secure-grafana-password"
	@echo "  CORS_ORIGINS=https://your-domain.com"
	@echo ""
	@echo "See .env.example and .env.production.example for complete templates"

# Version information
version: ## Show version information
	@echo "Cerebro AI Brain Platform"
	@echo "========================"
	@echo "Python: $$(python --version)"
	@echo "Docker: $$(docker --version)"
	@echo "Docker Compose: $$(docker-compose --version)"
	@echo "Kubernetes: $$(kubectl version --client --short 2>/dev/null || echo 'Not installed')"