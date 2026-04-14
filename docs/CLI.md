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

