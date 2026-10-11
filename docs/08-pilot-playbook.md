# 08 — Pilot playbook (Phase 11)

Spec §48, Phase 11: *deploy to ONE real restaurant; observe customer ordering, waiter workflow,
kitchen workflow, PIN usage, QR scanning, network failures, order mistakes and staff confusion;
use these observations to improve the product before expanding.*

This is the plan for doing that: what to prepare, how to train the staff, how to start, what to
watch every day, what to do when something goes wrong, and how to decide what comes next. The
server side is in [07-deployment.md](07-deployment.md).

## 1. Shape of the pilot

| Week | What happens |
|---|---|
| Before | Server up and checked; menu, tables and staff loaded; devices set up; staff trained; QR cards printed. |
| Week 1 | **Soft start.** Weekday lunch only, 2–3 tables first, then the whole floor. You (the operator) are in the restaurant. Paper orders remain the fallback. |
| Week 2 | All services including the dinner rush. Daily 10-minute review (§5). Fix what blocks people. |
| Week 3–4 | Normal running with less help. Weekly report. Decide (§8). |

Agree beforehand with the owner: who decides to switch back to paper during a service (usually
the floor manager), and how you are reached during service hours.

## 2. Preparation checklist

**System**
- [ ] Deployed and `check_deployment.py` passes all checks (07 §3).
- [ ] Nightly backup running and copied off the server; one restore rehearsed (07 §5).
- [ ] Uptime monitor on `/healthz` alerting your phone.

**Restaurant data** (with the owner, from the printed menu)
- [ ] `setup.json` written (§9): every dish, its section, veg/non-veg/egg, price as on the menu
      card, the options people actually ask for (spice level, size, add-ons).
- [ ] Loaded with `apply-setup`; the owner has checked the menu **on a phone**, dish by dish.
- [ ] Tables named as the staff call them (T1…, "Family 1"); one QR card per table, laminated,
      fixed where guests sit. Scan each card once after placing it.
- [ ] Staff accounts created; every person has signed in once and chosen their own password.
- [ ] The owner's account has an email address (needed for *Forgot password?*).

**How guests get the PIN** — decide one, and print the card's PIN line to match (`qr-sheet --hint`):
- the waiter tells it when seating guests (default; the app shows it big: tap the PIN on
  *Tables*), or
- a small board at the counter, updated when the PIN changes (managers see when it will change).

**PIN rotation** — start with *Every 4 hours* (the default). If guests or staff struggle, a
longer interval is a setting, not a code change (*Manage → Table PIN*).

