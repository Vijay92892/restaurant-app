# 03 — Security Model & Threat Model

Fundamental rule: **never trust the client.** The client can lie about restaurant, table, user,
role, menu item, quantity, order, status and session. The server derives or re-validates all of
them on every request.

## 1. Actors and roles

| Actor | Authenticates with | Tenant derived from |
|---|---|---|
| Customer | Table session cookie (opened with QR token + current PIN) | the table session row |
| Owner / Admin / Waiter / Kitchen | Staff access token (JWT) + server-side session check | the token's `rid` claim, re-checked against the DB user row |
| System (scheduler) | n/a (in-process) | resolver function → one tenant at a time |

### Permission matrix (enforced in `app/core/permissions.py`, checked server-side)

| Permission | Owner | Admin | Waiter | Kitchen |
|---|:-:|:-:|:-:|:-:|
| restaurant.settings.read | ✓ | ✓ | ✓ | ✓ |
| restaurant.settings.write | ✓ | ✓ | | |
| staff.read | ✓ | ✓ | | |
| staff.manage (waiter/kitchen accounts) | ✓ | ✓ | | |
| staff.manage_admins (admin accounts) | ✓ | | | |
| tables.read | ✓ | ✓ | ✓ | ✓ |
| tables.manage (create/rename/disable/delete/regenerate QR) | ✓ | ✓ | | |
| tables.close (end customer sessions at one table) | ✓ | ✓ | ✓ | |
| pin.view (to tell/display to customers) | ✓ | ✓ | ✓ | |
| pin.manage (rotate, change interval) | ✓ | ✓ | | |
| table_sessions.invalidate_all | ✓ | ✓ | | |
| menu.read | ✓ | ✓ | ✓ | ✓ |
| menu.manage | ✓ | ✓ | | |
| menu.availability (mark sold out / back) | ✓ | ✓ | | ✓ |
| orders.read | ✓ | ✓ | ✓ | ✓ |
| orders.create (on behalf of a table) | ✓ | ✓ | ✓ | |
| orders.kitchen_transition (accept / preparing / ready) | ✓ | ✓ | | ✓ |
| orders.serve (served / completed) | ✓ | ✓ | ✓ | |
| orders.cancel (per state-machine policy) | ✓ | ✓ | ✓ | |
| orders.cancel_privileged (any non-terminal state) | ✓ | ✓ | | |
| orders.cancel_request (flag an issue) | ✓ | ✓ | ✓ | ✓ |
| orders.cancel_review (approve/reject requests) | ✓ | ✓ | | |
| audit.read | ✓ | ✓ | | |
| problem_reports.create (report a problem during service) | ✓ | ✓ | ✓ | ✓ |
| problem_reports.read | ✓ | ✓ | | |

Role-escalation guards (service layer): nobody can grant a role higher than their own; only the
owner manages admins; the owner account cannot be deleted/deactivated/demoted through staff
management (ownership transfer is a dedicated owner-only action); users cannot change their own
role.

## 2. Staff authentication

- **Login** = restaurant code + username + password. (Waiters and kitchen staff often have no email;
  the restaurant code scopes usernames per tenant, so login never needs a cross-tenant user lookup.)
- **Passwords**: Argon2id (`argon2-cffi`, RFC 9106 low-memory profile: t=3, m=64 MiB, p=4),
  hashed in a worker thread so the event loop is never blocked. Length 8–128 characters
  (upper bound prevents hash-DoS), a small common-password blocklist, may not equal the username.
  Hashes are transparently upgraded on login when parameters change.
- **Uniform failure**: unknown restaurant, unknown user, inactive user and wrong password all return
  the same `401 invalid_credentials` and spend the same Argon2 time (a dummy hash is verified).
- **Access token**: JWT, HS256, 15-minute lifetime, claims `sub, rid, sid, iat, nbf, exp, jti, typ,
  iss, aud`. Algorithm is pinned on verification (no `none`, no algorithm confusion). The role is
  **not** in the token: on every request the server loads the user (under RLS) and checks
  `is_active`, `deleted_at`, and that the `sid` session is not revoked/expired. Deactivation,
  logout and role changes therefore take effect immediately, not after token expiry.
- **Refresh token**: 256-bit random, opaque, stored only as SHA-256. Rotated on every use.
  Presenting an already-rotated token is treated as theft → the whole session is revoked —
  *except* within a 30-second grace window where the rotated child has never been used (a lost
  response on a flaky kitchen network); in that case the unused child is revoked and a fresh one
  issued. Session absolute lifetime 30 days (kitchen tablets), idle lifetime 14 days.
