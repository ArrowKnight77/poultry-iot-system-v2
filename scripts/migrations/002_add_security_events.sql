BEGIN;

CREATE TABLE IF NOT EXISTS security_events (
    id SERIAL PRIMARY KEY,
    event_type VARCHAR(100) NOT NULL,
    severity VARCHAR(20) NOT NULL DEFAULT 'warning',
    source VARCHAR(50) NOT NULL,
    module VARCHAR(50),
    source_ip VARCHAR(64),
    actor_username VARCHAR(80),
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT ck_security_events_severity
        CHECK (severity IN ('info', 'warning', 'error', 'critical'))
);

CREATE INDEX IF NOT EXISTS ix_security_events_event_type
    ON security_events(event_type);

CREATE INDEX IF NOT EXISTS ix_security_events_source
    ON security_events(source);

CREATE INDEX IF NOT EXISTS ix_security_events_module
    ON security_events(module);

CREATE INDEX IF NOT EXISTS ix_security_events_created_at
    ON security_events(created_at DESC);

COMMIT;
