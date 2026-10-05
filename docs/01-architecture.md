# 01 — System Architecture

Status: **Phase 1 (approved baseline)**. Later phases may refine details; any change to a
security boundary must update [03-security.md](03-security.md) in the same change.

## 1. What this product is (and is not)

A multi-tenant SaaS that connects **customer → table → kitchen → waiter** for medium-sized
restaurants in India.

In scope: menu, ordering, kitchen display, table management, staff operations, order status.

Explicitly **out of scope, permanently**: payments of any kind (UPI/card/cash settlement),
billing, GST invoices, receipts, reconciliation, revenue/expense reports, financial dashboards,
payment webhooks. Menu prices, if a restaurant enters them, are *display text only*
(`display_price_paise`). No code path sums, totals, settles or reports money.

## 2. Components

```text
                          ┌───────────────────────────────┐
  Customer phone          │  Edge: TLS + reverse proxy     │
  (any browser) ──HTTPS──▶│  (Caddy / nginx / CDN)         │
                          │                                 │
                          │  order.example.com/       ──────┼──▶ Customer web (static files, CDN-cacheable)
                          │  order.example.com/api/   ──────┼──┐
  Staff Flutter app ─────▶│  api.example.com/api/     ──────┼──┤
  (owner/admin/waiter/    │  api.example.com/api/v1/ws ─────┼──┤
   kitchen tablet)        └───────────────────────────────┘  │
                                                              ▼
                                        ┌─────────────────────────────────────┐
                                        │ FastAPI (N stateless workers)        │
                                        │  REST  /api/v1/...                   │
                                        │  WS    /api/v1/ws/staff               │
                                        │  PIN rotation scheduler (leader-only) │
                                        └───────┬───────────────────┬──────────┘
                                                │                   │
                                     ┌──────────▼─────────┐  ┌──────▼──────────────────┐
                                     │ PostgreSQL 16       │  │ Redis                    │
                                     │ - source of truth   │  │ - rate-limit counters    │
                                     │ - Row Level Security│  │ - WS pub/sub fan-out     │
                                     │ - constraints       │  │   (tenant channels)      │
                                     └─────────────────────┘  │ - never source of truth  │
                                                              └──────────────────────────┘
```

| Component | Technology | Why |
|---|---|---|
| Backend API | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async) + asyncpg, Alembic | Async I/O for WebSockets; strict schema validation; parameterised SQL only. |
| Database | PostgreSQL 16 | Transactions, constraints, **Row Level Security** for tenant isolation. |
| Cache / bus | Redis | Shared rate-limit counters and cross-worker WebSocket fan-out. Optional in single-process dev (in-memory fallback). Losing Redis loses no data. |
| Customer web | Static HTML + vanilla TypeScript built with Vite (no framework) | ≈ 11 KB gzipped in total (JS 7.5 KB, CSS 2.9 KB); PIN screen in ≈ 0.8 s on emulated weak 4G with a cold cache. No install. |
| Staff app | Flutter (Dart), BLoC, GoRouter, Dio, flutter_secure_storage, local DB for kitchen state, WebSockets | One app, role-based navigation for Owner/Admin/Waiter/Kitchen. Tablet-friendly. |

## 3. Trust boundaries

```text
 UNTRUSTED                               │ TRUSTED
─────────────────────────────────────────┼────────────────────────────────────────
 Customer browser, QR code, URL,          │ FastAPI process (validates everything)
 cookies, request bodies, headers,        │ PostgreSQL (RLS = second, independent wall)
 Flutter app (even when staff use it),    │ Server-side secrets (env / secret manager)
 WebSocket messages from any client       │
```

Rules that follow from this:

1. **The client never chooses its tenant.** Restaurant/table context is *derived* on the server —
   from a verified staff access token, or from a verified customer table session — never from a
   request field.
2. **Every request is authorised on the server** (role → permission → resource ownership).
   The Flutter UI hiding a button is a convenience, not a control.
3. **Defence in depth for tenancy.** Application code scopes every query by tenant *and* the
   database enforces RLS. A bug in one layer does not leak another restaurant's data.
4. **The QR code is public.** It identifies a table; it authorises nothing.

## 4. Tenancy model

