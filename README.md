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

Remote deployment, TLS, and production security modules are intentionally out of scope for this local baseline.

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
