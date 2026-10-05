# 02 — Data Model

PostgreSQL 16. All ids are UUIDv4 unless noted. All timestamps are `timestamptz` (UTC in storage;
the restaurant's `timezone` is used only for display). Enumerations are `text` + `CHECK` (simpler
to evolve than Postgres enums).

There are **no payment, billing, invoice, tax or revenue tables**, and none may be added.

## 1. Tenant-enforcement rules (apply to every table below)

1. Every tenant-owned table has `restaurant_id uuid NOT NULL`.
2. RLS is enabled with policy `restaurant_id = app_current_restaurant_id()` for both `USING` and
   `WITH CHECK` (`restaurants` uses `id = …`). `app_current_restaurant_id()` reads the
   transaction-local setting `app.restaurant_id`; unset ⇒ `NULL` ⇒ no rows visible, no rows writable.
3. Parents expose `UNIQUE (id, restaurant_id)`; children reference them with **composite foreign
   keys** `(parent_id, restaurant_id)`. A row can therefore never point at another tenant's row,
   even if application code is wrong.
4. The runtime DB role (`APP_DB_ROLE`) has no `BYPASSRLS`, does not own any table, and receives only
   the privileges listed in §4. Append-only tables (`audit_logs`, `order_events`) get no `UPDATE`
   or `DELETE`.
5. Nothing is hard-deleted that history depends on: users, tables, menu entities use `deleted_at`
   (soft delete). Orders are never deleted.

## 2. Entities

### Tenancy & staff

**restaurants** — the tenant.
| column | type | notes |
|---|---|---|
| id | uuid PK | |
| code | text UNIQUE | Restaurant code used at staff login, `^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$` |
| name | text | 1–120 chars |
| timezone | text | default `Asia/Kolkata` |
| status | text | `active` \| `suspended` |
| pin_length | smallint | `4` \| `6`, default 4 |
| pin_rotation_minutes | int NULL | `120` \| `240` \| `360`; `NULL` = manual. Default 240 |
| table_session_ttl_minutes | int | 30–720, default 180 |
| waiter_can_cancel_preparing | bool | cancellation policy, default false |
| kitchen_delay_minutes | int | 5–120, default 15 — threshold for "DELAYED" badge (derived, not a status) |
| order_seq | bigint | per-restaurant human order number counter |
| created_at / updated_at | timestamptz | |

**users** — staff only (customers never have accounts).
| column | type | notes |
|---|---|---|
| id, restaurant_id | | UNIQUE (id, restaurant_id) |
| username | text | `^[a-z0-9._-]{3,32}$`, unique per restaurant among non-deleted users |
| email | text NULL | lower-cased; unique per restaurant; only needed for self-service reset |
| display_name | text | 1–80 |
| role | text | `owner` \| `admin` \| `waiter` \| `kitchen`; **one non-deleted owner per restaurant** (partial unique index) |
| password_hash | text | Argon2id (PHC string) |
| is_active | bool | deactivation takes effect on the next request |
| must_change_password | bool | set after admin reset; blocks everything except change-password |
| password_changed_at, last_login_at, deleted_at, created_at, updated_at | timestamptz | |

**auth_sessions** — one row per staff login (device).
`id, restaurant_id, user_id → users, created_at, last_used_at, expires_at (absolute, 30 d),
revoked_at, revoke_reason, user_agent (≤200), created_ip inet`.
Access tokens carry `sid`; every authenticated request checks the session is not revoked/expired.

**refresh_tokens** — rotating refresh tokens within a session.
`id, restaurant_id, session_id → auth_sessions, parent_id, token_hash bytea UNIQUE (SHA-256),
created_at, expires_at, rotated_at, revoked_at`.

**password_reset_tokens**
`id, restaurant_id, user_id → users, token_hash bytea UNIQUE, created_at, expires_at (1 h), used_at`.

### Tables, QR, PIN, customer sessions

**dining_tables**
`id, restaurant_id, label (1–20, unique per restaurant case-insensitively among non-deleted),
is_active, sort_order, deleted_at, created_at, updated_at`.

