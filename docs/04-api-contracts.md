# 04 — API Contracts (v1)

Base path `/api/v1`. JSON only (`Content-Type: application/json`) except image upload
(multipart) and QR download. Timestamps are ISO-8601 UTC. Ids are UUID strings.

**Error envelope** (every non-2xx):
```json
{ "error": { "code": "invalid_credentials", "message": "Invalid credentials." }, "request_id": "..." }
```
Validation errors (`422 validation_error`) add `"fields": [{"loc": ["body","username"], "msg": "..."}]`
— the submitted value is never echoed.

Common codes: `400 bad_request`, `401 unauthorized | invalid_credentials | invalid_table_access |
token_expired`, `403 forbidden | password_change_required`, `404 not_found`, `409 conflict |
version_conflict`, `413 payload_too_large`, `422 validation_error | idempotency_mismatch`,
`429 rate_limited` (+ `Retry-After`), `500 internal_error`.

Legend: 🔓 public · 🍽 customer session cookie · 🔑 staff bearer token (+ permission)

---

## 1. Auth (Phase 3 ✅)

| Method & path | Auth | Body → Response |
|---|---|---|
| `POST /auth/login` | 🔓 rate-limited | `{restaurant_code, username, password}` → `200 TokenPair` |
| `POST /auth/refresh` | 🔓 rate-limited | `{refresh_token}` → `200 TokenPair` |
| `POST /auth/logout` | 🔑 any | → `204` (revokes current session) |
| `POST /auth/logout-all` | 🔑 any | → `204` (revokes all own sessions) |
| `GET /auth/me` | 🔑 any (allowed while `must_change_password`) | → `200 Me` |
| `POST /auth/change-password` | 🔑 any (allowed while `must_change_password`) | `{current_password, new_password}` → `204` |
| `POST /auth/password-reset/request` | 🔓 rate-limited | `{restaurant_code, email}` → `202` always |
| `POST /auth/password-reset/confirm` | 🔓 rate-limited | `{token, new_password}` → `204` |

```jsonc
// TokenPair
{ "access_token": "eyJ...", "token_type": "bearer", "expires_in": 900,
  "refresh_token": "opaque", "user": Me }
// Me
{ "id": "…", "restaurant_id": "…", "restaurant_name": "Spice Garden", "restaurant_code": "spice-garden",
  "username": "ramesh", "display_name": "Ramesh", "role": "waiter",
  "must_change_password": false, "permissions": ["orders.read", "..."] }
```
`permissions` is for UI convenience only; the server re-checks every call.

## 2. Restaurant & settings (Phase 4 ✅)

| | | |
|---|---|---|
| `POST /restaurants` | 🔓 rate-limited; gated by `REGISTRATION_MODE` (`closed` \| `invite` \| `open`) | `{restaurant_name, restaurant_code, owner_display_name, owner_username, owner_email?, password, invite_code?}` → `201 TokenPair` |
| `GET /restaurant` | 🔑 restaurant.settings.read | → settings |
| `PATCH /restaurant` | 🔑 restaurant.settings.write | `{name?, timezone?, table_session_ttl_minutes?, waiter_can_cancel_preparing?, kitchen_delay_minutes?}` |

## 3. Staff (Phase 4 ✅)

| | | |
|---|---|---|
| `GET /staff` | 🔑 staff.read | → list |
| `POST /staff` | 🔑 staff.manage (+ staff.manage_admins for role=admin) | `{username, display_name, role: admin\|waiter\|kitchen, email?, temporary_password}` → `201` (`must_change_password = true`) |
| `PATCH /staff/{id}` | 🔑 same | `{display_name?, role?, email? (null clears), is_active?}`; never your own account or the owner; other tenants' ids ⇒ `404` |
| `POST /staff/{id}/reset-password` | 🔑 same | `{temporary_password}` → `204` (revokes sessions, forces change) |
| `DELETE /staff/{id}` | 🔑 same | soft delete → `204` |

## 4. Tables, QR, PIN, sessions (Phase 4 ✅)

