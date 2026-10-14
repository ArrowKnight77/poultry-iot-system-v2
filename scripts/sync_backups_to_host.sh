#!/usr/bin/env bash
set -Eeuo pipefail

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage: scripts/sync_backups_to_host.sh [destination]

Downloads and decrypts PostgreSQL backups from the configured rclone crypt
remote. The destination can also be set with HOST_BACKUP_DIR.

Example:
  scripts/sync_backups_to_host.sh /mnt/e/PoultryBackups
EOF
  exit 0
fi

if (( $# > 1 )); then
  fail "expected zero or one argument"
fi

require_command find
require_command gzip
require_command rclone

rclone_remote="${BACKUP_RCLONE_CRYPT_REMOTE:-poultry-offsite-crypt:}"
destination="${1:-${HOST_BACKUP_DIR:-}}"

[[ "$rclone_remote" == *:* ]] || \
  fail "BACKUP_RCLONE_CRYPT_REMOTE must use rclone remote:path syntax"
[[ -n "$destination" ]] || fail "destination or HOST_BACKUP_DIR is required"
[[ "$destination" == /* ]] || fail "host backup destination must be an absolute path"

mkdir -p -- "$destination"
destination="$(cd -- "$destination" && pwd -P)"

rclone lsd "$rclone_remote" >/dev/null || \
  fail "encrypted rclone remote is unavailable: $rclone_remote"

rclone copy \
  --checksum \
  --immutable \
  --include 'poultry_postgres_*.sql.gz' \
  "$rclone_remote" \
  "$destination"

backup_count=0
while IFS= read -r -d '' backup_path; do
  gzip -t -- "$backup_path" || fail "host backup integrity check failed: $backup_path"
  backup_count=$((backup_count + 1))
done < <(find "$destination" -maxdepth 1 -type f -name 'poultry_postgres_*.sql.gz' -print0)

(( backup_count > 0 )) || fail "no PostgreSQL backups were found in $destination"

printf 'Host backup synchronization completed: %s\n' "$destination"
printf 'Backup files available: %d\n' "$backup_count"