**table_qr_tokens** — public identifiers printed in QR codes.
`id, restaurant_id, table_id → dining_tables, token text UNIQUE (22 chars base64url = 128 bits),
created_at, revoked_at`. At most one non-revoked token per table. "Regenerate QR" revokes and
replaces. Tokens are *not* secrets (they are printed), so they are stored as-is, but they are
unguessable, so possessing one is required to even see a restaurant's landing page.

**pin_versions** — one row per PIN generation.
| column | type | notes |
|---|---|---|
| id, restaurant_id | | |
| version | int | monotonically increasing per restaurant; UNIQUE (restaurant_id, version) |
| pin_hmac | bytea | `HMAC-SHA256(PIN_HMAC_KEY, restaurant_id ‖ version ‖ pin)` — verification |
| pin_ciphertext | bytea | `AES-256-GCM(PIN_ENC_KEY, pin, aad = restaurant_id ‖ version)` — staff display only |
| key_version | smallint | supports key rotation |
| reason | text | `initial` \| `manual` \| `scheduled` |
| created_by_user_id | uuid NULL | NULL for scheduled rotations |
| created_at, superseded_at | timestamptz | exactly one row with `superseded_at IS NULL` per restaurant = current PIN |

Why both: a 4-digit PIN has only 10 000 values, so any hash of it is reversible by brute force if
an attacker steals the database. Keying the hash with a secret held **outside** the database
(`PIN_HMAC_KEY`) removes that offline attack. Staff must be able to *read* an automatically rotated
PIN to display it in the restaurant, so a separately-keyed ciphertext is kept, decryptable only by
the API for authorised staff. Plaintext PINs are never stored or logged.

**table_sessions** — a customer's temporary access to one table.
| column | type | notes |
|---|---|---|
| id, restaurant_id | | UNIQUE (id, restaurant_id) |
| table_id | uuid | → dining_tables (composite FK) — **fixed for life** |
| token_hash | bytea UNIQUE | SHA-256 of the 256-bit random cookie value; the token itself is never stored |
| pin_version | int | PIN generation used to open the session |
| status | text | `active` \| `revoked` \| `closed` (expiry is derived from `expires_at`) |
| created_at, expires_at, last_seen_at | timestamptz | |
| ended_at, end_reason, ended_by_user_id | | `invalidated_by_staff`, `pin_rotated`, `table_closed`, `customer_left` |

### Menu

**menu_categories** `id, restaurant_id, name (unique per restaurant among non-deleted), description,
sort_order, is_active, deleted_at, timestamps`.

**menu_items** `id, restaurant_id, category_id → menu_categories, name, description (≤500),
dietary (veg | non_veg | egg | NULL), display_price_paise (int ≥ 0, NULL, informational only),
image_path, is_available (sold-out toggle), is_active (shown on menu), sort_order, deleted_at,
timestamps`.

**menu_modifier_groups** `id, restaurant_id, name ("Size", "Spice level", "Add-ons"),
min_select, max_select (CHECK 0 ≤ min ≤ max, max ≥ 1), sort_order, deleted_at, timestamps`.
`min=1,max=1` ⇒ required single choice; `min=0,max=3` ⇒ optional multi-select.

**menu_modifiers** (options) `id, restaurant_id, group_id → menu_modifier_groups, name,
display_price_delta_paise (NULL, informational), is_available, sort_order, deleted_at, timestamps`.

