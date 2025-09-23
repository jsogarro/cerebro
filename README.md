# Multi-Agent Graduate-Level Research Platform

A sophisticated AI-powered research platform that orchestrates multiple specialized agents to conduct comprehensive, graduate-level research on any given topic.

## 🚀 Features

- **Multi-Agent System** with specialized agents for different research tasks
- **LangGraph Orchestration** for sophisticated workflow coordination
- **Google Gemini Integration** for advanced AI capabilities
- **Temporal Workflows** for robust distributed task execution
- **Docker & Kubernetes Ready** for scalable deployment
- **Comprehensive CLI** for complete API interaction
- **Real-time Progress Tracking** with WebSocket support
- **MCP Protocol Support** for tool integration

## 📋 Table of Contents

- [Quick Start](#quick-start)
- [Installation](#installation)
- [CLI Documentation](#cli-documentation)
- [API Documentation](#api-documentation)
- [Development](#development)
- [Deployment](#deployment)
- [Architecture](#architecture)
- [Contributing](#contributing)

## Quick Start

### Prerequisites
- Python 3.11+
- Docker & Docker Compose
- uv package manager

### Installation

1. **Clone the repository:**
```bash
git clone <repository-url>
cd research-platform
```

2. **Install dependencies:**
```bash
uv pip install -e ".[dev]"
```

3. **Set up environment:**
```bash
cp .env.example .env
cp .env.cli.example .env.cli
# Edit .env files with your configuration
```

4. **Start services:**
```bash
# Using Docker Compose
docker-compose up -d

# Or start API server directly
uvicorn src.api.main:app --host 0.0.0.0 --port 8000
```

5. **Verify installation:**
```bash
research-cli health
```

## 📚 CLI Documentation

The Research Platform CLI (`research-cli`) provides a comprehensive command-line interface for interacting with the Research Platform API. It supports multiple output formats, interactive modes, and batch operations.

### Installation & Configuration

#### Install the CLI
The CLI is installed automatically when you install the package:
```bash
uv pip install -e ".[dev]"
```

#### Configuration
The CLI can be configured through multiple methods:

1. **Environment Variables** (`.env.cli` or `~/.research-cli.env`):
```bash
RESEARCH_API_URL=http://localhost:8000
RESEARCH_API_TIMEOUT=30
RESEARCH_OUTPUT_FORMAT=table  # Options: table, json, yaml, csv
RESEARCH_VERBOSE=false
RESEARCH_COLOR=true
RESEARCH_MAX_RETRIES=3
```

2. **Command-line Options:**
```bash
research-cli --api-url http://localhost:8000 --format json --verbose
```

3. **Configuration Commands:**
```bash
# Show current configuration
research-cli config show

# Set configuration value
research-cli config set api_url http://localhost:8000
research-cli config set output_format json

# Save configuration to file
research-cli config save
```

### Global Options

All commands support these global options:

| Option | Short | Description | Default |
|--------|-------|-------------|---------|
| `--api-url` | - | API base URL | `http://localhost:8000` |
| `--format` | `-f` | Output format (table/json/yaml/csv) | `table` |
| `--verbose` | `-v` | Enable verbose output | `false` |
| `--no-color` | - | Disable colored output | `false` |
| `--help` | - | Show help message | - |
| `--version` | - | Show CLI version | - |

### Commands Reference

#### 🏥 Health Check

Check API health and readiness status:

```bash
# Basic health check
research-cli health

# With verbose output to see service status
research-cli --verbose health
```

Output shows:
- API health status
- Service readiness
- Component status (database, redis, temporal)

#### 📝 Project Management

##### Create Project

Create a new research project with multiple input methods:

**Basic Usage:**
```bash
research-cli projects create \
  --title "Impact of AI on Employment" \
  --query "How will AI affect job markets in the next decade?" \
  --domains "AI,Economics,Labor" \
  --user-id "researcher-001"
```

**Options:**
| Option | Short | Description | Required |
|--------|-------|-------------|----------|
| `--title` | `-t` | Project title | Yes* |
| `--query` | `-q` | Research query text | Yes* |
| `--domains` | `-d` | Research domains (comma-separated) | Yes* |
| `--user-id` | `-u` | User identifier | No (default: cli-user) |
| `--depth` | - | Research depth (survey/comprehensive/exhaustive) | No (default: comprehensive) |
| `--scope` | `-s` | Scope parameters (key=value) | No |
| `--interactive` | `-i` | Interactive mode | No |
| `--file` | `-f` | Load projects from YAML/JSON file | No |

*Required unless using `--interactive` or `--file` mode

**Interactive Mode:**
```bash
research-cli projects create --interactive
```
The CLI will prompt for all required information step by step.

**Batch Creation from File:**
```bash
research-cli projects create --file projects.yaml
```

Example YAML file (`projects.yaml`):
```yaml
- title: "Climate Change Impact Study"
  query_text: "What are the effects of climate change on agriculture?"
  domains:
    - Climate Science
    - Agriculture
    - Environmental Science
  depth_level: exhaustive
  user_id: researcher-001
  scope:
    max_sources: 150
    languages: ["en", "es", "fr"]
    geographic_scope: ["Global"]

- title: "Quantum Computing Applications"
  query_text: "How can quantum computing advance drug discovery?"
  domains:
    - Quantum Computing
    - Pharmaceutical Science
  depth_level: comprehensive
  user_id: researcher-002
```

**Advanced Scope Configuration:**
```bash
research-cli projects create \
  --title "Advanced Research" \
  --query "Complex research question" \
  --domains "AI,Ethics" \
  --scope max_sources=100 \
  --scope languages=[en,es,fr] \
  --scope geographic_scope=[Europe,Asia]
```

##### Get Project Details

Retrieve detailed information about a specific project:

```bash
# Table format (default)
research-cli projects get <project-id>

# JSON format for parsing
research-cli --format json projects get <project-id>

# YAML format
research-cli --format yaml projects get <project-id>
```

##### List Projects

List all research projects with filtering options:

```bash
# List all projects
research-cli projects list

# Filter by user
research-cli projects list --user-id researcher-001

# Filter by status
research-cli projects list --status in_progress

# Pagination
research-cli projects list --limit 20 --offset 40

# Combined filters with JSON output
research-cli --format json projects list \
  --user-id researcher-001 \
  --status completed \
  --limit 10
```

**List Options:**
| Option | Short | Description |
|--------|-------|-------------|
| `--user-id` | `-u` | Filter by user ID |
| `--status` | `-s` | Filter by status |
| `--limit` | `-l` | Maximum results (default: 10) |
| `--offset` | `-o` | Pagination offset (default: 0) |

##### Monitor Progress

Track research project progress with real-time updates:

```bash
# Get current progress
research-cli projects progress <project-id>

# Watch progress in real-time
research-cli projects progress <project-id> --watch

# Custom update interval (seconds)
research-cli projects progress <project-id> --watch --interval 3
```

**Progress Display Shows:**
- Total tasks and completion status
- Progress percentage
- Current agent activities
- Estimated time remaining
- Task breakdown (completed/in-progress/pending/failed)

##### Cancel Project

Cancel an active research project:

```bash
# With confirmation prompt
research-cli projects cancel <project-id>

# Skip confirmation
research-cli projects cancel <project-id> --force
```

##### Get Results

Retrieve research results for completed projects:

```bash
# Display results
research-cli projects results <project-id>

# Save to file
research-cli projects results <project-id> --output results.json

# Different format
research-cli --format yaml projects results <project-id> --output results.yaml
```

##### Refine Scope

Refine the scope of an existing research project:

```bash
# Update specific parameters
research-cli projects refine <project-id> \
  --max-sources 200 \
  --languages en,es,fr,de

# Using key=value pairs
research-cli projects refine <project-id> \
  --scope max_sources=200 \
  --scope languages=[en,es,fr,de] \
  --scope time_period_start=2020-01-01
```

### Output Formats

The CLI supports multiple output formats for different use cases:

#### Table Format (Default)
Human-readable tables with colors and formatting:
```bash
research-cli projects list
```
```
                          Research Projects
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┓
┃ ID                         ┃ Title         ┃ Status   ┃ Created           ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━┩
│ 550e8400-e29b-41d4-a716... │ AI Research   │ pending  │ 2024-01-15 10:30  │
│ 6ba7b810-9dad-11d1-80b4... │ Climate Study │ progress │ 2024-01-15 11:45  │
└────────────────────────────┴───────────────┴──────────┴───────────────────┘
```

#### JSON Format
Machine-readable JSON for scripting and automation:
```bash
research-cli --format json projects get <project-id>
```
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "title": "AI Research",
  "status": "in_progress",
  "query": {
    "text": "Impact of AI on society",
    "domains": ["AI", "Ethics", "Sociology"],
    "depth_level": "comprehensive"
  },
  "created_at": "2024-01-15T10:30:00"
}
```

#### YAML Format
Human and machine-readable YAML:
```bash
research-cli --format yaml projects list
```

#### CSV Format
Spreadsheet-compatible CSV for data analysis:
```bash
research-cli --format csv projects list > projects.csv
```

### Advanced Usage

#### Shell Completion

Enable auto-completion for your shell:

```bash
# Bash
eval "$(_RESEARCH_CLI_COMPLETE=bash_source research-cli)"

# Zsh
eval "$(_RESEARCH_CLI_COMPLETE=zsh_source research-cli)"

# Fish
eval (env _RESEARCH_CLI_COMPLETE=fish_source research-cli)
```

Or get the completion script:
```bash
research-cli completion bash
research-cli completion zsh
research-cli completion fish
```

#### Scripting Examples

**Monitor Multiple Projects:**
```bash
#!/bin/bash
PROJECT_IDS=("id1" "id2" "id3")

for id in "${PROJECT_IDS[@]}"; do
  echo "Checking project $id..."
  research-cli --format json projects progress "$id" | jq '.progress_percentage'
done
```

**Batch Processing with Error Handling:**
```bash
#!/bin/bash
research-cli projects create --file batch.yaml 2>&1 | tee creation.log

if [ $? -eq 0 ]; then
  echo "All projects created successfully"
else
  echo "Some projects failed. Check creation.log"
  grep "Failed" creation.log
fi
```

**Export All Results:**
```bash
#!/bin/bash
# Get all completed projects and export their results
research-cli --format json projects list --status completed | \
  jq -r '.[] | .id' | \
  while read -r id; do
    echo "Exporting results for project $id..."
    research-cli projects results "$id" --output "results_${id}.json"
  done
```

#### Using with Docker

If the API is running in Docker:
```bash
# Default Docker Compose setup
research-cli --api-url http://localhost:8000 health

# Custom Docker network
docker run --network research-network \
  -v $(pwd):/app \
  research-platform \
  research-cli --api-url http://api:8000 projects list
```

### Troubleshooting

#### Common Issues

**Connection Refused:**
```bash
# Check if API is running
curl http://localhost:8000/health

# Verify API URL configuration
research-cli config show | grep api_url

# Set correct API URL
research-cli config set api_url http://localhost:8001
```

**Authentication Errors (Future):**
```bash
# Set API key
export RESEARCH_API_KEY=your-api-key

# Or in config
research-cli config set api_key your-api-key
```

**Timeout Issues:**
```bash
# Increase timeout for long operations
research-cli config set api_timeout 60

# Or per command
RESEARCH_API_TIMEOUT=60 research-cli projects create --file large-batch.yaml
```

**Format Issues:**
```bash
# Ensure proper format for domains
research-cli projects create \
  --domains "AI,Machine Learning,Ethics"  # Correct
  # NOT: --domains AI Machine Learning     # Wrong
```

### Examples

#### Complete Workflow Example

```bash
# 1. Check system health
research-cli health

# 2. Create a research project
PROJECT_ID=$(research-cli --format json projects create \
  --title "AI Safety Research" \
  --query "What are the key challenges in AI alignment?" \
  --domains "AI,Safety,Ethics" \
  --depth exhaustive | jq -r '.id')

echo "Created project: $PROJECT_ID"

# 3. Monitor progress
research-cli projects progress $PROJECT_ID --watch --interval 5

# 4. Check final status
research-cli projects get $PROJECT_ID

# 5. Export results
research-cli projects results $PROJECT_ID --output "ai_safety_results.json"

# 6. Create summary report
research-cli --format yaml projects get $PROJECT_ID > project_summary.yaml
```

#### Interactive Research Session

```bash
# Start interactive project creation
research-cli projects create --interactive

# Follow prompts:
# > Project title: Quantum Computing Impact
# > Research query: How will quantum computing affect cryptography?
# > Research domains (comma-separated): Quantum Computing,Cryptography,Security
# > Research depth (survey/comprehensive/exhaustive): comprehensive
# > User ID: researcher-001
# > Configure research scope? (y/n): y
# > Maximum number of sources: 100
# > Languages (comma-separated): en,de,zh
# > Geographic scope (optional): Global

# Monitor the created project
research-cli projects list --user-id researcher-001
```

### CLI Architecture

The CLI is built with:
- **Click**: Command-line interface framework
- **Rich**: Beautiful terminal formatting
- **httpx**: Async HTTP client with retry logic
- **Pydantic**: Data validation
- **python-dotenv**: Environment configuration

Key features:
- Async/await for efficient API calls
- Automatic retry with exponential backoff
- Progress bars and spinners for long operations
- Color-coded output for better readability
- Comprehensive error handling
- Support for multiple output formats

## 📊 API Documentation

### Base URL
```
http://localhost:8000
```

### Endpoints

#### Health & Status

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Basic health check |
| `/ready` | GET | Readiness check with service status |
| `/live` | GET | Liveness check |
| `/metrics` | GET | Prometheus metrics |

#### Research Projects

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/research/projects` | POST | Create new research project |
| `/api/v1/research/projects/{id}` | GET | Get project details |
| `/api/v1/research/projects` | GET | List all projects |
| `/api/v1/research/projects/{id}/progress` | GET | Get project progress |
| `/api/v1/research/projects/{id}/cancel` | POST | Cancel project |
| `/api/v1/research/projects/{id}/refine` | POST | Refine project scope |
| `/api/v1/research/projects/{id}/results` | GET | Get project results |

### Request/Response Examples

#### Create Project
```http
POST /api/v1/research/projects
Content-Type: application/json

{
  "title": "AI Impact Research",
  "query": {
    "text": "What are the impacts of AI on society?",
    "domains": ["AI", "Ethics", "Sociology"],
    "depth_level": "comprehensive"
  },
  "user_id": "researcher-001",
  "scope": {
    "max_sources": 100,
    "languages": ["en", "es"]
  }
}
```

#### Response
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "title": "AI Impact Research",
  "status": "pending",
  "created_at": "2024-01-15T10:30:00Z",
  "query": {...},
  "scope": {...}
}
```

## 🛠️ Development

### Project Structure

```
research-platform/
├── src/
│   ├── agents/           # Agent implementations
│   ├── api/              # FastAPI application
│   ├── cli/              # CLI implementation
│   ├── core/             # Core business logic
│   ├── models/           # Data models
│   ├── orchestration/    # LangGraph workflows
│   ├── temporal/         # Temporal workflows
│   ├── mcp/              # MCP protocol servers
│   └── services/         # Service layer
├── tests/                # Test files
├── docker/               # Docker configurations
├── k8s/                  # Kubernetes manifests
├── helm/                 # Helm charts
├── examples/             # Example files
└── docs/                 # Documentation
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=html

# Run specific test file
pytest tests/test_cli.py -v

# Run tests in watch mode
pytest-watch
```

### Code Quality

```bash
# Format code
black src tests

# Lint code
ruff check src tests

# Type checking
mypy src

# All quality checks
make quality
```

### Local Development

1. **Set up pre-commit hooks:**
```bash
pre-commit install
```

2. **Run API locally:**
```bash
uvicorn src.api.main:app --reload --port 8000
```

3. **Run with Docker:**
```bash
docker-compose up
```

4. **Access services:**
- API: http://localhost:8000
- API Docs: http://localhost:8000/docs
- Temporal UI: http://localhost:8080
- pgAdmin: http://localhost:5050 (with --profile dev-tools)

## 🚀 Deployment

### Docker Deployment

Build and run with Docker:
```bash
# Build images
docker build -t research-platform-api .
docker build -f docker/Dockerfile.worker -t research-platform-worker .

# Run with Docker Compose
docker-compose up -d

# View logs
docker-compose logs -f api worker
```

### Kubernetes (GKE) Deployment

1. **Build and push images:**
```bash
export PROJECT_ID=your-gcp-project
docker build -t gcr.io/$PROJECT_ID/research-platform-api:latest .
docker push gcr.io/$PROJECT_ID/research-platform-api:latest
```

2. **Deploy to GKE:**
```bash
# Create cluster
gcloud container clusters create research-platform \
  --num-nodes=3 \
  --zone=us-central1-a

# Apply manifests
kubectl apply -k k8s/

# Or use Helm
helm install research-platform helm/research-platform/
```

3. **Monitor deployment:**
```bash
kubectl get pods -n research-platform
kubectl logs -f deployment/research-api -n research-platform
```

### Environment Variables

Key configuration variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `GEMINI_API_KEY` | Google Gemini API key | Required |
| `DATABASE_URL` | PostgreSQL connection string | Required |
| `REDIS_URL` | Redis connection string | Required |
| `TEMPORAL_HOST` | Temporal server address | localhost:7233 |
| `ENVIRONMENT` | Deployment environment | development |
| `LOG_LEVEL` | Logging level | INFO |

## 🏗️ Architecture

### System Components

1. **API Layer** (FastAPI)
   - RESTful endpoints
   - WebSocket support
   - Authentication & authorization
   - Request validation

2. **Agent System**
   - Literature Review Agent
   - Comparative Analysis Agent
   - Methodology Agent
   - Synthesis Agent
   - Citation & Verification Agent

3. **Orchestration** (LangGraph + Temporal)
   - Workflow management
   - Task distribution
   - State management
   - Error recovery

4. **Data Layer**
   - PostgreSQL for structured data
   - Redis for caching
   - Vector DB for embeddings
   - S3/GCS for document storage

5. **Integration Layer** (MCP)
   - Academic database connectors
   - Tool servers
   - External API integrations

### Technology Stack

- **Language**: Python 3.11+
- **API Framework**: FastAPI
- **CLI Framework**: Click + Rich
- **LLM**: Google Gemini
- **Workflow**: Temporal + LangGraph
- **Database**: PostgreSQL + Redis
- **Container**: Docker
- **Orchestration**: Kubernetes (GKE)
- **Package Management**: uv

## 🤝 Contributing

### Development Workflow

1. Fork the repository
2. Create a feature branch
3. Follow TDD principles - write tests first
4. Ensure all tests pass
5. Update documentation
6. Submit a pull request

### Code Standards

- Follow PEP 8 style guide
- Use type hints
- Write docstrings for all public functions
- Maintain >80% test coverage
- Use semantic commit messages

### Commit Message Format

```
type(scope): description

[optional body]

[optional footer]
```

Types: feat, fix, docs, style, refactor, test, chore

Example:
```
feat(cli): add interactive mode for project creation

- Add prompts for all required fields
- Support scope configuration
- Add validation for user inputs

Closes #123
```

## 📄 License

[Your License Here]

## 🙏 Acknowledgments

- Built with FastAPI, LangGraph, and Temporal
- Uses Google Gemini for AI capabilities
- Implements Anthropic's MCP protocol for tool integration
- CLI powered by Click and Rich

## 📞 Support

- GitHub Issues: [Report bugs or request features](https://github.com/your-org/research-platform/issues)
- Documentation: [Full documentation](https://docs.research-platform.ai)
- Email: support@research-platform.ai

## 🔄 Roadmap

### Phase 1 (Current)
- ✅ Core platform setup
- ✅ Basic API implementation
- ✅ CLI tool
- ✅ Docker containerization
- ✅ Kubernetes manifests

### Phase 2 (In Progress)
- ⏳ Temporal workflow implementation
- ⏳ Gemini integration
- ⏳ Agent implementations
- ⏳ LangGraph orchestration

### Phase 3 (Planned)
- 📅 MCP tool servers
- 📅 WebSocket real-time updates
- 📅 Advanced report generation
- 📅 Authentication system
- 📅 Production deployment

See [TODO.md](TODO.md) for detailed development tasks.