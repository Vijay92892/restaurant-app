# 06 — Security Test Report (Phase 10)

Adversarial test campaign over the whole system, required by spec §48 Phase 10. Every earlier
phase shipped with its own security tests (docs/05); this phase attacks the system *as a
whole* — every route, every role, every input — so that nothing depends on someone having
remembered to write a test.

**Result:** 7 issues found, all fixed and covered by regression tests. No cross-tenant or
cross-table access, role escalation, SQL injection, XSS or duplicate-order path was found.

| | |
|---|---|
| Backend tests | 270 passing (36 new in `backend/tests/security/`) |
| Customer web | 16 browser tests passing in Chrome, WebKit (Safari's engine) and Firefox, over HTTPS |
| Staff app | 84 Flutter tests passing (unchanged in this phase) |
| Dependencies | no known vulnerabilities (pip-audit: 56 Python packages; npm audit; OSV: 122 Dart packages) |
| Load | 600 live kitchen sockets on one API worker: no lost, duplicated or cross-restaurant events |

---

## 1. Findings

| # | Severity | Finding | Fix | Regression test |
|---|---|---|---|---|
| 1 | **High** | **Failure budgets could be bypassed by simultaneous requests.** Budgets were checked first and counted after the slow part (database lookups, Argon2), so a burst all passed the check. Measured before the fix: 14 PIN guesses from one IP (budget 5), 30 password guesses on one account (budget 5), 15 wrong current passwords on change-password (budget 5), 25 orders from one table session (cap 10). | Budgets are now **taken atomically before** the attempt and refunded when it succeeds (`Budget` in `app/core/rate_limit.py`). The check-only API was removed so the pattern cannot return. | `tests/security/test_races.py` (5 bursts + the primitive on both limiter backends) |
| 2 | Medium | Simultaneous retries of the same order made the losing requests fail with `500` (the order itself was created exactly once). A rollback expired the signed-in user's data, which was then read again. | The insert runs in a savepoint, so only it is undone; the staff context keeps plain copies of its ids. Retries now get the original order back. | `test_simultaneous_retries_of_one_order_create_exactly_one` |
| 3 | Low | Simultaneous "new QR code" requests for one table failed with `500` (the database still allowed only one live code). | The table row is locked while its code is replaced. | `test_simultaneous_pin_rotations_leave_exactly_one_current_pin` |
| 4 | Low | A NUL character in the QR code part of the PIN-entry URL reached PostgreSQL and caused a `500`. | The code's shape is checked first (it counts as a failed attempt); and as a safety net, any input PostgreSQL rejects as data (SQLSTATE class 22) is answered `400`. | `test_hostile_query_strings_and_path_segments_never_break_anything`, `test_input_the_database_refuses_is_a_400_not_a_500` |
| 5 | Low | A binary WebSocket frame crashed the connection handler (no clean close, an error logged per frame — possible before authenticating). Deeply nested JSON could do the same. | Binary frames are closed with `1003`; over-deep JSON is treated as invalid. | `tests/security/test_websocket_abuse.py` |
| 6 | Low | No limit on WebSocket connection attempts per address: one IP could keep thousands of unauthenticated sockets open for the 5 s login window. | 300 attempts per IP per 5 minutes (a restaurant's tablets share one IP and reconnect after Wi-Fi drops), then `4429`. | `test_one_address_cannot_hold_endless_sockets` |
| 7 | Info | Safari/WebKit does not store the `Secure` session cookie over plain `http://localhost`, so the customer page could not be tested there. Production is unaffected (HTTPS is enforced at startup). | The end-to-end tests now run over HTTPS with a throwaway certificate, in all three engines. | `customer-web/e2e` (WebKit project) |

Configuration advice from the load test, now in the README: run uvicorn with
`--ws-max-size 65536`. Its default (16 MiB) lets a client make the server buffer a large frame
before the application's 8 KB check applies.

---

## 2. Results by attack (spec §48 Phase 10)

| Attack | How it was tested | Result |
|---|---|---|
| **Cross-tenant access** | A second restaurant's owner and admin call all 27 routes that take an id with the first restaurant's real ids (valid bodies, so the handler's own checks run); a digest of every row the first restaurant owns is compared before and after. Ids from another restaurant are also placed inside bodies (table, dish, option, category, option group). | Always `404`, nothing changed (`test_another_restaurants_ids_are_never_found_and_nothing_changes`, `test_ids_from_another_restaurant_inside_a_body_are_refused`). The database enforces this independently (RLS, composite foreign keys: docs/02). |
| **Cross-table access** | A diner at table T2 tries to read and cancel an order placed at T1. | `404`, order unchanged; sessions are bound to one table (`test_a_diner_cannot_touch_another_tables_orders`, Phase 4 tests). |
| **PIN brute force** | Sequential guesses per IP, per table across IPs, per restaurant (Phase 4); now also simultaneous bursts. | Budgets hold under bursts after fix #1. |
| **Old PIN** | Guessing with the PIN before a rotation. | Refused; existing sessions survive unless invalidated (Phase 4 tests). |
| **Session replay** | Access token after logout, logout-all, password change, deactivation and role change; reused rotated refresh tokens; reset links used twice; idempotency keys reused with another body; diner cookies after leaving, table close, invalidate-all, table disabled, expiry. | All refused (Phase 3–6 tests; ended diner sessions now swept on every customer route). |
| **IDOR** | As cross-tenant, plus random ids on every route for every role. | No route reveals or changes another restaurant's or table's data. |
| **API manipulation** | Every request model is checked to refuse unknown fields (no mass assignment of `restaurant_id`, `status`, prices, roles); quantities, option counts and shapes (Phase 6); malformed bodies on every write route: deep nesting, NaN, huge numbers, invalid UTF-8, wrong content types, XML entities, top-level arrays, 10,000 keys, oversized bodies. | Clean `4xx` envelope every time, no stack traces or database messages (`test_every_request_body_refuses_fields_it_does_not_define`, `test_malformed_bodies_get_a_clean_error_on_every_route`). |
| **Role escalation** | Every staff route × every role (46 × 4 = 184 checks), derived from the role → permission table; a user who must change their password is blocked from every other route; finer rules inside order transitions and cancellations (Phase 7). | Exactly the declared access (`test_each_role_reaches_exactly_what_its_permissions_allow`, `test_a_forced_password_change_blocks_everything_else`). |
| **Duplicate orders** | Simultaneous retries with one idempotency key (customer and waiter); simultaneous conflicting status changes; simultaneous creation of uniquely named things; simultaneous refresh-token use. | One order, one status change, one row, at most one usable refresh token (`tests/security/test_races.py`); 600-socket load test: 0 duplicates. |
| **WebSocket isolation** | Phase 8 tests plus 19 hostile first messages (forged, expired, wrong type/audience, other restaurant, `alg: none`, wrong key, odd JSON shapes, a binary frame), garbage after authentication, a diner's cookie instead of a staff token; load test checks every delivery's restaurant. | Never subscribed without a valid staff token; 0 cross-restaurant deliveries in 6,000. |
| **SQL injection** | 14 hostile strings (quotes, `DROP TABLE`, `pg_sleep`, NUL, CRLF, bidi controls, 10,000 characters) in every text field of every write route (500+ requests), every query parameter and every path segment; source scan for SQL built from strings. | Stored literally or rejected, never executed (`pg_sleep` never delayed a response); no string-built SQL in `app/` (ruff `S608` enforced). |
| **XSS** | Script, event-handler, `javascript:` and SVG payloads as restaurant name, table label, category, dish, description, option group, option and diner note, driven through the whole customer flow in three browser engines; staff app renders text widgets only (no HTML). | Shown as text, nothing parsed or run; strict CSP (`script-src 'self'`) as a second layer. |
| **Rate limiting** | Every anonymous route must declare its limit (enforced by the route inventory) and is hammered until `429`; ordering, polling and cancelling are limited per table session and IP; budget bursts as above. | All limited, `Retry-After` set (`test_every_anonymous_route_is_rate_limited`). |

### How the sweep stays complete

`backend/tests/security/surface.py` declares **every** route with who may call it (anonymous
with its rate limit, diner, any signed-in staff, or staff with specific permissions).
`test_every_route_is_declared_with_the_protection_it_really_has` compares that declaration
with the application — routes and the permissions their dependencies really demand — so a new
endpoint fails the suite until someone decides its protection, and is then attacked by every
sweep automatically.

---

## 3. Load test (live updates)

`backend/scripts/ws_load_test.py` registers restaurants over HTTP, opens 10 kitchen sockets per
restaurant (each authenticates in its first message and loads the board, like the app), places
orders in every restaurant at once and checks every delivery, then drops and reconnects every
socket at the same instant (worst case: real tablets add jittered backoff). One uvicorn worker,
in-process event bus, on a development laptop (Windows):

| | 300 sockets (30 restaurants) | 600 sockets (60 restaurants) |
|---|---|---|
| Orders / deliveries checked | 300 / 3,000 | 600 / 6,000 |
| Lost / duplicated / wrong restaurant | 0 / 0 / 0 | 0 / 0 / 0 |
| Order rate (all restaurants) | ~60 per second | ~120 per second |
| "Send" → on every tablet: median / p95 / p99 | 98 / 442 / 681 ms | 611 / 1,185 / 1,720 ms |
| All sockets connecting at once: median / max | 2.2 / 4.9 s | 5.7 / 8.5 s |
| Reconnect storm: all live again after | 5.9 s, 0 failures | 11.4 s, 0 failures |
| Server memory per socket | ~210 KB | ~185 KB |

Reading: one worker carries about 300 tablets with sub-second delivery at an order rate far
above a real dinner rush (a busy restaurant sends a few orders per minute); 600 is where one
worker saturates. Beyond that, add workers — which requires the Redis bus. The script runs that
configuration too (`--workers 4 --redis-url …`); it was not run here because no Redis server
was available on the test machine (the Redis bus's cross-worker routing is covered by
`test_redis_bus_fans_out_across_workers_per_tenant`). **Run it on the staging server before the
pilot** (open item).

In the 600-socket run one request hit a keep-alive connection the server had just closed and
was retried with the same idempotency key, as the app does — it created no second order.

---

## 4. Browsers

`customer-web/e2e` now runs every test in three engines, each emulating a phone, over HTTPS:
Chrome (Android), WebKit (Safari on iPhone) and Firefox. All 16 pass; the weak-4G timing test
(which uses Chrome's DevTools protocol) and screenshot capture run in Chrome only. WebKit on
Windows is the same engine as iPhone Safari but not the same product: a check on a real iPhone
remains on the pilot list.

---

## 5. Dependencies

| | Tool | Packages | Known vulnerabilities |
|---|---|---|---|
| Backend | pip-audit | 56 | none |
| Customer web | npm audit | (dev tooling only; the shipped bundle has no dependencies) | none |
| Staff app | OSV database, `pubspec.lock` | 122 | none |

Re-run before every release; a dependency update is a change like any other (tests, review).

---

## 6. Accepted risks (with reasons)

| Risk | Why it is accepted |
|---|---|
| A diner's session cookie works on any device it is copied to. | Spec §8: QR + PIN create a session that survives rotations; the cookie is `__Host-`, `Secure`, `HttpOnly`, `SameSite=Strict`, expires with the table session, and staff can end every session at once. |
| Every staff role can see a table's QR code. | It is printed on the table; it identifies a table and grants nothing without the current PIN (spec §13). |
| The kitchen board saved on a tablet is protected by the tablet's lock and Android storage encryption only. | Encrypting the file would not help: the signed-in app shows the same board. The protection that matters is a screen lock / managed devices — a pilot requirement. |
| Customer order status is polled every 10 s. | Cheap and robust on weak 4G; revisit with pilot data (open item). |

---

## 7. Deployment requirements found by this campaign

Already enforced at startup in production: strong distinct secrets, Redis for rate limits and
live updates, SMTP, HTTPS URLs, `PUBLIC_WEB_ORIGINS`, a runtime database role without
`BYPASSRLS`. In addition, the reverse proxy and server must:

* serve the customer page and `/api` from **one HTTPS origin** (the session cookie is first-party);
* send `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer` and `Content-Security-Policy: frame-ancestors 'none'` for the
  customer page (a `<meta>` CSP cannot forbid framing; the API sets its own headers);
* run uvicorn with `--ws-max-size 65536` and behind the proxy listed in `TRUSTED_PROXIES`;
* keep `--workers` > 1 only with `EVENT_BUS_BACKEND=redis`.

**Phase 11:** all of the above is implemented by [`deploy/`](../deploy) (Caddyfile, Dockerfile
flags, compose network and `TRUSTED_PROXIES`) and checked from outside by
`scripts/check_deployment.py` — 31/31 in the rehearsal (docs/07 §9). The load test was re-run
with **4 workers and a real Redis**: 3000/3000 deliveries, none missing, duplicated or crossing
restaurants (p95 577 ms).

---

## 8. Re-running

```bash
cd backend
pytest tests/security                     # the sweep (about 1 minute)
pytest                                    # everything
python scripts/ws_load_test.py --restaurants 30 --tablets 10 --orders 10
python scripts/ws_load_test.py --workers 4 --redis-url redis://<staging-redis>:6379/0

cd ../customer-web
npx playwright install webkit firefox     # once
npm run e2e                               # Chrome, WebKit and Firefox, over HTTPS
npm audit
```