**menu_item_modifier_groups** (the spec's *menu_item_modifiers*) `restaurant_id, menu_item_id,
group_id, sort_order; PK (menu_item_id, group_id)` — which groups apply to which item.

### Orders

**orders**
| column | type | notes |
|---|---|---|
| id, restaurant_id | | UNIQUE (id, restaurant_id) |
| order_number | int | per-restaurant counter, UNIQUE (restaurant_id, order_number) |
| table_id | uuid | → dining_tables |
| source | text | `customer` \| `waiter` |
| table_session_id | uuid NULL | set iff source = customer |
| placed_by_user_id | uuid NULL | set iff source = waiter (CHECK enforces exactly one) |
| status | text | see §3 |
| version | int | +1 on every change; optimistic concurrency & sync |
| notes | text NULL | ≤ 500, stored as plain text, rendered escaped |
| idempotency_key | text | client UUID; UNIQUE per (restaurant, session) and per (restaurant, user) |
| request_fingerprint | bytea | SHA-256 of canonical request; same key + different body ⇒ 422 |
| cancel_requested_at, cancel_request_reason, cancel_requested_by, cancel_requested_by_user_id | | pending cancellation request (does not stop the kitchen until approved) |
| cancelled_at, cancel_reason_code, cancel_reason, cancelled_by_user_id | | `cancel_reason_code` ∈ customer_changed_mind, item_unavailable, duplicate, kitchen_issue, `mistake` (entered by mistake, Phase 11), other |
| accepted_at, preparing_at, ready_at, served_at, completed_at, created_at, updated_at | timestamptz | |

**order_items** `id, restaurant_id, order_id → orders, line_no, menu_item_id → menu_items,
item_name (snapshot), quantity (1–50), notes (≤200), display_price_paise (snapshot, informational),
created_at`. Items are **never merged** across orders; kitchen aggregation is computed at read time.

**order_item_modifiers** `id, restaurant_id, order_item_id → order_items, modifier_id →
menu_modifiers, group_name, modifier_name (snapshots), display_price_delta_paise (snapshot)`.
UNIQUE (order_item_id, modifier_id).

**order_events** (fulfils the spec's *kitchen_events*) — append-only lifecycle log, also the source
of WebSocket envelopes. `id (event uuid), seq bigint identity, restaurant_id, order_id → orders,
type, from_status, to_status, order_version, actor_type (customer | staff | system),
actor_user_id, actor_table_session_id, created_at, payload jsonb`.

**problem_reports** (Phase 11, pilot observation) — `id, restaurant_id, reported_by_user_id →
users, category (order_mistake | customer_help | app_confusing | network | slow | other), note
(≤ 500, plain text, NULL if empty), order_id → orders NULL, created_at`. Composite foreign keys
keep the reporter and the order inside the restaurant.

**usage_counters** (Phase 11) — `(restaurant_id, day, metric)` primary key, `count bigint ≥ 0`.
`day` is the restaurant's local day; `metric` ∈ `qr_opened, pin_accepted, pin_wrong,
pin_locked, staff_connected, staff_connection_lost, staff_resync`. Counts only.

**audit_logs** — append-only. `id bigint identity, restaurant_id, occurred_at, actor_type
(staff | customer | system | anonymous), actor_user_id, actor_table_session_id, action,
entity_type, entity_id, details jsonb (sanitised: never passwords, tokens, PINs, hashes), ip,
request_id`.

## 3. Order state machine

```text
             ┌──────────── cancel (see policy) ─────────────┐
             │                                               ▼
 PLACED ──▶ ACCEPTED ──▶ PREPARING ──▶ READY ──▶ SERVED ──▶ COMPLETED      CANCELLED
    └────────────────────▶ (kitchen may start directly)
```

| from → to | Kitchen | Waiter | Admin | Owner | Customer |
|---|---|---|---|---|---|
| placed → accepted | ✓ | | ✓ | ✓ | |
| placed / accepted → preparing | ✓ | | ✓ | ✓ | |
| preparing → ready | ✓ | | ✓ | ✓ | |
| ready → served | | ✓ | ✓ | ✓ | |
| served → completed | | ✓ | ✓ | ✓ | |
| placed → cancelled | | ✓ | ✓ | ✓ | ✓ (own session's order) |
| accepted → cancelled | | ✓ | ✓ | ✓ | request only |
| preparing → cancelled | | ✓ if `waiter_can_cancel_preparing`, else request | ✓ | ✓ | ✗ |
| ready / served → cancelled | | ✗ | ✓ | ✓ | ✗ |
| raise cancellation request / flag issue | ✓ | ✓ | ✓ | ✓ | (see above) |
| approve / reject cancellation request | | | ✓ | ✓ | |

Terminal states: `completed`, `cancelled`. Every transition increments `version`, writes an
`order_events` row and (for cancellations and privileged actions) an `audit_logs` row, in the same
transaction. Transitions require `expected_version`; mismatch ⇒ `409 Conflict`.

"DELAYED" is **derived** in the kitchen view (not yet `ready` and `server now - created_at >
kitchen_delay_minutes`, i.e. measured from when the guest ordered), not stored.

## 4. Runtime role privileges

| table | SELECT | INSERT | UPDATE | DELETE |
|---|---|---|---|---|
| restaurants, users, dining_tables, table_qr_tokens, pin_versions, menu_categories, menu_items, menu_modifier_groups, menu_modifiers, orders | ✓ | ✓ | ✓ | |
| auth_sessions, refresh_tokens, password_reset_tokens, table_sessions, menu_item_modifier_groups | ✓ | ✓ | ✓ | ✓ |
| order_items, order_item_modifiers (immutable once placed), order_events, audit_logs (append-only) | ✓ | ✓ | | |
| problem_reports (append-only; old ones deleted by the retention job) | ✓ | ✓ | | ✓ |
| usage_counters (added to, never removed) | ✓ | ✓ | ✓ | |

Old audit logs are removed only through `app_purge_audit_logs` (§7), never by a direct `DELETE`.

In addition, `BEFORE UPDATE` triggers make identity columns immutable even where `UPDATE` is
granted — e.g. a table session's `table_id`/`token_hash`/`expires_at`, an order's `table_id`/
`source`/`table_session_id`/`idempotency_key`, a PIN version's `pin_hmac`, and every row's
`restaurant_id`.

## 5. Pre-tenant resolver functions

`SECURITY DEFINER`, `STABLE`, `SET search_path = pg_catalog, public`, `EXECUTE` revoked from
`PUBLIC` and granted only to the runtime role. Each returns routing ids only.

| function | input | returns |
|---|---|---|
| `app_resolve_restaurant_code(text)` | restaurant code typed at login | restaurant id if active |
| `app_resolve_qr_token(text)` | QR token | (restaurant_id, table_id) if token, table and restaurant are all active |
| `app_resolve_table_session(bytea)` | SHA-256 of session cookie | restaurant_id if session active & unexpired |
| `app_resolve_refresh_token(bytea)` | SHA-256 of refresh token | restaurant_id (even if rotated — needed for reuse detection) |
| `app_resolve_password_reset_token(bytea)` | SHA-256 of reset token | restaurant_id |
| `app_due_pin_rotations(timestamptz)` | now | restaurant ids whose current PIN is older than their rotation interval |
| `app_restaurant_ids()` | — | every restaurant id (for background jobs that then work tenant by tenant) |

All further reads happen under RLS after binding the returned tenant.

## 6. Key indexes

- `orders (restaurant_id, status, created_at)` — kitchen board & active lists.
- `orders (restaurant_id, table_id, created_at DESC)` — table view.
- `orders (table_session_id)` — customer "my orders".
- `order_items (order_id)`, `order_item_modifiers (order_item_id)`.
- `table_sessions (restaurant_id, table_id) WHERE status = 'active'`.
- `auth_sessions (user_id) WHERE revoked_at IS NULL`.
- `audit_logs (restaurant_id, occurred_at DESC)`.
- `order_events (restaurant_id, seq)`.
- `menu_items (restaurant_id, category_id, sort_order)`.
- `auth_sessions`, `refresh_tokens`, `table_sessions` on `(restaurant_id, expires_at)` — retention.
- `order_events (actor_table_session_id) WHERE actor_table_session_id IS NOT NULL` — so deleting
  a table session does not scan every order event for the foreign-key check.

## 7. Retention (migration `0004`, `app/services/retention_service.py`)

| data | deleted when | why it is safe |
|---|---|---|
| `refresh_tokens` | 1 day past `expires_at` | an expired token is refused before the reuse check; rotated tokens of live sessions stay for theft detection |
| `auth_sessions` (cascade: their tokens) | 30 days past `expires_at` (revoked sessions expire too) | nothing can use them; kept a month for investigations |
| `password_reset_tokens` | 30 days past `expires_at` | single-use, 1 h links |
| `table_sessions` | 30 days past `expires_at`, only if no order or order event references them | sessions with orders stay with their orders |
| `problem_reports` | older than 365 days (`PROBLEM_REPORT_RETENTION_DAYS`, ≥ 30) | staff free text that may mention guests; kept long enough to learn from the pilot |
| `audit_logs` | older than 365 days (configurable, never < 180) | via `app_purge_audit_logs(p_before, p_limit)`: `SECURITY DEFINER`, deletes only the bound tenant's rows, raises if `p_before` is within 180 days or no tenant is bound, at most 10 000 rows per call |
| `orders`, `order_items`, `order_events` | kept | the restaurant's own history; no personal data by design |
