# 04: Setup plan and acceptance tests

Nothing here has been run on the live server. Each step names who does it and what must be true
before the next step. **Steps 4–8 change production data, and run only after the owner's written
approval of the dry run (step 3).** Nothing is ever deleted: dishes are hidden, table cards replaced
and accounts switched off instead.

Placeholders: `<code>` is the restaurant code agreed in 03. Proposed: `old-rao-sidhrawali`
(3–32 characters; staff type it when they sign in, so the owner may prefer a shorter one).
Server commands run in `/opt/restaurant/deploy` as `azureuser`.

## Step 0: Before the setup day (development work, then one release)

| | What | Who |
|---|---|---|
| 0.1 | Owner's answers (03) and the menu checklist (02) complete and signed | Operator with the owner |
| 0.2 | Decide the gaps in 01 (search, logo, stations, Hindi). Any code change goes through `dev` → tests → release → server update (README, "Developing without touching the live site") | Operator |
| 0.3 | Install release `v1.2-live` (operator `reset-password`, menu search) on the server: `sh backup.sh && git pull && docker compose up -d --build`, as in 05, part A. Not yet done: the server runs `v1.0-live` | Operator, at a quiet hour |
| 0.4 | Build `setup.final.json` from `setup.draft.json` plus the answers: tables (C2), staff (G; owner excluded, see step 4), hidden dishes (`"active": false`), corrected prices, extra option groups (E). Kept out of Git | Developer |
| 0.5 | Check it on the development PC: dry run on a fresh throwaway restaurant, apply, run again → **0 changes** | Developer |
| 0.6 | Copy the backups off the server (`scp -r azureuser@172.198.57.41:/opt/restaurant/deploy/backups D:\restaurant-backups`) | Operator |

## Step 1: Confirm identity

- [ ] The outlet is confirmed in 03, A1–A4 (Sidhrawali, this outlet only).
- [ ] `<code>` is agreed and is **not** `test-dhaba`. `create-restaurant` refuses a code that
      already exists ("That restaurant code is already taken."), so it cannot overwrite another
      restaurant. `apply-setup` only ever acts on the restaurant named by `--code`.
- [ ] The display name (B1) and permission to show it (B2) are signed.

## Step 2: Verify a fresh backup

```
sh backup.sh
ls -lht backups | head -3
tail -3 backups/backup.log
```
The newest `restaurant-*.dump` must be from a few seconds ago and not tiny (today's is about
120 KB), and the log must show the nightly runs ("backup ok"). Copy it to the PC (0.6).

## Step 3: Dry run, then approval

The restaurant must exist before `apply-setup` can dry-run against it, and creating it changes
production. So:

- **3a, development PC:** dry run on a throwaway local restaurant (as 0.5). Expected: only
  "Would create" lines: the option groups, 22 categories (fewer if whole sections are hidden),
  the dishes, the tables and the staff; **no "Would update" lines**.
- **3b:** show the owner the list of what will be created (dish names, prices, hidden dishes,
  tables, staff names and roles). **Written approval** (name, date) before step 4.

## Step 4: Create the restaurant and the owner's account (production)

The owner's starting password is random and never shown to anyone. The owner then gets a
temporary password, shown once, which the app replaces with their own at first sign-in.
```
python3 -c "import secrets; print(secrets.token_urlsafe(32))" | docker compose exec -T api \
  python -m app.cli create-restaurant --code <code> --name "<B1 name>" \
  --owner-username <owner username> --owner-name "<owner name>" --owner-email <owner email> \
  --password-stdin
docker compose exec api python -m app.cli reset-password --code <code> --username <owner username>
```
Hand the temporary password to the owner in person (never in chat), then `clear` the screen.

## Step 5: Dry run on production, then apply

```
# from the PC:
scp setup.final.json azureuser@172.198.57.41:old-rao-setup.json
# on the server:
docker compose exec -T api python -m app.cli apply-setup --code <code> --file /dev/stdin --dry-run < ~/old-rao-setup.json
```
- [ ] The output matches 3a line for line (only "Would create", the same counts).
- [ ] The test restaurant is untouched: `docker compose exec -T api python -m app.cli apply-setup --code test-dhaba --file /dev/stdin --dry-run < setup.example.json` → only "Already up to date".

Then apply (same command without `--dry-run`). It prints each new staff member's temporary
password **once**: hand them over in person, then `clear`. Afterwards:
```
docker compose exec -T api python -m app.cli apply-setup --code <code> --file /dev/stdin --dry-run < ~/old-rao-setup.json   # must be 0 changes
rm ~/old-rao-setup.json        # it contains staff names
```

## Step 6: PIN settings and QR cards

- Owner or manager in the app: *Manage → Table PIN* → interval and length from 03, H1–H2.
- QR cards, printed **only once B5 is "yes"**:
  ```
  docker compose exec -T api python -m app.cli qr-sheet --code <code> --hint "<H3 wording>" > ~/old-rao-cards.html
  # on the PC:
  scp azureuser@172.198.57.41:old-rao-cards.html D:\Reataraunt\old-rao-cards.html
  ```
  Scan every card once after placing it on its table (docs/08 §2). The cards file has no PIN in
  it. `D:\Reataraunt\old-rao-cards.html` is ignored by Git (`*-cards.html`).

## Step 7: Devices and people

docs/08 §2–3: kitchen tablet (Android screen lock, charger, Wi-Fi tested at the pass, chime
audible), waiters' phones with the live **Restaurant Staff** app (not "Staff TEST"), everyone
signed in once with their own password, 15–20 minutes of training per group.

## Step 8: Acceptance tests, then the soft start

