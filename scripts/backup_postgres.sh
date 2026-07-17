#!/usr/bin/env bash
set -Eeuo pipefail

readonly DEFAULT_BACKUP_DIR="backups/postgres"
readonly DEFAULT_DB_SERVICE="db"

usage() {
  cat <<'EOF'
Usage: scripts/backup_postgres.sh [backup-directory]

Creates a compressed PostgreSQL logical backup from the Docker Compose
database service. The destination defaults to backups/postgres and can also be
set with BACKUP_DIR. DB_SERVICE can override the default service name (db).

Examples:
  scripts/backup_postgres.sh
  BACKUP_DIR=/srv/poultry-backups scripts/backup_postgres.sh
  scripts/backup_postgres.sh /srv/poultry-backups
EOF
}

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

warn() {
  printf 'Warning: %s\n' "$*" >&2
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

if (( $# > 1 )); then
  usage >&2
  fail "expected zero or one argument"
fi

require_command docker
require_command gzip

docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is not available"

backup_dir="${1:-${BACKUP_DIR:-$DEFAULT_BACKUP_DIR}}"
db_service="${DB_SERVICE:-$DEFAULT_DB_SERVICE}"

[[ -n "$backup_dir" ]] || fail "backup directory cannot be empty"
[[ "$db_service" =~ ^[A-Za-z0-9_.-]+$ ]] || fail "DB_SERVICE contains invalid characters"

umask 077
mkdir -p -- "$backup_dir"
chmod 700 -- "$backup_dir"

timestamp="$(date -u +'%Y%m%dT%H%M%SZ')"
final_path="${backup_dir%/}/poultry_postgres_${timestamp}.sql.gz"
partial_path="${final_path}.partial"

cleanup() {
  rm -f -- "$partial_path"
}
trap cleanup EXIT INT TERM

[[ ! -e "$final_path" ]] || fail "backup already exists: $final_path"

docker compose exec -T "$db_service" sh -ec '
  : "${POSTGRES_USER:?POSTGRES_USER is not set in the database container}"
  : "${POSTGRES_DB:?POSTGRES_DB is not set in the database container}"
  pg_isready --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" >/dev/null
  exec pg_dump \
    --username="$POSTGRES_USER" \
    --dbname="$POSTGRES_DB" \
    --format=plain \
    --no-owner \
    --no-privileges
' | gzip -9 > "$partial_path"

[[ -s "$partial_path" ]] || fail "pg_dump produced an empty backup"
gzip -t -- "$partial_path" || fail "compressed backup validation failed"

chmod 600 -- "$partial_path"
mv -- "$partial_path" "$final_path"
trap - EXIT INT TERM

if command -v stat >/dev/null 2>&1; then
  directory_mode="$(stat -c '%a' "$backup_dir" 2>/dev/null || true)"
  file_mode="$(stat -c '%a' "$final_path" 2>/dev/null || true)"

  if [[ "$directory_mode" != "700" || "$file_mode" != "600" ]]; then
    warn "the destination filesystem did not enforce permissions 700/600"
    warn "observed modes: directory=${directory_mode:-unknown}, file=${file_mode:-unknown}"
    warn "use a Linux filesystem with Unix permissions for production backups"
  fi
fi

printf 'PostgreSQL backup created: %s\n' "$final_path"
