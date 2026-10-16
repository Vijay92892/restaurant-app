# 05 — Per-feature Security Reviews

One entry per shipped feature, in the format required by the spec (§50). A feature is not
"done" until its entry exists and every listed test passes.

---

## Phase 2 — Tenant-isolated schema

**Feature:** PostgreSQL schema, constraints, RLS, grants, resolver functions
(`alembic/versions/0001_initial_schema.py`).

- **Authentication required:** n/a (database layer).
- **Allowed roles:** runtime role `APP_DB_ROLE` with least-privilege grants; schema owner for
  migrations only.
- **Tenant scope:** every tenant table carries `restaurant_id`; RLS policy
  `restaurant_id = app_current_restaurant_id()` for reads *and* writes; unset ⇒ no rows.
- **User-controlled inputs:** none directly; all values arrive through validated API schemas.
- **Validation:** `CHECK` constraints on every enum, length, range and format (codes, usernames,
  token shapes, hash lengths, quantities 1–50, price ≥ 0, Argon2id hash prefix).
- **Authorization:** runtime role cannot `DELETE` history, cannot `UPDATE` order lines or logs,
  cannot execute resolvers unless granted, owns nothing, has no `BYPASSRLS`.
- **Rate limiting:** n/a.
- **Potential attack vectors:** application bug omitting a tenant filter; cross-tenant foreign keys;
  moving rows between tenants; tampering with history; misconfigured DB role silently bypassing RLS;
  tenant setting leaking across pooled connections.
- **Mitigations:** RLS fail-closed; composite `(id, restaurant_id)` FKs (enforced even for
  RLS-bypassing roles); immutable-column triggers; append-only grants; transaction-local tenant
  setting re-applied on every transaction (`after_begin`); API refuses to start in production if
  its DB role is superuser / `BYPASSRLS` / table owner; `bind_tenant` refuses to switch tenants.
- **Tests:** `tests/test_schema_rls.py` (14), `tests/test_model_drift.py` (1),
  `test_runtime_role_check_refuses_privileged_roles_in_production`.

---

## Phase 3 — Staff authentication

### Login — `POST /api/v1/auth/login`
- **Authentication required:** no.
- **Allowed roles:** any active staff user of an active restaurant.
- **Tenant scope:** tenant resolved from restaurant code via `app_resolve_restaurant_code`; user
  looked up under RLS in that tenant only.
- **User-controlled inputs:** `restaurant_code`, `username` (normalised, regex-validated),
  `password` (1–128 chars, never normalised).
- **Validation:** strict Pydantic schema, `extra="forbid"`, type-strict.
- **Authorization:** n/a (this *is* authentication).
- **Rate limiting:** 30 requests / 5 min per IP; 5 failures / 15 min per account; 20 failures /
  15 min per IP. Successful login resets the account counter.
- **Potential attack vectors:** credential stuffing, brute force, user/restaurant enumeration
  (response body or timing), SQL injection via fields, Argon2 memory-exhaustion DoS.
- **Mitigations:** identical `401 invalid_credentials` for every failure cause; dummy Argon2
  verification for unknown/inactive users; parameterised queries; Argon2id with bounded worker
  concurrency; failures audited (no secrets).
- **Tests:** `test_login_failures_are_indistinguishable` (×4),
  `test_inactive_user_and_suspended_restaurant_cannot_login`,
  `test_account_is_temporarily_locked_after_repeated_failures`, `test_login_is_rate_limited_per_ip`,
  `test_injection_strings_are_rejected_safely` (×6),
  `test_failed_logins_are_audited_without_secrets`,
  `test_passwords_are_argon2id_and_rehashed_when_parameters_change`.

### Access tokens & per-request authorization (`app/api/deps.py`)
- **Authentication required:** yes — `Authorization: Bearer <JWT>`.
- **Allowed roles:** per route via `require(Permission...)`.
- **Tenant scope:** tenant bound from the signed `rid` claim; user, session and restaurant
  re-loaded under RLS; role taken from the database, never from the token.
- **User-controlled inputs:** the token.
- **Validation:** HS256 pinned; `exp/iat/nbf/iss/aud/sub/rid/sid/jti/typ` required; `typ=access`.
- **Authorization:** session must be unrevoked and unexpired; user active and not deleted;
  restaurant active; `must_change_password` blocks all but `me`/`change-password`/`logout`.
- **Rate limiting:** n/a per request (token is short-lived; endpoints add their own).
- **Potential attack vectors:** `alg=none`, key confusion, forged/expired tokens, refresh token
  used as access token, stale role after demotion, token for tenant A used against tenant B.
- **Mitigations:** as above; deactivation/logout/role change effective on the next request.
- **Tests:** `test_malformed_forged_and_expired_access_tokens_are_rejected`,
  `test_validly_signed_token_cannot_cross_tenants`,
  `test_role_and_status_are_read_from_the_database_on_every_request`,
  `test_permissions_are_enforced_server_side` (×4), `test_api_reads_are_tenant_scoped`,
  `test_must_change_password_blocks_everything_else`.

### Refresh — `POST /api/v1/auth/refresh`
- **Authentication required:** refresh token.
- **Tenant scope:** tenant resolved from the token hash; rows locked `FOR UPDATE` under RLS.
- **User-controlled inputs:** `refresh_token` (`^[A-Za-z0-9_-]{20,128}$`).
- **Rate limiting:** 120 / 5 min per IP.
- **Potential attack vectors:** stolen refresh token replay; concurrent refresh races; lost
  responses on flaky kitchen Wi-Fi logging tablets out.
- **Mitigations:** rotation on every use; reuse of a rotated token revokes the whole session and
  is audited — except within 30 s when the replacement was never used (lost response), in which
  case the unused replacement is revoked and a new one issued; row locks serialise races.
- **Tests:** `test_refresh_rotates_tokens`, `test_reusing_a_rotated_refresh_token_revokes_the_session`,
  `test_refresh_replay_within_grace_recovers_a_lost_response`,
  `test_refresh_replay_after_child_was_used_is_treated_as_theft`,
  `test_refresh_fails_for_deactivated_user_and_expired_session`,
  `test_refresh_token_shape_is_validated`.

