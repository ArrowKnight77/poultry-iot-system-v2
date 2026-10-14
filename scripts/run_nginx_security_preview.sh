#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
nginx_image="${NGINX_TEST_IMAGE:-nginx:1.24.0-alpine@sha256:77e5d4a6ad906c5d3793764085706577fa705b1dc6f244ea0241c4b5e2155385}"
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/poultry-nginx-preview.XXXXXX")"
container_name="poultry-nginx28-preview-$$"

cleanup() {
  docker rm --force "$container_name" >/dev/null 2>&1 || true
  rm -rf -- "$tmp_dir"
}
trap cleanup EXIT INT TERM

for command_name in docker curl openssl awk sed; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    printf 'ERROR: required command not found: %s\n' "$command_name" >&2
    exit 1
  fi
done

cd "$repo_root"
api_container="$(docker compose ps -q api)"
dashboard_container="$(docker compose ps -q dashboard)"

if [ -z "$api_container" ] || [ -z "$dashboard_container" ]; then
  printf 'ERROR: api and dashboard must be running first.\n' >&2
  printf 'Run: docker compose up -d\n' >&2
  exit 1
fi

compose_network="$(
  docker inspect "$api_container" \
    --format '{{range $name, $_ := .NetworkSettings.Networks}}{{println $name}}{{end}}' \
    | awk 'NF {print; exit}'
)"

if [ -z "$compose_network" ]; then
  printf 'ERROR: could not determine the Docker Compose network\n' >&2
  exit 1
fi

mkdir -p "$tmp_dir/certs" "$tmp_dir/snippets"
openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 1 \
  -subj '/CN=localhost' \
  -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1' \
  -keyout "$tmp_dir/certs/preview.key" \
  -out "$tmp_dir/certs/preview.crt" >/dev/null 2>&1

# Browsers cache HSTS. The preview exercises the real CSP and remaining
# headers, but deliberately omits HSTS on the self-signed localhost origin.
sed '/^[[:space:]]*add_header Strict-Transport-Security /d' \
  deploy/nginx/snippets/poultry-security-headers.conf \
  > "$tmp_dir/snippets/poultry-security-headers.conf"

cat > "$tmp_dir/nginx.conf" <<'NGINX'
events {}

http {
    server {
        listen 8443 ssl;
        server_name localhost;

        ssl_certificate /etc/nginx/preview-certs/preview.crt;
        ssl_certificate_key /etc/nginx/preview-certs/preview.key;
        ssl_protocols TLSv1.2 TLSv1.3;

        include /etc/nginx/snippets/poultry-security-headers.conf;

        location = /api/security-events {
            proxy_pass http://dashboard:5001/api/security-events;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        location /api/ {
            proxy_pass http://api:5000/api/;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        location /lecturas {
            proxy_pass http://api:5000/lecturas;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        location / {
            proxy_pass http://dashboard:5001/;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }
    }
}
NGINX

docker run --detach --rm \
  --name "$container_name" \
  --network "$compose_network" \
  --publish 127.0.0.1:8443:8443 \
  --volume "$tmp_dir/nginx.conf:/etc/nginx/nginx.conf:ro" \
  --volume "$tmp_dir/certs:/etc/nginx/preview-certs:ro" \
  --volume "$tmp_dir/snippets:/etc/nginx/snippets:ro" \
  "$nginx_image" >/dev/null

for attempt in $(seq 1 30); do
  if curl --insecure --silent --show-error --max-time 2 \
    https://127.0.0.1:8443/login >/dev/null 2>&1; then
    break
  fi
  if [ "$attempt" -eq 30 ]; then
    printf 'ERROR: local Nginx preview did not become ready\n' >&2
    docker logs "$container_name" >&2 || true
    exit 1
  fi
  sleep 1
done

printf '\nLocal HTTPS preview is ready:\n'
printf '  https://localhost:8443/login\n\n'
printf 'The certificate is ephemeral and self-signed. The browser warning is expected.\n'
printf 'HSTS is disabled only in this localhost preview; all other headers are active.\n'
printf 'Review login, MFA, dashboard pages, charts, profile image and browser console.\n'
printf 'Press Ctrl+C here when the manual review is complete.\n'

while docker inspect "$container_name" >/dev/null 2>&1; do
  sleep 1
done