| | | |
|---|---|---|
| `GET /tables` | 🔑 tables.read | → `[{id, label, is_active, sort_order, qr_url, active_sessions, open_orders}]` |
| `POST /tables` | 🔑 tables.manage | `{label, sort_order?}` → `201`; duplicate label ⇒ `409 table_label_taken` |
| `PATCH /tables/{id}` | 🔑 tables.manage | `{label?, is_active?, sort_order?}` |
| `DELETE /tables/{id}` | 🔑 tables.manage | soft delete (history kept) |
| `POST /tables/{id}/qr/regenerate` | 🔑 tables.manage | → new `qr_url` (old QR stops working) |
| `GET /tables/{id}/qr.svg` | 🔑 tables.read | printable SVG |
| `POST /tables/{id}/close` | 🔑 tables.close | → `{sessions_ended, orders_completed}`; ends the table's customer sessions and completes its served orders |
| `GET /pin` | 🔑 pin.view | → `{pin, version, pin_length, rotated_at, next_rotation_at, rotation_minutes}` (`Cache-Control: no-store`) |
| `POST /pin/rotate` | 🔑 pin.manage | `{invalidate_active_sessions?: bool}` → PIN fields + `sessions_invalidated` |
| `PUT /pin/settings` | 🔑 pin.manage | `{rotation_minutes: 120\|240\|360\|null, pin_length: 4\|6}` → PIN fields; changing the length rotates immediately |
| `POST /table-sessions/invalidate-all` | 🔑 table_sessions.invalidate_all | → `{invalidated: n}` |

## 5. Menu (Phase 5 ✅)

Prices (`display_price_paise`, `display_price_delta_paise`) are **informational menu text only**.

| | | |
|---|---|---|
| `GET /menu` | 🔑 menu.read | full tree: `{categories:[{…, items:[…]}], modifier_groups:[{…, modifiers:[…]}]}` (incl. inactive, excl. deleted) |
| `GET /menu/ordering` | 🔑 menu.read | what can be ordered now — same shape as the customer menu, with `orderable` flags (waiter app) |
| `POST /menu/categories` · `PATCH /menu/categories/{id}` | 🔑 menu.manage | `{name, description?, sort_order?, is_active?}`; duplicate name ⇒ `409 category_name_taken` |
| `DELETE /menu/categories/{id}` | 🔑 menu.manage | soft delete; ⇒ `409 category_not_empty` while it has dishes |
| `POST /menu/items` · `PATCH /menu/items/{id}` | 🔑 menu.manage | `{category_id, name, description?, dietary?: veg\|non_veg\|egg, display_price_paise?, sort_order?, is_active?, is_available?}`; foreign `category_id` ⇒ `422 unknown_category` |
| `DELETE /menu/items/{id}` | 🔑 menu.manage | soft delete (past orders keep it) |
| `PUT /menu/items/{id}/modifier-groups` | 🔑 menu.manage | `{group_ids:[…]}` ordered, ≤ 20, no duplicates; foreign ids ⇒ `422 unknown_modifier_group` |
| `PATCH /menu/items/{id}/availability` | 🔑 menu.availability (owner/admin/kitchen) | `{is_available}` — "sold out" |
| `POST /menu/items/{id}/image` | 🔑 menu.manage | multipart field `image` (JPG/PNG/WebP, ≤ 5 MB) → re-encoded WebP; `422 invalid_image` / `413` |
| `DELETE /menu/items/{id}/image` | 🔑 menu.manage | removes the photo |
| `POST /menu/modifier-groups` · `PATCH /menu/modifier-groups/{id}` | 🔑 menu.manage | `{name, min_select 0–20, max_select 1–20, sort_order?}`, min ≤ max |
| `DELETE /menu/modifier-groups/{id}` | 🔑 menu.manage | soft delete; detached from all dishes |
| `POST /menu/modifier-groups/{id}/modifiers` · `PATCH /menu/modifiers/{id}` | 🔑 menu.manage | `{name, display_price_delta_paise?, sort_order?, is_available?}`; duplicate name in group ⇒ `409` |
| `PATCH /menu/modifiers/{id}/availability` | 🔑 menu.availability | `{is_available}` |
| `GET /media/items/{restaurant}/{file}.webp` | 🔓 | menu photos (`_sm` = 400 px thumbnail), immutable cache headers |

## 6. Customer (public) API (✅ Phases 4–6)

