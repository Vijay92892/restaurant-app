# 07 — Deployment & operations (Phase 11)

How to run the product for one restaurant (the pilot) on one small server, keep it backed up,
update it, and handle the usual incidents. Everything here is in [`deploy/`](../deploy) and was
rehearsed end to end on a development machine (§9). Read [06 §7](06-security-test-report.md)
for *why* the proxy and server settings are what they are.

## 1. What you need

| | |
|---|---|
| Server | Ubuntu 24.04 LTS, 2 vCPU, 4 GB RAM, 40 GB SSD, in an Indian region (low latency for phones on 4G). Only ports 22, 80 and 443 open. |
| Domain | A (sub)domain such as `order.yourrestaurant.in`, with an **A record** pointing at the server. Caddy gets the HTTPS certificate for it automatically. |
| Mail | An SMTP account (any provider) for staff password-reset emails. The API does not start without one. |
| Devices | One Android tablet (10"+) for the kitchen with a charger and a stand; the waiters' Android phones; a printer for the QR cards. |
| Your laptop | This repository with the backend's Python environment (for `check_deployment.py`) and Flutter (to build the staff app). |

## 2. What runs on the server

```text
 phones / tablets ──HTTPS──▶ web  (Caddy: TLS, security headers, customer page, /api proxy)
                                 │
                                 ▼
                             api  (FastAPI, 2 uvicorn workers, --ws-max-size 65536)
                              │        │
                 ┌────────────┘        └───────────┐
                 ▼                                  ▼
             db (PostgreSQL 16)                 redis (rate limits, live-update fan-out)
             volume: pgdata                     nothing persisted
 migrate: runs `alembic upgrade head` as the schema owner, then exits, before every API start
```

One origin serves everything: `https://<domain>/` (customer page), `/api/v1/…` (API and the
staff WebSocket), `/media/…` (menu photos) and `/reset-password` (staff password reset). Only
`web` publishes ports. Secrets are split so each container gets only what it needs: the API
connects as `restaurant_app` (no `BYPASSRLS`, owns nothing — checked at startup) and never
sees the schema owner's or the database administrator's password.

## 3. First deployment, step by step

**On the server** (as a sudo user, not root):

```bash
# 1. Updates, firewall, Docker
sudo apt update && sudo apt -y upgrade && sudo apt -y install unattended-upgrades docker.io docker-compose-v2 git
sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw allow 443/udp && sudo ufw enable
sudo usermod -aG docker $USER && newgrp docker

# 2. The code
sudo mkdir -p /opt/restaurant && sudo chown $USER /opt/restaurant
git clone <your repository> /opt/restaurant          # or copy it with scp/rsync
cd /opt/restaurant/deploy

# 3. Configuration with fresh secrets (once; it refuses to overwrite). Add --small on a
#    1 GiB server (one API process, lighter database and Redis) and create a swap file:
#    sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile
#    sudo swapon /swapfile && echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
python3 make_env.py --domain order.yourrestaurant.in --email you@yourrestaurant.in
nano api.env          # fill in SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD (and SMTP_PORT if not 587)
```

Without a domain of your own, a dedicated Gmail account is the simplest sender: turn on 2-step
verification, create an *app password*, and set `SMTP_HOST=smtp.gmail.com`, `SMTP_USERNAME` and
`MAIL_FROM` to the Gmail address and `SMTP_PASSWORD` to the app password. With your own domain,
a transactional mail service (with SPF/DKIM set up) looks more professional.

Copy `.env`, `api.env` and `migrate.env` into your password manager now. Losing them later
means: a new `JWT_SECRET` signs every device out (harmless); a new `PIN_ENC_KEY` makes the stored
table PIN unreadable until a manager presses *Change PIN now*.

```bash
# 4. DNS must already point here, or the certificate cannot be issued
getent hosts order.yourrestaurant.in        # must print this server's address

# 5. Build and start (first build takes a few minutes)
docker compose up -d --build
docker compose ps                            # db, api, web "healthy"/"running"; migrate "exited (0)"
docker compose logs web | grep -i certificate # "certificate obtained successfully"
```

**Create the restaurant and load its setup.** The restaurant code is what staff type at sign-in.

```bash
docker compose exec api python -m app.cli create-restaurant --code shree-dhaba \
    --name "Shree Dhaba" --owner-username owner --owner-name "Asha" --owner-email asha@example.in
# (asks for the owner's password twice; it must not contain the username)

cp setup.example.json setup.json && nano setup.json   # the real menu, tables and staff (§ format in docs/08)
docker compose exec -T api python -m app.cli apply-setup --code shree-dhaba --file /dev/stdin --dry-run < setup.json
docker compose exec -T api python -m app.cli apply-setup --code shree-dhaba --file /dev/stdin < setup.json
#   → prints each new staff member's temporary password ONCE: hand them over in person.
docker compose exec -T api python -m app.cli qr-sheet --code shree-dhaba > qr-cards.html
```

Open `qr-cards.html` in a browser on your laptop (`scp` it over) and print it on A4 at 100 %.

**Check it from outside** (on your laptop, in `backend/`):

```bash
python scripts/check_deployment.py https://order.yourrestaurant.in \
    --table https://order.yourrestaurant.in/t/<one table's token> --pin <current PIN>
```

All checks must pass: HTTPS and the http→https redirect, security headers on the page and the
API, caching, API docs switched off, other websites refused (CORS, customer writes, WebSocket),
body and WebSocket size limits, the live-update socket through the proxy, and the customer
session cookie's flags. (Pass the whole table link: tokens can start with `-`.)

**Build and install the staff app:**

```bash
cd staff_app
flutter build apk --release --dart-define=API_BASE_URL=https://order.yourrestaurant.in
# build/app/outputs/flutter-apk/app-release.apk → copy to each device and install
```

`API_BASE_URL` is the site's address only — the app adds `/api/v1` itself. Release builds
refuse plain `http://` API addresses. Device setup (screen lock, sound, Wi-Fi) is
in [docs/08 §2](08-pilot-playbook.md).

**Backups and monitoring** (do not skip):

```bash
chmod +x backup.sh restore.sh
./backup.sh                                   # first backup now
crontab -e                                    # then add:
# 15 4 * * *  /opt/restaurant/deploy/backup.sh >> /opt/restaurant/deploy/backups/backup.log 2>&1
```

Copy `deploy/backups/` off the server regularly (another machine, or cloud storage with
`rclone`) — a backup on the same disk does not survive losing the server. Add an external uptime
monitor (any free service, e.g. UptimeRobot) on `https://<domain>/healthz`, alerting your phone.
Monitors often check with `HEAD` requests; `/healthz` and `/readyz` answer those too.

## 4. Updating

Update outside service hours. Staff devices reconnect by themselves and the kitchen board
survives the restart (it is saved on the tablet and reloaded from the server).

```bash
cd /opt/restaurant && ./deploy/backup.sh && git pull
cd deploy && docker compose up -d --build     # migrations run first, automatically
docker compose ps
# then, from your laptop: python scripts/check_deployment.py https://<domain>
```

If the build fails (for example `ReadTimeoutError` while downloading packages: slow links to
PyPI or npm), nothing was replaced: the old containers keep running. Run
`docker compose up -d --build` again. Python packages are downloaded only when
`backend/pyproject.toml` changes; code-only updates build without them.

If the new version misbehaves, go back to the previous live tag (`git tag -l '*-live'`):
`git checkout v1.0-live && docker compose up -d --build`; later `git checkout main && git pull`.
Tag each version you put live (README, "Developing without touching the live site").
A migration that already ran stays (they only add); if one must be undone, restore the backup
taken just before (§5).

## 5. Backup and restore

`backup.sh` writes `backups/restaurant-<time>.dump` (the whole database: schema, row-level
security, grants and data) and `backups/media-<time>.tar.gz` (menu photos), keeping 14 days
(`KEEP_DAYS`). `restore.sh <dump> [media]` stops the API, recreates the database exactly as
`bootstrap_db.sql` does, loads the dump, restores photos, and starts everything again
(migrations bring an older dump up to date). It asks you to type `RESTORE` first.

The restore was rehearsed (§9): every table's row count, all 21 row-level-security policies,
the runtime role's 64 grants and the 8 security-definer functions came back identical, the
runtime role still owns nothing, and with no restaurant bound it still sees zero rows.

## 6. Day-to-day operations

| Task | Command (in `/opt/restaurant/deploy`) |
|---|---|
| See what is running | `docker compose ps` |
| Recent API errors | `docker compose logs --since 1h api` |
| Restart the API | `docker compose restart api` |
| Weekly pilot report | `docker compose exec -T api python -m app.cli pilot-report --code <code> --days 7 > report.md` |
| A QR card was lost or copied | `docker compose exec api python -m app.cli regenerate-qr --code <code> --table T4`, then `qr-sheet --table T4 > card.html` and print. The old card stops working. |
| Menu changes | Edit `setup.json`, run `apply-setup --dry-run`, then without. It only adds and updates, never deletes; switching a dish off is `"active": false`. Sold-out switches belong to the kitchen (app). |
| Owner forgot the password | *Forgot password?* on the app's sign-in screen (email link → `https://<domain>/reset-password`). Works only if the account has an email address: give `--owner-email` when creating the restaurant. Without email: `docker compose exec api python -m app.cli reset-password --code <code> --username <owner>` prints a temporary password once (the app asks for a new one at sign-in; the account's devices are signed out; the 15-minute wrong-password lock is lifted). |
| Staff forgot the password | A manager resets it in the app (*Manage → Staff*); if no manager can sign in, the operator's `reset-password` (above) works for any account. |
| Data retention | Runs hourly by itself; `docker compose exec api python -m app.cli purge` runs it now. |