### Logout / logout-all
- **Authentication required:** yes (allowed during forced password change).
- **Mitigations:** revokes session(s) and all their refresh tokens; access tokens fail the
  per-request session check immediately.
- **Tests:** `test_logout_revokes_access_and_refresh_immediately`,
  `test_logout_all_revokes_every_session`.

### Change password — `POST /api/v1/auth/change-password`
- **Authentication required:** yes.
- **User-controlled inputs:** `current_password`, `new_password`.
- **Validation:** 8–128 chars, common-password blocklist, ≥ 4 distinct characters, must not
  contain the username, must differ from current.
- **Rate limiting:** 5 wrong current-password attempts / 15 min per user.
- **Potential attack vectors:** hijacked session changing the password; brute-forcing the current
  password through this endpoint.
- **Mitigations:** current password required; other sessions revoked; audited.
- **Tests:** `test_change_password_revokes_other_sessions`.

### Password reset — `POST /api/v1/auth/password-reset/{request,confirm}`
- **Authentication required:** no (reset token for confirm).
- **Tenant scope:** restaurant code + email (request); token hash resolver (confirm).
- **User-controlled inputs:** `restaurant_code`, `email`; `token`, `new_password`.
- **Rate limiting:** request 5/h per IP and 3/h per account; confirm 10/15 min per IP.
- **Potential attack vectors:** account enumeration; token guessing/reuse; token leakage via logs
  or `Referer`.
- **Mitigations:** always `202` with identical body; email sent in a background task; 256-bit
  token stored as SHA-256, single use, 1 h expiry, previous tokens deleted; token placed in the
  URL *fragment* (never sent to servers or in `Referer`); weak new password rejected *before*
  the token is consumed; all sessions revoked on success.
- **Known limitation:** response *timing* differs slightly when the account exists (a few DB
  writes). Mitigated by rate limits; to be revisited if it matters in practice.
- **Tests:** `test_password_reset_flow`, `test_expired_reset_token_is_rejected`,
  `test_password_reset_requests_are_rate_limited`.

### Cross-cutting HTTP hardening
- Uniform error envelope; validation errors never echo input; 500s generic
  (`test_unhandled_errors_do_not_leak_internals`, `test_unknown_fields_and_wrong_types_are_rejected_without_echo`).
- 1 MiB body cap for declared and streamed bodies (`test_oversized_bodies_are_rejected`).
- Security headers, `Cache-Control: no-store` (`test_security_headers_are_set`).
- `X-Request-ID` accepted only if well-formed (`test_inbound_request_id_is_validated`).
- `X-Forwarded-For` honoured only from configured proxies
  (`test_forwarded_for_is_only_trusted_from_configured_proxies`).
- Production refuses dev secrets, in-memory rate limiting, console mail, weak Argon2
  (`test_production_refuses_insecure_configuration`).
- Audit details refuse secret-looking keys (`test_audit_refuses_secret_looking_details`).

---

## Phase 4 — Restaurant setup

### Restaurant registration — `POST /api/v1/restaurants`
- **Authentication required:** no (gated by `REGISTRATION_MODE`).
- **Allowed roles:** n/a — creates a new tenant whose first user is its owner.
- **Tenant scope:** a brand-new tenant; the session is bound to it before any insert.
- **User-controlled inputs:** restaurant name/code, owner name/username/email, password, invite code.
- **Validation:** strict schema; names reject control/bidi characters and are whitespace-normalised;
  code/username regexes; password policy; unknown fields (e.g. `role`) rejected.
- **Authorization:** `closed` ⇒ 403; `invite` ⇒ invite code compared in constant time against
  every configured code; `open` ⇒ anyone (development default only).
- **Rate limiting:** 5 / hour per IP (counts failures too).
- **Potential attack vectors:** mass tenant creation, invite-code guessing, code squatting, name spoofing.
- **Mitigations:** as above; production requires 16+ character invite codes; restaurant, owner
  and first PIN created in one transaction; audited (`restaurant.created`, source `self_service`).
- **Tests:** `tests/test_registration.py` (5 tests, 8 cases).

### Restaurant settings — `PATCH /api/v1/restaurant`
- **Allowed roles:** owner, admin (`restaurant.settings.write`). **Tenant scope:** own restaurant only (no id in the URL).
- **Inputs/validation:** name, IANA timezone (validated against tzdata), session TTL 30–720,
  delay 5–120; `code`/`status` are not editable; explicit nulls rejected.
- **Tests:** `test_restaurant_settings_update`.

### Staff management — `/api/v1/staff`
- **Authentication required:** yes. **Allowed roles:** owner, admin (`staff.read`/`staff.manage`);
  admin accounts only by the owner (`staff.manage_admins`).
- **Tenant scope:** every lookup filtered by tenant and under RLS; other tenants' ids ⇒ 404.
- **User-controlled inputs:** username, display name, role (`admin|waiter|kitchen` only), email,
  active flag, temporary password, path id (UUID).
- **Authorization / escalation guards:** no `owner` role assignable; admins cannot create, edit,
  reset or delete admins; nobody can act on the owner or on themselves through these endpoints.
- **Potential attack vectors:** privilege escalation, IDOR across tenants, locking out the owner,
  lingering sessions after deactivation, duplicate-username races.
- **Mitigations:** guards above; deactivation/reset/delete revoke the target's sessions
  immediately; new accounts and resets force a password change; soft delete keeps history;
  unique-index violations mapped to `409`; every change audited.
- **Tests:** `tests/test_staff.py` (7 tests, 9 cases).

### Tables & QR — `/api/v1/tables`
- **Allowed roles:** read: all staff; manage: owner/admin; close: owner/admin/waiter.
- **Tenant scope:** tenant-filtered + RLS; other tenants' table ids ⇒ 404 on every route.
- **User-controlled inputs:** label (1–20 chars, plain text), sort order, active flag, path id.
- **Potential attack vectors:** IDOR, label-based XSS, stale/leaked printed QR codes, table-count abuse.
- **Mitigations:** QR tokens are 128-bit random and contain no PIN or ids; regeneration revokes
  the old token instantly; disabling/deleting a table ends its customer sessions; soft delete keeps
  order history; labels stored as plain text (rendered escaped by clients; SVG has no
  script); 300-table cap per restaurant; changes audited.
