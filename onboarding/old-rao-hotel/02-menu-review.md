# 02: Menu review (draft import and questions for the owner)

**Source:** <https://oldraohotels.in/menu/>, fetched live on **2026-10-15**: 22 sections and 167
priced entries. The page has no descriptions, dietary marks, sizes or spice levels. It is the
chain's website menu, so **no entry is confirmed for the Sidhrawali outlet** yet.

**Draft:** [setup.draft.json](setup.draft.json), in the existing `apply-setup` format
(docs/08 §9). Not imported anywhere except a throwaway restaurant on the development PC.

## How the website menu became the draft

- **Names are exactly as published**, spelling included (questions in part D). Sections keep the
  website's names and order.
- **Prices are as published** (rupees, display only: never totalled or billed).
- **Nothing was invented.** There are no descriptions, no veg/non-veg/egg marks, no photos and no
  options beyond what a published name spells out. The customer page shows no dietary mark when
  none is set.
- **One entry shows "Variable price":** *Cold Drinks / Real Juice* is published at ₹0.00. Decided
  2026-10-15: it is on the menu with no price and the description "Variable price" (the price
  depends on the drink), instead of ₹0.
- **Every other price is exactly as published** (decided 2026-10-15). The staff confirm them
  later; corrections are re-applied with the same file (part C).
- **Choices written into a name became option groups.** Each one is a required choice of one:

| Option group | Options | Used by |
|---|---|---|
| Sweet or salted | Sweet, Salted | Fresh Lime Water (Sweet & Salted), Fresh Lime Soda (Sweet & Salted) |
| Flavour (Green, Lemon, Peach) | Green, Lemon, Peach | Green / Lemon / Peach Tea |
| Flavour (Mango, Butter Scotch, Chocolate) | Mango, Butter Scotch, Chocolate | Mango / Butter Scotch / Chocolate Shake |
| Flavour (Vanilla, Strawberry, Pineapple) | Vanilla, Strawberry, Pineapple | Vanilla / Strawberry / Pineapple Shake |
| Dry or gravy | Dry, Gravy | Veg Manchurian Dry / Gravy, Chilli Mushroom Dry / Gravy, Chilli Paneer Dry / Gravy |
| Raita (Pineapple or Fruit) | Pineapple, Fruit | Pineapple / Fruit Raita |
| Add Dahi *(optional)* | With Dahi, +₹40 | Mix, Aloo Pyaz, Aloo, Gobhi and Paneer Paratha |

- **Five pairs were merged.** On the website every "X Paratha with Dahi" costs exactly ₹40 more
  than "X Paratha" (checked by the script that built the draft). So each pair is one dish with an
  optional "With Dahi (+₹40)": the guest sees the same two prices, and the kitchen sees
  "Aloo Paratha · With Dahi". That makes 167 published entries → 162 dishes.

## Questions for the owner

### A. Needed before anything goes live

1. **Branch availability.** Which dishes are actually served at Sidhrawali, and at which prices?
   Tick the checklist at the end; dishes marked "no" will be hidden (`"active": false`), never
   deleted.
2. **Cold Drinks / Real Juice.** Shown as "Variable price" for now. Should guests choose the
   drink or brand on the page (an option group, with prices), or is "Variable price" right?
3. **Pure vegetarian?** Every published dish looks vegetarian, but the website does not say so.
   If the owner confirms it, every dish gets the green veg mark; otherwise no marks.
4. **"Sweet & Salted" lime water and soda.** Is it a choice of sweet *or* salted, as drafted, or is
   "sweet & salted together" (mixed) also served?
5. **Paratha with Dahi.** Is "one paratha + optional Dahi (+₹40)" right, or should the 10 entries
   stay separate as on the website?
6. **"Dry / Gravy" dishes.** The names are kept in full ("Veg Manchurian Dry / Gravy") and the
   choice is required. Should the names be shortened to "Veg Manchurian" and so on now that Dry or
   Gravy is chosen separately?
7. **Chaap / Tikka section.** Are *Afghani Tikka*, *Achari Tikka* and *Malai Tikka* (₹310, without
   "Paneer") soya-chaap dishes? The names stay as they are unless the owner wants them clearer for
   guests.

### B. Possible duplicates (kept as published until answered)

