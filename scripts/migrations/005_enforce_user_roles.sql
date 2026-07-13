BEGIN;

DO $$
DECLARE
    users_table_exists BOOLEAN;
    legacy_role_count INTEGER;
BEGIN
    SELECT to_regclass('public.users') IS NOT NULL
    INTO users_table_exists;

    IF NOT users_table_exists THEN
        RAISE EXCEPTION
            'users table does not exist. Start the API once before running this migration.';
    END IF;

    SELECT COUNT(*)
    FROM users
    WHERE role IS NULL
       OR btrim(role) = ''
       OR lower(btrim(role)) NOT IN (
            'admin',
            'administrator',
            'administrador',
            'operador',
            'operator',
            'visor',
            'viewer',
            'user',
            'usuario'
       )
    INTO legacy_role_count;

    RAISE NOTICE
        'User role audit: % empty or unknown roles will be normalized to visor.',
        legacy_role_count;
END $$;

UPDATE users
SET role = CASE
    WHEN lower(btrim(COALESCE(role, ''))) IN (
        'admin',
        'administrator',
        'administrador'
    ) THEN 'admin'
    WHEN lower(btrim(COALESCE(role, ''))) IN (
        'operador',
        'operator'
    ) THEN 'operador'
    WHEN lower(btrim(COALESCE(role, ''))) IN (
        'visor',
        'viewer',
        'user',
        'usuario'
    ) THEN 'visor'
    ELSE 'visor'
END;

ALTER TABLE users
    ALTER COLUMN role TYPE VARCHAR(20),
    ALTER COLUMN role SET DEFAULT 'visor',
    ALTER COLUMN role SET NOT NULL;

ALTER TABLE users
    DROP CONSTRAINT IF EXISTS ck_users_role_valid;

ALTER TABLE users
    ADD CONSTRAINT ck_users_role_valid
    CHECK (role IN ('admin', 'operador', 'visor'));

DO $$
DECLARE
    admin_count INTEGER;
    operador_count INTEGER;
    visor_count INTEGER;
BEGIN
    SELECT COUNT(*) FROM users WHERE role = 'admin' INTO admin_count;
    SELECT COUNT(*) FROM users WHERE role = 'operador' INTO operador_count;
    SELECT COUNT(*) FROM users WHERE role = 'visor' INTO visor_count;

    RAISE NOTICE
        'User role normalization complete. admin=%, operador=%, visor=%',
        admin_count,
        operador_count,
        visor_count;
END $$;

COMMIT;
