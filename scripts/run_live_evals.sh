#!/usr/bin/env bash
# Live evaluation suite runner
# Checks environment, runs pytest with live_eval marker, generates reports

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
EVALS_OUT_DIR="$PROJECT_ROOT/evals/out"

echo "=== Live Eval Suite Runner ==="
echo "Project root: $PROJECT_ROOT"
echo ""

# Environment checks
if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
    echo "ERROR: OPENROUTER_API_KEY not set"
    echo "Export OPENROUTER_API_KEY before running live evals"
    exit 1
fi

if [[ "${ENABLE_LIVE_EVAL:-0}" != "1" ]]; then
    echo "ERROR: ENABLE_LIVE_EVAL not set to 1"
    echo "Export ENABLE_LIVE_EVAL=1 to confirm intent to run live provider calls"
    exit 1
fi

echo "✓ OPENROUTER_API_KEY set"
echo "✓ ENABLE_LIVE_EVAL=1"
echo ""

# Budget check
BUDGET="${LIVE_EVAL_BUDGET_USD:-0.25}"
echo "Cost budget: \$${BUDGET}"
echo ""

# Create output directory
mkdir -p "$EVALS_OUT_DIR"

# Run pytest with live_eval marker
echo "Running live eval suite..."
cd "$PROJECT_ROOT"

# Use --no-cov to avoid coverage overhead for live evals
# Use -v for verbose output showing each test
# Use -m live_eval to run only live eval tests
pytest evals/live -m live_eval -v --no-cov --tb=short

# Capture pytest exit code
PYTEST_EXIT=$?

# Generate report from pytest session (report.py will be called from conftest finalizer)
# For now, we'll use a simple Python script to generate the report
python3 << 'EOFPYTHON'
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent if hasattr(Path(__file__), 'parent') else Path.cwd()
sys.path.insert(0, str(project_root))

# This would be called from conftest session finalizer in production
# For this runner, we assume conftest already wrote the report
print("\nReport generation delegated to pytest session finalizer")
EOFPYTHON

echo ""
if [[ $PYTEST_EXIT -eq 0 ]]; then
    echo "✅ Live eval suite PASSED"
    echo "Reports available in: $EVALS_OUT_DIR"
else
    echo "❌ Live eval suite FAILED (exit code: $PYTEST_EXIT)"
    exit $PYTEST_EXIT
fi