| Entries | Question |
|---|---|
| *Chilli Paneer Dry / Gravy* (Chinese, ₹300) and *Rao Special Chilli Paneer* (Paneer Special, ₹329) | Two different dishes, or one? |
| *Lassi Salted* and *Lassi Sweet* (both ₹110) | Keep two dishes, or one "Lassi" with Sweet / Salted? |
| *Green / Lemon / Peach Tea* (₹55) and *Lemon Ice Tea* (₹220) | Is the ₹55 tea a hot tea and the ₹220 one iced? Guests may confuse them |
| *Extra Pav (1 Pc)*, *Extra Bhature (1 Pc)*, *Extra Poori (2 Pcs)* | Separate dishes (as drafted), or add-ons offered on Pav Bhaji, Choley Bhature and Poori Aloo? |

### C. Prices to confirm with the staff (kept as published until then)

| Entry | Published | Why it is flagged |
|---|---|---|
| Cold Drinks / Real Juice | ₹0.00 | Zero price: shown as "Variable price" |
| Extra Pav (1 Pc) | ₹75 | High for one pav next to Pav Bhaji at ₹175 |
| Paneer Bread Pakora / Aloo Bread Pakora | ₹40 / ₹35 | Far below the other pakoras (₹150–180): per piece? |
| Samosa / Samosa Paneer | ₹35 / ₹45 | Per piece? (not stated, unlike "Rasgulla (1 Pc)") |
| Aloo Tikki / Aloo Tikki Burger | ₹110 / ₹88 | The burger costs less than the tikki: correct? |
| Malai Roll, Angoori Rasmalai / Pineapple Rabri, Fruit Rabri | ₹70 / ₹75 | Per piece or per portion? |
| Aloo Tikki Burger ₹88, Rajma Rashila ₹258, Dal Dhaba ₹324, Rao Special Chilli Paneer ₹329, Chana Amritshari ₹339, Paneer Do Pyaza / Paneer Lababdar / Rao Special Paneer Butter Masala / Handi Paneer ₹362, Kadhai Mushroom ₹373 | | Not multiples of 5, unlike the rest of the menu: correct as printed? |

### D. Spelling (kept exactly as published)

*Chana Amritshari* (Amritsari?), *Rajma Rashila* (Rasila?), *Sambhar Idly*, *Hakka Noodle*,
*Butter Scotch*, *Choley Bhature*: correct them, or keep the restaurant's own spelling?

### E. Not on the website (added only if the owner asks)

- Descriptions: for example what comes in *Veg Thali*, *Rao Special Thali*, *Tandoori Platter* and
  *Combo Platter*.
- Choices the kitchen actually asks about: spice level, half/full plates, pizza sizes, butter on
  rotis, and so on.
