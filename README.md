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
available again. Retention and restore testing remain separate controls.

From the local WSL environment, use the existing crypt remote to pull decrypted
backup files onto the Windows host disk without exposing an inbound service on
the workstation:

```bash
./scripts/sync_backups_to_host.sh /mnt/e/PoultryBackups
```

Protect the host destination with NTFS access controls and full-disk encryption
such as BitLocker. The script only copies missing or changed files and does not
delete older backups from the host.
