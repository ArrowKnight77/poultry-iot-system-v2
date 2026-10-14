#!/usr/bin/env bash
set -euo pipefail

base_url="${1:-https://poultry-system.duckdns.org}"
base_url="${base_url%/}"

case "$base_url" in
  https://*) ;;
  *)
    printf 'ERROR: base URL must use HTTPS: %s\n' "$base_url" >&2
    exit 2
    ;;
esac

for command_name in curl awk tr; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    printf 'ERROR: required command not found: %s\n' "$command_name" >&2
    exit 1
  fi
done

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/poultry-header-check.XXXXXX")"
trap 'rm -rf -- "$tmp_dir"' EXIT

header_values() {
  local header_file="$1"
  local wanted_header="$2"

  awk -v wanted="$wanted_header" '
    index($0, ":") {
      name = $0
      sub(/:.*/, "", name)
      if (tolower(name) == tolower(wanted)) {
        value = $0
        sub(/^[^:]+:[[:space:]]*/, "", value)
        sub(/\r$/, "", value)
        print value
      }
    }
  ' "$header_file"
}

check_header() {
  local header_file="$1"
  local header_name="$2"
  local expected_value="$3"
  local values
  local count

  values="$(header_values "$header_file" "$header_name")"
  count="$(printf '%s\n' "$values" | awk 'NF {count++} END {print count + 0}')"

  if [ "$count" -ne 1 ] || [ "$values" != "$expected_value" ]; then
    printf 'ERROR: %s expected once as %q, got count=%s value=%q\n' \
      "$header_name" "$expected_value" "$count" "$values" >&2
    return 1
  fi
}

check_https_endpoint() {
  local path="$1"
  local expected_status="$2"
  local safe_name
  local header_file
  local status

  safe_name="$(printf '%s' "$path" | tr '/?' '__')"
  header_file="$tmp_dir/${safe_name:-root}.headers"

  curl --silent --show-error --connect-timeout 10 --max-time 20 \
    --dump-header "$header_file" --output /dev/null \
    "$base_url$path"

  status="$(awk '/^HTTP\// {code = $2} END {print code}' "$header_file")"
  if [ "$status" != "$expected_status" ]; then
    printf 'ERROR: %s%s expected HTTP %s, got %s\n' \
      "$base_url" "$path" "$expected_status" "$status" >&2
    return 1
  fi

  check_header "$header_file" Strict-Transport-Security \
    "max-age=31536000; includeSubDomains"
  check_header "$header_file" X-Content-Type-Options "nosniff"
  check_header "$header_file" X-Frame-Options "SAMEORIGIN"
  check_header "$header_file" X-XSS-Protection "0"
  check_header "$header_file" Referrer-Policy "strict-origin-when-cross-origin"
  check_header "$header_file" Permissions-Policy \
    "geolocation=(), camera=(), microphone=(), payment=(), usb=()"
  check_header "$header_file" Content-Security-Policy \
    "object-src 'none'; base-uri 'self'; frame-ancestors 'self'; form-action 'self'; upgrade-insecure-requests"
  check_header "$header_file" Server "nginx"

  printf 'OK: %s%s HTTP %s\n' "$base_url" "$path" "$status"
}

check_https_endpoint / 302
check_https_endpoint /login 200
check_https_endpoint /api/health 200
check_https_endpoint /health 200
check_https_endpoint /commit28-security-header-probe 404

http_base="http://${base_url#https://}"
http_headers="$tmp_dir/http-redirect.headers"
curl --silent --show-error --connect-timeout 10 --max-time 20 \
  --dump-header "$http_headers" --output /dev/null \
  "$http_base/"

http_status="$(awk '/^HTTP\// {code = $2} END {print code}' "$http_headers")"
location_value="$(header_values "$http_headers" Location)"
hsts_count="$(header_values "$http_headers" Strict-Transport-Security | awk 'NF {count++} END {print count + 0}')"

if [ "$http_status" != "301" ]; then
  printf 'ERROR: %s/ expected HTTP 301, got %s\n' "$http_base" "$http_status" >&2
  exit 1
fi
if [ "$location_value" != "$base_url/" ]; then
  printf 'ERROR: HTTP redirect expected %s/, got %s\n' \
    "$base_url" "$location_value" >&2
  exit 1
fi
if [ "$hsts_count" -ne 0 ]; then
  printf 'ERROR: clear-text HTTP response must not emit HSTS\n' >&2
  exit 1
fi
check_header "$http_headers" Server "nginx"

printf 'OK: HTTP redirects to HTTPS without emitting HSTS\n'
printf 'All public Nginx security-header checks passed.\n'
