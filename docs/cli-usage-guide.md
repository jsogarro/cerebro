# CLI Usage Guide

## Overview

The Research Platform CLI (`research-cli`) provides a comprehensive command-line interface for managing research projects, monitoring progress, and generating reports. The CLI supports both interactive and batch operations with rich terminal output and real-time progress streaming.

## Installation

### From Source

```bash
# Clone repository
git clone https://github.com/your-org/research-platform.git
cd research-platform

# Install with development dependencies
uv pip install -e ".[dev]"

# Verify installation
research-cli --version
```

### Using pip

```bash
pip install research-platform-cli
```

### Using Docker

```bash
docker run --rm -it research-platform/cli:latest research-cli --help
```

## Configuration

### Environment Setup

Create a configuration file at `~/.research-cli/config.toml`:

```toml
[api]
url = "http://localhost:8000/api/v1"
timeout = 30

[auth]
token = "your-jwt-token"

[output]
format = "table"  # table, json, yaml, csv
color = true
verbose = false

[projects]
default_user_id = "your-user-id"
default_depth = "comprehensive"
```

### Environment Variables

The CLI respects the following environment variables:

```bash
# API Configuration
export RESEARCH_API_URL="http://localhost:8000/api/v1"
export RESEARCH_API_TOKEN="your-jwt-token"

# Output Configuration
export RESEARCH_CLI_FORMAT="table"
export RESEARCH_CLI_VERBOSE="true"
export RESEARCH_CLI_NO_COLOR="false"

# Project Defaults
export RESEARCH_DEFAULT_USER_ID="your-user-id"
export RESEARCH_DEFAULT_DEPTH="comprehensive"
```

### Authentication

#### Login

```bash
# Login with credentials
research-cli auth login --email user@example.com

# Login with API key
research-cli auth login --api-key your-api-key

# Login interactively
research-cli auth login --interactive
```

#### Token Management

```bash
# Check current token
research-cli auth status

# Refresh token
research-cli auth refresh

# Logout
research-cli auth logout
```

## Global Options

All commands support these global options:

```bash
research-cli [GLOBAL_OPTIONS] <command> [COMMAND_OPTIONS]
```

### Global Options

- `--api-url URL` - API base URL
- `--format, -f FORMAT` - Output format (table, json, yaml, csv)
- `--verbose, -v` - Enable verbose output
- `--no-color` - Disable colored output
- `--help` - Show help message

### Examples

```bash
# Use JSON output format
research-cli --format json projects list

# Enable verbose logging
research-cli --verbose projects create --title "Test"

# Use custom API URL
research-cli --api-url http://prod-api.com/v1 projects list
```

## Commands

### Configuration Commands

#### config

Manage CLI configuration.

```bash
# Show current configuration
research-cli config show

# Set configuration value
research-cli config set api.url "http://localhost:8000/api/v1"
research-cli config set output.format "json"
research-cli config set auth.token "your-token"

# Get configuration value
research-cli config get api.url

# Reset configuration to defaults
research-cli config reset

# Edit configuration interactively
research-cli config edit
```

#### health

Check API health and connectivity.

```bash
# Basic health check
research-cli health

# Detailed health check
research-cli health --detailed

# Health check with custom timeout
research-cli health --timeout 10
```

**Example Output:**
```
✓ API Status: healthy
✓ Response Time: 45ms
✓ Database: connected
✓ Redis: connected
✓ Temporal: connected
✓ WebSocket: available
```

#### completion

Generate shell completion scripts.

```bash
# Bash completion
research-cli completion bash > ~/.bash_completion.d/research-cli

# Zsh completion
research-cli completion zsh > ~/.zsh/completions/_research-cli

# Fish completion
research-cli completion fish > ~/.config/fish/completions/research-cli.fish
```

### Project Management Commands

#### projects create

Create a new research project.

