# Restaurant Ordering & Kitchen Management (SaaS)

Multi-tenant ordering and kitchen-management platform for restaurants: customers scan a table QR
code, enter the restaurant's current table PIN, and order from a lightweight mobile web page;
the kitchen and waiters work from a Flutter app in real time.

**This is not a payments, POS or billing system.** No monetary transactions are implemented, by
design.

## Documents

| | |
|---|---|
| [docs/00-product-spec.md](docs/00-product-spec.md) | Original product brief — the requirements baseline ("spec §N") |
| [docs/01-architecture.md](docs/01-architecture.md) | Components, trust boundaries, flows, real-time & sync design, phase plan |
| [docs/02-data-model.md](docs/02-data-model.md) | Tables, tenant enforcement, order state machine, DB privileges |
| [docs/03-security.md](docs/03-security.md) | Roles/permissions, auth, table PIN & sessions, threat model → mitigations → tests |
| [docs/04-api-contracts.md](docs/04-api-contracts.md) | REST & WebSocket contracts for all phases |
| [docs/05-security-reviews.md](docs/05-security-reviews.md) | Per-feature security reviews (required before a feature is "done") |
| [docs/06-security-test-report.md](docs/06-security-test-report.md) | Phase 10 adversarial test campaign: findings, results by attack, load test, re-running it |
| [docs/07-deployment.md](docs/07-deployment.md) | Deploying to one server (Docker Compose + Caddy), backups, updates, incidents, rehearsal results |
| [docs/08-pilot-playbook.md](docs/08-pilot-playbook.md) | Running the pilot: preparation, staff training, what to observe, fallbacks, setup file format |

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | Architecture, data model, security model, API contracts | ✅ |
| 2 | PostgreSQL schema, RLS, constraints, migrations | ✅ |
| 3 | Staff auth (Argon2id, JWT + rotating refresh tokens), RBAC, rate limiting, password reset | ✅ |
| 4 | Restaurant registration, staff, tables & QR codes, table PIN & rotation, customer table sessions | ✅ |
| 5 | Menu: categories, dishes, option groups, sold-out toggles, photos | ✅ |
| 6 | Customer web: QR → PIN → menu (search, section chips, dish cards; per-restaurant themes) → cart → order, order status, cancellation | ✅ |
| 7 | Waiter app (Flutter) + staff order API, order state machine, cancellations | ✅ |
| 8 | Kitchen screen (Flutter): live board, dishes summed across tables, delays; live updates (WebSocket) for all staff screens | ✅ |
| 9 | Reliability: kitchen board saved on the device, offline rules, alerts, data retention | ✅ |
| 10 | Security sweep: every route attacked automatically, races, fuzzing, 3 browser engines, load test, dependency audit ([report](docs/06-security-test-report.md)) | ✅ |
| 11 | Pilot readiness: manager tools in the app (cancellation decisions, sold out, table PIN, staff), problem reports, usage counts and pilot report, operator setup tools, password-reset page, deployment kit ([deploy](docs/07-deployment.md)), [pilot playbook](docs/08-pilot-playbook.md) | ✅ ready to run in a restaurant |

## Backend quick start (Windows / macOS / Linux)

Requires Python 3.12+. No Docker or system PostgreSQL needed for development.

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"

python scripts/dev_db.py                     # local PostgreSQL 16 on :54329, roles, migrations, .env
python -m app.cli create-restaurant --code spice-garden --name "Spice Garden" \
    --owner-username asha --owner-name "Asha" --owner-email asha@example.com
