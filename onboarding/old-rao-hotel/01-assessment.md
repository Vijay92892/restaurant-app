# 01: Repository assessment for the Old Rao Hotel pilot

Checked on 2026-10-15 against the code on branch `dev`, the docs (00–08), the operator CLI
(`backend/app/cli.py`), the setup format (`backend/app/services/setup_service.py`) and the live
site. Nothing below needs an architecture change.

## Live system today

| | State (verified) |
|---|---|
| Site | `https://order.housecareforyou.in`; `check_deployment.py` **29/29 passed** (2026-10-15) |
| Server | One Azure VM (2 vCPU, 1 GiB RAM + swap), Docker Compose: Caddy, API (1 worker), PostgreSQL 16, Redis |
| Version on the server | `09d33d7` (= tag `v1.0-live`). **`v1.1-live` (operator `reset-password`) is released on `main` but not yet installed on the server**; install it before onboarding (04, step 0) |
| Tenants | Only the test restaurant `test-dhaba` |
| Backups | Nightly at 03:00 IST (`backup.sh`, 14 days on the server); not yet copied off the server |
| Monitoring | UptimeRobot on `/healthz` |
| Email | Not configured: *Forgot password?* emails are not sent. The operator `reset-password` covers this once v1.1 is installed |
| Tests | Backend 291 passing (2026-10-12, dev); Flutter and browser (3 engines) suites exist (docs/06) |

## Ready for this pilot (no code change)

| Need | What exists | Where |
|---|---|---|
| Tenant isolation | Row-level security on every table, composite foreign keys, runtime DB role without bypass; every route attacked automatically | `tests/test_schema_rls.py`, `tests/security/` |
| QR per table | One permanent QR link per table, printable cards (`qr-sheet`), replaceable per table (`regenerate-qr`) | `table_service.py`, CLI |
| Rotating PIN | One PIN for the whole restaurant, 4 or 6 digits, rotating every 2, 4 or 6 hours or only by hand; managers see it in the app; brute-force caps per IP, per table and per restaurant | `pin_service.py`, `schemas/pin.py` |
| Temporary table sessions | A cookie bound to one table; ends on table close or when invalidated | `test_pin_and_sessions.py` |
| Customer menu | Category chips, dishes with price, required/optional choices with price differences (`+₹40`), "Sold out" / "Unavailable" marks, cart kept across reloads | `customer-web/src/app.ts`, `options.ts`, `cart.ts` |
| Customer ordering and status | Order with a retry-safe key, status shown and refreshed every 10 s, customer cancellation while still allowed | `test_customer_orders.py` |
| Waiter ordering | Waiter app orders for any table without a PIN | `test_staff_orders.py` |
| Kitchen | Live board (WebSocket), dishes summed across tables with each order still traceable, chime, kept on the tablet when offline, cancellation notices | `test_realtime.py`, `staff_app/test/kitchen_*` |
| Sold out | Kitchen and managers switch dishes/options off during service; the server refuses orders for them | `test_menu.py`, `test_customer_orders.py` |
| Cancellations | Rules by role and stage; waiters request, managers decide; reasons incl. "entered by mistake"; audit log | `test_staff_orders.py` |
| Duplicate prevention | Idempotency keys for customers and staff; simultaneous retries create one order | `tests/security/test_races.py` |
| Menu setup | `apply-setup`: one JSON file, dry run, matched by name, never deletes or renames, re-runnable | `setup_service.py` |
| Pilot observation | Problem reports from staff, usage counts, `pilot-report` | docs/08 |
| No payments | Prices are display only, never totalled or billed | `customer-web/src/types.ts`, spec §37 |

## Needs a change or a decision (nothing started)

| # | Gap | Why it matters here | Size | Do it? |
|---|---|---|---|---|
| 1 | **No logo or brand colour.** The ordering page shows the restaurant's name only (header and page title) | "Old Rao Hotel branding" | Small to medium: restaurant fields + migration + customer page + setup option | Only with written permission to use the name and logo (03, part B) |
| 2 | ~~No search on the customer menu~~ **Built (2026-10-15, release `v1.2-live`):** a search box above the section chips; finds dishes by any part of the name, description or section, forgiving everyday spellings ("panner", "matar", "chili") | 162 dishes in 22 sections is a long scroll on a phone | Done: `customer-web/src/search.ts`, 7 unit tests, a browser test in 3 engines | Done |
| 3 | **One kitchen board for everything** (no stations) | A large highway dhaba usually has separate counters: tandoor, Chinese, South Indian, drinks/sweets | Medium to large (section → station mapping, board filter, tests) | Decide after the owner answers 03, part F |
| 4 | **Per-address limits if guests share the restaurant's Wi-Fi.** Per IP: 30 PIN attempts and 5 wrong PINs per 15 min, 60 order attempts per 10 min, 120 QR look-ups per 5 min | Many guests behind one Wi-Fi router look like one address; at a rush they could be blocked | Configuration-level change, but only after measuring | Ask how guests get online (03, part I); measure in the soft start |
| 5 | **Production capacity not measured.** Load test: one worker served about 300 tablets on a laptop; the live VM has 1 GiB and one worker | One restaurant is far below that, but it was never measured on the VM | The load test (`scripts/ws_load_test.py`) starts its own server and registers its own restaurants, so it cannot be pointed at the live site as it is. Either adapt it (small) or rehearse by hand with the test restaurant: a tablet and several phones at a quiet hour | Rehearsal before the pilot |
| 6 | **Customer page in English only** | Many highway guests may prefer Hindi | Medium: translations for the page's own text (dish names stay as entered) | Ask the owner (03, part I) |
| 7 | **No dish photos at start.** The API accepts them, but the staff app has no upload screen | Not required for ordering | Small to medium | Only if the owner supplies authorised photos |
| 8 | **Takeaway / counter orders are not supported** (every order belongs to a table) | Highway dhabas often sell at the counter | Out of scope for this pilot | Ask, but keep the pilot to table service |
| 9 | **Menu edits go through the operator** (setup file), not the app | Price changes need us | None for the pilot (sold-out switches are in the app) | Revisit after the pilot (docs/08 §8) |

## Not in scope, by design

Payments, bills, GST invoices, totals and payment gateways (spec §37): the restaurant keeps its
existing billing. The pilot changes only how orders reach the kitchen.
