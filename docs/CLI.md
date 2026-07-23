## 📚 CLI Documentation

> **Product note:** Cerebro is a general research-workflow workbench.
> `research-cli` / `research-platform` and the "Research Platform CLI" help
> string are legacy `research-platform` identifiers that remain accurate to the
> code, so command output examples below are left verbatim. There is no
> `cerebro-cli` entrypoint and no PyPI package.

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

# Set a configuration value (in-memory only — see note below)
research-cli config set api_url http://localhost:8000
research-cli config set output_format json

# Save the current configuration to ~/.research-cli.env
research-cli config save
```

> **Important — `config set` does not persist.** Each `research-cli` invocation rebuilds its configuration from environment variables and the config file, so `config set` only mutates the config of that single process and then exits; the next command starts fresh and is unaffected. `config set` is therefore not a way to change configuration for later commands — use environment variables or the config file (methods 1 and 2 above) for that. Note also that `config save` writes the values derived from the current environment/config file, **not** values you passed to `config set` in a separate invocation.

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
- A `checks` block with `database`, `redis`, and `temporal` fields

> All three `/ready` check values (`database`, `redis`, `temporal`) are currently hardcoded to `ok` behind TODOs in `health.py` — none of them actually probe the underlying service yet. In particular, the `temporal: ok` field is vestigial: Temporal has been removed from the runtime (`DirectExecutionService` replaced it), so that field is not evidence Temporal is in use.

#### 🤖 Agent Framework

The `agents` group exposes the intelligent-routing (MASR) and direct-agent surfaces of the API.

##### Intelligent Query (MASR-routed)

Execute a query through the MASR router:

```bash
# Default research query
research-cli agents query "How will AI affect job markets?"

# Scope to domains and pick a query type
research-cli agents query "Analyze inflation impact on equities" \
  --domains finance --domains economics \
  --type analyze
```

> Under the `agents` group, `--domains` is a repeatable option — pass it once per domain (`--domains finance --domains economics`). It does **not** split on commas, so `--domains "finance,economics"` is sent to the API as a single domain named `finance,economics`. (Comma-separated domains are only supported by `projects create`.)

**Options:**
| Option | Short | Description | Default |
|--------|-------|-------------|---------|
| `--domains` | `-d` | Research domains (repeatable) | - |
| `--type` | `-t` | Query type (research/analyze/synthesize) | `research` |

##### Get Routing Decision

Compute a MASR routing decision with cost optimization (no execution):

```bash
research-cli agents route "Value a DCF for a SaaS company" --strategy quality_focused
```

**Options:**
| Option | Short | Description | Default |
|--------|-------|-------------|---------|
| `--strategy` | `-s` | Routing strategy (cost_efficient/quality_focused/balanced) | `balanced` |

##### Estimate Cost

Estimate execution cost with a component breakdown:

```bash
research-cli agents estimate "Comprehensive research on AI ethics" \
  --domains ai --domains ethics --domains philosophy
```

> As with `agents query`, `--domains` here is repeatable and is **not** comma-split — pass one `--domains` per domain.

**Options:**
| Option | Short | Description |
|--------|-------|-------------|
| `--domains` | `-d` | Research domains (repeatable) |

##### Direct Agent Execution (bypass)

Execute a single agent directly, bypassing MASR routing:

```bash
research-cli agents execute literature-review "Find papers on machine learning" \
  --max-sources 25
```

**Arguments/Options:**
| Argument/Option | Description |
|-----------------|-------------|
| `AGENT_TYPE` | Bypass agent type (e.g. `literature-review`, `synthesis`, `financial-analysis`) |
| `QUERY_TEXT` | Query text |
| `--max-sources` | Maximum sources (for `literature-review`) |

##### Chain-of-Agents

Run an ordered Chain-of-Agents workflow:

```bash
research-cli agents chain "Analyze AI impact" \
  --agents literature-review \
  --agents synthesis
```

**Options:**
| Option | Short | Description | Required |
|--------|-------|-------------|----------|
| `--agents` | `-a` | Agent chain, in order (repeatable) | Yes |

##### Router Status

Show MASR router health and performance metrics:

```bash
research-cli agents status
```

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
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃ ID                                   ┃ Title         ┃ Status      ┃ Created             ┃ User            ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ 550e8400-e29b-41d4-a716-446655440000 │ AI Research   │ pending     │ 2024-01-15 10:30    │ researcher-001  │
│ 6ba7b810-9dad-11d1-80b4-00c04fd430c8 │ Climate Study │ in_progress │ 2024-01-15 11:45    │ researcher-002  │
└──────────────────────────────────────┴───────────────┴─────────────┴─────────────────────┴─────────────────┘
```

The list table has five columns — `ID` (full 36-character UUID, not truncated), `Title`, `Status`, `Created`, and `User` — and status values are the raw enum values (`pending`, `in_progress`, `completed`, `failed`).

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
# Default Docker Compose setup — the API publishes port 8000 to the host,
# so a CLI installed locally can reach it directly.
research-cli --api-url http://localhost:8000 health

# Run the CLI inside the running API container instead. The Compose `api`
# service is built (no standalone `research-platform` image is published),
# its container is named `research-api`, and the `research-cli` entry point
# ships inside it. From within that container the API is on localhost:8000.
docker compose exec api research-cli --api-url http://localhost:8000 projects list

# Equivalent using the container name directly:
docker exec research-api research-cli --api-url http://localhost:8000 projects list
```

### Troubleshooting

#### Common Issues

**Connection Refused:**
```bash
# Check if API is running
curl http://localhost:8000/health

# Verify API URL configuration
research-cli config show | grep api_url

# Point the CLI at the correct API URL. `config set` only affects the current
# process, so use one of these persistent methods instead:
#   - per-command flag:  research-cli --api-url http://localhost:8001 health
#   - environment var:   export RESEARCH_API_URL=http://localhost:8001
#   - config file:       set RESEARCH_API_URL in ~/.research-cli.env (or run
#                        `research-cli config save` after exporting the env var)
```

**Authentication Errors (Future):**
```bash
# Set API key (environment variable — persists for subsequent commands)
export RESEARCH_API_KEY=your-api-key

# Or persist it to the config file (config set alone does NOT persist):
#   export RESEARCH_API_KEY=your-api-key && research-cli config save
```

**Timeout Issues:**
```bash
# Increase timeout for long operations. `config set` does not persist, so set
# it via the environment (per command or exported for the session):
RESEARCH_API_TIMEOUT=60 research-cli projects create --file large-batch.yaml

# Or persist it to the config file:
#   export RESEARCH_API_TIMEOUT=60 && research-cli config save
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
#    NOTE: `projects create` prints a human-readable "✓ Created project: ..."
#    line to stdout *before* the JSON body, so piping straight into `jq` fails.
#    Strip that leading line first (it is a single line) before parsing.
PROJECT_ID=$(research-cli --format json projects create \
  --title "AI Safety Research" \
  --query "What are the key challenges in AI alignment?" \
  --domains "AI,Safety,Ethics" \
  --depth exhaustive | tail -n +2 | jq -r '.id')

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
