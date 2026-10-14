BEGIN;

CREATE TABLE IF NOT EXISTS login_lockouts (
    id SERIAL PRIMARY KEY,
    username VARCHAR(80) NOT NULL,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    window_started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_failed_at TIMESTAMP,
    locked_until TIMESTAMP,
    last_source_ip VARCHAR(64),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_login_lockouts_failed_attempts_nonnegative
        CHECK (failed_attempts >= 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_login_lockouts_username
    ON login_lockouts(username);

CREATE INDEX IF NOT EXISTS ix_login_lockouts_locked_until
    ON login_lockouts(locked_until);

CREATE INDEX IF NOT EXISTS ix_login_lockouts_last_failed_at
    ON login_lockouts(last_failed_at DESC);

COMMIT;
