# 05: Demo for the owner or manager

A demo restaurant, **"Old Rao Hotel (Demo)"** (code `oldrao-demo`), on the live server with the
menu from their website, 10 tables and demo staff accounts. It is for showing to the
restaurant's owner or manager **in person**. Until they agree:

- don't place demo cards on their tables, and don't share demo links or screenshots publicly
  (it carries their name);
- the name always says "(Demo)". Nobody can reach it without a printed card **and** the current
  PIN.

The real restaurant is set up separately later (04), with its own code, using the owner's
answers.

## A. Put the demo on the server (once, about 15 minutes)

Needs release **`v1.2-live`** (menu search and the operator password reset).

**On the PC** (PowerShell, in `D:\Reataraunt`):
```
git push origin main dev v1.1-live v1.2-live
```

**On the server** (`ssh azureuser@172.198.57.41`):
```
cd /opt/restaurant/deploy
sh backup.sh
git pull
docker compose up -d --build
```
Wait until `docker compose ps` shows **api (healthy)**. Then create the demo:
```
python3 -c "import secrets; print(secrets.token_urlsafe(32))" | docker compose exec -T api python -m app.cli create-restaurant --code oldrao-demo --name "Old Rao Hotel (Demo)" --owner-username owner --owner-name "Demo Owner" --password-stdin
docker compose exec -T api python -m app.cli apply-setup --code oldrao-demo --file /dev/stdin < ../onboarding/old-rao-hotel/setup.draft.json
docker compose exec -T api python -m app.cli apply-setup --code oldrao-demo --file /dev/stdin < ../onboarding/old-rao-hotel/demo-extras.json
docker compose exec api python -m app.cli reset-password --code oldrao-demo --username owner
docker compose exec -T api python -m app.cli qr-sheet --code oldrao-demo > ~/oldrao-demo-cards.html
exit
```
- The first `apply-setup` lists the 191 things it created (menu).
- The second prints **temporary passwords** for `manager`, `waiter` and `kitchen`, and
  `reset-password` prints one for `owner`. Write them down on paper (never in chat).

**On the PC**, fetch the QR cards and print one or two (T1, Family 1):
```
scp azureuser@172.198.57.41:oldrao-demo-cards.html D:\Reataraunt\oldrao-demo-cards.html
```

## B. Before the meeting (10 minutes)

1. **Your phone, staff app (Restaurant Staff):** sign out, then sign in with code `oldrao-demo`,
   username `owner` and the temporary password. Choose your own password.
2. **Kitchen screen:** on a second phone or a tablet, sign in as `kitchen`. With only one phone,
   use the account menu → **Kitchen screen** on your own phone. Switch its sound on and turn the
   media volume up.
3. **Note the PIN:** in the app, *Manage → Table PIN* (it changes every 4 hours; check it shortly
   before the meeting).
4. **Rehearse once:** scan the T1 card with mobile data, place an order, and watch it arrive on
   the kitchen screen. Then cancel it as a manager with the reason **"Entered by mistake"**.
5. Phones charged, mobile data on, printed card with you.

## C. The demo (about 5 minutes)

Let the owner or manager use **their own phone** as the guest.

1. **"A family sits at table T1."** They scan the card with the phone camera. Their own menu
   opens in the browser, with no app to install.
2. **"The waiter tells them today's PIN."** You read it out; they type it.
3. **They order.** They search "paratha", pick *Aloo Paratha* + *With Dahi* (+₹40), then add
   *Chilli Paneer*: the page insists on **Dry or Gravy**, so the kitchen never has to ask. They
   tap *Place order*.
4. **The kitchen screen chimes** with "T1" and every choice spelled out. Tap *Accept → Start
   cooking → Ready*: their phone shows each step within a few seconds.
5. **Manager tools on your phone:** mark a dish *Sold out* (guests can no longer order it), the
   table PIN, staff accounts, and approving a waiter's cancellation.
6. **The waiter's way:** in the app, take an order for a table directly, for guests who don't
   want to scan.

Points worth saying:

- **No payments and no billing.** Their counter and billing stay exactly as they are; this only
  carries orders from the table to the kitchen.
- **Fewer mistakes:** no handwriting, every option chosen, the table number on every order.
- **Safe:** the PIN changes every few hours, so a photo of the card is useless at home; nobody
  outside the restaurant can order.
- **Works on mobile data.** If the internet goes down, paper KOT is the fallback, as today.
- **The menu is from their website.** They confirm which dishes and prices apply at Sidhrawali
  (checklist in 02) before anything goes live.
- **The pilot starts small:** a few tables at weekday lunch, with you present (docs/08).

## D. At the end of the meeting

- Leave the questionnaire (03) and the menu checklist (end of 02), or fill them in together.
- Agree on who answers, by when, and who signs the permission (03, part B).

## E. Afterwards

- **If they agree:** set up the real restaurant with 04 (a new code; the demo stays a demo).
- **If not, or not yet:** without the PIN the cards are useless. In the app, *Manage → Table PIN*
  → change the PIN and end all sessions. To retire a printed card for good:
  `docker compose exec api python -m app.cli regenerate-qr --code oldrao-demo --table T1`.
  Nothing has to be deleted.