### 8a. Automated (development PC, on the exact version installed on the server)

```
cd backend && python -m pytest -q                       # 291+ tests
cd staff_app && flutter test
cd customer-web && npm run e2e                           # Playwright: Chromium, Firefox, WebKit
cd backend && python scripts/check_deployment.py https://order.housecareforyou.in
cd backend && python scripts/check_deployment.py https://order.housecareforyou.in --table <T1 link> --pin <current PIN>
```
The last line also checks the guest session cookie on the new restaurant (it opens one table
session and closes it again). Type the PIN in the terminal, never in chat.

### 8b. Checklist: what each area is covered by, and what to check on site

| Area | Automated coverage (backend unless noted) | On site at Sidhrawali, before guests |
|---|---|---|
| QR + current PIN | `test_customer_opens_a_session_with_qr_and_current_pin`, `test_every_failure_looks_the_same`, `test_pin_brute_force_is_capped_per_ip`, `..._per_table_across_ips_and_reset_by_rotation` | Scan every placed card; a wrong PIN is refused; the right PIN opens that table's menu |
| PIN rotation, old sessions | `test_rotation_kills_old_pin_but_keeps_existing_sessions`, `test_rotate_and_invalidate_ends_every_session`, `test_scheduled_rotation` | Rotate in the app: the old PIN stops working, a seated table keeps ordering |
| Cross-table isolation | `test_sessions_are_bound_to_one_table_and_expire`, `test_customer_order_must_be_for_its_sessions_table`, `test_diners_see_only_their_own_sessions_orders`, `test_close_table_ends_only_that_tables_sessions` | Two phones at two tables: each sees only its own orders |
| Cross-tenant isolation | `test_schema_rls.py` (14 tests), `tests/security/test_surface.py` (every route), `test_each_session_sees_only_its_own_restaurant`, `test_other_restaurants_tables_are_invisible`, `test_other_restaurants_menu_is_unreachable` | Signed in as `test-dhaba`, nothing of Old Rao is visible, and the other way round |
| Customer ordering | `test_customer_places_an_order`; browser `customer-web/e2e/customer.spec.ts` (3 engines) | Order from an iPhone (Safari) and a low-end Android on mobile data |
| Waiter ordering | `test_waiter_orders_for_a_table_without_a_pin`, `test_who_may_order_and_for_which_table`; Flutter `compose_order_cubit_test.dart` | Each waiter places one test order |
| Kitchen aggregation, traceability | `test_kitchen_board_lists_open_kitchen_work_oldest_first`; Flutter `kitchen_test.dart`, `kitchen_store_test.dart` | Same dish from 3 tables: summed on the board, each order still shows its table |
| Sold out | `test_only_managers_edit_but_kitchen_can_mark_sold_out`, `test_menu_rules_are_enforced_server_side`, `test_diners_see_only_live_items_with_options` | Kitchen marks a dish sold out: the guest page shows "Sold out" and refuses it |
| Option validation | `test_menu_rules_are_enforced_server_side`, `test_item_is_not_orderable_when_a_required_choice_is_impossible`, `test_chosen_options_keep_menu_order` | *Chilli Paneer Dry / Gravy* cannot be ordered without Dry or Gravy; *Aloo Paratha* + "With Dahi" shows +₹40 |
| Duplicate orders | `test_retries_and_double_taps_never_duplicate`, `test_staff_idempotency_is_per_person`, `security/test_races.py::test_simultaneous_retries_of_one_order_create_exactly_one` | Double-tap "Send" on a weak connection: one order |
| Cancellations and audit | `test_cancellation_policy_by_role_and_stage`, `test_cancellation_requests_need_a_manager`, `test_customer_cancellation_rules`, `test_staff.py` (audit actions) | Waiter requests a cancel, manager approves; the operator sees it in the audit log (`GET /api/v1/audit-logs` as the owner: the app has no screen for it) |
| WebSocket isolation | `test_events_never_cross_restaurants`, `security/test_websocket_abuse.py` (4 tests), `test_a_diners_cookie_is_not_a_staff_credential` | A `test-dhaba` kitchen tablet open at the same time receives no Old Rao orders |
| Network loss and recovery | `test_slow_consumers_are_told_to_resync`, `test_ping_and_reauthentication`; Flutter `realtime_client_test.dart`, `kitchen_store_test.dart` | Kitchen tablet Wi-Fi off for 2 minutes during a test order: the board stays, then catches up; the "Live" mark returns |

Test orders on site are cancelled with the reason **"Entered by mistake"**, so they don't
count in the pilot report.

### 8c. Soft start (docs/08 §1)

Weekday lunch, 2–3 tables first, operator present, paper KOT as the fallback. Daily 10-minute
review (docs/08 §5) using `pilot-report --code <code>` and the problem reports.

## Undo without deleting anything

| Problem | Undo |
|---|---|
| A dish should not be on the menu | Set `"active": false` in `setup.final.json`, dry run, apply; or *Sold out* in the app for today |
| A wrong price or name | Fix it in the file and re-apply (`apply-setup` updates prices; it never renames: a renamed dish arrives as a new dish, so hide the old one) |
| A table should not take orders | `regenerate-qr --code <code> --table <label>`: its card stops working at once and its history is kept (the API can also switch a table off; the app has no switch for it yet) |
| A staff account should not exist | Switch it off in the app (*Manage → Staff*) |
| Something broke after a code release | Server: `git checkout v1.0-live` (or the previous `-live` tag) and `docker compose up -d --build` |
| The database itself is damaged | `restore.sh` with the backup from step 2. Last resort: it puts back **every** restaurant's data as of that backup |