- **Tests:** `tests/test_tables.py` (7 tests).

### Table PIN — `/api/v1/pin`, `/api/v1/table-sessions/invalidate-all`
- **Allowed roles:** view: owner/admin/waiter; rotate/settings/invalidate: owner/admin.
- **Tenant scope:** own restaurant only (no id in the URL).
- **User-controlled inputs:** `invalidate_active_sessions`, `rotation_minutes ∈ {120,240,360,null}`,
  `pin_length ∈ {4,6}`.
- **Potential attack vectors:** PIN disclosure (DB leak, logs, caches), old-PIN reuse, rotation
  races between workers, weak PINs.
- **Mitigations:** keyed HMAC (key outside DB) + AES-GCM bound to restaurant/version; plaintext
  never stored/logged; responses `no-store`; only the current version verifies; rotations
  serialise on the restaurant row lock; scheduler elects one leader per tick via an advisory lock
  and re-checks due-ness; weak/sequential/repeating PINs and the previous PIN are never issued.
- **Tests:** `tests/test_pin_and_sessions.py` (primitives, visibility, rotation, settings,
  scheduler).

### Customer table sessions — `/api/v1/public/tables/{qr}`, `/api/v1/public/session`
- **Authentication required:** QR token + current PIN to open; session cookie afterwards.
- **Tenant scope:** restaurant and table come from the resolver/session row only.
- **User-controlled inputs:** QR token (path), PIN, cookie, `Origin`.
- **Validation:** QR token shape; PIN length/digits checked against the restaurant's setting,
  failing exactly like a wrong PIN.
- **Rate limiting:** see 03-security §4.2 (per IP, per table+version, per restaurant+version).
- **Potential attack vectors:** remote ordering with a photographed QR, PIN brute force
  (single and distributed), old PIN reuse, existence oracles, session theft/replay, CSRF,
  cross-table/cross-restaurant cookie reuse, deliberate lockout.
- **Mitigations:** QR alone grants nothing; identical `401 invalid_table_access` for every failure;
  256-bit cookie token, only SHA-256 stored, `__Host-`/HttpOnly/Secure/SameSite=Strict; sessions
  fixed to one table; expiry, close-table, rotate-and-invalidate, invalidate-all; Origin check;
  brute-force alert audited; rotation lifts a deliberate lockout.
- **Residual risk:** the PIN is shared (see 03-security §4.2); a distributed attacker gets ≤ 60
  guesses per PIN version.
- **Tests:** `tests/test_pin_and_sessions.py` (sessions, uniform failures, per-IP, per-table,
  per-restaurant caps, binding/expiry, origin check).

---

## Phase 5 — Menu

### Menu management — `/api/v1/menu/*`
- **Authentication required:** yes.
- **Allowed roles:** read: all staff; manage (categories, dishes, option groups, options, photos,
  group links): owner/admin; sold-out toggles: owner/admin/kitchen.
- **Tenant scope:** every lookup filtered by tenant and under RLS. Ids inside request bodies
  (`category_id`, `group_ids`) are re-resolved inside the caller's restaurant; foreign ids ⇒
  `422 unknown_*`; foreign ids in the URL ⇒ `404` on every route (13 routes tested).
- **User-controlled inputs:** names (plain text, control/zero-width/bidi characters rejected),
  descriptions (multi-line plain text), dietary enum, informational prices (integers in range,
  strict types: `"249"` and `12.5` rejected), sort orders, selection rules (0 ≤ min ≤ max ≤ 20),
  availability flags, path ids.
- **Validation:** strict schemas, `extra="forbid"`; explicit nulls only for nullable fields;
  min/max re-checked against stored values on partial updates.
- **Rate limiting:** authenticated staff only; per-restaurant caps (100 categories, 1000 dishes,
  200 groups, 50 options per group) bound abuse.
- **Potential attack vectors:** IDOR, cross-tenant references via body ids, stored XSS in names/
  descriptions, role escalation (kitchen editing the menu), menu states customers cannot satisfy.
- **Mitigations:** as above; text stored verbatim as data and returned as JSON (the customer web
  renders with `textContent`); soft deletes keep order history; deleting a group detaches it;
  categories with dishes cannot be deleted; customer view marks a dish not orderable when a
  required choice has no available option; every change audited (`menu.*`).
- **Tests:** `tests/test_menu.py` (6 tests, 18 cases).

### Menu photos — `POST/DELETE /api/v1/menu/items/{id}/image`, `GET /media/...`
- **Allowed roles:** upload/remove: owner/admin. Serving: public (unguessable random names).
- **User-controlled inputs:** file bytes, filename, declared content type.
- **Validation:** extension ∈ {jpg, jpeg, png, webp} **and** declared type ∈ {image/jpeg,
  image/png, image/webp} **and** Pillow can decode it using *only* the JPEG/PNG/WebP decoders;
  32–10 000 px per side, ≤ 40 MP checked from the header *before* decoding; ≤ 5 MB (the 1 MiB
  body cap is raised for this one route only).
- **Potential attack vectors:** executable/HTML/SVG uploads, polyglots, decompression bombs,
  parser exploits in exotic formats, metadata leaks (GPS), path traversal, filename injection,
  content sniffing, disk exhaustion.
- **Mitigations:** re-encoding to WebP drops EXIF/GPS, ICC, comments and appended payloads;
  random 128-bit file names; strict key pattern + resolved-path containment for storage and
  serving; files outside the app code (`MEDIA_ROOT`); served as `image/webp` with `nosniff` and
  `default-src 'none'`; processing in worker threads (max 2 concurrent); old files deleted on
  replace/remove.
- **Tests:** `tests/test_menu_images.py` (12 rejection cases incl. SVG, disguised HTML/EXE/GIF,
  BMP, truncated, empty; GPS stripping; resize; bombs; polyglot; oversize; traversal).

### Customer menu — `GET /api/v1/public/menu`
- **Authentication required:** table session cookie.
- **Tenant scope:** restaurant taken from the session row; no request parameter can select another.
- **Exposure:** only active categories/dishes and options used by visible dishes; no staff-only
  fields (`is_active`, `sort_order`, ids of hidden entities).