**Basic Usage:**
```bash
research-cli projects create \
  --title "AI Impact on Healthcare" \
  --query "How does artificial intelligence improve healthcare outcomes?" \
  --domains "AI,Healthcare,Medicine" \
  --user-id "researcher-001"
```

**Options:**
- `--title, -t TEXT` - Project title (required)
- `--query, -q TEXT` - Research query text (required)
- `--domains, -d TEXT` - Research domains, comma-separated (required)
- `--user-id, -u TEXT` - User ID (default: "cli-user")
- `--depth CHOICE` - Research depth: survey, comprehensive, exhaustive (default: comprehensive)
- `--scope, -s TEXT` - Scope parameters as key=value pairs (multiple)
- `--interactive, -i` - Interactive mode
- `--file, -f PATH` - Load projects from file

**Interactive Mode:**
```bash
research-cli projects create --interactive
```

This will prompt you for:
- Project title
- Research query
- Domains (with suggestions)
- Research depth
- Additional scope parameters

**Batch Mode:**
```bash
research-cli projects create --file projects.yaml
```

Example `projects.yaml`:
```yaml
projects:
  - title: "AI in Education"
    query_text: "Impact of AI on educational outcomes"
    domains: ["AI", "Education"]
    user_id: "researcher-001"
    depth_level: "comprehensive"
    scope:
      max_sources: 100
      include_preprints: true
      
  - title: "Machine Learning in Finance"
    query_text: "Applications of ML in financial services"
    domains: ["ML", "Finance"]
    user_id: "researcher-002"
    depth_level: "survey"
```

**Advanced Examples:**
```bash
# Create with custom scope
research-cli projects create \
  --title "Climate Change Research" \
  --query "Effects of climate change on agriculture" \
  --domains "Climate,Agriculture,Environment" \
  --scope "timeframe=last_10_years" \
  --scope "geographic_scope=global" \
  --scope "include_preprints=true"

# Create with exhaustive depth
research-cli projects create \
  --title "Comprehensive AI Survey" \
  --query "Latest developments in artificial intelligence" \
  --domains "AI,ML,DeepLearning" \
  --depth exhaustive \
  --scope "max_sources=500"
```

#### projects get

Get details of a specific project.

```bash
# Get project by ID
research-cli projects get proj-550e8400-e29b-41d4-a716-446655440000

# Get with JSON output
research-cli --format json projects get proj-123
```

**Example Output:**
```
┌─────────────────────────────────────────────────────────────────────┐
│                            Project Details                         │
├─────────────────────────────────────────────────────────────────────┤
│ ID: proj-550e8400-e29b-41d4-a716-446655440000                      │
│ Title: AI Impact on Healthcare                                      │
│ Status: in_progress                                                 │
│ Progress: 67%                                                       │
│ Created: 2024-01-01 12:00:00                                        │
│ User ID: researcher-001                                             │
│                                                                     │
│ Query: How does artificial intelligence improve healthcare outcomes? │
│ Domains: AI, Healthcare, Medicine                                   │
│                                                                     │
│ Scope:                                                              │
│ • Research Depth: comprehensive                                     │
│ • Max Sources: 100                                                  │
│ • Include Preprints: Yes                                            │
│ • Geographic Scope: global                                          │
└─────────────────────────────────────────────────────────────────────┘
```

#### projects list

List research projects with filtering.

```bash
# List all projects
research-cli projects list

# Filter by user
research-cli projects list --user-id researcher-001

# Filter by status
research-cli projects list --status in_progress

# Combine filters with pagination
research-cli projects list \
  --user-id researcher-001 \
  --status completed \
  --limit 20 \
  --offset 0
```

**Options:**
- `--user-id, -u TEXT` - Filter by user ID
- `--status, -s TEXT` - Filter by status (pending, in_progress, completed, failed, cancelled)
- `--limit, -l INTEGER` - Maximum results (default: 10)
- `--offset, -o INTEGER` - Pagination offset (default: 0)

