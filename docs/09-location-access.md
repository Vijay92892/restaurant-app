# 09 — Location-based access (ordering without the PIN)

An optional, per-restaurant way in: a guest who scans a table's QR code at the restaurant taps
*Use my location*, their phone reports one fresh position, and if it is inside the restaurant's
radius they go straight to the menu without the table PIN. Off by default. Security model and
limits: [03-security.md §4.5](03-security.md). API: [04-api-contracts.md](04-api-contracts.md).

## 1. What it is, and what it isn't

- **It is** a convenience: no PIN to ask for or type, for guests whose phone shares its location.
- **It isn't** proof that someone is at the restaurant. A phone reports its own position, and that
  can be faked. Someone with a photo of a table card and a location-faking tool can order without
  the PIN. Keep the **PIN backup on** unless the restaurant accepts that, and remember that waiters
  see which tables order and closing a table ends its sessions.
- Guests' positions are used once for the check and **never stored** (no table holds them, request
  bodies are never logged).

## 2. Turning it on (owner or a manager, in the staff app)

*Manage → Customer access*:

1. **Restaurant location.** In Google Maps, find the restaurant, press and hold on the building
   itself (not the road or the car park) and copy the two numbers, e.g. `28.2449519, 76.8135882`.
   Paste them. Check the pin while standing in the dining area.
2. **Allowed distance.** 100 m covers a dining hall and outdoor seating; pick more (150–300 m) for
   a large site. Phones indoors are often 20–60 m off; half their own uncertainty is allowed on top.
3. **Allow the table PIN as a backup.** Recommended (default). Without it, guests whose phone can't
   confirm the location (denied permission, poor signal, old phone) must ask a waiter.
4. **Save.** The summary at the top says what guests now experience. The change is audited.

**Testing it away from the restaurant:** the check only passes near the saved point. To try it at
home, save your home's coordinates temporarily, test with your phone, then put the restaurant's
point back (or simply turn the option off again).

## 3. What guests see

The PIN screen gains a card, *Order without a PIN*, explaining that the phone's location is
checked once and not stored, with *Use my location*. The browser asks for permission only after
that tap. On success the menu opens; otherwise the page says what to do ("You don't seem to be at
the restaurant", "Your phone isn't sure enough where it is…") and offers the PIN if the backup is
on, or asks them to call a member of staff if not. Restaurants without the option see no change.

**"Location isn't allowed for this page", without being asked.** The browser remembers a
refusal (an earlier *Block*) and won't ask again; or the phone's location is off. Turn on the
phone's location; in Chrome tap the icon left of the web address → *Permissions* → *Location* →
*Allow* (iPhone: *Settings → Privacy → Location Services → Safari Websites → While Using*),
reload, tap *Use my location* again. Some QR-scanner apps open links in their own built-in
browser, which may never ask: open the link in Chrome or Safari instead.

## 4. Behaviour reference

| Situation | What happens |
|---|---|
| Option switched off | New PIN-free sessions refused at once; tables already seated keep ordering until their session expires (default 3 h) or staff end it |
| PIN backup off, option on | The PIN is refused (`access_method_off`), even the right one |
| PIN backup off, option off | The PIN works (the backup switch only matters while the option is on) |
| PIN rotated | Location sessions continue, like PIN sessions; *rotate and sign out every phone* ends both kinds |
| Table closed, *sign out every phone*, guest leaves | The session ends, as for PIN sessions |
| Same position report sent twice | The second is refused (`location_reused`) |
| Old report (> 2 min) or from the future (> 1 min) | Refused (`location_stale`) |
| Phone unsure (accuracy > 100 m) | Refused (`location_inaccurate`) |

**Server settings** (`deploy/api.env`, optional; defaults shown): `LOCATION_MAX_ACCURACY_M=100`,
`LOCATION_MAX_AGE_SECONDS=120`, `LOCATION_CLOCK_SKEW_SECONDS=60`. Rate limits are fixed in code
(docs/03 §4.5). Usage counts `location_accepted` / `location_refused` appear in the pilot report.

## 5. Rollout (production)

The feature ships **switched off for every restaurant**: after the update nothing changes for
guests until an owner or manager turns it on.

1. **Release** (after approval): merge `dev` into `main`, tag `v1.5-live`, push.
2. **Server:** `sh backup.sh`, `git pull`, `docker compose up -d --build`, then `docker compose ps`
   (api healthy) and, from the PC, `check_deployment.py` (all checks pass). Migration 0007 runs by
   itself: it only adds columns with safe defaults, relaxes `table_sessions.pin_version` to allow
   NULL and adds checks; existing sessions become `access_method = 'pin'` and satisfy them.
3. **Staff app:** install the new APK (version 1.2.0) on the managers' phones; *Customer access*
   appears under *Manage* for owners and managers only.
4. **Try it on the demo first** (section 2, with a temporary point), then decide with the
   restaurant whether to use it, and how (PIN backup on is the safe start).

## 6. Rollback

- **Instant, no deployment:** switch the option off in the app. New PIN-free sessions stop at once.
  To end the open ones too: *Table PIN → sign out every phone*.
- **Code:** on the server `git checkout v1.4.1-live && docker compose up -d --build` (later
  `git checkout main && git pull`). The previous version runs on the new schema: it ignores the new
  columns, and its PIN sessions satisfy the new checks.
- **Schema (last resort, after a fresh backup):**
  `docker compose run --rm migrate alembic downgrade 0006`. Location sessions get the restaurant's
  latest PIN version so `pin_version` can be NOT NULL again; the location columns are dropped.
