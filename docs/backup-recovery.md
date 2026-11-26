# Backup and recovery

The repository ships no backup scripts beyond two Make targets, `make db-backup` and `make backup-all`. This guide lists what holds state, the commands that back it up and restore it and what losing each store costs. Commands use the development stack (`docker compose`); for the production stack add `-f docker-compose.production.yml`, and supply the passwords it requires.

## What holds state

**Postgres** holds every application table. See [data model and persistence](reference/14-data-model-and-persistence.md). Back it up first; nothing else can rebuild it.

**Redis** holds short-lived or derived state. Each key family below is written by code in this repository:

| Key prefix | Written by | Effect of losing it |
|---|---|---|
| `blacklist:token:<jti>` | `JWTService` (logout) | A revoked access token that is still within its lifetime validates again |
| `blacklist:user:<user_id>` | `JWTService.revoke_all_user_tokens` (also called by the GDPR erasure route) | Tokens issued before the revocation validate again until they expire |
| `refresh:token:<jti>` | `JWTService` (login, refresh) | Refresh fails with "Refresh token not found or expired"; users log in again |
| `password:history:<user_id>` | `PasswordService` | The password-reuse history is empty |
| `password:reset:<token>`, `email:verification:<token>` | `PasswordService` | Outstanding reset and verification links stop working |
| `idempotency:<hash>` and `idempotency:<hash>:pending` | `RedisIdempotencyStore` (24-hour records, 300-second reservations) | A retried `POST /api/v1/query/*` with the same `Idempotency-Key` is processed again instead of replaying the stored response |
| `rate_limit:user:<id>`, `rate_limit:ip:<address>` | `RedisRateLimitStore` | Request counters reset |
| `login_fail:*` | `LoginThrottle` | Failed-login counters and lockouts reset |
| `cerebro:masr:adaptive:{1:masr-adaptive-v1}:state` and `...:outcome:<digest>` | `RedisAdaptiveStateStore` | Only exists when `ADAPTIVE_ROUTING_ENABLED=true` (default `false`). `RedisAdaptiveStateStore.load` reports `missing` and routing starts without restored adaptive state |

The blacklist, user-revocation and refresh keys are security state: restore Redis from a snapshot taken at or after the last revocation rather than starting empty, or force every user to re-authenticate by rotating the JWT key pair. The routing decision cache is an in-process dictionary in the API process, not Redis, so it needs no backup.

**Filesystem.** The API container reads and writes these paths:

- `SOURCE_SNAPSHOT_DIR` (default `data/source_snapshots`, resolved against the working directory): verbatim source snapshots that evidence rows cite by digest. Losing it leaves evidence rows whose files are gone.
- `REPORT_STORAGE_PATH` (default `./reports`): generated report files.
- The JWT key pair at `JWT_PRIVATE_KEY_PATH` and `JWT_PUBLIC_KEY_PATH` (default `/secrets/jwt_private.pem` and `/secrets/jwt_public.pem`). The pair signs and verifies tokens; replacing it invalidates every issued token.

In the development compose file, `source_snapshots` is a named volume mounted at `/var/lib/cerebro/source_snapshots`. In the production compose file the `source_snapshots_prod` volume is mounted at the same path, which the production image sets as the default `SOURCE_SNAPSHOT_DIR`. The Kubernetes manifests declare no volume for it; see [deployment and monitoring](deployment-monitoring-guide.md).

## Back up Postgres

`make db-backup` creates `backups/` and writes a plain-SQL dump:

```bash
./scripts/compose.sh exec postgres pg_dump -U research research_db > backups/backup_$(date +%Y%m%d_%H%M%S).sql
```

The equivalent direct command, with `-T` so no terminal is allocated:

```bash
docker compose exec -T postgres pg_dump -U research research_db > backup.sql
```

The development compose file sets the role `research`, the password `research123` and the database `research_db`. The production file uses the same role and database with `DB_PASSWORD`.

## Restore Postgres

Stop the API so nothing writes during the restore, drop and recreate the database, load the dump and start the API again:

```bash
docker compose stop api
docker compose exec -T postgres psql -U research -d postgres -c "DROP DATABASE research_db"
docker compose exec -T postgres psql -U research -d postgres -c "CREATE DATABASE research_db"
docker compose exec -T postgres psql -U research -d research_db < backup.sql
docker compose up -d api
```

`DROP DATABASE` deletes all data in `research_db`. The container entrypoint runs `alembic upgrade head` on every start, so starting the API applies any migration newer than the dump. The extensions and schemas that `docker/init.sql` creates are part of a plain dump, but `init.sql` itself runs only when the Postgres data directory is empty.

## Redis

Snapshot the dataset to the container's data directory and copy it out:

```bash
mkdir -p backups
docker compose exec redis redis-cli BGSAVE
docker compose exec redis redis-cli LASTSAVE
docker compose cp redis:/data/dump.rdb ./backups/dump.rdb
```

Run `LASTSAVE` until its timestamp changes to confirm the save finished. The production Redis requires a password: add `-a "$REDIS_PASSWORD" --no-auth-warning` to each `redis-cli` call. The development service runs with `--appendonly yes`, so its volume also keeps an append-only file.

To restore:

1. Stop Redis.
2. If append-only persistence is on, remove the append-only files from `/data` of the volume, because Redis loads them in preference to `dump.rdb`.
3. Place `dump.rdb` in `/data`.
4. Start Redis.

With an empty Redis, the effects in the table above apply.

## Files

Copy the directories from the container (or the volume that backs them) to a backup location:

```bash
mkdir -p backups
docker compose cp api:/var/lib/cerebro/source_snapshots ./backups/source_snapshots
docker compose cp api:/app/reports ./backups/reports
```

Back up the JWT key files from wherever they are provisioned (`JWT_PRIVATE_KEY_FILE` and `JWT_PUBLIC_KEY_FILE` for the production compose file, the `research-platform-jwt-keys` secret in Kubernetes).

## make backup-all

The recipe, as written in `Makefile`:

```make
backup-all:
	@mkdir -p backups/$$(date +%Y%m%d)
	make db-backup
	./scripts/compose.sh exec redis redis-cli --rdb /tmp/redis_backup.rdb
	./scripts/compose.sh cp redis:/tmp/redis_backup.rdb backups/$$(date +%Y%m%d)/redis_backup.rdb
	@echo "Full system backup created in backups/$$(date +%Y%m%d)/"
```

The target:

- creates a dated directory on the host;
- runs `make db-backup`, which writes to `backups/`, not the dated directory;
- dumps Redis inside the container and copies the file to the dated directory;
- does not copy snapshots, reports or keys.

Use the commands above for a complete backup.
