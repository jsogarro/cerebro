#!/usr/bin/env bash
# Install exactly the versions uv.lock pins. `--locked` fails when pyproject.toml
# has changed without a lock refresh, so a dependency edit cannot silently install
# whatever the open ranges resolve to that day. Extra flags (e.g. `--extra dev`)
# pass through to `uv export`. Set UV_SYSTEM_PYTHON=1 to install outside a venv.
set -euo pipefail

requirements="$(mktemp)"
trap 'rm -f "$requirements"' EXIT

uv export --locked --no-emit-project --format requirements-txt "$@" -o "$requirements"
uv pip install -r "$requirements"
uv pip install --no-deps -e .