- Every restaurant is a tenant (`restaurants.id`). Every tenant-owned row carries `restaurant_id`.
- The API connects to Postgres as a role **without** `BYPASSRLS` that does **not** own the tables.
  Every transaction sets `app.restaurant_id` (transaction-local) and RLS policies compare against it.
  Unset ⇒ `NULL` ⇒ zero rows (fail-closed).
- Cross-table references include `restaurant_id` in composite foreign keys, so the database itself
  rejects "order in restaurant A pointing at a table in restaurant B".
- A handful of **resolver functions** (`SECURITY DEFINER`, fixed `search_path`) map an opaque,
  high-entropy credential (QR token, session-token hash, refresh-token hash, reset-token hash,
  restaurant code) to a `restaurant_id` *before* the tenant is known. They return routing ids
  only; all subsequent reads happen under RLS. Details: [02-data-model.md §5](02-data-model.md).

## 5. Primary flows

### 5.1 Customer: scan → PIN → menu → order

```text
Phone camera ─▶ https://order.example.com/t/<qr_token>       (static page, cached at CDN)
  page JS ─▶ GET  /api/v1/public/tables/<qr_token>           → restaurant name, table label,
                                                               "has valid session for this table?"
  user enters PIN
  page JS ─▶ POST /api/v1/public/tables/<qr_token>/session    {pin}
               server: rate-limit (IP, table, restaurant) → resolve token → verify PIN against the
               restaurant's *current* PIN version (HMAC, constant-time) → create table_session
               → Set-Cookie: __Host-ts=<random 256-bit token>; HttpOnly; Secure; SameSite=Strict
  page JS ─▶ GET  /api/v1/public/menu                         (context from session cookie only)
  page JS ─▶ POST /api/v1/public/orders  Idempotency-Key: <uuid>
               server: session → restaurant/table → items exist, belong to restaurant, available,
               modifiers valid for item & group rules, quantities bounded → single transaction:
               order + items + modifiers + order_event → commit → publish to tenant channel
  page JS ─▶ GET  /api/v1/public/orders  (poll every ~10 s, or SSE later) for status
```

### 5.2 Waiter: table → order → send

Authenticated staff (Bearer access token). No PIN. Same order-creation service as customers, with
`source = waiter` and `placed_by_user_id`. Server checks the waiter's permission and that the
table belongs to the waiter's restaurant.

### 5.3 Kitchen: new → aggregate → prepare → ready

The kitchen tablet holds a WebSocket to `/api/v1/ws/staff` and loads `GET /api/v1/kitchen/board`
(all `placed`/`accepted`/`preparing`/`ready` orders, oldest first) on start and after every
reconnection. It shows three lanes (tabs on a phone):

- **New orders** (`placed`), one ticket per order: *Accept* or *Start cooking*.
- **Cooking** (`accepted`, `preparing`): a **To cook** panel sums each dish across orders, keyed by
  `(menu item, chosen options)`, with the per-table/per-order breakdown kept underneath
  (aggregation is a *view* computed on the device; order items are never merged in storage).
  Tickets below: *Start cooking* / *Ready*.
- **Ready for pickup** (`ready`) with how long each has been waiting; waiters mark them served.
- **Delayed** is derived on the device: not yet ready and older than `kitchen_delay_minutes`,
  measured on the *server's* clock (the board carries `server_time`), so a tablet with a wrong
  clock still flags the right orders. Shown with an icon and the word "DELAYED", not colour alone.
- Status changes go over REST (`POST /orders/{id}/transition` with `expected_version`); the socket
  only pushes. Kitchen staff can also flag "can't make it" (a cancellation request for a manager).
- Owners/admins can open the same screen from their account menu.

## 6. Real-time design

- Services **stage** events inside their transaction (`app/realtime/outbox.py`); they are released
  only by that transaction's commit (a rollback drops them) and published after the request's work
  is done, to `tenant:{restaurant_id}:staff` (Redis pub/sub across workers; in-process bus in dev).
- Each WebSocket connection is authenticated with a staff access token sent in the **first
  message** (not the URL, so it never appears in proxy logs) and bound to exactly one tenant.
  There is no global stream; the server subscribes the socket only to its own tenant channel.
  Every 60 s the server re-checks the session (logout, deactivation, role change close the socket)
  and closes it when the access token expires unless the client re-authenticated in-band.
