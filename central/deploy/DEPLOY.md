# Deploying the central service

The central service holds project settings, PC enrolment and the audit log. It
stores no scanned documents. These files deploy it behind HTTPS with a real
PostgreSQL database and a backup. Choose ONE of the two ways below.

## A. Docker (recommended)

Needs a Linux server with Docker and Docker Compose, and a name (for example
`central.example.com`) that points at it, with ports 80 and 443 open.

```sh
git clone <the repository> /opt/dokscanner-central
cd /opt/dokscanner-central/central/deploy
cp .env.example .env              # fill in the domain, a database password, the secret
docker compose up -d --build
docker compose run --rm central python -m central.manage create-admin root
```

Then open `https://<your domain>/dashboard` and sign in.

- **First start only:** `CENTRAL_WORKERS=1` (the default). The tables are created
  on the first start of an empty database, and several workers would race to
  create them. After it is up, set `CENTRAL_WORKERS=2` (or 3 or 4) in `.env` and
  run `docker compose up -d`.
- **TLS:** Caddy gets and renews the certificate by itself. With no public name
  (a private network), see the note at the top of `Caddyfile` (`tls internal`);
  each PC must then trust Caddy's certificate authority.
- **Only the proxy is reachable from outside.** The database and the service
  publish no ports. Do not add `ports:` to them.
- **Upload size:** the proxy caps request bodies at 1 MB (required: the service
  checks its own 256 KB limit only after the body has been read).

### Update to a new release

```sh
cd /opt/dokscanner-central && git pull
cd central/deploy && docker compose up -d --build
```

### Backups and restore

Every night (cron), from `central/deploy`:

```sh
0 2 * * *  cd /opt/dokscanner-central/central/deploy && ./backup.sh
```

It keeps the newest 14 dumps in `./backups` (`KEEP=30 ./backup.sh` for more) and
fails loudly on an empty or damaged dump. **Copy the backups off this server**
and treat them as confidential (they hold the audit log and the PC list).

Restore into an empty database:

```sh
docker compose down
docker volume rm dokscanner-central_dbdata          # only when replacing a broken database
docker compose up -d db
gunzip -c backups/dokcentral-YYYY-MM-DD_HHMMSS.sql.gz | docker compose exec -T db psql -U dokcentral -d dokcentral
docker compose up -d
```

Rehearse a restore once before relying on it.

## B. Without Docker (Linux, systemd)

1. Install Python 3.11+, PostgreSQL 14+ and nginx.
2. Create a database and a user for it (`createdb dokcentral`, `createuser ...`).
3. Copy **only** these into `/opt/dokscanner-central/`: the folder `central/`, and
   from `core/` the three files `__init__.py`, `profile_manager.py` and
   `project_schema.json` (the service imports them to check project settings;
   without them it fails at start-up). Do not copy the desktop application.
4. `python3 -m venv venv && venv/bin/pip install -r central/requirements.txt`
5. Write `/etc/dokscanner-central.env` (`chmod 600`):
   ```
   CENTRAL_DATABASE_URL=postgresql+psycopg://dokcentral:PASSWORD@localhost/dokcentral
   CENTRAL_SESSION_SECRET=<48 random characters>
   ```
6. Install `dokscanner-central.service` under `/etc/systemd/system/`, set
   `--workers 1` for the very first start, then `systemctl enable --now dokscanner-central`.
   Create the first admin: `sudo -u dokcentral env $(cat /etc/dokscanner-central.env) venv/bin/python -m central.manage create-admin root`
   (add `--role auditor` for a read-only account).
7. Put nginx in front using `nginx.conf.example` (TLS, and `client_max_body_size 1m`).
8. Back up with `pg_dump dokcentral | gzip > ...` on a schedule.

## Accounts

- **admin:** can change everything.
- **auditor:** can read everything (overview, PCs, projects, audit log) and
  change nothing. Create one with *Admins > Add admin*, role `auditor`, or with
  `create-admin NAME --role auditor`.

## Checks after installing

- `https://<domain>/healthz` answers `{"status": "ok"}`.
- `http://<domain>/` redirects to HTTPS.
- Sign in, add a PC, copy its enrolment code into DOK Scanner (Admin > Central)
  on a workstation, and confirm the PC appears as registered and assigned
  projects arrive.
- Before going live, run the concurrency checks against a throw-away PostgreSQL:
  `set CENTRAL_TEST_DATABASE_URL=postgresql+psycopg://...` then
  `pytest tests/central/test_concurrency.py`.
