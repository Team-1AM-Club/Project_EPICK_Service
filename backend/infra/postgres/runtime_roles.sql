-- Execute this template as a database administrator before assigning the login principals.
-- The NOLOGIN roles are privilege groups. Production login roles are granted membership by the
-- platform operator; the API, worker, and deletion paths must not use epick_migrator.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'epick_migrator') THEN
        CREATE ROLE epick_migrator NOLOGIN NOSUPERUSER NOBYPASSRLS;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'epick_runtime') THEN
        CREATE ROLE epick_runtime NOLOGIN NOSUPERUSER NOBYPASSRLS;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'epick_worker') THEN
        CREATE ROLE epick_worker NOLOGIN NOSUPERUSER NOBYPASSRLS;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'epick_deleter') THEN
        CREATE ROLE epick_deleter NOLOGIN NOSUPERUSER NOBYPASSRLS;
    END IF;
END
$$;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO epick_migrator;
GRANT USAGE ON SCHEMA public TO epick_runtime, epick_worker, epick_deleter;

-- Table, sequence, and default-privilege grants are applied after each migration by the
-- migration principal or deployment role. Runtime group roles never receive schema CREATE,
-- database CREATE, SUPERUSER, or BYPASSRLS privileges.