**Example Output:**
```
┌──────────────────────────────────┬────────────────────────────┬─────────────┬──────────┬─────────────────────┐
│ ID                               │ Title                      │ Status      │ Progress │ Created             │
├──────────────────────────────────┼────────────────────────────┼─────────────┼──────────┼─────────────────────┤
│ proj-550e8400-e29b-41d4-a716-... │ AI Impact on Healthcare    │ in_progress │ 67%      │ 2024-01-01 12:00:00 │
│ proj-550e8400-e29b-41d4-a716-... │ ML in Finance              │ completed   │ 100%     │ 2024-01-01 10:00:00 │
│ proj-550e8400-e29b-41d4-a716-... │ Climate Change Study       │ pending     │ 0%       │ 2024-01-01 14:00:00 │
└──────────────────────────────────┴────────────────────────────┴─────────────┴──────────┴─────────────────────┘

Showing 3 results. Use --offset to see more.
```

#### projects progress

Monitor project progress with multiple modes.

**Single Progress Check:**
```bash
research-cli projects progress proj-550e8400-e29b-41d4-a716-446655440000
```

**Real-time Streaming (WebSocket):**
```bash
# Stream progress updates in real-time
research-cli projects progress proj-123 --stream

# Stream with verbose logging
research-cli projects progress proj-123 --stream --verbose
```

**Polling Mode (Legacy):**
```bash
# Watch progress with polling
research-cli projects progress proj-123 --watch

# Custom polling interval
research-cli projects progress proj-123 --watch --interval 10
```

**Options:**
- `--watch, -w` - Watch progress in real-time (polling mode)
- `--stream, -s` - Stream progress via WebSocket (real-time)
- `--interval, -i INTEGER` - Update interval in seconds (polling mode only, default: 5)

**Note:** `--stream` and `--watch` are mutually exclusive. The CLI automatically falls back to polling if WebSocket connection fails.

**Example Output (Streaming Mode):**
```
┌─────────────────────────────────────────────────────────────────────┐
│                          Project Progress                           │
├─────────────────────────────────────────────────────────────────────┤
│ Project: proj-550e8400-e29b-41d4-a716-446655440000                  │
│ Title: AI Impact on Healthcare                                      │
│                                                                     │
│ Overall Progress: ████████████████░░░░ 67% (3/5 tasks)             │
│                                                                     │
│ Agent Status:                                                       │
│ ✓ Literature Review    │ Completed │ Confidence: 92%                │
│ ✓ Comparative Analysis │ Completed │ Confidence: 88%                │
│ ✓ Methodology         │ Completed │ Confidence: 85%                │
│ ⟳ Synthesis           │ In Progress (40%) │ Running...              │
│ ⏸ Citation            │ Pending   │ Waiting...                      │
│                                                                     │
│ Current Phase: Analysis                                             │
│ ETA: ~15 minutes                                                    │
│                                                                     │
│ Last Update: 2024-01-01 12:15:23                                    │
└─────────────────────────────────────────────────────────────────────┘

⟳ Literature Review Agent: Found 147 papers, analyzing key findings...
⟳ Synthesis Agent: Integrating results from 3 agents...
```

#### projects cancel

Cancel a running research project.

```bash
# Cancel with confirmation
research-cli projects cancel proj-550e8400-e29b-41d4-a716-446655440000

# Force cancel without confirmation
research-cli projects cancel proj-123 --force
```

**Options:**
- `--force, -f` - Skip confirmation prompt

#### projects results

Get results from a completed project.

```bash
# Get results (JSON output)
research-cli projects results proj-550e8400-e29b-41d4-a716-446655440000

# Save results to file
research-cli projects results proj-123 --output results.json

# Get results in different formats
research-cli --format yaml projects results proj-123 --output results.yaml
```

**Options:**
- `--output, -o PATH` - Save results to file

