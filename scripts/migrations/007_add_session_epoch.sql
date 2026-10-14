-- Invalida en servidor las cookies de sesión emitidas antes del último logout.
-- Ejecutar ANTES de desplegar el código que usa la columna.
BEGIN;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS session_epoch INTEGER NOT NULL DEFAULT 0;

COMMIT;