**Kitchen tablet**
- [ ] Android 10+, on its charger, mounted where the cooks can see and reach it.
- [ ] Wi-Fi tested *in the kitchen*, during service, with the microwave and tandoor fans on
      (open any website; the app's top bar must say **Live**).
- [ ] Media volume up; the app's sound switch on (it plays a test chime) — audible over the
      kitchen noise from where the cook stands.
- [ ] Android screen lock set (PIN). The app keeps the screen awake while the kitchen board is
      open; the lock protects the tablet when the app is closed or the tablet walks away.
- [ ] Signed in with the kitchen account; the board shows a test order end to end.

**Waiter phones** — staff app installed, signed in, notifications not needed; each waiter has
taken one test order and marked it served.

**Customer page on real phones** (an open item from Phase 10) — scan a card with:
- [ ] an iPhone (Safari), and
- [ ] a low-end Android on mobile data (not the restaurant Wi-Fi):

  PIN screen appears quickly, the menu scrolls smoothly, an order goes through, its status updates.

**Fallback** — a pad of paper order slips (KOT) at the pass, and every waiter knows the rule in §6.

## 3. Staff training (15–20 minutes per group, on their own devices)

**Waiters**
1. Sign in (restaurant code, username, password). *Tables* shows each table: free, phones
   ordering, open orders, **ready to serve**.
2. Take an order for a table: dishes, options, notes → *Send to kitchen*. It appears on the
   kitchen tablet within a second or two.
3. When the kitchen marks it ready, the table tile turns green: serve, then *Mark served*.
4. Guests ordering by phone: their orders appear like yours (*From customer phone*). Tell guests
   the PIN (tap it on *Tables* to show it big).
5. Cancelling: before the kitchen starts, cancel directly and pick why (**Entered by mistake**
   when you made the mistake). Once cooking has started, *Ask a manager to cancel*.
6. Anything odd — wrong order, guest stuck at the PIN, the app confusing, Wi-Fi — use
   **Report a problem** (account menu, or the ⋮ on an order). It takes ten seconds and it is how
   we find out what to fix.
7. *Close table* when the party leaves: guests' phones are signed out of that table.

**Kitchen**
1. Three lanes: **New**, **Cooking** (with *To cook* summing each dish across tables), **Ready**.
2. *Accept* / *Start cooking* / *Ready*. The tablet chimes for new orders.
3. **Sold out** (top bar): switch a dish off the moment it runs out; switch it back when it is
   available. Guests and waiters can no longer order it.
4. A red **STOP** notice means an order you were cooking was cancelled: read it, tap OK.
5. The flag button on a ticket (*Can't make it — ask a manager to cancel*) sends a
   cancellation request to the managers; keep cooking until they decide.
6. If the bar says the server can't be reached: the orders on screen stay; buttons pause until it
   is back. Keep cooking from the screen; tell the floor manager if it lasts more than a few
   minutes (§6).

**Managers and the owner**
1. Everything above, plus **Orders → Waiting for your decision**: cancellation requests, with
   *Yes, cancel it* / *Keep cooking* on the order itself.
2. **Manage restaurant** (account menu): *Table PIN* (see it, change it now, how often it
   changes, sign out every guest phone), *Sold-out dishes*, *Staff* (add a person — the app
   shows a temporary password once; reset a password; switch someone off), *Problem reports*.
3. If the PIN gets out (people ordering from outside): *Change PIN now* with *also sign out
   phones already ordering*.

## 4. What to observe — and where each answer comes from

`python -m app.cli pilot-report --code <code> --days 7` turns the system's own data into a
report (orders, timings, PIN use, cancellations, problem reports); it holds no money figures and
nothing about individual guests. The rest comes from staff reports and from watching.

| Spec item | From the data (pilot report) | From staff and observation | Warning sign |
|---|---|---|---|
| Customer ordering | share of orders from guests' phones; orders per day/hour | Do guests order a second time by phone? Who gives up and calls the waiter? | phone share falls after week 1 |
| QR scanning | *QR opened* per day vs *PIN accepted* | Cards readable in the light? Cards moved or damaged? | many QR opens, few PINs accepted |
| PIN usage | wrong PINs as a share of tries; *locked out* count | How do guests learn the PIN? Do waiters get asked repeatedly? | > 1 wrong try in 5; any lock-outs |
| Waiter workflow | *ready → served* time; cancellations by staff | Where do waiters hesitate? Do they still write on paper? | ready→served p90 above a few minutes |
| Kitchen workflow | *ordered → accepted*, *ordered → ready* (median, 90 %); orders past the delay mark | Is the tablet visible and reachable? Do cooks use *Sold out*? | late orders rising; sold-out switches never used |
| Network failures | *connections dropped* per day (staff devices) | When, where, how long; what staff did meanwhile | drops during every rush |
| Order mistakes | cancellations **Entered by mistake**; *Order mistake* reports | What kind: wrong dish, wrong table, missing option? | more than 1–2 per service |
| Staff confusion | *App was confusing* reports | The screen and the moment; what they expected instead | the same screen reported twice |

## 5. Daily routine (10 minutes, after lunch or before dinner)

1. Open *Manage → Problem reports* (or run the pilot report with `--days 1`).
2. Ask one waiter and one cook: *What slowed you down? What did a guest ask you? What would you
   change?* Write the answers in the sheet below, in their words.
3. Look at the warning signs in §4. Anything that blocks service is fixed (or worked around)
   before the next service; everything else goes on the list for the weekly review.

**Observation sheet** (print one per day)

| Time | Who / where | What happened (in their words) | Category (§4) | Severity: blocks service / slows / annoys | Follow-up |
|---|---|---|---|---|---|
| | | | | | |

## 6. When something goes wrong during service

| Situation | Rule |
|---|---|
| Server unreachable for more than 5 minutes (every device says so) | Floor manager announces **paper**: waiters write slips, the kitchen cooks from slips. Orders already on the kitchen screen stay there. When the system is back, don't re-enter paper orders that were already cooked. Report it (category *Connection problem*). |
| Only the kitchen tablet is offline | It keeps showing its orders and pauses its buttons. Check the tablet's Wi-Fi; waiters tell the kitchen about new orders verbally until it reconnects. |
| A dish runs out | Kitchen switches it to **sold out** at once (top bar). |
| Wrong order sent | Cancel it with *Entered by mistake* (or ask a manager if cooking started) and send the right one. |
| Guest can't get past the PIN | Read the PIN out again / show it full screen; if it was just changed, the guest needs the new one. Report it (*Customer needed help*). |
| PIN got out, unknown orders appear | Manager: *Change PIN now* + *sign out phones*. Cancel the unknown orders. |
| Someone can't sign in | Manager resets their password in the app; the owner uses *Forgot password?*, or you run `reset-password` on the server (docs/07 §6). |

## 7. Success criteria (agree the numbers with the owner before the pilot)

- **No lost orders and no duplicates** — every order placed reached the kitchen once (the system
  is built for this; any counter-example is the top priority).
- Staff work **without help by the end of week 1**.
- Kitchen *ordered → ready* times **no worse than before the pilot** (measure a paper-based
  service beforehand if possible).
- PIN problems in **fewer than 1 in 10** tables; QR cards working on every table.
- Order mistakes **lower or equal** to the paper process.
- The owner wants to keep using it.

## 8. After the pilot: decisions to take

| Question | Inputs |
|---|---|
| Keep 10-second status polling for guests, or add server-sent events? | Guests' comments on status updates; *QR opened* vs orders. |
| PIN rotation interval and how guests learn the PIN | PIN rows of the report; waiters' comments. |
| How long to keep order history (kept indefinitely today) | The owner's needs; privacy (orders hold no personal data). |
| Which screens to change first | Problem reports, observation sheets (anything reported twice). |
| Menu editing in the app (today: the setup file, by the operator) | How often the menu changed during the pilot. |
| Ready to add a second restaurant? | §7 met; incidents of §6 handled without you. |

Record the decisions and their reasons in [05-security-reviews.md](05-security-reviews.md)
("Open items") and the phase plan in [01-architecture.md](01-architecture.md).

## 9. Setup file format (`apply-setup`)

One JSON file describes the restaurant; [`deploy/setup.example.json`](../deploy/setup.example.json)
is a complete example. Re-running it is safe: everything is matched by name (capital letters
don't matter); missing things are created and differing details updated; **nothing is deleted
or renamed**, and sold-out switches are left to the kitchen. Run it with `--dry-run` first.

```json
{
  "tables": ["T1", "T2", "Family 1"],
  "staff": [
    { "username": "ravi", "name": "Ravi", "role": "waiter" }
  ],
  "option_groups": [
    { "name": "Spice level", "min": 1, "max": 1, "options": ["Mild", "Medium", "Hot"] },
    { "name": "Add-ons", "min": 0, "max": 2,
      "options": [{ "name": "Extra cheese", "price": 40 }, "Onion salad"] }
  ],
  "menu": [
    { "category": "Starters", "description": "optional",
      "dishes": [
        { "name": "Paneer Tikka", "type": "veg", "price": 249,
          "description": "optional", "options": ["Spice level", "Add-ons"] }
      ] }
  ]
}
```

| Field | Meaning |
|---|---|
| `tables` | Labels, up to 20 characters, in display order. |
| `staff[].role` | `waiter`, `kitchen` or `admin` (manager). New people get a temporary password, printed once. Existing accounts are never changed from the file. |
| `option_groups[]` | `min`/`max`: how many options a guest must/may pick (`1`/`1` = choose one). An option is a name, or `{ "name", "price" }` where `price` is the extra in rupees. |
| `dishes[].type` | `veg`, `non_veg` or `egg` (shown with the usual marks). |
| `dishes[].price` | Rupees as on the menu card (`249` or `249.50`). **Display only** — the system never totals or bills. |
| `dishes[].options` | Names of option groups, in the order guests see them. |
| `dishes[].active` | `false` hides a dish from the menu (seasonal dishes). |
