#!/usr/bin/env bash
set -Eeuo pipefail

readonly DEFAULT_POSTGRES_IMAGE="postgres:15-alpine"
readonly RESTORE_DATABASE="poultry_restore_test"

usage() {
  cat <<'EOF'
Usage: scripts/test_postgres_restore.sh BACKUP.sql.gz

Restores a compressed PostgreSQL backup into an isolated, temporary container,
validates the application schema and removes the container when finished.

Environment:
  RESTORE_POSTGRES_IMAGE  PostgreSQL image to use (default: postgres:15-alpine)
  RESTORE_READY_TIMEOUT   Readiness timeout in seconds (default: 60)
EOF
}

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

case "${1:-}" in
  -h|--help)
    usage
    exit 0
    ;;
esac

if (( $# != 1 )); then
  usage >&2
  fail "exactly one backup file is required"
fi

require_command docker
require_command gzip

backup_path="$1"
[[ -f "$backup_path" ]] || fail "backup file does not exist: $backup_path"
[[ -s "$backup_path" ]] || fail "backup file is empty: $backup_path"
gzip -t -- "$backup_path" || fail "backup gzip integrity check failed"

backup_dir="$(cd -- "$(dirname -- "$backup_path")" && pwd -P)"
backup_path="$backup_dir/$(basename -- "$backup_path")"

postgres_image="${RESTORE_POSTGRES_IMAGE:-$DEFAULT_POSTGRES_IMAGE}"
ready_timeout="${RESTORE_READY_TIMEOUT:-60}"

[[ "$postgres_image" =~ ^[A-Za-z0-9][A-Za-z0-9._/:@-]*$ ]] || \
  fail "RESTORE_POSTGRES_IMAGE contains invalid characters"
[[ "$ready_timeout" =~ ^[1-9][0-9]*$ ]] || \
  fail "RESTORE_READY_TIMEOUT must be a positive integer"

docker image inspect "$postgres_image" >/dev/null 2>&1 || \
  fail "PostgreSQL image is not available locally: $postgres_image"

container_name="poultry-restore-test-$(date -u +'%Y%m%dT%H%M%SZ')-$$"
restore_password="$(docker run --rm --network none "$postgres_image" \
  sh -ec 'head -c 48 /dev/urandom | base64 | tr -d "\n"')"

cleanup() {
  docker rm -f "$container_name" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker run \
  --detach \
  --rm \
  --network none \
  --name "$container_name" \
  --label com.poultry-iot.purpose=restore-test \
  --env "POSTGRES_PASSWORD=$restore_password" \
  --env "POSTGRES_DB=$RESTORE_DATABASE" \
  "$postgres_image" >/dev/null

printf 'Restore test container started: %s\n' "$container_name"

ready=false
for ((attempt = 1; attempt <= ready_timeout; attempt++)); do
  if docker exec --user postgres "$container_name" \
    pg_isready --username postgres --dbname "$RESTORE_DATABASE" >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done

[[ "$ready" == "true" ]] || fail "temporary PostgreSQL did not become ready"

printf 'Restoring backup: %s\n' "$(basename -- "$backup_path")"

gzip -cd -- "$backup_path" | docker exec --interactive --user postgres \
  "$container_name" \
  psql \
    --username postgres \
    --dbname "$RESTORE_DATABASE" \
    --no-psqlrc \
    --quiet \
    --output /dev/null \
    --set ON_ERROR_STOP=1

docker exec --interactive --user postgres "$container_name" \
  psql \
    --username postgres \
    --dbname "$RESTORE_DATABASE" \
    --no-psqlrc \
    --quiet \
    --set ON_ERROR_STOP=1 <<'SQL'
DO $restore_validation$
DECLARE
    missing_tables TEXT[];
    required_column_count INTEGER;
    invalid_role_count INTEGER;
    invalid_mfa_secret_count INTEGER;
BEGIN
    SELECT ARRAY_AGG(required_table ORDER BY required_table)
    INTO missing_tables
    FROM UNNEST(ARRAY[
        'alertas',
        'granjas',
        'lecturas',
        'login_lockouts',
        'modulos',
        'naves',
        'security_events',
        'umbrales',
        'users'
    ]) AS required_table
    WHERE TO_REGCLASS('public.' || required_table) IS NULL;

    IF missing_tables IS NOT NULL THEN
        RAISE EXCEPTION 'Missing required tables: %', missing_tables;
    END IF;

    SELECT COUNT(*)
    INTO required_column_count
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND (table_name, column_name) IN (
          ('lecturas', 'oxigeno'),
          ('users', 'mfa_enabled'),
          ('users', 'mfa_secret_encrypted'),
          ('users', 'mfa_enrolled_at'),
          ('users', 'mfa_last_verified_at')
      );

    IF required_column_count <> 5 THEN
        RAISE EXCEPTION
            'Expected 5 required hardening columns, found %',
            required_column_count;
    END IF;

    SELECT COUNT(*)
    INTO invalid_role_count
    FROM users
    WHERE role IS NULL OR role NOT IN ('admin', 'operador', 'visor');

    IF invalid_role_count <> 0 THEN
        RAISE EXCEPTION 'Found % users with invalid roles', invalid_role_count;
    END IF;

    SELECT COUNT(*)
    INTO invalid_mfa_secret_count
    FROM users
    WHERE mfa_secret_encrypted IS NOT NULL
      AND mfa_secret_encrypted NOT LIKE 'gAAAA%';

    IF invalid_mfa_secret_count <> 0 THEN
        RAISE EXCEPTION
            'Found % MFA secrets without the expected encrypted format',
            invalid_mfa_secret_count;
    END IF;
END
$restore_validation$;
SQL

required_tables=(
  alertas
  granjas
  lecturas
  login_lockouts
  modulos
  naves
  security_events
  umbrales
  users
)

for table_name in "${required_tables[@]}"; do
  row_count="$(docker exec --user postgres "$container_name" \
    psql \
      --username postgres \
      --dbname "$RESTORE_DATABASE" \
      --no-psqlrc \
      --tuples-only \
      --no-align \
      --command "SELECT COUNT(*) FROM public.$table_name;")"
  printf 'Validated table: %s rows=%s\n' "$table_name" "$row_count"
done

printf 'Restore test completed successfully.\n'
docker rm -f "$container_name" >/dev/null
trap - EXIT INT TERM
printf 'Temporary database removed: %s\n' "$container_name"