**Example Output:**
```json
{
  "project_id": "proj-550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "completion_time": "2024-01-01T12:28:00Z",
  "results": {
    "literature_review": {
      "papers_found": 147,
      "papers_analyzed": 89,
      "key_findings": [
        "AI diagnostic tools show 15% improvement in accuracy",
        "Cost reduction of 23% in diagnostic workflows"
      ]
    },
    "synthesis": {
      "main_conclusions": "AI demonstrates significant potential...",
      "confidence_score": 0.87,
      "research_gaps": ["Long-term outcome studies"]
    }
  }
}
```

#### projects refine

Refine the scope of an existing project.

```bash
# Refine with scope parameters
research-cli projects refine proj-123 \
  --scope "max_sources=200" \
  --scope "include_preprints=false" \
  --languages "en,es"

# Refine with specific options
research-cli projects refine proj-123 \
  --max-sources 150 \
  --languages "en,fr,de"
```

**Options:**
- `--scope, -s TEXT` - Scope parameters as key=value pairs (multiple)
- `--max-sources INTEGER` - Maximum number of sources
- `--languages TEXT` - Languages, comma-separated

## Output Formats

The CLI supports multiple output formats controlled by the `--format` flag:

### Table Format (Default)

Rich, colored tables with borders and formatting:

```bash
research-cli projects list --format table
```

### JSON Format

Machine-readable JSON output:

```bash
research-cli --format json projects list
```

```json
[
  {
    "id": "proj-123",
    "title": "AI Impact Study",
    "status": "completed",
    "progress_percentage": 100.0,
    "created_at": "2024-01-01T12:00:00Z"
  }
]
```

### YAML Format

Human-readable YAML output:

```bash
research-cli --format yaml projects get proj-123
```

```yaml
id: proj-550e8400-e29b-41d4-a716-446655440000
title: AI Impact on Healthcare
status: in_progress
progress_percentage: 67.0
created_at: '2024-01-01T12:00:00Z'
query:
  text: How does artificial intelligence improve healthcare outcomes?
  domains:
    - AI
    - Healthcare
    - Medicine
```

### CSV Format

Spreadsheet-compatible CSV output:

```bash
research-cli --format csv projects list > projects.csv
```

```csv
id,title,status,progress_percentage,created_at
proj-123,AI Impact Study,completed,100.0,2024-01-01T12:00:00Z
proj-456,ML in Finance,in_progress,45.0,2024-01-01T14:00:00Z
```

## Advanced Features

### Batch Operations

#### Creating Multiple Projects

Create multiple projects from a configuration file:

```yaml
# projects.yaml
projects:
  - title: "AI in Education"
    query_text: "Impact of AI on educational outcomes"
    domains: ["AI", "Education"]
    user_id: "researcher-001"
    depth_level: "comprehensive"
    
  - title: "Blockchain in Supply Chain"
    query_text: "Blockchain applications in supply chain management"
    domains: ["Blockchain", "Supply Chain"]
    user_id: "researcher-002"
    depth_level: "survey"
```

```bash
research-cli projects create --file projects.yaml
```

#### Bulk Status Monitoring

Monitor multiple projects:

```bash
# List all in-progress projects
research-cli projects list --status in_progress

# Watch multiple projects (script example)
#!/bin/bash
for project_id in $(research-cli --format json projects list --status in_progress | jq -r '.[].id'); do
  echo "Checking $project_id..."
  research-cli projects progress "$project_id"
done
```

### Real-time Progress Streaming

The CLI supports real-time progress updates via WebSocket connections:

```bash
# Stream progress with Rich terminal output
research-cli projects progress proj-123 --stream
```

Features:
- Live progress bars and status updates
- Agent-level progress tracking
- Automatic fallback to polling if WebSocket fails
- Rich terminal formatting with colors and icons

### Shell Integration

#### Aliases

Add common aliases to your shell configuration:

