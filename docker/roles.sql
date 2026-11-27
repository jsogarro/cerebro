-- Database roles for the API. Idempotent: safe to run on every start, before
-- migrations. docker/apply_roles.py runs it through an admin connection and
-- supplies the passwords as the session settings read below, so no secret
-- lives in this file.
--
--   cerebro_migrator  owns the schema (existing objects are handed over below,
--                     later ones by migrating as this role); BYPASSRLS so
--                     backfills work under FORCE.
--   cerebro_app       runtime role; owns nothing, DML grants only, bound by RLS.
--   cerebro_maint     cross-tenant jobs; sees only what a migration-created
--                     "TO cerebro_maint" policy admits.
DO $$
DECLARE
    spec record;
BEGIN
    FOR spec IN
        SELECT * FROM (VALUES
            ('cerebro_migrator', 'cerebro.migrator_password', 'BYPASSRLS'),
            ('cerebro_app',      'cerebro.app_password',      'NOBYPASSRLS'),
            ('cerebro_maint',    'cerebro.maint_password',    'NOBYPASSRLS')
        ) AS t(name, password_setting, rls_attribute)
    LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = spec.name) THEN
            EXECUTE format('CREATE ROLE %I', spec.name);
        END IF;
        IF coalesce(current_setting(spec.password_setting), '') = '' THEN
            RAISE EXCEPTION 'empty password for role %', spec.name;
        END IF;
        EXECUTE format(
            'ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE %s PASSWORD %L',
            spec.name,
            spec.rls_attribute,
            current_setting(spec.password_setting)
        );
    END LOOP;
END
$$;

GRANT USAGE, CREATE ON SCHEMA public TO cerebro_migrator;
GRANT USAGE ON SCHEMA public TO cerebro_app, cerebro_maint;

-- The runtime role gets DML on every table and sequence, existing and future.
-- Tables the migrator or the admin creates later inherit the grants through the
-- default privileges below; cerebro_maint is granted per table by the migration.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO cerebro_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO cerebro_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO cerebro_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO cerebro_app;
ALTER DEFAULT PRIVILEGES FOR ROLE cerebro_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO cerebro_app;
ALTER DEFAULT PRIVILEGES FOR ROLE cerebro_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO cerebro_app;

-- Hand existing schema objects to the migrator so it can run every migration,
-- downgrades included. Extension members stay with their extension.
DO $$
DECLARE
    obj record;
BEGIN
    FOR obj IN
        SELECT 'TABLE' AS kind, format('%I.%I', n.nspname, c.relname) AS name
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm')
          AND pg_get_userbyid(c.relowner) <> 'cerebro_migrator'
        UNION ALL
        SELECT 'TYPE', format('%I.%I', n.nspname, t.typname)
        FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public' AND t.typtype = 'e'
          AND pg_get_userbyid(t.typowner) <> 'cerebro_migrator'
        UNION ALL
        SELECT 'FUNCTION', p.oid::regprocedure::text
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.prokind = 'f'
          AND pg_get_userbyid(p.proowner) <> 'cerebro_migrator'
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e'
          )
    LOOP
        EXECUTE format('ALTER %s %s OWNER TO cerebro_migrator', obj.kind, obj.name);
    END LOOP;
END
$$;

-- The ledger is the migrator's alone.
DO $$
BEGIN
    IF to_regclass('public.alembic_version') IS NOT NULL THEN
        REVOKE ALL ON public.alembic_version FROM cerebro_app, cerebro_maint;
    END IF;
END
$$;