**Incidents**

| Symptom | What to do |
|---|---|
| Site unreachable | `docker compose ps`; `docker compose up -d`; check the uptime monitor and the server's disk (`df -h`). Meanwhile the restaurant takes orders on paper (docs/08 §6). |
| "Can't reach the server" on every device | Restaurant Wi-Fi/Internet first (open any website on the tablet). The kitchen keeps showing its saved board and pauses buttons; nothing is lost. |
| Certificate errors | `docker compose logs web`; DNS must still point at the server; ports 80/443 open. |
| Redis restarted | Nothing to do: devices reload their screens automatically. |
| The PIN got out (people ordering from outside) | Manager: *Manage → Table PIN → Change PIN now* with *also sign out phones*. |
| Suspicious activity | The audit log (`GET /api/v1/audit-logs` as owner) and `docker compose logs api`. Logs never contain passwords, tokens or PINs. |

## 7. What the deployment enforces (security)

- One HTTPS origin; HTTP redirects to HTTPS; HSTS on the page and the API.
- Customer page: `nosniff`, `Referrer-Policy: no-referrer`, `frame-ancestors 'none'` (plus the
  page's own strict CSP); hashed bundles cached, the page itself always revalidated.
- API: production settings refuse to start with development secrets, without Redis, SMTP or
  HTTPS URLs, or if the runtime database role could bypass row-level security. API docs are off.
- Client addresses come only from Caddy's `X-Forwarded-For` (`TRUSTED_PROXIES` is the Docker
  network); Caddy discards any value a client sends.
