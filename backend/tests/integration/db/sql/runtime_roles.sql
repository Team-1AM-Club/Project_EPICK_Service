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

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'epick_lookup') THEN
        CREATE ROLE epick_lookup NOLOGIN NOSUPERUSER NOBYPASSRLS;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'epick_deleter') THEN
        CREATE ROLE epick_deleter NOLOGIN NOSUPERUSER NOBYPASSRLS;
    END IF;
END
$$;
