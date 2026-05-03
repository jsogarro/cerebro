#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -n "${PYTHON:-}" ]]; then
  PYTHON_BIN="$PYTHON"
elif command -v python3.11 >/dev/null 2>&1; then
  PYTHON_BIN="python3.11"
elif command -v python3.12 >/dev/null 2>&1; then
  PYTHON_BIN="python3.12"
else
  echo "error: Python 3.11 or 3.12 is required. Set PYTHON=/path/to/python." >&2
  exit 1
fi

"$PYTHON_BIN" -m venv .venv
. .venv/bin/activate

python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements-dev.txt
python -m pip install -e .

echo "Environment ready. Activate it with: . .venv/bin/activate"