- **Logout** revokes the session (all its refresh tokens; its access tokens fail the per-request
  session check). **Logout everywhere** revokes all of the user's sessions.
- **Password change** requires the current password and revokes all *other* sessions.
- **Admin reset**: owner/admin sets a temporary password → `must_change_password = true`, all the
  user's sessions revoked. Until changed, the account can only call `me`, `change-password`,
  `logout`.
- **Self-service reset** (users with an email): request always returns `202`; token 256-bit,
  SHA-256 stored, 1 h, single-use; success revokes all sessions.
- **Flutter storage**: refresh token in `flutter_secure_storage` (Android Keystore / iOS Keychain);
  access token in memory only. No secrets are compiled into the app. The signed-in user's profile
  (name, role, permissions — no secrets) is kept next to the refresh token and removed with it.
- **Unreachable ≠ signed out**: the app ends a session only when the server *rejects* the refresh
  token (`401`). Network failures, timeouts, `429` and `5xx` keep the session, so a kitchen tablet
  is not logged out by bad Wi-Fi. Starting offline, the saved profile lets the device show what it
  saved earlier; the server still requires a valid refresh before it answers anything, and a
  revoked session ends at the first contact.

## 3. Tenant isolation

Two independent walls:

1. **Application**: tenant id is bound to the DB session from verified credentials only. Every
   repository method is tenant-scoped; resource lookups are `WHERE id = :id` *under RLS*, so another
   tenant's id yields "not found" (404), never 403 — no existence oracle.
2. **Database**: RLS on every tenant table, `FORCE` not required because the runtime role does not
   own tables; the runtime role lacks `BYPASSRLS`; composite FKs make cross-tenant references
   impossible; unset tenant ⇒ zero rows.

Binding a session to a second, different tenant raises an error (prevents accidental mixing).

## 4. Table PIN & customer sessions

### 4.1 Model
- The **QR** encodes `https://order.example.com/t/<qr_token>`; the token is 128-bit random,
  identifies one table, contains no PIN and no internal ids. QR ≠ authentication.
- Each restaurant has one **current PIN** (4 or 6 digits, CSPRNG; weak patterns such as `0000`,
  `1234`, `1212` and the previous PIN are rejected at generation).
- PIN verification: `HMAC-SHA256(PIN_HMAC_KEY, restaurant_id ‖ version ‖ candidate)` compared in
  constant time against the **current** version only. Old PINs never verify.
- On success a **table session** is created: 256-bit random token → `__Host-ts` cookie
  (`HttpOnly; Secure; SameSite=Strict; Path=/`), SHA-256 stored server-side. The session row
  permanently fixes `restaurant_id` and `table_id`. The PIN is never used as, or embedded in, the
  session token.
- Rotation (auto every 2/4/6 h or manual) creates `version + 1`. Existing sessions continue until
  expiry unless the staff member chooses **"rotate and invalidate active sessions"** or uses the
  separate **"invalidate all active table sessions"** action (both `pin.manage` /
  `table_sessions.invalidate_all`, both audited).
- **Close table** (waiter): ends every active session at that table and completes served orders —
  the next party needs the current PIN. This also limits the window for a departed customer to
  keep ordering from outside.

### 4.2 Brute-force controls (`POST /public/tables/{qr}/session`)

| Counter (failures only) | Limit | Effect |
|---|---|---|
| per client IP | 5 / 15 min | 429 with `Retry-After` |
| per table **and PIN version** | 10 / 15 min | 429 |
| per restaurant **and PIN version** | 60 / 60 min | 429 + `pin.bruteforce_suspected` audit + live alert to staff devices |
| per IP, all requests to this endpoint | 30 / 15 min | 429 |
| per IP, QR landing lookups | 120 / 5 min | 429 |

**Lockout trade-off.** The table/restaurant caps protect the PIN but also let an attacker who
photographed a QR code deliberately lock customers out. Because those counters are keyed by
PIN *version*, staff can lift such a lockout instantly by rotating the PIN (guesses against the
old PIN are worthless anyway), and waiters can always place orders for a table without a PIN.

All failure causes (unknown QR token, disabled table, suspended restaurant, wrong PIN) return the
identical body `401 {"error":{"code":"invalid_table_access","message":"Invalid table access code."}}`.

**Residual risk, stated honestly**: the PIN is a *shared* code. Anyone physically told the PIN can
open a session for any table whose QR they have. Against a remote attacker spreading guesses
across many IPs, the per-restaurant cap bounds guesses to ~60/h; for a 4-digit PIN rotated every
4 h that is ≤ 240/10 000 ≈ 2.4 % per rotation window *for a determined distributed attacker*
(and staff are alerted). Restaurants wanting more margin choose 6 digits (≈ 0.024 %). A
successful remote order still arrives at a physical table where staff will notice no one is
sitting.