| | | |
|---|---|---|
| `GET /public/tables/{qr_token}` | 🔓 | → `{restaurant_name, table_label, pin_length, has_session}`; invalid token ⇒ `404 not_found` |
| `POST /public/tables/{qr_token}/session` | 🔓 PIN rate limits | `{pin}` → `200 {table_label, expires_at}` + `Set-Cookie: __Host-ts=…`; any failure ⇒ `401 invalid_table_access` |
| `GET /public/session` | 🍽 | → `{restaurant_name, table_label, expires_at}` |
| `DELETE /public/session` | 🍽 | leave table → `204` |
| `GET /public/menu` | 🍽 (✅ Phase 5) | → `{restaurant_name, table_label, categories:[{items:[{…, is_available, orderable, modifier_group_ids}]}], modifier_groups:[{options}]}`; `ETag` + `If-None-Match` ⇒ `304` |
| `POST /public/orders` | 🍽 + `Idempotency-Key` header (8–64 `[A-Za-z0-9_-]`) | `{items:[{menu_item_id, quantity 1–50, modifier_ids[], notes? ≤200}] (1–30 lines, ≤100 dishes), notes? ≤500}` → `201 Order`; same key + same body ⇒ `200` + the original order; same key + different body ⇒ `422 idempotency_mismatch` |
| `GET /public/orders` | 🍽 | → this session's orders, newest first; `ETag`/`304` for polling |
| `POST /public/orders/{id}/cancel` | 🍽 | `{reason?}` → `placed` ⇒ cancelled; `accepted` ⇒ cancellation requested; later ⇒ `409 order_too_late`; another session's order ⇒ `404` |

```jsonc
// Order (customer view) — no prices, totals or amounts, by design
{ "id": "…", "order_number": 12, "status": "placed", "version": 1, "created_at": "…",
  "notes": "Birthday table", "cancel_requested": false,
  "items": [ { "id": "…", "name": "Paneer Tikka", "quantity": 2, "notes": "less oil",
               "modifiers": ["Spice level: Spicy", "Add-ons: Extra Cheese"] } ] }
```

Order errors carry context the page can act on: `409 item_unavailable` / `option_unavailable` (sold out; includes `menu_item_id`), `422 item_not_on_menu`, `invalid_option`, `invalid_choice` (selection rules), `401 table_session_required`, `429 rate_limited`.

## 7. Orders (staff) (✅ Phase 7)

| | | |
|---|---|---|
| `GET /orders?status=…&status=…&table_id=&limit=&before=` | 🔑 orders.read | newest first, ≤ 200; repeated `status` filters |
| `GET /orders/{id}` | 🔑 orders.read | full order |
| `POST /orders` | 🔑 orders.create + `Idempotency-Key` | `{table_id, items:[...], notes?}` — no PIN; idempotent per staff member; switched-off table ⇒ `422 table_unavailable` |
| `POST /orders/{id}/transition` | 🔑 per state machine | `{to_status, expected_version}` → `200 Order` / `409 version_conflict` |
| `POST /orders/{id}/cancel` | 🔑 orders.cancel / cancel_privileged | `{reason_code, reason?, expected_version}` |
| `POST /orders/{id}/cancellation-request` | 🔑 orders.cancel_request | `{reason}` |
| `POST /orders/{id}/cancellation-request/approve` · `/reject` | 🔑 orders.cancel_review | `{note?}` |

`reason_code ∈ customer_changed_mind | item_unavailable | duplicate | kitchen_issue | mistake | other`
(`mistake`: the order was entered wrongly — Phase 11, so the pilot report can count order mistakes).

Staff order view = customer order view + `table_id, table_label, source (customer\|waiter), placed_by, updated_at, ready_at, cancel_request_reason, cancel_requested_by, cancel_reason_code, cancel_reason`.
Errors: `409 invalid_transition` (not allowed from the current status), `409 version_conflict` (includes `current_version`, `status`), `403 forbidden` (wrong role for this step), `403 cancel_requires_approval` (request a cancellation instead), `409 no_cancellation_request`.

## 8. Kitchen & real-time (✅ Phase 8, cancellations added in Phase 9)