- **Performance:** single compact JSON with groups referenced by id; `ETag`/`304`.
- **Tests:** `tests/test_public_menu.py` (6 tests).

---

## Phase 6 — Customer ordering

### Place order — `POST /api/v1/public/orders`
- **Authentication required:** table session cookie (QR + current PIN); `Idempotency-Key` header.
- **Allowed roles:** customer of that table session only.
- **Tenant scope:** restaurant and table taken from the session row. The body cannot name a
  restaurant, table, price or status (`extra="forbid"`); the database additionally enforces that a
  customer order's table equals its session's table (composite FK).
- **User-controlled inputs:** menu item ids, quantities, option ids, line notes, order notes,
  idempotency key.
- **Validation:** strict types (`"2"`, `2.5`, negatives rejected); 1–30 lines, 1–50 per line,
  ≤ 100 dishes per order; notes are plain text (≤ 200 / ≤ 500, control/invisible characters
  rejected). Every item and option is re-loaded inside the restaurant and must be live, in an
  active category, in stock, belong to *this* dish, and satisfy each group's min/max.
- **Authorization:** session must be active, unexpired, for an active table and restaurant.
- **Rate limiting:** 10 created orders / 10 min per session; 30 attempts / 10 min per session;
  60 attempts / 10 min per IP.
- **Potential attack vectors:** remote ordering with a photographed QR, order duplication
  (double taps, retries, concurrent races), quantity/price/menu manipulation, cross-restaurant
  item ids, options from another dish, ordering sold-out dishes, spam, key reuse, stored XSS via notes.
- **Mitigations:** as above; one transaction per order; idempotency per (session, key) enforced
  by a unique index, with the request fingerprint distinguishing a retry (original returned) from
  key reuse (`422`); names and display prices snapshotted from the database; orders carry no money
  fields at all; every order writes an `order.created` event for the kitchen.
- **Tests:** `tests/test_customer_orders.py` (31 cases incl. 5 concurrent identical requests ⇒
  exactly one order, 16 manipulation payloads, cross-restaurant ids).

### My orders / cancel — `GET /api/v1/public/orders`, `POST …/orders/{id}/cancel`
- **Scope:** only orders created by *this* session (another diner at the same table ⇒ `404`).
- **Cancellation rules:** `placed` ⇒ cancelled; `accepted` ⇒ request for staff; later ⇒ `409`.
  Every cancellation/request writes an order event and an audit record.
- **Tests:** `test_diners_see_only_their_own_sessions_orders`, `test_customer_cancellation_rules`,
  `test_order_list_supports_conditional_polling`.

### Customer web page — `customer-web/`
- **Data handling:** all server-provided text rendered with `textContent`/text nodes (a helper
  that refuses `on*` attributes); no `innerHTML` anywhere.
- **Credentials:** the session token lives only in the HttpOnly `__Host-` cookie; the page
  stores only the cart (sessionStorage, re-validated on load as untrusted) and a pending
  idempotency key so a retry after a dropped connection can never duplicate an order.
- **Headers:** production build carries a strict CSP (`default-src 'self'`, no inline script);
  `Referrer-Policy: no-referrer` keeps the QR token out of referrers.
- **Tests:** unit (`customer-web/tests`, 14) and real-browser Playwright tests in Chrome
  (`customer-web/e2e`, 5): full QR → PIN → order journey, double tap ⇒ one order, an HTML/JS
  dish name renders as text with no script execution, a photographed QR alone gets `401`,
  first screen in < 2.5 s on emulated weak 4G (measured ≈ 0.8 s locally).

### Customer menu search — `customer-web/src/search.ts` (added after go-live)
- **Surface:** none new. It filters the menu the page already holds; no request, no new
  endpoint, nothing stored. The query never leaves the phone.
- **Rendering:** results and the "No dishes match …" line go through the same text-only DOM
  helper, so a typed query or a dish name is never markup. Input capped at 60 characters.
- **Tests:** unit (`tests/search.test.ts`, 7: spelling, every word, sections, empty and
  punctuation-only queries, the menu left unchanged) and a browser test in Chrome, WebKit and
  Firefox (search, add from the results, no match, back to the whole menu).

### Customer page redesign and restaurant themes — `restaurants.theme/tagline` (migration 0006)
- **Surface:** no new endpoint. The public table landing response gains `theme` and `tagline`,
  public like the restaurant's name (shown before the PIN). Set only through the operator's
  setup file (`branding`); every change is in the audit log (`restaurant.settings_changed`).
- **Inputs:** `theme` is one of the page's built-in names (setup validation; the database also
  checks its shape) and the page ignores any name it doesn't know. The server never sends
  colours, CSS or URLs: a theme only switches tokens already in the stylesheet. `tagline` is
  single-line plain text (control and invisible characters refused, ≤ 80), rendered as text.