### 4.3 Customer request rules
- Context (`restaurant_id`, `table_id`) is taken from the session row, never from the request.
- CSRF: cookie is `SameSite=Strict`; state-changing public endpoints additionally require a JSON
  body (form posts fail validation) and reject a browser `Origin` that is not in
  `PUBLIC_WEB_ORIGINS` (`403 origin_not_allowed`; required in production).
- The customer web app and `/api/v1/public/*` must be served from the **same origin**
  (e.g. `order.example.com` and `order.example.com/api/…` behind one reverse proxy) so the
  `__Host-` cookie is first-party.

### 4.4 Restaurant registration
`REGISTRATION_MODE` = `closed` (operators only, via CLI) | `invite` (default; requires one of
`REGISTRATION_INVITE_CODES`, compared in constant time, ≥ 16 chars in production) | `open`.
Rate-limited to 5 attempts/hour per IP. Registration creates the restaurant, the owner and the
first PIN in one transaction, then signs the owner in.
- Customers can only read/cancel orders created by their own session.
- Ordering limits: 10 *created* orders / 10 min per session; 30 attempts / 10 min per session and
  60 / 10 min per IP (attempts are counted separately so a few "sold out" errors never lock a
  diner out); order list polling 120 / 5 min per session.
- The customer web app renders all server text with `textContent` (never `innerHTML`) and ships
  with a strict CSP (`default-src 'self'`, no inline script, `object-src 'none'`, `base-uri 'none'`).

## 5. API hardening (all endpoints)

- Pydantic v2 schemas with `extra="forbid"`, length/range bounds on every field, enums for states.
- SQLAlchemy Core/ORM only; no string-built SQL. `text()` is used only with bound parameters.
- Uniform error envelope `{"error":{"code","message"},"request_id"}`; validation errors list field
  paths and messages but never echo the submitted input; unhandled errors return a generic 500 and
  are logged server-side with the request id. No stack traces, SQL, paths or secrets reach clients.