`GET /kitchen/board` (🔑 orders.read) → every order the kitchen still has to act on
(`placed`, `accepted`, `preparing`, `ready`), oldest first, capped at 500:
```jsonc
{ "server_time": "2026-10-04T14:05:11Z",   // lets devices with a wrong clock time orders correctly
  "kitchen_delay_minutes": 15,              // "DELAYED" threshold (derived on the device)
  "truncated": false,                       // true only past 500 open orders
  "orders": [ StaffOrder ],                 // same shape as GET /orders/{id}
  "recently_cancelled": [                   // last 2 hours, newest first, at most 200
    { "id": "…", "version": 4, "cancelled_at": "2026-10-04T14:01:02Z" } ] }
```
`recently_cancelled` lets a tablet that was offline tell the cook to stop, and remember each
order's final version so a late, older event cannot bring it back. Ids and versions only.
Order lines now include `menu_item_id` (customer and staff views), so the device groups dishes by
menu item and chosen options. Aggregation ("Chilli Paneer ×4: T4 ×1, T7 ×2, T3 ×1") is computed
on the device from these orders — it keeps working from cached orders while offline and never
loses which order and table each portion belongs to.

WebSocket `GET /ws/staff` (any signed-in staff role with `orders.read`):
1. Client → `{"type":"auth","access_token":"…"}` within 5 s (`WS_AUTH_TIMEOUT_SECONDS`), else
   close `4401`. A token in the URL is ignored. Browsers must come from `CORS_ORIGINS`
   (otherwise refused: HTTP 403 at the handshake, or close `4403`); native apps send no `Origin`.
2. Server → `{"type":"ready","restaurant_id":"…","server_time":"…"}`; the socket is now
   subscribed to that restaurant only.
3. Server → event envelopes
   `{event_id, type, restaurant_id, order_id, order_version, occurred_at, payload}`:

   | type | payload | sent when |
   |---|---|---|
   | `order.created` | `{order: StaffOrder}` | customer or waiter places an order |
   | `order.updated` | `{order: StaffOrder}` | any status change, cancellation, cancellation request or review |
   | `menu.availability_changed` | `{menu_item_id \| modifier_id, is_available}` | sold out / back |
   | `table.closed` | `{table_id}` | a table is closed |
   | `pin.rotated` | `{version}` — **never the PIN** | manual or scheduled rotation |
   | `pin.bruteforce_suspected` | `{table_id}` | once per window when wrong-PIN attempts hit the restaurant limit |

   Events are published only after the change commits; nothing is sent for failed requests.
4. Client → `{"type":"ping"}` (→ `{"type":"pong"}`) or `{"type":"auth","access_token":"…"}`
   with a renewed token (→ `{"type":"reauthenticated"}`; a different user ⇒ `4401`). Anything
   else is ignored; more than 30 messages in 10 s ⇒ `4429`; a message over 8 KB ⇒ `1009`.
5. Every 60 s (`WS_RECHECK_SECONDS`) the server re-checks the session: logout, deactivation, a
   suspended restaurant or an expired, un-renewed access token ⇒ `4401`.
6. More than 5 open sockets per user (`WS_MAX_CONNECTIONS_PER_USER`), or more than 300
   connection attempts from one IP in 5 minutes ⇒ `4429`. Binary frames ⇒ `1003` (text only).
7. A socket that falls behind (256 queued events) or loses events in a broker reconnect is closed
   with `4408`: reconnect and reload the board.

Close codes: `4401` re-authenticate (refresh the token, reconnect) · `4403` don't retry ·
`4408` reconnect now and resync · `4429` back off · `1003`/`1009` the client sent something
the protocol does not allow (fix the client).

## 9. Problem reports (✅ Phase 11)

| | | |
|---|---|---|
| `POST /problem-reports` | 🔑 problem_reports.create (every role) | `{category, note?, order_id?}` → `201` report. `category ∈ order_mistake, customer_help, app_confusing, network, slow, other`; `note` ≤ 500 plain text; `order_id` must be an order of the same restaurant (`404` otherwise). 20 per person per hour (`429`). |
| `GET /problem-reports?limit=` | 🔑 problem_reports.read (owner, admin) | newest first, ≤ 200: `{id, category, note, order_id, order_number, table_label, reported_by, reported_by_role, created_at}` |

## 10. Staff password reset page (✅ Phase 11)

`PASSWORD_RESET_URL` points at `https://<domain>/reset-password`, a page of the customer web app
(same origin as the API). The emailed link is `…/reset-password#token=<token>`: the token stays
in the fragment (never sent to a server), the page removes it from the address bar at once and
posts it to `POST /auth/password-reset/confirm` with the new password. The staff app's sign-in
screen has *Forgot password?* (`POST /auth/password-reset/request`; same answer whether or not
the email exists).