- Section descriptions, dish photos (only with the restaurant's permission), section order.

## Dish-by-dish checklist (for the owner)

Tick each dish as available or not at Sidhrawali and write the price if it differs.

#### Beverages

| Published item | Published price | In the draft | Available here? | Price here |
|---|---|---|---|---|
| Fresh Lime Water (Sweet & Salted) | ₹80.00 | dish with "Sweet or salted" | ☐ yes ☐ no | |
| Fresh Lime Soda (Sweet & Salted) | ₹90.00 | dish with "Sweet or salted" | ☐ yes ☐ no | |
| Shikanji | ₹80.00 | dish | ☐ yes ☐ no | |
| Cold Drinks / Real Juice | ₹0.00 | dish, price shown as "Variable price" | ☐ yes ☐ no | |
| Special Chaach (Butter Milk) | ₹85.00 | dish | ☐ yes ☐ no | |
| Lassi Salted | ₹110.00 | dish | ☐ yes ☐ no | |
| Lassi Sweet | ₹110.00 | dish | ☐ yes ☐ no | |
| Cold Coffee with Vanilla Ice-cream | ₹185.00 | dish | ☐ yes ☐ no | |
| Cold Coffee | ₹150.00 | dish | ☐ yes ☐ no | |
| Green / Lemon / Peach Tea | ₹55.00 | dish with "Flavour (Green, Lemon, Peach)" | ☐ yes ☐ no | |

#### Pakora

| Published item | Published price | In the draft | Available here? | Price here |
|---|---|---|---|---|
| Pyaz Pakora | ₹150.00 | dish | ☐ yes ☐ no | |
| Aloo Pyaz Pakora | ₹150.00 | dish | ☐ yes ☐ no | |
| Vegetable Pakora | ₹150.00 | dish | ☐ yes ☐ no | |
| Paneer Pakora | ₹180.00 | dish | ☐ yes ☐ no | |
| Paneer Bread Pakora | ₹40.00 | dish | ☐ yes ☐ no | |
| Aloo Bread Pakora | ₹35.00 | dish | ☐ yes ☐ no | |

#### Mocktails & Shakes

| Published item | Published price | In the draft | Available here? | Price here |
|---|---|---|---|---|
| Mango / Butter Scotch / Chocolate Shake | ₹185.00 | dish with "Flavour (Mango, Butter Scotch, Chocolate)" | ☐ yes ☐ no | |
| Vanilla / Strawberry / Pineapple Shake | ₹175.00 | dish with "Flavour (Vanilla, Strawberry, Pineapple)" | ☐ yes ☐ no | |
| Lemon Ice Tea | ₹220.00 | dish | ☐ yes ☐ no | |
| Blue Lagoon | ₹220.00 | dish | ☐ yes ☐ no | |
| Green Apple | ₹150.00 | dish | ☐ yes ☐ no | |
| Virgin Mojito | ₹150.00 | dish | ☐ yes ☐ no | |

#### Indian Snacks

| Published item | Published price | In the draft | Available here? | Price here |
|---|---|---|---|---|
| Aloo Tikki | ₹110.00 | dish | ☐ yes ☐ no | |
| Samosa Paneer | ₹45.00 | dish | ☐ yes ☐ no | |
| Samosa | ₹35.00 | dish | ☐ yes ☐ no | |
| Extra Pav (1 Pc) | ₹75.00 | dish | ☐ yes ☐ no | |
| Pav Bhaji | ₹175.00 | dish | ☐ yes ☐ no | |
| Extra Bhature (1 Pc) | ₹70.00 | dish | ☐ yes ☐ no | |
| Extra Poori (2 Pcs) | ₹70.00 | dish | ☐ yes ☐ no | |
| Poori Aloo with Halwa (4 Pcs) | ₹200.00 | dish | ☐ yes ☐ no | |
| Choley Bhature (2 Pcs) | ₹200.00 | dish | ☐ yes ☐ no | |

#### Chaap / Tikka

| Published item | Published price | In the draft | Available here? | Price here |
|---|---|---|---|---|
| Paneer Afghani Tikka | ₹330.00 | dish | ☐ yes ☐ no | |
| Paneer Malai Tikka | ₹330.00 | dish | ☐ yes ☐ no | |
| Paneer Achari Tikka | ₹330.00 | dish | ☐ yes ☐ no | |
| Paneer Tikka | ₹320.00 | dish | ☐ yes ☐ no | |
| Tandoori Platter | ₹560.00 | dish | ☐ yes ☐ no | |
| Special Mushroom Tikka | ₹330.00 | dish | ☐ yes ☐ no | |
| Afghani Tikka | ₹310.00 | dish | ☐ yes ☐ no | |
| Achari Tikka | ₹310.00 | dish | ☐ yes ☐ no | |
| Malai Tikka | ₹310.00 | dish | ☐ yes ☐ no | |
| Soya Tikka | ₹295.00 | dish | ☐ yes ☐ no | |

#### Sandwich / Burger

| Published item | Published price | In the draft | Available here? | Price here |
|---|---|---|---|---|
| Combo Platter | ₹240.00 | dish | ☐ yes ☐ no | |
| French Fries | ₹135.00 | dish | ☐ yes ☐ no | |
| Paneer Burger | ₹140.00 | dish | ☐ yes ☐ no | |
| Veggie Delight Burger | ₹115.00 | dish | ☐ yes ☐ no | |
| Cheese Burger | ₹115.00 | dish | ☐ yes ☐ no | |
| Aloo Tikki Burger | ₹88.00 | dish | ☐ yes ☐ no | |
| Mexican Sandwich | ₹170.00 | dish | ☐ yes ☐ no | |
| Butter Toast | ₹110.00 | dish | ☐ yes ☐ no | |
| Veg Paneer Sandwich | ₹180.00 | dish | ☐ yes ☐ no | |
| Cheese Grilled Sandwich | ₹165.00 | dish | ☐ yes ☐ no | |

#### Pizza Special

| Published item | Published price | In the draft | Available here? | Price here |
|---|---|---|---|---|
| Rao Special Cheese Pizza | ₹310.00 | dish | ☐ yes ☐ no | |
| Mix Pizza | ₹290.00 | dish | ☐ yes ☐ no | |
| Onion Capsicum Pizza | ₹290.00 | dish | ☐ yes ☐ no | |
| Onion Tomato Pizza | ₹250.00 | dish | ☐ yes ☐ no | |

#### Street Food

| Published item | Published price | In the draft | Available here? | Price here |
|---|---|---|---|---|
| Bharwan Gol Gappe | ₹125.00 | dish | ☐ yes ☐ no | |
| Gol Gappe (6 pcs) | ₹60.00 | dish | ☐ yes ☐ no | |
| Raj Kachori | ₹160.00 | dish | ☐ yes ☐ no | |
| Bhalla Papdi | ₹150.00 | dish | ☐ yes ☐ no | |
| Papdi Chaat | ₹150.00 | dish | ☐ yes ☐ no | |
| Dahi Bhalla | ₹150.00 | dish | ☐ yes ☐ no | |

#### South Indian

| Published item | Published price | In the draft | Available here? | Price here |
|---|---|---|---|---|
| Sambhar Idly | ₹175.00 | dish | ☐ yes ☐ no | |
| Sambhar Vada | ₹175.00 | dish | ☐ yes ☐ no | |
| Rao Special Mix Uttapam | ₹255.00 | dish | ☐ yes ☐ no | |
| Tomato Uttapam | ₹235.00 | dish | ☐ yes ☐ no | |
| Onion Tomato Uttapam | ₹235.00 | dish | ☐ yes ☐ no | |
| Onion Uttapam | ₹235.00 | dish | ☐ yes ☐ no | |
| Butter Masala Dosa | ₹235.00 | dish | ☐ yes ☐ no | |
| Butter Plain Dosa | ₹205.00 | dish | ☐ yes ☐ no | |
| Onion Dosa | ₹215.00 | dish | ☐ yes ☐ no | |
| Paneer Dosa | ₹275.00 | dish | ☐ yes ☐ no | |

#### Chinese

| Published item | Published price | In the draft | Available here? | Price here |
|---|---|---|---|---|
| Veg Fried Rice | ₹225.00 | dish | ☐ yes ☐ no | |
| Hakka Noodle | ₹215.00 | dish | ☐ yes ☐ no | |
| Chilli Garlic Noodle | ₹200.00 | dish | ☐ yes ☐ no | |
| Cheese Noodle | ₹200.00 | dish | ☐ yes ☐ no | |
| Vegetable Noodle | ₹190.00 | dish | ☐ yes ☐ no | |
| Veg Manchurian Dry / Gravy | ₹270.00 | dish with "Dry or gravy" | ☐ yes ☐ no | |
| Chilli Mushroom Dry / Gravy | ₹310.00 | dish with "Dry or gravy" | ☐ yes ☐ no | |
| Chilli Paneer Dry / Gravy | ₹300.00 | dish with "Dry or gravy" | ☐ yes ☐ no | |
| Spring Roll | ₹185.00 | dish | ☐ yes ☐ no | |
| Chilli Potato | ₹230.00 | dish | ☐ yes ☐ no | |

#### Soups

| Published item | Published price | In the draft | Available here? | Price here |
|---|---|---|---|---|
| Hot & Sour Soup | ₹160.00 | dish | ☐ yes ☐ no | |
| Manchow Soup | ₹160.00 | dish | ☐ yes ☐ no | |
| Sweet Corn Soup | ₹160.00 | dish | ☐ yes ☐ no | |
| Vegetable Soup | ₹150.00 | dish | ☐ yes ☐ no | |
| Tomato Soup | ₹150.00 | dish | ☐ yes ☐ no | |

#### Paneer Special

| Published item | Published price | In the draft | Available here? | Price here |
|---|---|---|---|---|
| Paneer Do Pyaza | ₹362.00 | dish | ☐ yes ☐ no | |
| Paneer Lababdar | ₹362.00 | dish | ☐ yes ☐ no | |
| Rao Special Paneer Butter Masala | ₹362.00 | dish | ☐ yes ☐ no | |
| Handi Paneer | ₹362.00 | dish | ☐ yes ☐ no | |
| Paneer Tikka Masala | ₹395.00 | dish | ☐ yes ☐ no | |
| Rao Special Chilli Paneer | ₹329.00 | dish | ☐ yes ☐ no | |
| Kadhai Paneer | ₹330.00 | dish | ☐ yes ☐ no | |
| Mattar Paneer | ₹320.00 | dish | ☐ yes ☐ no | |
| Shahi Paneer | ₹320.00 | dish | ☐ yes ☐ no | |
| Palak Paneer | ₹310.00 | dish | ☐ yes ☐ no | |

#### Mushroom Special

| Published item | Published price | In the draft | Available here? | Price here |
|---|---|---|---|---|
| Kadhai Mushroom | ₹373.00 | dish | ☐ yes ☐ no | |
| Masala Mushroom | ₹330.00 | dish | ☐ yes ☐ no | |
| Mattar Mushroom | ₹330.00 | dish | ☐ yes ☐ no | |

#### Har Man Ki Pasand

| Published item | Published price | In the draft | Available here? | Price here |
|---|---|---|---|---|
| Aloo Methi | ₹285.00 | dish | ☐ yes ☐ no | |
| Dum Aloo Kashmiri | ₹320.00 | dish | ☐ yes ☐ no | |
| Dum Aloo | ₹255.00 | dish | ☐ yes ☐ no | |
| Aloo Mattar | ₹210.00 | dish | ☐ yes ☐ no | |
| Aloo Jeera | ₹210.00 | dish | ☐ yes ☐ no | |
| Aloo Palak | ₹210.00 | dish | ☐ yes ☐ no | |
| Palak Corn | ₹280.00 | dish | ☐ yes ☐ no | |
| Mix Vegetable | ₹285.00 | dish | ☐ yes ☐ no | |
| Gobhi Masala | ₹210.00 | dish | ☐ yes ☐ no | |
| Gobhi Do Pyaza | ₹210.00 | dish | ☐ yes ☐ no | |

#### Dal Special

| Published item | Published price | In the draft | Available here? | Price here |
|---|---|---|---|---|
| Dal Dhaba | ₹324.00 | dish | ☐ yes ☐ no | |
| Chana Amritshari | ₹339.00 | dish | ☐ yes ☐ no | |
| Rajma Rashila | ₹258.00 | dish | ☐ yes ☐ no | |
| Chana Masala | ₹240.00 | dish | ☐ yes ☐ no | |
| Dal Makhani | ₹290.00 | dish | ☐ yes ☐ no | |
| Dal Tadka | ₹250.00 | dish | ☐ yes ☐ no | |
| Dal Fry | ₹250.00 | dish | ☐ yes ☐ no | |

#### Raita & Salad

| Published item | Published price | In the draft | Available here? | Price here |
|---|---|---|---|---|
| Masala Papad | ₹70.00 | dish | ☐ yes ☐ no | |
| Fry Papad | ₹45.00 | dish | ☐ yes ☐ no | |
| Tandoori Papad | ₹35.00 | dish | ☐ yes ☐ no | |
| Green Salad | ₹90.00 | dish | ☐ yes ☐ no | |
| Pineapple / Fruit Raita | ₹210.00 | dish with "Raita (Pineapple or Fruit)" | ☐ yes ☐ no | |
| Mix Veg Raita | ₹165.00 | dish | ☐ yes ☐ no | |
| Boondi Raita | ₹160.00 | dish | ☐ yes ☐ no | |
| Plain Dahi | ₹110.00 | dish | ☐ yes ☐ no | |

#### Thali

| Published item | Published price | In the draft | Available here? | Price here |
|---|---|---|---|---|
| Veg Thali | ₹285.00 | dish | ☐ yes ☐ no | |
| Rao Special Thali | ₹365.00 | dish | ☐ yes ☐ no | |

#### Combo Meal

| Published item | Published price | In the draft | Available here? | Price here |
|---|---|---|---|---|
| Dal Makhani Rice | ₹285.00 | dish | ☐ yes ☐ no | |
| Chana Rice | ₹285.00 | dish | ☐ yes ☐ no | |
| Kadhi Rice | ₹230.00 | dish | ☐ yes ☐ no | |
| Rajma Rice | ₹250.00 | dish | ☐ yes ☐ no | |

#### Rice Special

| Published item | Published price | In the draft | Available here? | Price here |
|---|---|---|---|---|
| Tandoori Chaap Biryani | ₹340.00 | dish | ☐ yes ☐ no | |
| Veg Biryani | ₹280.00 | dish | ☐ yes ☐ no | |
| Paneer Pulao | ₹255.00 | dish | ☐ yes ☐ no | |
| Mattar Pulao | ₹230.00 | dish | ☐ yes ☐ no | |
| Veg Pulao | ₹230.00 | dish | ☐ yes ☐ no | |
| Jeera Rice | ₹180.00 | dish | ☐ yes ☐ no | |
| Plain Rice | ₹160.00 | dish | ☐ yes ☐ no | |

#### Indian Paratha

| Published item | Published price | In the draft | Available here? | Price here |
|---|---|---|---|---|
| Mix Paratha with Dahi | ₹175.00 | Mix Paratha + option "With Dahi" (+₹40) | ☐ yes ☐ no | |
| Mix Paratha | ₹135.00 | dish with "Add Dahi" | ☐ yes ☐ no | |
| Aloo Pyaz Paratha with Dahi | ₹165.00 | Aloo Pyaz Paratha + option "With Dahi" (+₹40) | ☐ yes ☐ no | |
| Aloo Pyaz Paratha | ₹125.00 | dish with "Add Dahi" | ☐ yes ☐ no | |
| Aloo Paratha with Dahi | ₹165.00 | Aloo Paratha + option "With Dahi" (+₹40) | ☐ yes ☐ no | |
| Aloo Paratha | ₹125.00 | dish with "Add Dahi" | ☐ yes ☐ no | |
| Gobhi Paratha with Dahi | ₹165.00 | Gobhi Paratha + option "With Dahi" (+₹40) | ☐ yes ☐ no | |
| Gobhi Paratha | ₹125.00 | dish with "Add Dahi" | ☐ yes ☐ no | |
| Paneer Paratha with Dahi | ₹185.00 | Paneer Paratha + option "With Dahi" (+₹40) | ☐ yes ☐ no | |
| Paneer Paratha | ₹145.00 | dish with "Add Dahi" | ☐ yes ☐ no | |

#### Indian Bread

| Published item | Published price | In the draft | Available here? | Price here |
|---|---|---|---|---|
| Rao Special Onion Kulcha | ₹135.00 | dish | ☐ yes ☐ no | |
| Laccha Paratha | ₹70.00 | dish | ☐ yes ☐ no | |
| Paneer Naan | ₹140.00 | dish | ☐ yes ☐ no | |
| Garlic Naan | ₹115.00 | dish | ☐ yes ☐ no | |
| Gobhi Naan | ₹125.00 | dish | ☐ yes ☐ no | |
| Stuffed Naan | ₹125.00 | dish | ☐ yes ☐ no | |
| Aloo Naan | ₹115.00 | dish | ☐ yes ☐ no | |
| Butter Naan | ₹80.00 | dish | ☐ yes ☐ no | |
| Plain Naan | ₹70.00 | dish | ☐ yes ☐ no | |
| Onion Missi Roti | ₹70.00 | dish | ☐ yes ☐ no | |

#### Sweet Dishes

| Published item | Published price | In the draft | Available here? | Price here |
|---|---|---|---|---|
| Malai Roll | ₹70.00 | dish | ☐ yes ☐ no | |
| Angoori Rasmalai | ₹70.00 | dish | ☐ yes ☐ no | |
| Pineapple Rabri | ₹75.00 | dish | ☐ yes ☐ no | |
| Fruit Rabri | ₹75.00 | dish | ☐ yes ☐ no | |
| Bengali Sweets (1 Pc) | ₹50.00 | dish | ☐ yes ☐ no | |
| Moong Dal Halwa | ₹100.00 | dish | ☐ yes ☐ no | |
| Kesar Kheer | ₹100.00 | dish | ☐ yes ☐ no | |
| Rabri (50 Gm) | ₹70.00 | dish | ☐ yes ☐ no | |
| Jalebi (100 Gm) | ₹95.00 | dish | ☐ yes ☐ no | |
| Rasgulla (1 Pc) | ₹40.00 | dish | ☐ yes ☐ no | |
