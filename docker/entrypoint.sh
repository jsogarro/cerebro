#!/bin/sh
set -eu

if [ -n "${ROLES_ADMIN_DATABASE_URL:-}" ]; then
    echo "Applying database roles..."
    python /docker/apply_roles.py
fi

echo "Running database migrations..."
if DATABASE_URL="${MIGRATION_DATABASE_URL:-$DATABASE_URL}" alembic upgrade head; then
    echo "Database migrations completed."
else
    migration_status=$?
    echo "Database migrations failed with exit status ${migration_status}; refusing to start." >&2
    exit "${migration_status}"
fi

if [ "$#" -eq 0 ]; then
    echo "No application command supplied; refusing to start." >&2
    exit 64
fi

echo "Starting application..."
exec "$@"