- Event envelope: `{event_id, type, restaurant_id, order_id, order_version, occurred_at, payload}`.
  Order events carry the full staff view of the order, so screens update without another request.
- Each socket has a bounded queue; a client that falls behind is closed with `4408` and resyncs
  instead of silently missing events. A Redis reconnect forces every client to resync too.
- The app treats events as hints over a REST source of truth: after every (re)connection it
  reloads; it drops duplicate `event_id`s and applies an order only if its `version` is newer.
  A heartbeat (ping every 25 s, 10 s deadline) detects half-open Wi-Fi connections; reconnects
  use capped, jittered exponential backoff; the access token is renewed ~90 s before expiry and
  re-sent on the open socket.
- Customers do not get a WebSocket in v1: polling a tiny endpoint is cheaper and more robust on
  weak 4G. (SSE can be added later.)

## 7. Kitchen reliability & sync

The kitchen screen keeps working through network drops and restarts (spec §36):
local state → reconnection → server synchronisation → conflict resolution.

1. **Saved on the device.** About a second after every change, the board (orders with their items
   and versions, plus the versions of orders that left) is written to one JSON file in the app's
   private storage, replacing the old file atomically. On start the screen shows the saved board
   at once — labelled "saved on this tablet (as of …)" — and then loads the server's. The file
   belongs to one restaurant, is ignored once 12 h old, holds no secrets, and is deleted on
   sign-out or when the session ends; Android backup and device-to-device transfer of app data
   are disabled.
2. **Offline restart.** If the app starts without network, the profile saved at sign-in (next to
   the refresh token in the keystore) lets it open the saved board. That unlocks nothing on the
   server: there is no access token until a refresh succeeds. Only the server *rejecting* the
   refresh token ends a session — "can't reach the server" never signs a tablet out (the REST
   client and the WebSocket client follow the same rule).
3. **Offline rules.** While the board is unconfirmed (the saved copy) or the server is unreachable
   (live connection down and the last load failed), the action buttons are disabled and the
   banner says why. Changes are **never queued**: a "Ready" replayed minutes later could
   contradict what happened meanwhile. A request that fails mid-flight reloads the board and says
   the change "may not have gone through". Retrying is safe because every transition carries
   `expected_version`; if the earlier tap did get through, the retry's `409` is reported as
   "already done", not as a conflict.
4. **Reconnect = resync.** On every (re)connection the board is reloaded (authoritative snapshot;
   the server wins). While live it is also re-checked every 2 minutes, in case a pub/sub message
   was lost; while offline it is polled every 20 s.
5. **Merge rules** (`OrderBook`): duplicate `event_id`s are dropped; a change applies only if its
   `version` is newer; an order that leaves the board leaves a tombstone with its version (also
   when a snapshot drops it), so an older copy cannot bring it back; a snapshot never drops an
   order that changed after its `server_time`. The board also lists orders cancelled in the last
   2 hours with their final versions, so a tablet that was offline records the right version —
   no duplicate or resurrected orders after reconnecting.
6. **Telling the cook.** New orders chime (one chime per rush; a sound switch per tablet, which
   plays a test chime when turned on). If an order the kitchen had accepted, started or finished
   is cancelled — live, or discovered on reconnecting — a red "STOP" notice lists its dishes until
   someone taps OK, with a distinct alert tone. The screen stays awake while the board is open.

## 8. Background jobs

They run inside the API process; on several workers a Postgres advisory lock elects one leader
per run (PIN rotation, retention), and each restaurant is processed in its own tenant-bound
transaction.

### PIN rotation

A background loop (one leader across workers, elected via a Postgres advisory lock) runs every
60 s, asks the resolver function which restaurants are due, and rotates each one inside its own
tenant-scoped transaction. Staff devices receive a `pin.rotated` event (no PIN in the event; the
app fetches it via the authenticated PIN endpoint). If the scheduler is down, PINs simply rotate
late — the system fails toward "older PIN still works", never toward "no PIN needed".

### Data retention

