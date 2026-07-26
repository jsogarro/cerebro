#!/usr/bin/env bash
set -euo pipefail

project_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
project_env_file="${project_directory}/.env"
home_directory="${HOME:-}"

compose_arguments=(--project-directory "$project_directory")

if [[ -f "$project_env_file" ]]; then
    compose_arguments+=(--env-file "$project_env_file")
fi

if [[ -n "$home_directory" && -f "${home_directory}/.env" ]]; then
    compose_arguments+=(--env-file "${home_directory}/.env")
fi

exec docker compose "${compose_arguments[@]}" "$@"