uvicorn app.main:create_app --factory --reload --port 8000
```

API docs (development only): http://localhost:8000/docs. The dev database runs in the
background, independent of your terminal (Ctrl+C on uvicorn does not affect it); check it with
`python scripts/dev_db.py --status` and stop it with `python scripts/dev_db.py --stop`.
In development `REGISTRATION_MODE=open`, so `POST /api/v1/restaurants` works from the docs page.

```bash
pytest          # spins up its own throwaway PostgreSQL; ~40 s
ruff check . && ruff format --check .
```

## Customer web page (`customer-web/`)

Requires Node 22+. With the backend running on port 8000:

```bash
cd customer-web
npm install
npm run dev            # http://localhost:5173/t/<qr-token>  (/api and /media are proxied to :8000)
npm test               # unit tests (Node's built-in test runner)
npm run build          # production build in dist/ (≈ 11 KB gzipped, strict CSP)
npx playwright install webkit firefox   # once: Safari's engine and Firefox for the e2e run
npm run e2e            # real-browser tests over HTTPS in Chrome, WebKit and Firefox (starts its own
                       # backend on :8010 with a separate `restaurant_e2e` database)
```

Get a table's QR link from `GET /api/v1/tables` (field `qr_url`) and its PIN from `GET /api/v1/pin`.
In development the QR links point at `http://localhost:5173`. The session cookie is `Secure`, so on
a phone you need HTTPS (browsers only exempt `localhost`); in production the customer page and
`/api` must be served from the **same origin** by your reverse proxy.

## Demo data

With the dev database running, create a complete demo restaurant (staff accounts, six tables, an
Indian menu with options) and print the logins, table PIN and customer QR links:

```bash
cd backend
python scripts/seed_demo.py                    # restaurant code: demo-dhaba
```

## Staff app (`staff_app/`, Flutter)

One app for owner/admin, waiter and kitchen (role-based screens). Kitchen staff land on the
kitchen screen (three lanes on a tablet: New, Cooking with dishes summed across tables, Ready;
tabs on a phone); owners/admins can open it from the account menu. Every screen updates live over
one WebSocket per device and says "Offline" in words when it can't.

The kitchen screen is built to survive bad Wi-Fi: it keeps the board saved on the tablet (so a
restart during an outage still shows the orders), pauses its buttons while it can't reach the
server instead of queueing changes, chimes for new orders (a sound switch per tablet), shows a red
"stop" notice when an order being cooked is cancelled, and keeps the screen awake. A tablet is
never signed out just because the server is unreachable. With the backend on port 8000:

```bash
cd staff_app
flutter pub get
flutter test                                   # unit + widget tests
flutter run -d windows                         # needs Windows Developer Mode (see below)
flutter run -d <android-device>                # emulator reaches the PC at 10.0.2.2:8000
flutter build apk --dart-define=API_BASE_URL=https://api.example.com   # release: HTTPS only
```

* **Windows desktop** builds need *Developer Mode* (Settings → System → For developers), because
  Flutter plugins use symbolic links. Tests and Android builds do not need it.
* **A real phone/tablet** on the same Wi-Fi: run the API with `--host 0.0.0.0` and pass
  `--dart-define=API_BASE_URL=http://<your-PC-IP>:8000` (debug builds allow plain HTTP).
* **Live test** against a running backend (REST and the live WebSocket):
  `LIVE_API_URL=http://127.0.0.1:8000 LIVE_CODE=demo-dhaba LIVE_PASSWORD=<printed> flutter test test/live_api_test.dart`
* **More than one API worker** (e.g. `--workers 4`) needs `EVENT_BUS_BACKEND=redis` and `REDIS_URL`,
  so an order taken on one worker reaches kitchen sockets held by another (required in production).
* **Screenshots** of every screen: `SCREENSHOTS=1 flutter test test/screenshots_test.dart --update-goldens`
  (images in `test/screens/`).

## Developing without touching the live site

* **`main` is what runs on the server**, and the server changes only when someone runs `git pull`
  there (docs/07 §4). Every version put live is tagged: `v1.0-live`, `v1.1-live`, ...
* **New work goes on the `dev` branch** (`git switch dev`), or a branch per feature. Push it to
  GitHub freely: the server never pulls it.