```bash
# ~/.bashrc or ~/.zshrc
alias rp="research-cli"
alias rpl="research-cli projects list"
alias rpc="research-cli projects create"
alias rpg="research-cli projects get"
alias rpp="research-cli projects progress"

# Usage
rpl --status in_progress
rpc --interactive
```

#### Shell Functions

Create shell functions for complex operations:

```bash
# Monitor project until completion
monitor_project() {
  local project_id=$1
  echo "Monitoring project $project_id..."
  research-cli projects progress "$project_id" --stream
}

# Create and monitor project
create_and_monitor() {
  local title="$1"
  local query="$2"
  local domains="$3"
  
  local project_id=$(research-cli --format json projects create \
    --title "$title" \
    --query "$query" \
    --domains "$domains" | jq -r '.id')
  
  echo "Created project: $project_id"
  monitor_project "$project_id"
}
```

### Configuration Management

#### Multiple Profiles

Manage different API environments:

```bash
# Create production profile
research-cli config set --profile prod api.url "https://api.researchplatform.com/v1"
research-cli config set --profile prod auth.token "prod-token"

# Create development profile
research-cli config set --profile dev api.url "http://localhost:8000/api/v1"
research-cli config set --profile dev auth.token "dev-token"

# Use specific profile
research-cli --profile prod projects list
```

#### Environment-Specific Settings

```toml
# ~/.research-cli/config.toml
[profiles.development]
api_url = "http://localhost:8000/api/v1"
token = "dev-token"
verbose = true

[profiles.staging]
api_url = "https://staging-api.researchplatform.com/v1"
token = "staging-token"
verbose = false

[profiles.production]
api_url = "https://api.researchplatform.com/v1"
token = "prod-token"
verbose = false
```

## Error Handling

### Common Errors and Solutions

#### Authentication Errors

```bash
Error: Unauthorized (401)
Solution: Check your token with 'research-cli auth status'
```

#### Connection Errors

```bash
Error: Connection refused
Solution: Verify API URL with 'research-cli health'
```

#### Project Not Found

```bash
Error: Project not found (404)
Solution: Verify project ID with 'research-cli projects list'
```

### Debugging

Enable verbose output for debugging:

```bash
# Verbose mode shows HTTP requests and responses
research-cli --verbose projects create --title "Debug Test"

# Check configuration
research-cli config show

# Test API connectivity
research-cli health --detailed
```

### Logging

The CLI logs to `~/.research-cli/logs/` by default:

```bash
# View recent logs
tail -f ~/.research-cli/logs/research-cli.log

# Log rotation and cleanup
research-cli config set logging.max_files 10
research-cli config set logging.max_size_mb 50
```

## Performance and Optimization

### Caching

The CLI caches certain responses to improve performance:

```bash
# Clear cache
research-cli config cache clear

# Disable caching
research-cli config set cache.enabled false

# Set cache TTL
research-cli config set cache.ttl_seconds 300
```

### Concurrent Operations

For batch operations, the CLI supports concurrent execution:

```bash
# Set max concurrent requests
research-cli config set api.max_concurrent 5

# Batch create with concurrency
research-cli projects create --file large-batch.yaml --concurrent 3
```

## Integration Examples

### CI/CD Integration

#### GitHub Actions

```yaml
name: Research Pipeline
on: [push]

jobs:
  research:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Install CLI
        run: pip install research-platform-cli
        
      - name: Create Research Project
        run: |
          research-cli projects create \
            --title "Automated Research: ${{ github.sha }}" \
            --query "Latest developments in ${{ github.event.head_commit.message }}" \
            --domains "Technology,Software" \
            --user-id "ci-bot"
        env:
          RESEARCH_API_TOKEN: ${{ secrets.RESEARCH_API_TOKEN }}
```

#### Jenkins Pipeline