- Request body cap (1 MiB JSON; uploads have their own cap), security headers
  (`nosniff`, `frame-ancestors 'none'`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`,
  HSTS in production), OpenAPI docs disabled in production.
- Client IP derived from `X-Forwarded-For` **only** when the direct peer is a configured trusted proxy.
- Rate limiting backed by Redis in production (in-memory, bounded, for single-process dev/tests).
  Failure budgets (wrong PINs, wrong passwords, orders per table session) are **taken before** an
  attempt is checked and refunded if it succeeds, so a burst of simultaneous requests cannot slip
  past them (`Budget` in `app/core/rate_limit.py`; found by the Phase 10 sweep, docs/06).
- Input the database rejects as data (SQLSTATE class 22, e.g. a NUL character) is answered `400`
  as a safety net behind validation, never `500`.
- WebSocket: text frames only (binary ⇒ `1003`), 8 KB per message, 300 connection attempts per IP
  per 5 minutes; run uvicorn with `--ws-max-size 65536` so oversized frames are refused before
  they are buffered.
- Every route is declared with its protection in `backend/tests/security/surface.py`; a route
  that is not declared fails the test suite, and every declared route is attacked automatically
  (missing credentials, every role, other restaurants' ids, hostile input, rate limits).

## 6. Threat model (spec §46) → mitigation → test

| Threat | Mitigation | Verified by (phase) |
|---|---|---|
| QR photography / remote ordering | QR is only an identifier; current PIN required; sessions bound to one table; close-table ends sessions | Phase 6 tests: order without session ⇒ 401 |
| PIN brute force | Per-IP / per-table / per-restaurant failure caps, uniform errors, staff alert, 6-digit option | Phase 4/6 tests |
| Old PIN reuse | Only current `pin_versions` row verifies; version in HMAC input | Phase 4 tests |
| Session theft | HttpOnly+Secure+SameSite=Strict cookie, hashed at rest, expiry, close-table, invalidate-all | Phase 6 tests |
| Session replay after expiry/revocation | Resolver returns tenant only for active & unexpired; per-request status check | Phase 6 tests |
| Staff token theft / replay | 15-min access tokens; per-request session check; refresh rotation + reuse detection | **Phase 3 tests** |
| ID enumeration / IDOR / BOLA | UUIDs *plus* RLS-scoped lookups; other-tenant ids ⇒ 404; customer reads limited to own session | Phase 2 RLS tests, Phase 4+ API tests |
| Cross-table access | Table derived from session; composite FKs; order validation re-checks table | Phase 6 tests |
| Cross-restaurant access | Tenant derived from credentials; RLS; composite FKs | **Phase 2/3 tests** |
| Order manipulation | State machine with role permissions, `expected_version`, audit | ✅ Phase 7 tests |
| Quantity manipulation | 1–50 per line, ≤ 30 lines, server-side limits; integers only | Phase 6 tests |
| Menu manipulation | Items/modifiers re-loaded server-side; must belong to tenant, be active & available; modifier group min/max enforced; names snapshotted from DB, never from client | Phase 6 tests |
| Order duplication | `Idempotency-Key` unique per actor + request fingerprint; returns original order | Phase 6/7 tests |
| API abuse / flooding | Per-route rate limits; body-size cap; pagination limits | **Phase 3 tests** (auth) |
| WebSocket abuse | First-message auth with timeout; tenant-only subscription; origin allow-list; server ignores client-sent events; per-connection message rate and size caps; connection limit per user; periodic session re-check | ✅ Phase 8 tests |
| Role escalation | Role from DB, not token; permission checks per route; escalation guards in staff service | **Phase 3** (role-from-DB), Phase 4 (staff mgmt) |
| SQL injection | ORM/bound parameters only; inputs validated | **Phase 3 tests** (injection strings in login fields) |
| XSS | Customer text stored as plain text, length-limited, control chars stripped; customer web renders with `textContent` only; strict CSP on the web app | Phase 6 tests |
| Malicious file upload | MIME sniffing via Pillow decode, extension allow-list (jpg/png/webp), size & pixel caps, re-encode, random filenames, stored outside app dir, served with `nosniff` | Phase 5 tests |
| Request flooding | Rate limits (taken atomically, burst-proof) + reverse proxy limits + body caps + WebSocket connection limit | **Phase 10 sweep** (docs/06) |
| Credential stuffing | Per-IP and per-account failure limits, uniform errors, audit of failures | **Phase 3 tests** |
| Misconfigured deployment (no HTTPS, missing headers, docs exposed, framing) | `deploy/` configuration; production settings refuse to start when unsafe; `scripts/check_deployment.py` checks the live site from outside after every deploy | **Phase 11 rehearsal** (docs/07 §9) |
| Password-reset token leakage | Token only in the URL fragment (never sent to a server or in `Referer`), removed from the address bar at once, single use, 1 h, hashed at rest | Phase 3 tests + Phase 11 e2e (3 browsers) |

## 7. Audit events

Written in the same transaction as the action (or immediately for failed logins).
`auth.login.succeeded`, `auth.login.failed`, `auth.logout`, `auth.refresh.reuse_detected`,
`auth.password.changed`, `auth.password_reset.requested`, `auth.password_reset.completed`,
`staff.created|updated|role_changed|deactivated|deleted|password_reset`, `menu.*`, `table.*`,
`pin.rotated`, `pin.settings_changed`, `pin.bruteforce_suspected`, `table_sessions.invalidated`,
`table.closed`, `order.status_changed`, `order.cancel_requested`, `order.cancelled`,
`order.cancel_request_rejected`, `audit_log.purged` (by the retention job, with the count).
`details` is built from an allow-list; passwords, tokens, PINs and hashes are never included.

## 8. Secrets

`JWT_SECRET`, `PIN_HMAC_KEY`, `PIN_ENC_KEY` (≥ 32 random bytes each) and DB credentials come from
the environment / secret manager. The app refuses to start in production with missing or
development-default secrets. Nothing secret ships in the Flutter app or customer web bundle.

## 9. Data kept on devices, and retention

- **Kitchen tablets** save the active board (orders, dishes, table labels, guest notes, versions)
  in one file in app-private storage — never shared storage — so a restart during an outage still
  shows the orders. It holds no tokens or prices, belongs to one restaurant (another restaurant's
  file is never shown), is ignored after 12 h, and is deleted on sign-out or when the session
  ends (a late write from a closing screen cannot bring it back). Android backup and
  device-to-device transfer of app data are disabled, so neither this file nor the keystore
  entries leave the device. The device's own lock and storage encryption protect it at rest.
- **Server retention**: see docs/02 §7. The runtime role cannot erase recent audit history.
- **Pilot data** (Phase 11): problem reports are staff-written text (bounded, plain text, shown
  only to owners/admins and in the operator's report, deleted after 365 days). Usage counts are
  per restaurant and day, with no IP addresses, devices, sessions or people (spec §40).
