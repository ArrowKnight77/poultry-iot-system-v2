#!/usr/bin/env bash
set -Eeuo pipefail

CERT_ROOT="${1:-mosquitto/certs}"
CA_DIR="$CERT_ROOT/ca"
BROKER_DIR="$CERT_ROOT/broker"

mkdir -p "$CA_DIR" "$BROKER_DIR"
umask 077

openssl genrsa -out "$CA_DIR/ca.key" 4096

openssl req -x509 -new -sha256 \
  -key "$CA_DIR/ca.key" \
  -days 3650 \
  -out "$CA_DIR/ca.crt" \
  -subj "/C=MX/ST=Queretaro/L=Queretaro/O=Poultry IoT System/OU=Security/CN=Poultry IoT Local CA"

openssl genrsa -out "$BROKER_DIR/broker.key" 2048

cat > "$BROKER_DIR/openssl.cnf" <<'EOF'
[req]
distinguished_name = req_distinguished_name
req_extensions = req_ext
prompt = no

[req_distinguished_name]
C = MX
ST = Queretaro
L = Queretaro
O = Poultry IoT System
OU = MQTT Broker
CN = mqtt

[req_ext]
subjectAltName = @alt_names
extendedKeyUsage = serverAuth

[alt_names]
DNS.1 = mqtt
DNS.2 = localhost
DNS.3 = poultry-system.duckdns.org
IP.1 = 127.0.0.1
EOF

openssl req -new \
  -key "$BROKER_DIR/broker.key" \
  -out "$BROKER_DIR/broker.csr" \
  -config "$BROKER_DIR/openssl.cnf"

openssl x509 -req \
  -in "$BROKER_DIR/broker.csr" \
  -CA "$CA_DIR/ca.crt" \
  -CAkey "$CA_DIR/ca.key" \
  -CAcreateserial \
  -out "$BROKER_DIR/broker.crt" \
  -days 825 \
  -sha256 \
  -extfile "$BROKER_DIR/openssl.cnf" \
  -extensions req_ext

cp "$CA_DIR/ca.crt" "$BROKER_DIR/ca.crt"

rm -f \
  "$BROKER_DIR/broker.csr" \
  "$BROKER_DIR/openssl.cnf" \
  "$CA_DIR/ca.srl"

chmod 600 "$CA_DIR/ca.key" "$BROKER_DIR/broker.key"
chmod 644 "$CA_DIR/ca.crt" "$BROKER_DIR/ca.crt" "$BROKER_DIR/broker.crt"

echo "Certificados MQTT TLS creados en: $CERT_ROOT"
