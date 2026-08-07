# Poultry IoT System V2

Clean local Docker baseline for the poultry IoT monitoring system.

This repository is intentionally local-only. It does not include production tunnels, private keys, real `.env` files, or deployment certificates.

## Services

- PostgreSQL: internal Docker database
- Mosquitto: local MQTT broker on `localhost:1883`
- API: `http://localhost:5000`
- Dashboard: `http://localhost:5001`
- MQTT subscriber: consumes local MQTT messages and posts readings to the API

## Local Setup

1. Create your private environment file:

   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and replace the placeholder values.

3. Start the local stack:

   ```bash
   docker compose up --build
   ```

4. Open the dashboard:

   ```text
   http://localhost:5001
   ```

## Repository Hygiene

Do not commit:

- `.env` or any real environment file
- certificates or private keys
- tunnel credentials
- MQTT credential files
- database dumps, backups, logs, or debug captures

Production credentials, private TLS material and host-generated state remain
outside this repository. Reproducible deployment templates may be versioned as
long as they contain no secrets.

## Container hardening

The API, dashboard and MQTT subscriber images run as an explicit non-root user
with a read-only root filesystem, dropped Linux capabilities, bounded process
counts and service-specific healthchecks. See the
[container hardening procedure](docs/container-hardening.md) for the control
matrix, justified vendor-image exceptions, manual validation and rollback.

## Nginx security headers

The production Nginx virtual host is versioned without certificates or private
keys. Nginx emits one canonical set of security headers, enables HSTS only on
HTTPS, hides its version and preserves the existing Certbot and reverse-proxy
layout. See the
[Nginx security header procedure](docs/nginx-security-headers.md) for local
tests, browser checks, deployment and HSTS-aware rollback.

## Deployment and security evidence

The final phase 6 operator runbook is available in the
[deployment and security evidence guide](docs/deployment-security-evidence-guide.md).
It consolidates the dependency, container and Nginx validations, defines safe
screenshot handling, documents the documentation-only deployment path and
provides rollback and troubleshooting procedures.

## Endpoint inventory and risk matrix

The final hardening inventory maps the public and internal HTTP routes, Docker
and host ports, MQTT topics, authentication models, RBAC decisions and
residual risks. See the
[endpoint inventory and risk matrix](docs/endpoint-inventory-risk-matrix.md)
for the source-of-truth precedence, validation commands and open remediation
items that must not be treated as mitigated.

## PostgreSQL backup

Create a compressed logical backup from the running Docker Compose database:

```bash
./scripts/backup_postgres.sh
```

Backups are written to `backups/postgres/` by default with directory mode
`700` and file mode `600`. The script validates the database connection and
the generated gzip archive before publishing the final `.sql.gz` file. Backup
files are intentionally ignored by Git.

On WSL paths mounted from Windows, such as `/mnt/c` or `/mnt/e`, NTFS may show
permissions such as `777` even after `chmod`. The script reports this condition.
Production backups must use a Linux filesystem that enforces the expected
`700` directory and `600` file permissions.

For a protected destination on the server, pass the directory as an argument
or set `BACKUP_DIR`:

```bash
./scripts/backup_postgres.sh /srv/poultry-backups
BACKUP_DIR=/srv/poultry-backups ./scripts/backup_postgres.sh
```

This commit only creates a local backup. Off-site copies, retention policy and
restore testing are handled separately in the continuity workflow.

## Daily 3-2-1 backup workflow

The 3-2-1 workflow keeps a compressed backup on the Droplet and uploads an
immutable, client-side encrypted copy to a Google Drive folder through an
`rclone crypt` remote. The production database, the local dump and the
off-site encrypted object provide three copies across Droplet and Google
storage, with one copy outside the server. A host-side pull can retain an
additional independent copy on the operator workstation.

Install `rclone` and configure a Google Drive remote authenticated with an
account that can edit the shared backup folder. In the advanced Drive options,
set `root_folder_id` to the final segment of that folder's browser URL. Then
create an `rclone crypt` remote that wraps the Drive remote so database content
and filenames are encrypted before upload. Keep the OAuth token, crypt password
and crypt salt outside this repository.

```bash
rclone config
rclone lsd poultry-drive:
rclone lsd poultry-offsite-crypt:
```

Prepare the configuration and systemd units on the server:

```bash
sudo install -d -m 700 /etc/poultry-iot
sudo install -m 600 deploy/backup/backup.env.example /etc/poultry-iot/backup.env
sudo install -m 644 deploy/systemd/poultry-backup@.service /etc/systemd/system/
sudo install -m 644 deploy/systemd/poultry-backup@.timer /etc/systemd/system/
sudoedit /etc/poultry-iot/backup.env
sudo systemctl daemon-reload
sudo systemctl enable --now "poultry-backup@$(id -un).timer"
```

Run one manual service execution before relying on the timer:

```bash
sudo systemctl start "poultry-backup@$(id -un).service"
sudo systemctl status "poultry-backup@$(id -un).service" --no-pager
sudo journalctl -u "poultry-backup@$(id -un).service" -n 50 --no-pager
systemctl list-timers "poultry-backup@$(id -un).timer"
```

The timer runs daily at 02:15 with a randomized delay of up to 15 minutes and
uses `Persistent=true` to run a missed backup after the server becomes
available again. Retention remains a documented, manually controlled operation
until a separate automation change is implemented.

From the local WSL environment, use the existing crypt remote to pull decrypted
backup files onto the Windows host disk without exposing an inbound service on
the workstation:

```bash
./scripts/sync_backups_to_host.sh /mnt/e/PoultryBackups
```

Protect the host destination with NTFS access controls and full-disk encryption
such as BitLocker. The script only copies missing or changed files and does not
delete older backups from the host.

## PostgreSQL restore test

Validate a local or off-site backup in an isolated temporary PostgreSQL 15
container without modifying the production database:

```bash
./scripts/test_postgres_restore.sh \
  /srv/poultry-backups/poultry_postgres_YYYYMMDDTHHMMSSZ.sql.gz
```

The test stops on SQL errors, validates the critical application tables and
hardening fields, reports only table row counts and removes the temporary
container when finished. See the
[PostgreSQL restore test procedure](docs/restore-test-procedure.md) for local
and Google Drive recovery steps, evidence rules and expected results.

## Backup continuity policy

Retention responsibilities, recovery order and measurable RTO/RPO targets are
defined in the
[backup continuity policy](docs/backup-retention-rto-rpo.md). The current
targets are a 24-hour-and-15-minute RPO during normal operation and a four-hour
RTO after an incident is declared. Backup deletion is not automated by this
repository.