- **CSP unchanged:** the heading font is served from the page's own origin (`font-src
  'self'`); icons and motifs are tiny SVG shapes inside the stylesheet (`data:`, already
  allowed); sticky-bar heights are written through the CSSOM, which the CSP permits.
- **Unchanged guarantees:** prices remain menu information (the order sheet shows no prices or
  totals; test), the server still decides availability, options and the order; sold-out
  dishes cannot be added on the page and are refused by the server (test).
- **Tests:** backend `test_branding_from_the_setup_file_reaches_the_guest_page` (dry run, apply,
  re-run, partial change, other restaurant untouched, unknown theme / key, overlong or
  multi-line tagline refused) and the landing contract; browser tests in Chrome, WebKit and
  Firefox for the section chips, sold-out refusal, order-sheet quantities and removal, and a
  theme applied to one restaurant only.

---

## Phase 7 — Staff order API and waiter app

### Waiter ordering — `POST /api/v1/orders`
- **Authentication required:** staff bearer token. **Allowed roles:** `orders.create` (waiter,
  admin, owner); kitchen ⇒ 403.
- **Tenant scope:** `table_id` is resolved inside the caller's restaurant (another restaurant's
  table ⇒ 404); switched-off/deleted tables refused. Menu validation is the same code path as
  customers (Phase 6).
- **Idempotency:** per (staff member, key) — a retry returns the original; another waiter's
  identical key is a separate order.
- **Tests:** `test_waiter_orders_for_a_table_without_a_pin`, `test_staff_idempotency_is_per_person`,
  `test_who_may_order_and_for_which_table`.

### Order state machine — `POST /api/v1/orders/{id}/transition`
- **Allowed roles:** accept/prepare/ready ⇒ `orders.kitchen_transition` (kitchen, admin, owner);
  served/completed ⇒ `orders.serve` (waiter, admin, owner). Checked per transition.
- **Validation:** only the edges in the state machine (docs/02 §3); anything else ⇒ 409.
- **Concurrency:** row lock + `expected_version`; a stale or racing client gets
  `409 version_conflict` with the current version — never a silent overwrite.
- **Audit:** every transition writes an order event and an `order.status_changed` audit record.
- **Potential attack vectors:** role escalation (waiter marking ready, kitchen marking served),
  skipping/rewinding states, lost updates between two tablets, cross-tenant order ids.
- **Tests:** `test_full_lifecycle_with_the_right_roles`, `test_illegal_jumps_are_refused` (×4),
  `test_stale_versions_and_races_never_clobber` (two simultaneous "ready" ⇒ one 200, one 409),
  `test_listing_filters_and_isolation`.

### Cancellations — `/cancel`, `/cancellation-request`, `/cancellation-request/{approve|reject}`
- **Policy:** waiter cancels `placed`/`accepted` (and `preparing` only if the restaurant enables
  `waiter_can_cancel_preparing`); admin/owner may cancel any non-terminal order; kitchen and
  waiters can *request* (does not stop the kitchen); only admin/owner approve or reject.
- **Audit:** `order.cancelled`, `order.cancel_requested`, `order.cancel_request_rejected`.
- **Tests:** `test_cancellation_policy_by_role_and_stage`, `test_cancellation_requests_need_a_manager`.

### Staff app (Flutter) — `staff_app/`
- **Credentials:** refresh token in the platform keystore (flutter_secure_storage), access token
  in memory only, no secrets in the app; only the API base URL is configured at build time.
- **Token handling:** concurrent 401s share one refresh (parallel refreshes would trip the
  server's reuse detection); a failed refresh clears local credentials and returns to sign-in.
- **Authorization:** the UI hides actions by permission for clarity only; the server re-checks
  everything (the live test confirms a waiter's kitchen transition is refused with 403).
- **Retries:** an order draft keeps its idempotency key across retries and gets a new one when
  changed; sold-out dishes reported by the server are removed from the draft.
- **Transport:** Android release builds allow HTTPS only; plain HTTP is enabled in debug builds
  for a local server.
- **Tests:** 23 unit/widget tests (`flutter test`), a live data-layer test against a real backend
  (`test/live_api_test.dart`), and rendered screenshots reviewed for layout and contrast.

## Phase 8 — Kitchen screen and live updates

### Staff WebSocket — `GET /api/v1/ws/staff`
- **Authentication required:** a staff access token in the **first message**, within 5 s. Tokens
  in the URL are ignored (they would end up in proxy logs). Refresh tokens, garbage and expired
  tokens ⇒ close `4401`; staff who must still change their password cannot subscribe.
- **Allowed roles:** every role with `orders.read` (all four). The events carry nothing a role
  cannot already read over REST: `pin.rotated` carries only the version number, never the PIN.
- **Tenant scope:** the restaurant comes from the verified token; the socket is subscribed to
  `tenant:{restaurant_id}:staff` only. There is no global channel, and the Redis listener drops
  any message whose payload names a different restaurant than its channel.
- **Validation / abuse limits:** the server never acts on client messages (only `ping` and
  re-auth); > 30 messages / 10 s ⇒ `4429`; > 8 KB ⇒ `1009`; ≤ 5 sockets per user ⇒ `4429`;
  bounded per-socket queue, overflow ⇒ `4408` (resync) instead of unbounded memory.
- **Revocation:** the session is re-checked every 60 s — logout, deactivation, a suspended
  restaurant or a role without `orders.read` closes the socket; so does an access token that
  expires without in-band renewal. In-band re-auth with another user's token ⇒ `4401`.
- **Cross-site WebSocket hijacking:** a browser `Origin` must be in `CORS_ORIGINS` (`4403`).
  Auth is by explicit token, not cookies, so a hostile page has no ambient credential anyway.
- **Consistency:** events are staged in the transaction and released only on commit — a
  rejected or rolled-back change (403, 409, validation error) publishes nothing. Staging opens
  the transaction if needed, so an event can never leak into a later, unrelated commit (found
  and fixed by `test_outbox_publishes_only_committed_work`).
- **Logging:** tokens are never logged; the access log shows only `WebSocket /api/v1/ws/staff`.
- **Potential attack vectors:** eavesdropping on another restaurant's orders, replaying a
  revoked token, flooding, slow-reader memory exhaustion, origin spoofing from a web page,
  learning the PIN from events.
- **Tests (21, `tests/test_realtime.py`):** `test_socket_requires_an_auth_message`,
  `test_token_in_the_url_is_not_accepted`, `test_users_who_must_change_password_cannot_subscribe`,
  `test_browser_origins_must_be_allowed`, `test_connections_per_user_are_capped`,
  `test_message_floods_are_cut_off`, `test_events_never_cross_restaurants`,
  `test_failed_changes_publish_nothing`, `test_menu_and_pin_events_carry_no_secrets`,
  `test_pin_guessing_alerts_staff`, `test_ping_and_reauthentication`,
  `test_logout_and_deactivation_close_open_sockets`, `test_an_expired_token_closes_the_socket`,
  `test_slow_consumers_are_told_to_resync`, plus bus/outbox unit tests including the Redis bus
  across two workers (`fakeredis`).

### Kitchen board — `GET /api/v1/kitchen/board`
- **Authentication required:** staff bearer token; `orders.read`. **Tenant scope:** RLS + explicit
  `restaurant_id` filter; another restaurant's board is empty, not an error.
- **Bounded:** at most 500 orders (`truncated` tells the screen). No money fields.
- **Tests:** `test_kitchen_board_lists_open_kitchen_work_oldest_first` (statuses, order, isolation,
  401 without a token, no price/total fields).

### Staff app — kitchen screen and live client
- **Same credential rules as Phase 7:** the socket uses the in-memory access token; nothing new is
  stored. The client stops the socket on sign-out and when the server says the session is over.
- **Stale-data safety:** an "Offline" indicator (icon + word) and a banner with the last sync time;
  versions decide which copy of an order wins; tombstones stop finished orders reappearing;
  a double tap on an action sends once; a `409` reloads instead of retrying.
- **Clock safety:** "delayed" and waiting times use the server clock (`server_time` / HTTP `Date`),
  so a tablet with a wrong clock cannot hide a late order.
- **Tests:** 35 new Flutter tests (58 in total; order merging, aggregation, realtime client with a scripted
  socket — including a heartbeat bug found and fixed —, kitchen cubit and screen, waiter live
  updates), a live test against the real backend over a real WebSocket, and reviewed
  screenshots (tablet light/dark/offline, phone).

## Phase 9 — Reliability

### Data retention job — `app/services/retention_service.py`, migration `0004`
- **Authentication required:** none from users — it is a background job inside the API (and
  `python -m app.cli purge` for operators, which needs the database credentials anyway).
- **Tenant scope:** restaurant by restaurant: ids come from `app_restaurant_ids()` (ids only),
  then every delete runs in that restaurant's tenant-bound transaction (RLS + explicit filter).
- **User-controlled inputs:** none. Settings are validated (`AUDIT_LOG_RETENTION_DAYS` ≥ 180).
- **Authorization:** the runtime role still has no `DELETE` on `audit_logs`; it can only call
  `app_purge_audit_logs`, which deletes the bound restaurant's rows older than 180 days at the
  newest, at most 10 000 per call, and is not executable by `PUBLIC`.
- **Potential attack vectors:** a compromised API erasing recent audit history; retention
  weakening refresh-token theft detection; deleting another restaurant's data; long-held locks.
- **Mitigations:** the 180-day floor lives in the database, and cutoffs use the database clock;
  only *expired* refresh tokens are deleted (an expired token is refused before the reuse check,
  so detection is unchanged) and rotated tokens of live sessions are kept; per-tenant
  transactions under RLS; batches of 5 000, each committed; one leader via advisory lock; every
  audit purge writes `audit_log.purged`. Orders are not deleted.
- **Tests (`tests/test_retention.py`):**
  `test_retention_deletes_what_expired_and_keeps_everything_else` (two restaurants, every table,
  idempotent second run), `test_one_restaurant_at_a_time_in_small_batches`,
  `test_only_one_worker_purges_at_a_time`,
  `test_audit_purge_cannot_touch_recent_history_or_other_restaurants` (no tenant, 30/179-day
  cutoffs, bad limits, another restaurant's rows, direct `DELETE` still refused),
  `test_retention_settings_cannot_shorten_audit_history_below_the_database_floor`; the new
  functions are in `test_resolver_functions_are_not_executable_by_public`.

### Kitchen board — recent cancellations
- **Authentication / roles / tenant scope:** unchanged (`orders.read`, RLS + filter).
- **Data:** id, version and time of orders cancelled in the last 2 hours (≤ 200) — no reasons,
  no people, nothing a kitchen user cannot already read.
- **Tests:** `test_kitchen_board_reports_recent_cancellations` (window, versions, isolation),
  plus the live test against a real server.

### Staff app — offline resilience
- **Sessions:** renewal now has three outcomes. Only a server rejection signs the user out; an
  unreachable server keeps the session (REST and WebSocket alike). Starting offline uses the
  profile saved at sign-in (keystore, deleted with the refresh token); the server unlocks nothing
  until a refresh succeeds, and a revoked session ends at first contact.
- **Saved kitchen board:** app-private file, no secrets, one restaurant, 12 h maximum age,
  atomic writes, unreadable files discarded, deleted on sign-out/session end with a guard
  against late writes; Android backup and device transfer disabled (`allowBackup=false`,
  `dataExtractionRules`).
- **Offline actions:** disabled, never queued; a mid-flight network failure reloads the board;
  `409` after a lost answer is reported as "already done"; a double tap still sends once.
- **Alerts:** new-order chime (one per rush, per-tablet mute), cancellation notice + alert tone,
  screen kept awake on the kitchen screen. Sounds are generated tones bundled with the app.
- **Error recovery:** release builds survive an unhandled error in one widget or background
  task (logged by type only — messages could contain order data).
- **Potential attack vectors:** a stolen tablet reading the saved board; a stale board leading
  to wrong actions; replayed offline actions; bypassing sign-out by starting offline; one
  restaurant's board shown to another on a shared device.
- **Mitigations:** as above; the saved board holds table labels, dishes and guest notes only,
  and relies on the device lock and Android storage encryption (see open items).
- **Tests (26 new Flutter tests, 84 in total):** API client (offline restart, network failure
  during renewal, profile cleared with the session), realtime client (starting offline never
  signs out — this test fails on the Phase 8 client), order book (forget, snapshot tombstones,
  save/restore round trip), file store (round trip, other restaurant, age and clock jumps,
  corrupt file, sign-out guard), kitchen cubit (restored board with paused buttons, saving,
  chime once per rush, mute, cancellation notices live and after reconnecting, screen awake,
  periodic re-check, lost connection mid-action, lost answer), kitchen screen (banner and
  disabled buttons, stop notice, sound switch), the live test against the real backend, and
  reviewed screenshots (`11-kitchen-restored-offline`, `12-kitchen-cancel-notice`).

## Phase 10 — Adversarial test campaign

Full report: [docs/06-security-test-report.md](06-security-test-report.md). Summary:

- **Method:** every route is declared with its protection (`tests/security/surface.py`) and the
  declaration is checked against the application; every declared route is then attacked
  automatically — without or with forged credentials, by every role, with other restaurants'
  ids in the path and in the body, with hostile text, malformed bodies, query strings and path
  segments, and at its rate limit. Simultaneous-request attacks, WebSocket abuse, XSS in three
  browser engines, a 600-socket load test and dependency audits complete it.
- **Findings (all fixed, each with a regression test):** failure budgets bypassable by
  simultaneous requests (High); 500s on simultaneous duplicate orders and QR regenerations; a NUL
  character in the PIN-entry URL; binary WebSocket frames; no per-IP WebSocket connection
  limit; WebKit untestable over plain http (test setup).
- **Tests:** 36 new backend tests in `tests/security/` (270 in total), cross-browser e2e (16
  passing in Chrome, WebKit and Firefox), load-test script.

## Phase 11 — Pilot readiness

The pilot needs the restaurant to run without the API docs page (switched off in production)
and without the developer at every step, and it needs to be measured. New since Phase 10:

### Manager tools in the staff app — cancellation decisions, sold-out switches, table PIN, staff
- **Authentication / roles:** existing endpoints and permissions only (`orders.cancel_review`,
  `menu.availability`, `pin.view`/`pin.manage`/`table_sessions.invalidate_all`,
  `staff.read`/`staff.manage`/`staff.manage_admins`). The app hides what a role cannot do and
  keeps people out of screens they cannot use (`mayOpen` in the router); the server decides.
- **Inputs:** the same request bodies as before (already covered by the Phase 10 sweep).
- **Temporary passwords** (new staff, resets) are generated on the device from `Random.secure`
  (three groups of four characters from a 31-letter alphabet, ≈ 59 bits), shown once in a dialog,
  never stored or logged; the server forces a change at first sign-in and a reset signs the
  person out everywhere. The PIN screen hides the PIN until asked (spec §35).
- **Potential attack vectors:** a waiter reaching manager actions; an admin creating admins;
  editing the owner or oneself; shoulder-surfing the PIN; a stale screen deciding on a request
  someone else already decided.
- **Mitigations:** server permissions and escalation guards (Phase 4); owner and own account
  read-only in the app; PIN masked by default; a refused decision (`409`/`404`) reloads the list.
- **Tests (Flutter, 18 new):** decisions first and on the order (approve/reject, waiters can't),
  direct cancellation records *Entered by mistake*, sold-out switching in words + icon, search,
  a change made on another device, a refused switch, PIN hidden until asked, full-screen PIN,
  rotation interval / change now / sign out every phone, waiters see but can't change, adding
  staff with a one-time password, reset and switch-off, never the owner or oneself, password
  alphabet, route access per role, *Forgot password?*. Screenshots 13–17 reviewed.

### Location-based access without the PIN — `POST /public/tables/{qr}/session/location`, `GET/PUT /restaurant/customer-access` (migration 0007)
- **Surface:** one anonymous endpoint (rate-limited: 30 requests and 10 failures per IP, 30
  failures per table per 15 min, one use per position report) and two staff endpoints. The
  landing response adds the policy flags and radius, never the restaurant's coordinates.
- **Authorization:** the policy is read and changed only with `restaurant.settings.write` (owner,
  admin); waiters and the kitchen get `403` (test). The setting is per restaurant, bound to the
  caller's tenant like every staff endpoint; changes are audited with the actor.
- **Inputs:** coordinates and accuracy are finite numbers in range (NaN refused), the time is
  timezone-aware ISO 8601; unknown fields refused. The policy must be whole (both coordinates or
  neither, not 0,0, a point to turn it on; radius 25–500 m), checked in the API and the database.
- **Decision:** server-side only (freshness, accuracy, haversine distance with half the accuracy
  as tolerance). A client boolean is never accepted. Refusals carry a reason code, never the
  distance; unknown QR codes fail exactly like the PIN path.
- **Sessions:** the existing session service (random token, hash at rest, bound to restaurant and
  table, same expiry and revocation), with `access_method` and no `pin_version` (database check).
  No guest position is stored or logged (test checks every column).
- **PIN fallback:** the PIN endpoint refuses (`403 access_method_off`) only while location access
  is on without a backup; switching location access off restores the PIN regardless.
- **Potential attack vectors and residual risk:** fabricated positions (accepted if inside: the
  check is not proof of presence; stated in docs/03 §4.5, 09 and in the staff app), replayed
  reports (single use + 2-minute freshness), floods (budgets), other tables or restaurants (the
  session row decides; tests), switching the option off mid-service (no new sessions; open ones
  continue and can be ended by staff).
- **Tests:** backend `tests/test_location_access.py` (15: haversine, the check, permissions,
  database rules, sessions without PIN, refusals, replay, the fabricated-position limitation,
  table and tenant isolation, PIN backup off, switching off, PIN rotation, expiry, floods); the
  route sweep (`tests/security/`); customer page unit tests (`tests/location.test.ts`) and
  browser tests in Chrome, WebKit and Firefox with emulated positions (`e2e/location.spec.ts`:
  inside, outside then PIN, permission denied, no PIN backup, never asks before the tap); staff
  app widget tests (manager sets the policy, validation, PIN-backup rules, a refused save, routes
  per role).

### Hide a dish from the menu — managers, in the sold-out screen (added after go-live)
- **What:** switching a dish off, a manager or the owner chooses *Sold out* (guests still see
  it, cannot order it) or *Hide from the menu* (`PATCH /menu/items/{id}` with `is_active`);
  tapping a dish offers all three states. The kitchen's switch still marks sold out only.
- **Authorization:** unchanged on the server: hiding needs `menu.manage` (owner, admin), sold
  out `menu.availability`. The app offers the choice only with `menu.manage`; kitchen accounts
  never load hidden dishes.
- **Order of changes:** a hidden dish brought back as sold out is marked sold out *before* it is
  shown, so it is never orderable in between. Hidden dishes are refused by the server for
  guests and waiters (as before).
- **Live updates:** hiding or showing a dish now publishes `menu.availability_changed`
  `{menu_item_id, is_active}` after commit, so other staff screens refresh; other edits to a
  dish publish nothing.
- **Tests:** backend `test_menu_and_pin_events_carry_no_secrets` (hide, show, rename sends
  nothing); Flutter: a manager's two-way choice, a hidden dish coming back sold out (order of
  calls), the kitchen gets no choice and no hidden dishes, `fromJson` with and without hidden
  dishes, dismissing the choice changes nothing.

### Problem reports — `POST/GET /api/v1/problem-reports`
- **Authentication required:** staff token. **Allowed roles:** every role files
  (`problem_reports.create`); owner/admin read (`problem_reports.read`).
- **Tenant scope:** restaurant from the token; the optional `order_id` is looked up under RLS
  (another restaurant's id ⇒ `404`, also tested in the body-IDOR sweep); composite foreign keys.
- **User-controlled inputs:** `category` (enum), `note` (≤ 500, control/invisible characters
  refused, whitespace normalised, stored and shown as plain text), `order_id` (UUID).
- **Rate limiting:** 20 per person per hour.
- **Potential attack vectors:** spam; stored XSS through the note; leaking other restaurants'
  orders through `order_id`; guest personal data in notes.
- **Mitigations:** as above; rendered with Flutter `Text` and, in the operator's Markdown report,
  joined onto one line; retention 365 days.
- **Tests (`tests/pilot/test_problem_reports.py`):** every role reports, managers read newest
  first, waiters/kitchen refused; validation (unknown category, too long, bidi override, bad id,
  smuggled `restaurant_id`); per-person limit; isolation between restaurants; a reporter who
  left still shows by name. Declared in `tests/security/surface.py`, so the whole Phase 10 sweep
  attacks both routes.

### Pilot usage counts — `app/core/usage.py`, `usage_counters`
- **Authentication:** none from users (counted inside existing requests).
- **Tenant scope:** keyed by the restaurant already resolved by the request; written per
  restaurant in a tenant-bound transaction.
- **Data:** counts per restaurant and local day — no IP, device, session or person (spec §40).
- **Potential attack vectors:** memory growth under a flood; write amplification on public
  endpoints; one restaurant's counts landing on another.
- **Mitigations:** counted in memory (bounded to 20 000 keys), written once a minute; only
  requests that already passed rate limits and resolved a restaurant count; IP-level PIN refusals
  happen before the restaurant is known and are not counted; failures to write are dropped and
  logged (counts are observations, not records).
- **Tests (`tests/pilot/test_usage.py`):** the customer journey counted for its restaurant only
  and added up across flushes; lock-outs; staff connections, dropped links (not normal closes or
  server restarts) and forced reloads; unknown restaurants; the memory bound.

### "Entered by mistake" cancellation reason — migration `0005`
- New `reason_code` `mistake` (database check constraint, schema `Literal`, app chips). Tested
  via the API and the app.

### Operator tools — `apply-setup`, `qr-sheet`, `regenerate-qr`, `pilot-report`
- **Authentication:** shell access to the server and the database credentials; changes go
  through the same services (validation, limits, audit log) as the owner's account, with a
  `cli-…` request id.
- **Inputs:** the setup file is validated with the API's own field types (names, descriptions,
  prices, roles; duplicates, `min` ≤ `max`, unknown option groups refused before any change).
  New staff get random temporary passwords printed once. Nothing is deleted or renamed.
- **Output:** QR cards escape every restaurant-supplied text; the pilot report contains no money
  figures and nothing about individual guests.
- **Tests (`tests/pilot/test_operator_tools.py`):** dry run, create, idempotent re-run, updates
  never deleting, refusal before change, file validation (incl. BOM), QR cards (live tables only,
  escaping), pilot report figures and one-line staff text, unknown restaurant, table lookup.

### Operator password reset — `reset-password` (added after go-live)
- **Why:** an owner who forgot the password and has no working reset email had no way back in
  (the app's resets never touch the owner account).
- **Authentication:** as the other operator tools (shell access on the server). Bound to one
  restaurant by code: accounts of other restaurants are not found (row-level security).
- **Effect:** same as a manager's reset: a random temporary password (about 59 bits) printed
  once and never stored in clear, `must_change_password` set, every session of the account
  revoked. It also lifts that account's 15-minute wrong-password lock. Audit log
  `staff.password_reset` with actor `system` and `{"by": "operator"}`.
- **Tests:** `test_operator_reset_lets_a_locked_out_owner_back_in` (lock lifted, old password
  refused, temporary password must be changed, old sessions refused, other restaurant
  untouched, unknown username, audit row).

### Staff password-reset page — `customer-web/src/reset.ts`
- Token read from the URL fragment, validated, removed from the address bar immediately, sent
  only to `POST /auth/password-reset/confirm` (single use, 1 h, hashed at rest — Phase 3).
  Rendered with the same text-only DOM helper and strict CSP as the ordering page.
- **Tests:** unit tests (token parsing, password checks) and an end-to-end test in Chrome,
  WebKit and Firefox (real link, mismatch warning, success, sign-in with the new password, link
  refused the second time, incomplete link).

### Deployment — `deploy/`, `scripts/check_deployment.py`
- One HTTPS origin; Caddy adds HSTS, `nosniff`, `no-referrer`, `frame-ancestors 'none'` to the
  page; uvicorn `--ws-max-size 65536 --no-proxy-headers --no-server-header`; `TRUSTED_PROXIES`
  = the compose network only; secrets generated once (mode 600), split by container; the API
  never has the schema owner's or the database administrator's password; Redis password-protected
  and not published; only ports 80/443 published; registration closed.
- **Tests:** the rehearsal in docs/07 §9 (checker 31/31, restore drill, 4-worker Redis load test).
  Not verified on the development PC: building the Docker images (no Docker there).

## Open items carried forward

| Item | Phase |
|---|---|
| First `docker compose up --build` on the real server, then `check_deployment.py` (docs/07 §3) | pilot |
| Check the customer page on a real iPhone (Safari) and a low-end Android phone (docs/08 §2) | pilot |
| Kitchen tablet: Android screen lock; chime volume and screen-awake on the actual tablet (docs/08 §2) | pilot |
| Customer status updates use 10 s polling; consider SSE if the pilot shows it is too slow | after pilot |
| How long to keep order history (kept indefinitely for now; owners decide at the pilot) | after pilot |
| Menu editing in the app (today: the operator's setup file) | after pilot |
| Re-run the dependency audits before every release | ongoing |
