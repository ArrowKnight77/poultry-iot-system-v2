#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
nginx_image="${NGINX_TEST_IMAGE:-nginx:1.24.0-alpine@sha256:77e5d4a6ad906c5d3793764085706577fa705b1dc6f244ea0241c4b5e2155385}"
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/poultry-nginx28.XXXXXX")"
container_name="poultry-nginx28-test-$$"

cleanup() {
  docker rm --force "$container_name" >/dev/null 2>&1 || true
  rm -rf -- "$tmp_dir"
}
trap cleanup EXIT

for command_name in docker curl sed awk; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    printf 'ERROR: required command not found: %s\n' "$command_name" >&2
    exit 1
  fi
done

mkdir -p "$tmp_dir/conf.d"

# Certbot files and the private key deliberately stay outside the repository.
# Replace only those host-specific TLS directives so nginx can parse the full
# versioned virtual host in an isolated container.
sed \
  -e 's/listen 443 ssl;/listen 8081;/' \
  -e 's/listen 80;/listen 8082;/' \
  -e '/^[[:space:]]*ssl_certificate /d' \
  -e '/^[[:space:]]*ssl_certificate_key /d' \
  -e '\#^[[:space:]]*include /etc/letsencrypt/options-ssl-nginx.conf;#d' \
  -e '/^[[:space:]]*ssl_dhparam /d' \
  "$repo_root/deploy/nginx/poultry-api.conf" \
  > "$tmp_dir/conf.d/poultry-api.conf"

docker run --rm \
  --volume "$repo_root/local_tests/fixtures/nginx/production-nginx.conf:/etc/nginx/nginx.conf:ro" \
  --volume "$tmp_dir/conf.d:/etc/nginx/conf.d:ro" \
  --volume "$repo_root/deploy/nginx/snippets:/etc/nginx/snippets:ro" \
  "$nginx_image" \
  nginx -t

docker run --detach --rm \
  --name "$container_name" \
  --publish 127.0.0.1::8080 \
  --volume "$repo_root/local_tests/fixtures/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" \
  --volume "$repo_root/deploy/nginx/snippets:/etc/nginx/snippets:ro" \
  "$nginx_image" >/dev/null

published_address="$(docker port "$container_name" 8080/tcp | awk 'NR == 1 {print $1}')"
base_url="http://$published_address"

for attempt in $(seq 1 30); do
  if curl --silent --show-error --max-time 2 "$base_url/ok" >/dev/null 2>&1; then
    break
  fi
  if [ "$attempt" -eq 30 ]; then
    printf 'ERROR: nginx test container did not become ready\n' >&2
    docker logs "$container_name" >&2 || true
    exit 1
  fi
  sleep 1
done

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
    exit 1
  fi
}

for endpoint_spec in "ok:200" "redirect:302" "not-found:404"; do
  endpoint="${endpoint_spec%%:*}"
  expected_status="${endpoint_spec##*:}"
  header_file="$tmp_dir/$endpoint.headers"

  curl --silent --show-error --max-time 5 \
    --dump-header "$header_file" --output /dev/null \
    "$base_url/$endpoint"

  status="$(awk '/^HTTP\// {code = $2} END {print code}' "$header_file")"
  if [ "$status" != "$expected_status" ]; then
    printf 'ERROR: /%s expected HTTP %s, got %s\n' \
      "$endpoint" "$expected_status" "$status" >&2
    exit 1
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

  printf 'OK: /%s HTTP %s has one canonical value per security header\n' \
    "$endpoint" "$status"
done

printf 'Nginx configuration and response-header validation passed.\n'
