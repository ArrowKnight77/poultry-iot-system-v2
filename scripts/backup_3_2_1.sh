#!/usr/bin/env bash
set -Eeuo pipefail

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

require_command flock
require_command gzip
require_command rclone

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="${PROJECT_DIR:-$(cd -- "$script_dir/.." && pwd)}"
local_dir="${BACKUP_LOCAL_DIR:-/srv/poultry-backups}"
rclone_remote="${BACKUP_RCLONE_CRYPT_REMOTE:-}"
lock_file="${BACKUP_LOCK_FILE:-}"

[[ -x "$project_dir/scripts/backup_postgres.sh" ]] || \
  fail "backup script is not executable: $project_dir/scripts/backup_postgres.sh"
[[ "$local_dir" == /* ]] || fail "BACKUP_LOCAL_DIR must be an absolute path"
[[ -d "$local_dir" ]] || fail "BACKUP_LOCAL_DIR does not exist: $local_dir"
[[ -w "$local_dir" ]] || fail "BACKUP_LOCAL_DIR is not writable: $local_dir"
[[ -n "$rclone_remote" ]] || fail "BACKUP_RCLONE_CRYPT_REMOTE is required"
[[ "$rclone_remote" == *:* ]] || \
  fail "BACKUP_RCLONE_CRYPT_REMOTE must use rclone remote:path syntax"

local_dir="$(cd -- "$local_dir" && pwd -P)"
lock_file="${lock_file:-$local_dir/.backup-3-2-1.lock}"

rclone lsd "$rclone_remote" >/dev/null || \
  fail "encrypted rclone remote is unavailable: $rclone_remote"

exec 9>"$lock_file"
flock -n 9 || fail "another 3-2-1 backup operation is already running"

backup_output="$(
  cd -- "$project_dir"
  "$project_dir/scripts/backup_postgres.sh" "$local_dir"
)"
printf '%s\n' "$backup_output"

backup_path="${backup_output##*PostgreSQL backup created: }"
[[ "$backup_path" == "$local_dir/"* ]] || \
  fail "backup script returned an unexpected path: $backup_path"
[[ -f "$backup_path" && -s "$backup_path" ]] || \
  fail "local backup was not created: $backup_path"
gzip -t -- "$backup_path" || fail "local backup integrity check failed"

backup_name="$(basename -- "$backup_path")"
offsite_path="${rclone_remote%/}/$backup_name"

rclone copyto \
  --checksum \
  --immutable \
  "$backup_path" \
  "$offsite_path"

printf 'Encrypted off-site backup created: %s\n' "$offsite_path"
printf '3-2-1 backup workflow completed successfully.\n'