* **Test on this PC.** The dev database (`scripts/dev_db.py`) and the public test below are
  separate from the live data. `start.ps1 -Apk` builds a separate phone app, "Staff TEST", that
  installs next to the live staff app instead of replacing it, and its QR cards go to
  `local-test-qr-cards.html`.
* **Release:** all tests pass, then `git switch main`, `git merge dev`, `git push`, tag it
  (`git tag -a v1.1-live -m "..."`, `git push origin v1.1-live`), update the server (docs/07 §4,
  backup first) and run `check_deployment.py`.
* **Roll back** on the server: `git checkout v1.0-live`, then `docker compose up -d --build` in
  `deploy/`; back to normal later with `git checkout main && git pull`. Migrations only add tables
  and columns, so the previous version runs on the newer database; restore the backup only if a
  migration must be undone.

## Public test from your own PC (no server)

To try the system on real phones before renting a server, this starts the database, the API,
the ordering page and a free Cloudflare quick tunnel, then prints a public `https://…` address,
the table PIN and the table links, and writes printable QR cards (`local-test-qr-cards.html`):

```powershell
powershell -ExecutionPolicy Bypass -File deploy\local-test\start.ps1 [-Code dhaba-test] [-Apk]
powershell -ExecutionPolicy Bypass -File deploy\local-test\stop.ps1
```

`-Apk` also builds the staff app for phones, set to that address, as a separate "Staff TEST" app
(it installs next to the live staff app, never over it). The address changes on every
start, and Cloudflare deletes it when the PC sleeps or goes offline — run the script again. For
testing only: real customers need a server (below). `python -m app.cli show-pin --code <code>`
prints the current PIN and table links at any time.

## Deploying (Phase 11)

`deploy/` runs everything on one server with Docker Compose: Caddy (HTTPS, security headers, the
customer page), the API, PostgreSQL and Redis. Step by step, with backups, updates and incidents:
[docs/07-deployment.md](docs/07-deployment.md). In short:

```bash
cd deploy
python3 make_env.py --domain order.example.in --email you@example.in   # once: secrets
nano api.env                                                            # SMTP settings
docker compose up -d --build
docker compose exec api python -m app.cli create-restaurant --code ... --name ... --owner-username owner --owner-name ... --owner-email ...
docker compose exec -T api python -m app.cli apply-setup --code ... --file /dev/stdin < setup.json
docker compose exec -T api python -m app.cli qr-sheet --code ... > qr-cards.html
python ../backend/scripts/check_deployment.py https://order.example.in      # from your laptop
```

Operator commands (`python -m app.cli …`): `create-restaurant`, `apply-setup` (tables, staff,
option groups and menu from one JSON file — format in [docs/08 §9](docs/08-pilot-playbook.md)),
`qr-sheet` (printable table cards), `regenerate-qr` (replace a lost card), `pilot-report`
(orders, timings, PIN use, cancellations, problem reports), `purge`.

## Production database setup

Create two roles with [backend/scripts/bootstrap_db.sql](backend/scripts/bootstrap_db.sql): a schema
**owner** used only by migrations (`MIGRATION_DATABASE_URL`, `alembic upgrade head`) and a
**runtime** role for the API (`DATABASE_URL`) that is not a superuser, lacks `BYPASSRLS` and owns
nothing — otherwise Row Level Security would not apply. The API refuses to start in production if
that is violated, or if secrets, Redis, or SMTP are not configured (see
[backend/.env.example](backend/.env.example)).

**Security tests**: `pytest tests/security` attacks every route (missing credentials, every
role, other restaurants' ids, hostile input, simultaneous requests, rate limits);
`python scripts/ws_load_test.py` load-tests live updates. Results and deployment requirements
found by them (proxy headers, `--ws-max-size 65536`): [docs/06](docs/06-security-test-report.md).

**Data retention** runs inside the API every hour (one worker at a time): ended staff sessions and
unused customer table sessions are deleted after 30 days, audit logs after 365 (the database
refuses anything under 180). Orders are kept. `python -m app.cli purge` runs it once by hand.
