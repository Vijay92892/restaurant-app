# Old Rao Hotel (Sidhrawali): onboarding pack

**Status: DRAFT. Nothing has been created on the live server.** Every restaurant-specific
detail here is unconfirmed until the restaurant confirms it (questionnaire, file 03).

| | |
|---|---|
| Target outlet | Old Rao Hotel, Sidhrawali, NH-48 (old NH-8) Delhi–Jaipur highway: the "Main Branch" on the official locations page. Map pin given to us: 28.2449519, 76.8135882 ([Google Maps](https://www.google.com/maps/place/Old+Rao+Hotel/@28.2449519,76.8110133,818m/data=!3m2!1e3!4b1!4m6!3m5!1s0x390d478df68d5b63:0x2cfc8e6eac01dafa!8m2!3d28.2449519!4d76.8135882)). **Not yet confirmed with the restaurant.** The website's own "Directions" link for Sidhrawali is also used for the Kotwan entry, so the website alone cannot confirm the outlet. |
| Scope | This one outlet only, not the Old Rao Hotels chain (the website lists about 20 outlets). |
| Menu source | <https://oldraohotels.in/menu/>, fetched live on **2026-10-15**. It is the chain's website menu: nothing on it is confirmed for Sidhrawali. |
| Other pages read | <https://oldraohotels.in/locations/>, <https://oldraohotels.in/about-us/> (2026-10-15). No images were downloaded. |

## Files

| File | What it is |
|---|---|
| [01-assessment.md](01-assessment.md) | What the system already does for this pilot, and what would need changes |
| [setup.draft.json](setup.draft.json) | Draft menu import in the existing `apply-setup` format (docs/08 §9): 22 categories, 162 dishes, 7 option groups. Tables and staff are empty until the owner answers |
| [02-menu-review.md](02-menu-review.md) | How the website menu became the draft, every entry that needs the owner's answer, and a dish-by-dish availability checklist |
| [03-owner-questionnaire.md](03-owner-questionnaire.md) | The form to fill in with the owner |
| [04-setup-plan.md](04-setup-plan.md) | Step-by-step production setup (dry run first, approval before applying) and the acceptance tests |
| [05-demo-guide.md](05-demo-guide.md) | The demo restaurant "Old Rao Hotel (Demo)" for the first meeting: how to put it on the server and how to present it |
| [demo-extras.json](demo-extras.json) | The demo's 10 tables and 3 demo staff accounts (no personal data) |

## Checks already done on the draft (development PC only)

- Read with the operator CLI's own file validation: no errors.
- `apply-setup --dry-run` on a throwaway local restaurant: would create exactly 7 option groups,
  22 categories and 162 dishes, and nothing else.
- Applied to a throwaway local restaurant (`zz-menu-check`, development database), then run again:
  **0 changes** the second time (safe to re-run, no duplicates).
- The demo's whole server sequence (05, part A) rehearsed on the development database, and the
  demo's ordering page checked on an emulated phone (menu, search, "With Dahi", Dry or gravy).

## Rules for this folder

- The JSON setup format has no room for notes (unknown keys are refused), so the source and
  review date are recorded here and in 02.
- The owner's answers contain personal data (names, phone numbers, email addresses). They never
  go into Git. The final setup file with staff names is `setup.final.json`, which Git ignores; it
  is copied to the server for the one-time setup and deleted there afterwards (04).
- Passwords, table PINs and temporary passwords are never written here or in chat.
