BEGIN;

DO $$
DECLARE
    users_table_exists BOOLEAN;
    plaintext_password_column_exists BOOLEAN;
    missing_hash_count INTEGER;
    unsupported_hash_count INTEGER;
    current_scrypt_count INTEGER;
    supported_pbkdf2_count INTEGER;
BEGIN
    SELECT to_regclass('public.users') IS NOT NULL
    INTO users_table_exists;

    IF NOT users_table_exists THEN
        RAISE EXCEPTION
            'users table does not exist. Start the API once before running this migration.';
    END IF;

    SELECT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'users'
          AND column_name = 'password'
    )
    INTO plaintext_password_column_exists;

    IF plaintext_password_column_exists THEN
        RAISE EXCEPTION
            'Unsafe users.password column detected. Migrate or remove plaintext passwords before continuing.';
    END IF;

    SELECT COUNT(*)
    FROM users
    WHERE password_hash IS NULL
       OR btrim(password_hash) = ''
    INTO missing_hash_count;

    IF missing_hash_count > 0 THEN
        RAISE EXCEPTION
            'users.password_hash has % missing or blank values.',
            missing_hash_count;
    END IF;

    SELECT COUNT(*)
    FROM users
    WHERE password_hash !~ '^(scrypt|pbkdf2):[^$]+[$][^$]+[$][^$]+$'
    INTO unsupported_hash_count;

    IF unsupported_hash_count > 0 THEN
        RAISE EXCEPTION
            'users.password_hash has % unsupported or plaintext-looking values.',
            unsupported_hash_count;
    END IF;

    SELECT COUNT(*)
    FROM users
    WHERE password_hash ~ '^scrypt:[^$]+[$][^$]+[$][^$]+$'
    INTO current_scrypt_count;

    SELECT COUNT(*)
    FROM users
    WHERE password_hash ~ '^pbkdf2:[^$]+[$][^$]+[$][^$]+$'
    INTO supported_pbkdf2_count;

    RAISE NOTICE
        'User password hash audit passed. current_scrypt=%, supported_pbkdf2_pending_login_rehash=%',
        current_scrypt_count,
        supported_pbkdf2_count;
END $$;

ALTER TABLE users
    ALTER COLUMN password_hash TYPE VARCHAR(512),
    ALTER COLUMN password_hash SET NOT NULL;

ALTER TABLE users
    DROP CONSTRAINT IF EXISTS ck_users_password_hash_supported;

ALTER TABLE users
    ADD CONSTRAINT ck_users_password_hash_supported
    CHECK (
        password_hash ~ '^(scrypt|pbkdf2):[^$]+[$][^$]+[$][^$]+$'
    );

COMMIT;