- uvicorn: `--ws-max-size 65536`, no server header; Caddy: no server header, 6 MB body cap.
- Least privilege for secrets (§2); `.env`/`api.env`/`migrate.env` are created mode 600 and are
  git-ignored. Containers log to rotated files (10 MB × 5).
- Restaurants are created by the operator only (`REGISTRATION_MODE=closed`).

## 8. Running the API without Docker

Any host works if it reproduces §7: PostgreSQL 16 bootstrapped with `bootstrap_db.sql`,
Redis, `alembic upgrade head` with `MIGRATION_DATABASE_URL`, the API started exactly like the
`CMD` in `backend/Dockerfile` with `api.env`'s settings, and `deploy/Caddyfile` (or an
equivalent nginx configuration) in front. Then run `check_deployment.py`.

## 9. Rehearsal (2026-10-05, development PC)

The production configuration was run locally — the real `deploy/Caddyfile` (Caddy 2.11.7, local
certificate), the API in production mode with the image's flags (2 workers,
`--ws-max-size 65536`), a real Redis 8 with a password, PostgreSQL 16 — and the operator
commands were used to create a restaurant from `setup.example.json`.

| Check | Result |
|---|---|
| `check_deployment.py` (incl. session cookie) | **31 / 31 passed** |
| `apply-setup` | 32 items created; second run: "Already up to date: 32" |
| Backup → restore (`pg_dump -Fc` / `pg_restore`) | identical; RLS, grants, functions intact |
| Load test, **4 workers + real Redis**: 300 sockets, 300 orders | 3000/3000 deliveries, 0 missing, 0 duplicates, 0 cross-restaurant; latency p50 105 ms, p95 577 ms, p99 773 ms; 300 sockets back in 3.9 s after a forced drop |

Found and fixed during the rehearsal: QR tokens that start with `-` were read as an option by
the checker (it now takes the whole link); a confirmation message was queued behind an earlier
one; the load-test report recorded the Redis password (now redacted); the dev/test PostgreSQL
build has no time-zone database, so the restaurant's local day is computed in Python.
Found in the first real-phone test: the QR codes on the printed card sheet were cropped (a
fixed-size image squeezed into a smaller box), so phones could not read them. They now scale;
all six cards of the test restaurant were decoded from a browser screenshot by a QR reader.

**Not verified on this machine:** building the Docker images and running `docker compose`
(Docker is not installed here). The first `docker compose up --build` on the server is that
check; `check_deployment.py` then verifies the result from outside.