Every hour (`RETENTION_INTERVAL_SECONDS`) the job deletes, per restaurant and in committed batches
of 5 000: refresh tokens a day past expiry; staff sessions and password-reset links 30 days
(`AUTH_RECORD_RETENTION_DAYS`) past expiry; customer table sessions 30 days
(`TABLE_SESSION_RETENTION_DAYS`) past expiry that never placed or touched an order; audit logs
older than 365 days (`AUDIT_LOG_RETENTION_DAYS`). The runtime role cannot delete audit rows: it
calls `app_purge_audit_logs`, which only deletes the bound restaurant's rows and refuses anything
younger than 180 days. Purging audit logs is itself audited. Orders are kept (the restaurant's own
history, no personal data). `python -m app.cli purge` runs the job once. Staff problem reports
are deleted after 365 days (`PROBLEM_REPORT_RETENTION_DAYS`).

### Pilot usage counts

For the pilot (spec §48 Phase 11) each worker counts, in memory, how often a table's page is
opened, a PIN is accepted, wrong or refused by the guessing limit, and a staff device connects,
loses its connection or has to reload — per restaurant, nothing else (no IP addresses, devices
or people). About once a minute (`USAGE_FLUSH_SECONDS`) and at shutdown the counts are added to
`usage_counters` (restaurant, local day, metric). They feed `python -m app.cli pilot-report`.

## 9. Repository layout

```text
/
├── docs/                    architecture, data model, security, API contracts
├── backend/                 FastAPI service
│   ├── app/
│   │   ├── api/             routers (auth, restaurants, staff, tables, menu, orders, kitchen, public)
│   │   ├── core/            config, security primitives, errors, rate limiting, logging
│   │   ├── db/              engine, tenant-bound sessions
│   │   ├── models/          SQLAlchemy models
│   │   ├── schemas/         Pydantic request/response models (extra="forbid")
│   │   ├── repositories/    tenant-scoped data access
│   │   ├── services/        business logic (auth, table sessions, orders, kitchen board)
│   │   ├── middleware/      request id, security headers, body-size limit
│   │   ├── realtime/        event bus (memory / Redis) and transactional outbox
│   │   └── main.py
│   ├── alembic/             migrations (incl. RLS policies, grants, resolver functions)
│   └── tests/               unit + integration tests against real PostgreSQL
├── customer-web/            (Phase 6) static mobile ordering site (+ staff password-reset page)
├── staff_app/               (Phase 7–8, 11) Flutter app
└── deploy/                  (Phase 11) Docker Compose, Caddy, secrets generator, backup/restore
```

Authentication/authorization are implemented as FastAPI **dependencies** (`app/api/deps.py`)
rather than ASGI middleware: dependencies run per-route, are typed, and make each endpoint's
required permission visible in its signature. Rate limiting is likewise applied per route, because
limits differ per endpoint.

## 10. Phase plan

| Phase | Scope | Status |
|---|---|---|
| 1 | Architecture, roles, data model, security boundaries, PIN/session model, API contracts | ✅ this document set |
| 2 | PostgreSQL schema, migrations, RLS, constraints, indexes | ✅ |
| 3 | Login, Argon2id, access/refresh tokens with rotation, RBAC, tenant binding, rate limiting, password reset | ✅ |
| 4 | Restaurant registration, staff management, tables, QR, PIN management & rotation (+ customer table-session endpoints) | ✅ |
| 5 | Menu: categories, items, modifiers, availability, images (+ customer menu endpoint) | ✅ |
| 6 | Customer web: QR → PIN → session → menu → cart → order (+ order creation, idempotency, customer cancellation) | ✅ |
| 7 | Waiter Flutter app + staff order API & state machine | ✅ |
| 8 | Kitchen Flutter: board, aggregation, real-time (+ live updates for waiters) | ✅ |
| 9 | Reliability: local persistence, offline action rules, sync hardening, alerts, retention | ✅ |
| 10 | Security test campaign: whole-surface sweep, races, fuzzing, browsers, load, dependencies (docs/06) | ✅ |
| 11 | Pilot: manager tools in the app, problem reports and usage counts, operator setup tools, password reset page, deployment kit, rehearsal, pilot playbook (docs/07, docs/08) | ✅ ready — the pilot itself runs in the restaurant |
