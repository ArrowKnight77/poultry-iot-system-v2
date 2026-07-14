BEGIN;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS mfa_enabled BOOLEAN;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS mfa_secret_encrypted VARCHAR(512);

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS mfa_enrolled_at TIMESTAMP WITHOUT TIME ZONE;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS mfa_last_verified_at TIMESTAMP WITHOUT TIME ZONE;

UPDATE users
SET mfa_enabled = FALSE
WHERE mfa_enabled IS NULL;

ALTER TABLE users
    ALTER COLUMN mfa_enabled SET DEFAULT FALSE,
    ALTER COLUMN mfa_enabled SET NOT NULL;

ALTER TABLE users
    DROP CONSTRAINT IF EXISTS ck_users_mfa_state_valid;

ALTER TABLE users
    ADD CONSTRAINT ck_users_mfa_state_valid
    CHECK (
        (
            NOT mfa_enabled
            OR (
                NULLIF(btrim(mfa_secret_encrypted), '') IS NOT NULL
                AND mfa_enrolled_at IS NOT NULL
            )
        )
        AND (
            mfa_last_verified_at IS NULL
            OR mfa_enrolled_at IS NOT NULL
        )
    );

COMMENT ON COLUMN users.mfa_secret_encrypted IS
    'Encrypted TOTP secret; plaintext secrets are not permitted.';

DO $$
DECLARE
    admin_users INTEGER;
    operator_users INTEGER;
    enabled_users INTEGER;
BEGIN
    SELECT
        COUNT(*) FILTER (WHERE role = 'admin'),
        COUNT(*) FILTER (WHERE role = 'operador'),
        COUNT(*) FILTER (WHERE mfa_enabled)
    INTO admin_users, operator_users, enabled_users
    FROM users;

    RAISE NOTICE
        'MFA field preparation complete. admin=%, operator=%, enabled=%',
        admin_users,
        operator_users,
        enabled_users;
END
$$;

COMMIT;