```groovy
pipeline {
    agent any
    environment {
        RESEARCH_API_TOKEN = credentials('research-api-token')
    }
    stages {
        stage('Research') {
            steps {
                script {
                    def projectId = sh(
                        script: '''
                            research-cli --format json projects create \
                                --title "Jenkins Research ${BUILD_NUMBER}" \
                                --query "CI/CD best practices" \
                                --domains "DevOps,CI/CD" | jq -r '.id'
                        ''',
                        returnStdout: true
                    ).trim()
                    
                    sh "research-cli projects progress ${projectId} --stream"
                }
            }
        }
    }
}
```

### Scripting Examples

#### Bash Script for Daily Research

```bash
#!/bin/bash
# daily-research.sh

set -e

# Configuration
USER_ID="daily-bot"
TODAY=$(date +%Y-%m-%d)
DOMAINS="AI,Technology,Science"

# Create daily research project
echo "Creating daily research project for $TODAY..."
PROJECT_ID=$(research-cli --format json projects create \
  --title "Daily Research Digest - $TODAY" \
  --query "Latest developments in AI and technology for $TODAY" \
  --domains "$DOMAINS" \
  --user-id "$USER_ID" \
  --depth survey | jq -r '.id')

echo "Created project: $PROJECT_ID"

# Monitor progress
echo "Monitoring progress..."
research-cli projects progress "$PROJECT_ID" --stream

# Get results and save
echo "Saving results..."
research-cli projects results "$PROJECT_ID" --output "daily-research-$TODAY.json"

echo "Daily research completed: daily-research-$TODAY.json"
```

#### Python Integration

```python
#!/usr/bin/env python3
import subprocess
import json
import time

def run_cli_command(command):
    """Run CLI command and return JSON result."""
    result = subprocess.run(
        ["research-cli", "--format", "json"] + command,
        capture_output=True,
        text=True,
        check=True
    )
    return json.loads(result.stdout)

def create_research_project(title, query, domains):
    """Create a research project."""
    command = [
        "projects", "create",
        "--title", title,
        "--query", query,
        "--domains", ",".join(domains)
    ]
    return run_cli_command(command)

def monitor_project(project_id):
    """Monitor project until completion."""
    while True:
        command = ["projects", "progress", project_id]
        progress = run_cli_command(command)
        
        print(f"Progress: {progress['progress_percentage']:.1f}%")
        
        if progress['progress_percentage'] >= 100:
            break
            
        time.sleep(30)

# Example usage
if __name__ == "__main__":
    project = create_research_project(
        title="Python AI Research",
        query="Latest Python libraries for AI development",
        domains=["Python", "AI", "Programming"]
    )
    
    print(f"Created project: {project['id']}")
    monitor_project(project['id'])
```

## Troubleshooting

### Common Issues

#### WebSocket Connection Failed

```bash
# Test WebSocket connectivity
research-cli health --detailed

# Fallback to polling mode
research-cli projects progress proj-123 --watch

# Check firewall/proxy settings
export HTTP_PROXY=http://proxy.company.com:8080
export HTTPS_PROXY=http://proxy.company.com:8080
```

#### Token Expiration

```bash
# Check token status
research-cli auth status

# Refresh token
research-cli auth refresh

# Re-login if refresh fails
research-cli auth login
```

#### Performance Issues

```bash
# Reduce concurrent requests
research-cli config set api.max_concurrent 1

# Increase timeout
research-cli config set api.timeout 60

# Disable verbose output
research-cli config set output.verbose false
```

### Support

For issues and questions:

- GitHub Issues: https://github.com/your-org/research-platform/issues
- Documentation: https://docs.researchplatform.com
- CLI Reference: `research-cli --help`
- Command Help: `research-cli <command> --help`

### Version Information

```bash
# Check CLI version
research-cli --version

# Check API compatibility
research-cli health --version-check

# Update CLI
pip install --upgrade research-platform-cli
```

This comprehensive CLI guide provides everything needed to effectively use the Research Platform command-line interface, from basic operations to advanced automation and integration scenarios.