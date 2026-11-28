#!/bin/sh
set -eu

# A deployment that migrates in its own one-shot service sets SKIP_MIGRATIONS=1
# so the runtime container never holds the admin or owner credential.
if [ "${SKIP_MIGRATIONS:-0}" != "1" ]; then
    if [ -n "${ROLES_ADMIN_DATABASE_URL:-}" ]; then
        echo "Applying database roles..."
        python /docker/apply_roles.py
    fi

    echo "Running database migrations..."
    if alembic upgrade head; then
        echo "Database migrations completed."
    else
        migration_status=$?
        echo "Database migrations failed with exit status ${migration_status}; refusing to start." >&2
        exit "${migration_status}"
    fi
fi

if [ "$#" -eq 0 ]; then
    echo "No application command supplied; refusing to start." >&2
    exit 64
fi

echo "Starting application..."
exec "$@"
